"""크롤러 공통 유틸: 브라우저 컨텍스트, 지연, 스펙/리뷰 추출 스크립트."""
from __future__ import annotations

import asyncio
import contextlib
import os
import random
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from ..config import settings

# 로그인 쿠키/세션을 저장하는 지속 프로필 디렉터리(쓰기 가능한 데이터 폴더).
from ..paths import userdata_dir

USER_DATA_DIR = userdata_dir()


def proxy_kwargs(session_id: str | None = None) -> dict:
    """설정된 프록시를 Playwright launch 옵션으로 변환.

    앱 설정(.accounts.json)의 프록시를 우선 사용하고, 없으면 .env 폴백.
    프록시가 비활성/미설정이면 빈 dict. 로테이팅 프록시에서 '크롤 단위 sticky IP'를
    쓰려면 username에 `{session}` 토큰을 넣으면 크롤마다 session_id로 치환된다.
    (예: user-session-{session})
    """
    from ..accounts import get_proxy_config

    cfg = get_proxy_config()
    server = cfg.get("server") if cfg else (settings.proxy_server or "")
    if not server:
        return {}
    proxy: dict[str, str] = {"server": server}
    user = cfg.get("username") if cfg else settings.proxy_username
    if user:
        if session_id and "{session}" in user:
            user = user.replace("{session}", session_id)
        proxy["username"] = user
    pw = cfg.get("password") if cfg else settings.proxy_password
    if pw:
        proxy["password"] = pw
    return {"proxy": proxy}


def new_session_id() -> str:
    """크롤 단위 sticky 세션 식별자."""
    import secrets

    return secrets.token_hex(4)

# 봇 차단 완화를 위한 실사용자 유사 UA
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# navigator.webdriver 등 자동화 흔적 숨기기 (간단 stealth)
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ko-KR','ko','en-US','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = { runtime: {} };
"""


async def _inject_pasted_cookies(context) -> None:
    """사용자가 앱에 붙여넣은 세션 쿠키를 컨텍스트에 주입(있을 때만)."""
    try:
        from ..cookies import load_cookies

        cookies = load_cookies()
        if cookies:
            await context.add_cookies(cookies)
    except Exception:
        # 일부 쿠키가 형식 오류여도 크롤 자체는 계속되도록 개별 재시도
        try:
            from ..cookies import load_cookies

            for c in load_cookies():
                try:
                    await context.add_cookies([c])
                except Exception:
                    pass
        except Exception:
            pass


def _terminate_chrome_and_unlock(user_data_dir: str) -> None:
    """실제 프로필 크롤 직전, 남아있는 크롬 프로세스 종료 + 프로필 잠금 파일 제거.

    Windows에서 크롬을 닫아도 백그라운드 프로세스가 프로필을 잡고 있으면 hand-off로
    크롤러가 프로필을 제어할 수 없다. 실제 프로필 모드는 어차피 사용자가 크롬을 닫아야
    하므로, 남은 프로세스를 강제 종료하는 것은 안전하다.
    """
    import subprocess
    import sys as _sys

    try:
        if _sys.platform.startswith("win"):
            # 백그라운드 포함 모든 chrome.exe 종료(사용자는 크롤 전 크롬을 닫아둔 상태)
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                capture_output=True,
                timeout=10,
            )
        elif _sys.platform == "darwin":
            subprocess.run(["pkill", "-x", "Google Chrome"], capture_output=True, timeout=10)
    except Exception:
        pass
    # 잠금/싱글턴 파일 제거(다음 실행이 hand-off 없이 프로필을 점유하도록)
    import time

    time.sleep(1.2)  # 프로세스가 파일 핸들을 놓을 시간
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            os.remove(os.path.join(user_data_dir, name))
        except Exception:
            pass


@contextlib.asynccontextmanager
async def browser_context(session_id: str | None = None):
    """로그인 세션이 유지되는 지속(persistent) 브라우저 컨텍스트.

    - 실제 Google Chrome(channel="chrome")을 사용해 봇 탐지를 줄인다.
    - USER_DATA_DIR에 쿠키/세션을 저장해 네이버 로그인 상태를 재사용한다.
    - 네이버는 headless를 차단하므로 기본 headless=False(창 표시)로 동작한다.
    """
    # 프로필 결정: "내 크롬 프로필 사용" 토글이 켜져 있으면 사용자 실제 크롬 프로필로 엶
    # (쿠팡/네이버 모두 이미 로그인된 상태 → 쿠키 추출·ABE 문제 없음. 단 크롬은 종료 필요)
    from ..accounts import get_accounts
    from ..paths import real_chrome_user_data

    acc = get_accounts()
    # 실제 크롬 프로필 방식은 영구 비활성화한다.
    # 크롬은 자동화(--no-sandbox 등)로 로그인된 프로필을 열면 "본인 인증"을 요구하며
    # 프로필을 잠그고(디버깅 포트 미개방 → launch hang), 닫을 때 구글 계정을 로그아웃시킨다.
    # 이는 우회 불가한 크롬 보안 기능이므로 항상 전용 프로필(.userdata)만 사용한다.
    use_real = False
    extra_args = ["--disable-blink-features=AutomationControlled"]
    inject_cookies = settings.crawl_use_chrome_cookies

    if use_real and real_chrome_user_data():
        user_data_dir = real_chrome_user_data()
        extra_args += [
            f"--profile-directory={acc.get('chrome_profile') or 'Default'}",
            # 세션 복원/시작페이지/크래시 버블이 크롤을 방해하지 않게
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-crash-restore-bubble",
            "--disable-session-crashed-bubble",
            "--disable-features=InfiniteSessionRestore",
        ]
        inject_cookies = False  # 실제 프로필엔 이미 네이티브 쿠키가 있음
        # Windows 핵심 문제: 크롬을 닫아도 백그라운드 프로세스가 프로필을 잡고 있으면,
        # 크롤러가 같은 프로필로 크롬을 켤 때 Chrome이 "기존 인스턴스로 넘김(hand-off)"을
        # 하고 크롤러가 띄운 프로세스는 즉시 종료된다. → 조작 불가 about:blank 창만 남음.
        # 그래서 실제 프로필 실행 직전에 남은 크롬 프로세스와 프로필 잠금을 정리한다.
        _terminate_chrome_and_unlock(user_data_dir)
    else:
        user_data_dir = settings.crawl_chrome_user_data_dir or USER_DATA_DIR
        if settings.crawl_chrome_profile:
            extra_args.append(f"--profile-directory={settings.crawl_chrome_profile}")
        if not settings.crawl_chrome_user_data_dir:
            os.makedirs(USER_DATA_DIR, exist_ok=True)

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir,
                headless=settings.crawl_headless,
                channel="chrome",
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1440, "height": 900},
                args=extra_args,
                ignore_default_args=["--enable-automation"],
                **proxy_kwargs(session_id),
            )
        except Exception as e:
            if use_real:
                raise RuntimeError(
                    "크롬 프로필을 열 수 없습니다. 평소 쓰는 Chrome을 완전히 종료한 뒤 다시 시도하세요."
                ) from e
            raise
        context.set_default_navigation_timeout(settings.crawl_nav_timeout_ms)
        context.set_default_timeout(settings.crawl_nav_timeout_ms)

        if use_real:
            # 실제 프로필은 세션 복원으로 여러 탭이 열릴 수 있음 → 깨끗한 탭 하나만 남김
            await asyncio.sleep(1.5)
            fresh = await context.new_page()
            for pg in list(context.pages):
                if pg is not fresh:
                    try:
                        await pg.close()
                    except Exception:
                        pass

        if inject_cookies:
            try:
                from .cookie_sync import get_login_cookies

                cookies = get_login_cookies("coupang.com")
                if cookies:
                    await context.add_cookies(cookies)
            except Exception:
                pass

        # 사용자가 붙여넣은 세션 쿠키 주입(쿠팡/네이버 로그인·세션 재사용)
        await _inject_pasted_cookies(context)

        try:
            yield context
        finally:
            await context.close()


async def open_persistent(p, headless: bool = False, session_id: str | None = None):
    """로그인/확인용 지속 컨텍스트. 자동화 배너/플래그 제거 + 스텔스 주입."""
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    ctx = await p.chromium.launch_persistent_context(
        USER_DATA_DIR,
        headless=headless,
        channel="chrome",
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
        **proxy_kwargs(session_id),
    )
    await ctx.add_init_script(STEALTH_JS)
    return ctx


@contextlib.asynccontextmanager
async def anonymous_context(session_id: str | None = None):
    """계정 로그인 없는 별도 컨텍스트(리뷰 API 전용).

    쿠팡 봇우회 쿠키(기기 지문)만 주입하고 계정 쿠키는 넣지 않아, 리뷰 대량 호출을
    로그인 계정과 분리한다. 프로필을 쓰지 않아(non-persistent) .userdata 잠금과 무관.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=settings.crawl_headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            **proxy_kwargs(session_id),
        )
        context = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 900},
        )
        context.set_default_navigation_timeout(settings.crawl_nav_timeout_ms)
        context.set_default_timeout(settings.crawl_nav_timeout_ms)
        await context.add_init_script(STEALTH_JS)
        try:
            from .cookie_sync import get_bypass_cookies

            bp = get_bypass_cookies("coupang.com")
            if bp:
                await context.add_cookies(bp)
        except Exception:
            pass
        # 사용자가 붙여넣은 세션 쿠키 주입(리뷰 API도 사용자 세션으로 호출)
        await _inject_pasted_cookies(context)
        try:
            yield context
        finally:
            await context.close()
            await browser.close()


async def polite_delay() -> None:
    """요청 간 랜덤 지연."""
    lo, hi = settings.crawl_min_delay_ms, settings.crawl_max_delay_ms
    await asyncio.sleep(random.uniform(lo, hi) / 1000.0)


async def auto_scroll(page: Page, steps: int = 8, pause_ms: int = 400) -> None:
    """지연 로딩(lazy-load) 유도를 위해 아래로 스크롤."""
    for _ in range(steps):
        await page.mouse.wheel(0, 1600)
        await page.wait_for_timeout(pause_ms)
    await page.mouse.wheel(0, -3000)
    await page.wait_for_timeout(200)


# 상세페이지에서 스펙(정의목록/표/라벨값) 후보를 최대한 폭넓게 긁는 스크립트.
# 사이트마다 구조가 달라서, 흔한 패턴(dl/dt/dd, table th/td, "라벨 : 값")을 모두 시도한다.
EXTRACT_SPECS_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const pairs = {};
  const add = (k, v) => {
    k = clean(k); v = clean(v);
    if (!k || !v || k.length > 60 || v.length > 400) return;
    if (!(k in pairs)) pairs[k] = v;
  };

  // 1) 표 형태 (th/td)
  document.querySelectorAll('table tr').forEach(tr => {
    const th = tr.querySelector('th');
    const td = tr.querySelector('td');
    if (th && td) add(th.innerText, td.innerText);
  });

  // 2) 정의목록 (dl > dt/dd)
  document.querySelectorAll('dl').forEach(dl => {
    const dts = dl.querySelectorAll('dt');
    const dds = dl.querySelectorAll('dd');
    for (let i = 0; i < Math.min(dts.length, dds.length); i++) {
      add(dts[i].innerText, dds[i].innerText);
    }
  });

  // 3) "라벨 : 값" 텍스트 (네이버/쿠팡 스펙 리스트에 흔함)
  document.querySelectorAll('li, span, p, div').forEach(el => {
    if (el.children.length > 0) return; // leaf 노드만
    const t = clean(el.innerText);
    const m = t.match(/^([가-힣A-Za-z0-9()/\s]{1,30})\s*[:：]\s*(.+)$/);
    if (m) add(m[1], m[2]);
  });

  return pairs;
}
"""

EXTRACT_REVIEWS_JS = r"""
(maxN) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const set = new Set();
  const out = [];
  const candSel = [
    '[class*="review"] [class*="content"]',
    '[class*="review"] [class*="text"]',
    '[class*="reviewItems"] li',
    '[class*="reviewList"] li',
    'article[class*="review"]',
    '.sdp-review__article__list__review__content',
  ];
  for (const sel of candSel) {
    document.querySelectorAll(sel).forEach(el => {
      const t = clean(el.innerText);
      if (t.length >= 8 && t.length <= 800 && !set.has(t)) {
        set.add(t); out.push(t);
      }
    });
    if (out.length >= maxN) break;
  }
  return out.slice(0, maxN);
}
"""


async def extract_specs(page: Page) -> dict[str, str]:
    try:
        data = await page.evaluate(EXTRACT_SPECS_JS)
        return data or {}
    except Exception:
        return {}


async def extract_reviews(page: Page, max_n: int = 15) -> list[str]:
    try:
        data = await page.evaluate(EXTRACT_REVIEWS_JS, max_n)
        return data or []
    except Exception:
        return []


def specs_to_text(title: str, pairs: dict[str, str]) -> str:
    """스펙 딕셔너리를 사람이 읽는 자연어 문단으로 정리."""
    if not pairs:
        return f"{title}: 상세 스펙 정보를 수집하지 못했습니다."
    parts = [f"'{title}' 상품의 상세 스펙은 다음과 같습니다."]
    for k, v in pairs.items():
        parts.append(f"{k}은(는) {v}.")
    return " ".join(parts)
