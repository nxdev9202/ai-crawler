"""쿠팡 크롤러.

쿠팡은 봇 차단이 강해(데이터센터 IP 차단, 헤더 검증) 실패 확률이 있다.
실패 시 개별 상품 error로 기록하고 계속 진행한다.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Callable

from playwright.async_api import Page

from ..config import settings
from .base import (
    anonymous_context,
    auto_scroll,
    browser_context,
    new_session_id,
    extract_reviews,
    extract_specs,
    polite_delay,
    specs_to_text,
)

SEARCH_URL = "https://www.coupang.com/np/search?q={q}&channel=user"

COLLECT_LIST_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const toInt = (s) => {
    const m = (s || '').replace(/[^0-9]/g, '');
    return m ? parseInt(m, 10) : null;
  };
  const out = [];
  const seen = new Set();
  const cards = document.querySelectorAll(
    'li.search-product, ul#productList > li, li[class*="ProductUnit"]'
  );
  cards.forEach(card => {
    const a = card.querySelector('a.search-product-link, a[href*="/vp/products/"]');
    if (!a) return;
    const url = a.href;
    if (!url || seen.has(url)) return;
    seen.add(url);
    const title = clean(card.querySelector('[class*="name"], .name')?.innerText || card.querySelector('img')?.alt || '');
    const price = toInt(card.querySelector('[class*="price-value"], .price-value, strong.price-value')?.innerText || card.innerText.match(/([0-9,]+)\s*원/)?.[1] || '');
    let img = '';
    const imgEl = card.querySelector('img');
    if (imgEl) img = imgEl.src || imgEl.getAttribute('data-src') || '';
    if (img && img.startsWith('//')) img = 'https:' + img;
    out.push({ title, url, price, image: img });
  });
  return out;
}
"""


# 쿠팡 상세의 배송/교환·반품 정책 표에서 나오는 비(非)상품 스펙 키
CP_NOISE_TOKENS = (
    "배송방법", "묶음배송", "배송기간", "교환/반품", "반품 비용", "반품 신청",
    "의류/잡화", "계절상품", "전자/가전", "자동차용품", "CD/DVD", "GAME", "BOOK",
    "수입명품", "화장품", "설치상품", "청약철회", "판매자", "사업자",
)


def _filter_cp_specs(pairs: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in pairs.items() if not any(t in k for t in CP_NOISE_TOKENS)}


# 쿠팡 리뷰 API(next-api/review)를 페이지 안에서 직접 호출해 대량 수집.
# 리뷰는 rData.paging.contents[] 에 있고 size=10 고정, page 증가로 페이지네이션.
JS_FETCH_CP_REVIEWS = r"""
async ([pid, target]) => {
  const out = [];
  const maxPages = Math.ceil(target / 10);
  for (let page = 1; page <= maxPages; page++) {
    let j = {};
    try {
      const url = `/next-api/review?productId=${pid}&page=${page}&size=10&sortBy=ORDER_SCORE_ASC&ratingSummary=true&ratings=&market=`;
      const r = await fetch(url, { headers: { 'accept': 'application/json' } });
      if (r.status !== 200) break;
      j = await r.json();
    } catch (e) { break; }
    const rd = j.rData || {};
    const paging = rd.paging || {};
    const contents = paging.contents || [];
    for (const it of contents) {
      const c = (it.content || '').trim();
      if (c) out.push({
        score: (it.rating != null ? it.rating : (it.ratingValue != null ? it.ratingValue : null)),
        content: c,
        date: (it.reviewDate || it.registeredDatetime || it.reviewAt || '').toString().slice(0, 10),
      });
    }
    if (paging.isNext === false || contents.length < 10 || page >= (paging.totalPage || 1)) break;
    await new Promise((res) => setTimeout(res, 300));
  }
  return out;
}
"""


def _extract_product_id(url: str) -> str | None:
    m = re.search(r"/vp/products/(\d+)", url or "")
    return m.group(1) if m else None


async def _ensure_login(page: Page, log) -> bool:
    """쿠팡 홈으로 이동해 로그인 상태를 확인하고, 미로그인이면 로그인한다."""
    await page.goto("https://www.coupang.com/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    body = ""
    try:
        body = await page.inner_text("body")
    except Exception:
        pass
    # '마이쿠팡'은 로그아웃 상태에도 항상 있으므로 '로그아웃' 존재로만 판별
    if "로그아웃" in body:
        log("[쿠팡] 로그인 상태 확인됨.")
        return True

    # 크롤 시점의 자동 로그인은 Akamai(login.pang Access Denied)로 불가.
    # 로그인은 설정의 '미리 로그인'(진짜 Chrome + CDP)으로 먼저 수행해야 한다.
    log("[쿠팡] ⚠️ 미로그인 상태입니다. 설정 → 쿠팡 '미리 로그인'을 먼저 진행한 뒤 다시 크롤링하세요.")
    return False


async def _open_detail(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    await auto_scroll(page, steps=8, pause_ms=400)
    # 상품평 탭으로 이동 시도
    for sel in ['a:has-text("상품평")', 'li:has-text("상품평")', 'a:has-text("리뷰")']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click(timeout=2500)
                await page.wait_for_timeout(1200)
                await auto_scroll(page, steps=4, pause_ms=350)
                break
        except Exception:
            pass


async def crawl(
    query: str,
    max_products: int | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_product: Callable[[dict[str, Any]], Any] | None = None,
) -> list[dict[str, Any]]:
    max_products = max_products or settings.crawl_max_products
    log = on_progress or (lambda _m: None)
    results: list[dict[str, Any]] = []
    sid = new_session_id()  # 이 크롤 동안 로그인/익명 컨텍스트가 같은 IP를 쓰도록

    async with browser_context(session_id=sid) as ctx:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        # base에서 실제 크롬의 쿠팡 쿠키(로그인 세션)를 주입함. 홈에서 로그인 확인.
        try:
            await page.goto("https://www.coupang.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            hb = await page.inner_text("body")
            if "로그아웃" in hb:
                log("[쿠팡] 로그인 세션 확인됨(실제 크롬 쿠키 재사용).")
            else:
                log("[쿠팡] ⚠️ 로그인 세션이 없습니다. 평소 크롬에서 쿠팡에 로그인해두면 리뷰까지 수집됩니다.")
        except Exception:
            pass
        q = urllib.parse.quote(query)
        log(f"[쿠팡] 검색 페이지 이동: {query}")
        try:
            await page.goto(SEARCH_URL.format(q=q), wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
        except Exception as e:
            log(f"[쿠팡] 검색 접근 실패(차단 가능): {e}")
            await page.close()
            return results

        # 차단 페이지 감지
        body = ""
        try:
            body = await page.inner_text("body")
        except Exception:
            pass
        if "Access Denied" in body or "차단" in body[:200] or "block" in page.url.lower():
            log("[쿠팡] 접근 차단 감지. 헤드리스=false 또는 시간 간격을 두고 재시도 필요.")

        await auto_scroll(page, steps=8, pause_ms=400)
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
        log(f"[쿠팡] 목록에서 {len(uniq)}개 상품 확보.")

        # === Phase 1 (로그인): 리스트 + 상세 스펙 수집 (리뷰 제외 → 계정 노출 최소화) ===
        detail = await ctx.new_page()
        for i, it in enumerate(uniq, 1):
            entry: dict[str, Any] = {
                "source": "coupang",
                "rank": i,
                "title": it.get("title") or "",
                "price": it.get("price"),
                "mall_name": "쿠팡",
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
            try:
                log(f"[쿠팡] ({i}/{len(uniq)}) 상세 진입: {entry['title'][:30]}")
                await _open_detail(detail, it["url"])
                try:
                    h = await detail.locator("h1.prod-buy-header__title, h2.prod-buy-header__title, h1").first.inner_text(timeout=2500)
                    if h and len(h.strip()) > 3:
                        entry["title"] = h.strip()
                except Exception:
                    pass

                specs = await extract_specs(detail)
                page_text = ""
                try:
                    page_text = await detail.inner_text("body")
                except Exception:
                    pass
                rm = re.search(r"(\d\.\d)\s*점", page_text)
                cm = re.search(r"상품평\s*([\d,]+)", page_text)
                specs = _filter_cp_specs(specs)
                entry["spec_json"] = specs
                entry["spec_text"] = specs_to_text(entry["title"], specs)
                entry["rating"] = rm.group(1) if rm else None
                entry["review_count"] = cm.group(1) if cm else None
                entry["raw_json"]["product_id"] = (
                    _extract_product_id(detail.url) or _extract_product_id(it["url"])
                )
            except Exception as e:
                entry["crawl_error"] = f"{type(e).__name__}: {e}"
                log(f"[쿠팡] 상세 실패: {entry['crawl_error']}")
            results.append(entry)
            await polite_delay()

        await detail.close()
        await page.close()

    # === Phase 2: 리뷰 API 대량 호출 (로그인 세션 .userdata 재사용 - Windows 대응) ===
    log("[쿠팡] 리뷰 수집(API) 시작")
    try:
        async with browser_context(session_id=sid) as actx:
            rpage = actx.pages[0] if actx.pages else await actx.new_page()
            try:
                await rpage.goto("https://www.coupang.com/", wait_until="domcontentloaded")
                await rpage.wait_for_timeout(2500)
            except Exception:
                pass
            for entry in results:
                pid = entry["raw_json"].get("product_id")
                reviews: list[dict[str, Any]] = []
                if pid:
                    try:
                        reviews = await rpage.evaluate(JS_FETCH_CP_REVIEWS, [pid, settings.review_max])
                    except Exception:
                        reviews = []
                entry["reviews_json"] = reviews
                entry["raw_json"]["review_fetched"] = len(reviews)
                log(f"[쿠팡] 리뷰 {len(reviews)}개 - {entry['title'][:20]}")
                if on_product:
                    await on_product(entry)  # 스펙+리뷰 완성본 저장
                await polite_delay()
    except Exception as e:  # 익명 컨텍스트 실패 시 스펙만이라도 저장
        log(f"[쿠팡] 리뷰 단계 실패: {e} — 스펙만 저장합니다.")
        if on_product:
            for entry in results:
                await on_product(entry)

    log(f"[쿠팡] 완료: {len(results)}개")
    return results
