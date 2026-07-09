"""네이버 쇼핑 크롤러.

검색 1페이지의 상품 목록을 수집하고, 각 상세페이지에 진입해 스펙/리뷰를 수집한다.
스마트스토어/브랜드스토어는 페이지가 내부 JSON API로 데이터를 채우므로,
네트워크 응답(상품 API + 리뷰 API)을 가로채 구조화된 데이터를 그대로 얻는다.
카탈로그(가격비교)/외부몰은 DOM 폴백으로 스펙/리뷰를 긁는다.

핵심 전제: 네이버는 headless 브라우저와 UA 위조를 봇으로 차단하므로,
base.py에서 '실제 Chrome + 로그인된 지속 프로필 + 헤드풀'로 구동한다.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable

from playwright.async_api import Page

from ..accounts import get_accounts
from ..config import settings
from .base import (
    auto_scroll,
    browser_context,
    new_session_id,
    extract_reviews,
    extract_specs,
    polite_delay,
    specs_to_text,
)

SEARCH_URL = "https://search.shopping.naver.com/search/all?query={q}"

COLLECT_LIST_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const firstPrice = (s) => {
    const m = (s || '').match(/([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s*원/);
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : null;
  };
  const seen = new Set();
  const out = [];
  const cards = document.querySelectorAll(
    'div[class*="adProduct_item__"], div[class*="superSavingProduct_item__"], ' +
    'div[class*="product_item__"], li[class*="product_item__"]'
  );
  cards.forEach(card => {
    const cls = card.className || '';
    if (/purchaseOption_option_item/.test(cls)) return;
    let title = '', link = null;
    const titleEl = card.querySelector('[class*="title"] a, a[class*="title"], [class*="title__"]');
    if (titleEl) {
      title = clean(titleEl.innerText);
      link = titleEl.closest('a') ? titleEl.closest('a').href : (titleEl.querySelector('a')?.href || null);
    }
    if (!link) {
      for (const a of card.querySelectorAll('a[href]')) {
        const h = a.href || '';
        if (/ader\.naver|cr\.shopping\.naver|adcr|catalog\/|smartstore\.naver|brand\.naver/.test(h)) { link = h; break; }
      }
    }
    if (!link || seen.has(link)) return;
    if (!title) title = clean(card.querySelector('img')?.alt || '');
    let price = null;
    const priceEl = card.querySelector('[class*="price_area"], [class*="price__"], [class*="unit_price"]');
    price = firstPrice(priceEl ? priceEl.innerText : '');
    if (price === null) price = firstPrice(card.innerText);
    let mall = '';
    const mallEl = card.querySelector('[class*="mall_title"], [class*="mall_name"], [class*="mall__"]');
    if (mallEl) mall = clean(mallEl.innerText);
    let img = '';
    const imgEl = card.querySelector('img');
    if (imgEl) img = imgEl.src || imgEl.getAttribute('data-src') || '';
    const kind = /adProduct/.test(cls) ? 'ad' : (/superSaving/.test(cls) ? 'super' : 'organic');
    out.push({ title, url: link, price, mall, image: img, kind });
    seen.add(link);
  });
  return out;
}
"""

# 스펙 자연어화에서 제외할 판매자/법적 고지 성격의 키
NOISE_KEY_TOKENS = (
    "상호명", "대표자", "사업자등록", "통신판매업", "결제대금예치", "대표이사",
    "개인정보", "소비자상담", "청약철회", "환불", "지연배상", "분쟁", "A/S 관련 전화",
    "소비자피해보상", "품질보증", "교환·반품",
)


def _clean_mall(mall: str) -> str:
    return (mall or "").replace("정보", "").replace("상품만 보기", "").strip() or None


def _flatten_notice(notice: Any) -> dict[str, str]:
    """상품정보제공고시(중첩 dict)를 leaf 키:값 쌍으로 평탄화."""
    pairs: dict[str, str] = {}

    def walk(node: Any, leaf_key: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, leaf_key)
        elif isinstance(node, (str, int, float)):
            key = str(leaf_key).strip()
            val = str(node).strip()
            if key and val and len(key) < 90 and len(val) < 500:
                pairs.setdefault(key, val)

    walk(notice, "")
    return pairs


def _extract_reviews_from_json(obj: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if "reviewContent" in obj and obj.get("reviewContent"):
            out.append(
                {
                    "score": obj.get("reviewScore"),
                    "content": str(obj.get("reviewContent")).strip(),
                    "date": str(obj.get("createDate") or "")[:10],
                }
            )
        for v in obj.values():
            _extract_reviews_from_json(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _extract_reviews_from_json(v, out)


def _spec_text_from_pairs(title: str, pairs: dict[str, str]) -> str:
    """법적 고지·판매자정보를 제외하고 상품 스펙 위주로 자연어화."""
    useful = {
        k: v
        for k, v in pairs.items()
        if not any(tok in k for tok in NOISE_KEY_TOKENS)
        and v not in ("해당사항 없음",)
        and len(v) < 150
    }
    if not useful:
        return specs_to_text(title, pairs)
    return specs_to_text(title, useful)


# 페이지 안에서 리뷰 API를 직접 호출해 페이지네이션으로 대량 수집.
# pageSize는 20만 허용(그 이상 401), page만 증가시킨다.
JS_FETCH_REVIEWS = r"""
async ([merchant, origin, target]) => {
  const out = [];
  const maxPages = Math.ceil(target / 20);
  for (let page = 1; page <= maxPages; page++) {
    let j = {};
    try {
      const r = await fetch('/i/v1/contents/reviews/query-pages', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'accept': 'application/json, text/plain, */*' },
        body: JSON.stringify({ checkoutMerchantNo: merchant, originProductNo: origin, page, pageSize: 20, reviewSearchSortType: 'REVIEW_RANKING' })
      });
      if (r.status !== 200) break;
      j = await r.json();
    } catch (e) { break; }
    const c = j.contents || [];
    for (const it of c) {
      const t = (it.reviewContent || '').trim();
      if (t) out.push({ score: it.reviewScore, content: t, date: (it.createDate || '').slice(0, 10) });
    }
    if (j.last || c.length < 20 || page >= (j.totalPages || 1)) break;
    await new Promise((res) => setTimeout(res, 220));
  }
  return out;
}
"""


def _find_merchant(prod: dict[str, Any]) -> int | None:
    s = json.dumps(prod)
    for key in ("checkoutMerchantNo", "merchantNo", "payReferenceKey"):
        m = re.search(rf'"{key}":\s*"?([0-9]+)', s)
        if m:
            return int(m.group(1))
    return None


async def _crawl_smartstore_detail(detail: Page, url: str, entry: dict[str, Any]) -> bool:
    """스마트스토어/브랜드스토어: 상품 API 가로채기 + 리뷰 API 직접 호출."""
    captured: dict[str, Any] = {"product": None, "review_body": None}

    async def on_resp(resp) -> None:
        u = resp.url
        try:
            ct = resp.headers.get("content-type", "")
            if "json" in ct and "/products/" in u and "/i/v" in u and "withWindow" in u:
                captured["product"] = await resp.json()
        except Exception:
            pass

    async def on_req(req) -> None:
        # 리뷰 위젯이 스크롤로 로드될 때 나가는 첫 요청에서 merchant/origin 확보
        if "reviews/query-pages" in req.url and req.method == "POST" and not captured["review_body"]:
            captured["review_body"] = req.post_data

    detail.on("response", on_resp)
    detail.on("request", on_req)
    try:
        await detail.goto(url, wait_until="domcontentloaded")
        await detail.wait_for_timeout(2500)
        product_url = detail.url  # 리뷰 링크 클릭 없이 상품 URL 확정
        await auto_scroll(detail, steps=12, pause_ms=400)  # 상품 API + 리뷰 첫요청 유도
        await detail.wait_for_timeout(1000)
    finally:
        detail.remove_listener("response", on_resp)
        detail.remove_listener("request", on_req)

    prod = captured["product"]
    if not prod:
        return False

    entry["url"] = product_url
    if prod.get("name"):
        entry["title"] = prod["name"]
    entry["price"] = prod.get("discountedSalePrice") or prod.get("salePrice") or entry.get("price")

    ra = prod.get("reviewAmount") or {}
    if ra:
        entry["rating"] = str(ra.get("averageReviewScore")) if ra.get("averageReviewScore") is not None else None
        entry["review_count"] = str(ra.get("totalReviewCount")) if ra.get("totalReviewCount") is not None else None

    notice = prod.get("productInfoProvidedNoticeView")
    pairs = _flatten_notice(notice) if notice else {}
    for attr in (prod.get("productAttributes") or []):
        if attr.get("attributeName") and attr.get("attributeValueName"):
            pairs.setdefault(attr["attributeName"], attr["attributeValueName"])
    entry["spec_json"] = pairs
    entry["spec_text"] = _spec_text_from_pairs(entry["title"], pairs)

    # 리뷰: merchant/origin 확보 → API 페이지네이션으로 대량 수집
    merchant: int | None = None
    origin: int | None = None
    if captured["review_body"]:
        try:
            b = json.loads(captured["review_body"])
            merchant = b.get("checkoutMerchantNo")
            origin = b.get("originProductNo")
        except Exception:
            pass
    if origin is None:
        origin = prod.get("productNo")
    if merchant is None:
        merchant = _find_merchant(prod)

    reviews: list[dict[str, Any]] = []
    if merchant and origin:
        try:
            reviews = await detail.evaluate(JS_FETCH_REVIEWS, [merchant, origin, settings.review_max])
        except Exception:
            reviews = []
    entry["reviews_json"] = reviews
    entry["raw_json"]["smartstore_notice"] = notice
    entry["raw_json"]["review_meta"] = {"merchant": merchant, "origin": origin, "fetched": len(reviews)}
    return True


async def _crawl_generic_detail(detail: Page, url: str, entry: dict[str, Any]) -> None:
    """카탈로그/외부몰: DOM 폴백으로 스펙/리뷰 추출."""
    await detail.goto(url, wait_until="domcontentloaded")
    await detail.wait_for_timeout(1500)
    for sel in ['a:has-text("상세정보")', 'a:has-text("상품정보")', 'a:has-text("리뷰")']:
        try:
            el = detail.locator(sel).first
            if await el.count() > 0:
                await el.click(timeout=2000)
                await detail.wait_for_timeout(700)
        except Exception:
            pass
    await auto_scroll(detail, steps=8, pause_ms=350)
    entry["url"] = detail.url
    try:
        h = await detail.locator("h1, h2, [class*='product_title'], [class*='top_summary']").first.inner_text(timeout=2000)
        if h and len(h.strip()) > 3:
            entry["title"] = h.strip()
    except Exception:
        pass
    pairs = await extract_specs(detail)
    # 판매자/법적 노이즈 제거
    pairs = {k: v for k, v in pairs.items() if not any(t in k for t in NOISE_KEY_TOKENS)}
    entry["spec_json"] = pairs
    entry["spec_text"] = _spec_text_from_pairs(entry["title"], pairs)
    reviews = await extract_reviews(detail, max_n=15)
    entry["reviews_json"] = [{"content": r, "score": None, "date": ""} for r in reviews]


async def _ensure_login(page: Page, log) -> bool:
    """네이버 홈에서 로그인 상태 확인 후, 미로그인이면 전용 계정으로 로그인."""
    await page.goto("https://www.naver.com", wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    body = ""
    try:
        body = await page.inner_text("body")
    except Exception:
        pass
    if "로그아웃" in body:
        log("[네이버] 로그인 상태 확인됨.")
        return True

    acc = get_accounts()
    nid, npw = acc.get("naver_id"), acc.get("naver_pw")
    if not (nid and npw):
        log("[네이버] ⚠️ 미로그인 상태이고 앱에 네이버 계정이 없어 로그인 불가. 설정에서 계정을 입력하세요.")
        return False

    log("[네이버] 미로그인 → 전용 계정으로 로그인(2차 인증/기기등록 시 열린 창에서 처리, 최대 5분 대기).")
    await page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded")
    await page.wait_for_timeout(1200)
    try:
        await page.click("#id")
        await page.keyboard.type(nid, delay=90)
        await page.wait_for_timeout(350)
        await page.click("#pw")
        await page.keyboard.type(npw, delay=90)
        await page.wait_for_timeout(300)
        await page.click('button[type=submit], .btn_login, #log\\.login')
    except Exception as e:  # noqa: BLE001
        log(f"[네이버] 로그인 폼 입력 실패: {e}")
        return False

    for _ in range(150):  # 최대 5분(2차 인증 대기)
        await page.wait_for_timeout(2000)
        url = page.url
        b = ""
        try:
            b = await page.inner_text("body")
        except Exception:
            pass
        if "로그아웃" in b or ("naver.com/" in url and "nid" not in url):
            log("[네이버] 로그인 성공.")
            return True
    log("[네이버] 로그인 시간 초과(2차 인증 미완료 가능).")
    return False


async def crawl(
    query: str,
    max_products: int | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_product: Callable[[dict[str, Any]], Any] | None = None,
) -> list[dict[str, Any]]:
    max_products = max_products or settings.crawl_max_products
    log = on_progress or (lambda _m: None)
    results: list[dict[str, Any]] = []

    # 크롤 단위 sticky 프록시 세션(프록시 미설정 시 무영향)
    log("[네이버] 브라우저 실행 중… (크롬 창이 열립니다)")
    async with browser_context(session_id=new_session_id()) as ctx:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        log("[네이버] 브라우저 준비됨. 로그인 상태 확인 중…")
        # 크롤 전 로그인 확인/로그인
        await _ensure_login(page, log)
        q = urllib.parse.quote(query)
        log(f"[네이버] 검색 페이지 이동: {query}")
        await page.goto(SEARCH_URL.format(q=q), wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await auto_scroll(page, steps=8, pause_ms=400)

        body = ""
        try:
            body = await page.inner_text("body")
        except Exception:
            pass
        if "일시적으로 제한" in body or "보안 확인" in body:
            log("[네이버] ⚠️ 봇 차단/캡차 감지. 로그인 세션이 필요합니다(naver_login 실행).")
            return results

        items = await page.evaluate(COLLECT_LIST_JS)
        uniq: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            u = it.get("url")
            if not u or u in seen:
                continue
            seen.add(u)
            uniq.append(it)
        uniq = uniq[:max_products]
        log(f"[네이버] 목록에서 {len(uniq)}개 상품 확보. 상세페이지 진입 시작.")

        for i, it in enumerate(uniq, 1):
            entry: dict[str, Any] = {
                "source": "naver",
                "rank": i,
                "title": it.get("title") or "",
                "price": it.get("price"),
                "mall_name": _clean_mall(it.get("mall")),
                "url": it.get("url"),
                "image_url": it.get("image") or None,
                "spec_json": {},
                "spec_text": None,
                "reviews_json": [],
                "rating": None,
                "review_count": None,
                "raw_json": {"list_item": it},
                "crawl_error": None,
            }
            detail = await ctx.new_page()
            try:
                log(f"[네이버] ({i}/{len(uniq)}) 상세 진입: {entry['title'][:30]}")
                ok = await _crawl_smartstore_detail(detail, it["url"], entry)
                host = urllib.parse.urlparse(detail.url).netloc
                if not ok:
                    # 스마트스토어 API를 못 잡았으면 DOM 폴백
                    log(f"[네이버]   → API 미포착({host}), DOM 폴백")
                    await _crawl_generic_detail(detail, detail.url, entry)
                n_rev = len(entry["reviews_json"])
                n_spec = len(entry["spec_json"])
                log(f"[네이버]   ✓ 스펙 {n_spec}항목 · 리뷰 {n_rev}개 · 평점 {entry['rating']}")
            except Exception as e:  # noqa: BLE001
                entry["crawl_error"] = f"{type(e).__name__}: {e}"
                log(f"[네이버]   ✗ 실패: {entry['crawl_error']}")
            finally:
                await detail.close()
            results.append(entry)
            if on_product:
                await on_product(entry)  # 증분 저장
            await polite_delay()

    log(f"[네이버] 완료: {len(results)}개")
    return results
