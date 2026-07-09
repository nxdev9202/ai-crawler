"""CDP 기반 로그인.

쿠팡 로그인 페이지(login.pang)는 Akamai가 Playwright로 띄운 Chrome을 봇으로 차단한다.
그래서 로그인만은 '진짜 Chrome'을 subprocess로 직접 실행(자동화 하네스 없음)한 뒤
CDP(remote-debugging)로 연결해 수행한다. navigator.webdriver=false 라서 통과된다.

로그인 세션은 .userdata 프로필에 저장되고, 이후 크롤링(Playwright)이 재사용한다.
"""
from __future__ import annotations

import asyncio
import os
import platform
import socket
import subprocess
from typing import Callable

from playwright.async_api import async_playwright

from ..accounts import get_accounts
from ..config import settings
from .base import USER_DATA_DIR

CHROME_CANDIDATES = [
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


def _chrome_path() -> str | None:
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _real_chrome_user_data() -> str | None:
    """사용자가 평소 쓰는 실제 크롬 프로필 폴더(OS별)."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA", "")
        p = os.path.join(base, "Google", "Chrome", "User Data")
    elif system == "Darwin":
        p = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    else:
        p = os.path.expanduser("~/.config/google-chrome")
    return p if os.path.isdir(p) else None


def _sanitize_cookie(c: dict) -> dict:
    ck = {
        "name": c.get("name"),
        "value": c.get("value"),
        "domain": c.get("domain"),
        "path": c.get("path") or "/",
        "secure": bool(c.get("secure")),
        "httpOnly": bool(c.get("httpOnly")),
    }
    if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
        ck["expires"] = c["expires"]
    ss = c.get("sameSite")
    if ss in ("Strict", "Lax", "None"):
        ck["sameSite"] = ss
    return ck


async def import_coupang_session(on_progress: Callable[[str], None] | None = None) -> dict:
    """사용자 실제 크롬을 CDP로 잠깐 띄워 쿠팡 쿠키를 받아(크롬이 복호화) .userdata에 주입.

    최신 Windows 크롬의 앱단위 암호화(ABE)로 앱이 직접 쿠키를 못 읽는 문제를 우회한다.
    사용자 크롬이 실행 중이면 프로필이 잠겨 실패하므로 완전히 종료해야 한다.
    """
    log = on_progress or (lambda _m: None)
    chrome = _chrome_path()
    real_dir = _real_chrome_user_data()
    if not chrome:
        return {"ok": False, "error": "Google Chrome 실행 파일을 찾을 수 없습니다."}
    if not real_dir:
        return {"ok": False, "error": "크롬 사용자 프로필 폴더를 찾을 수 없습니다."}

    log("[쿠팡세션] 사용자 크롬에서 쿠팡 쿠키를 읽는 중… (크롬이 켜져 있으면 완전히 종료하세요)")
    port = _free_port()
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={real_dir}",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    coupang_cookies: list[dict] = []
    try:
        async with async_playwright() as p:
            browser = None
            for _ in range(20):
                try:
                    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception:
                    await asyncio.sleep(1)
            if browser is None:
                return {"ok": False, "error": "크롬 연결 실패 — 크롬을 완전히 종료한 뒤 다시 시도하세요."}
            for ctx in browser.contexts:
                try:
                    for c in await ctx.cookies():
                        if "coupang" in (c.get("domain") or ""):
                            coupang_cookies.append(_sanitize_cookie(c))
                except Exception:
                    pass
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    if not coupang_cookies:
        return {
            "ok": False,
            "error": "쿠팡 쿠키를 찾지 못했습니다. 평소 크롬에서 쿠팡에 로그인한 뒤(그리고 크롬 종료 후) 다시 시도하세요.",
        }

    # .userdata 프로필에 주입 + 로그인 확인
    log(f"[쿠팡세션] 쿠키 {len(coupang_cookies)}개 확보 → 크롤러 프로필에 주입")
    _clear_locks()
    logged = False
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=True, channel="chrome"
        )
        try:
            try:
                await ctx.add_cookies(coupang_cookies)
            except Exception:
                for ck in coupang_cookies:
                    try:
                        await ctx.add_cookies([ck])
                    except Exception:
                        pass
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://www.coupang.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            try:
                logged = "로그아웃" in (await page.inner_text("body"))
            except Exception:
                logged = False
        finally:
            await ctx.close()
        _clear_locks()

    log(f"[쿠팡세션] 완료 — 로그인 감지: {logged}")
    return {"ok": True, "count": len(coupang_cookies), "logged_in": logged}


def _clear_locks() -> None:
    for f in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(USER_DATA_DIR, f))
        except OSError:
            pass


async def _fill_and_submit(page, email_sels, email, pw_sels, pw, submit_sels) -> None:
    for sel in email_sels:
        if await page.locator(sel).count() > 0:
            await page.click(sel)
            await page.keyboard.type(email, delay=90)
            break
    await page.wait_for_timeout(350)
    for sel in pw_sels:
        if await page.locator(sel).count() > 0:
            await page.click(sel)
            await page.keyboard.type(pw, delay=90)
            break
    await page.wait_for_timeout(300)
    for sel in submit_sels:
        if await page.locator(sel).count() > 0:
            await page.click(sel)
            break


async def _coupang(page, acc, log, wait_seconds) -> bool:
    """쿠팡은 Akamai 봇차단이 강해 자동 클릭/입력이 불안정하다.

    가장 신뢰도 높은 방식: 진짜 Chrome 창을 열어두고 사용자가 직접 로그인하게 한다
    (어차피 2차 인증도 직접 해야 함). 앱은 완료(로그아웃 링크 등장)를 감지·저장한다.
    """
    email = acc.get("coupang_email") or ""
    # Chrome이 이미 coupang.com을 열어둠. 재이동/자동클릭 없이 대기만(Akamai 자극 방지).
    try:
        await page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await page.wait_for_timeout(3000)
    if "로그아웃" in (await page.inner_text("body")):
        log("[쿠팡] 이미 로그인되어 있습니다.")
        return True

    hint = f"(아이디: {email}) " if email else ""
    log(
        f"[쿠팡] 열린 Chrome 창에서 직접 '로그인'을 눌러 전용 계정으로 로그인해 주세요. "
        f"{hint}2차 인증까지 마치면 자동으로 감지됩니다. (최대 5분 대기)"
    )
    for _ in range(wait_seconds // 2):
        await page.wait_for_timeout(2000)
        try:
            if "로그아웃" in (await page.inner_text("body")):
                log("[쿠팡] 로그인 성공. 세션을 저장합니다.")
                return True
        except Exception:
            pass
    log("[쿠팡] 로그인 시간 초과(직접 로그인 미완료).")
    return False


async def _naver(page, acc, log, wait_seconds) -> bool:
    nid, npw = acc.get("naver_id"), acc.get("naver_pw")
    if not (nid and npw):
        log("[네이버] 계정이 설정되지 않았습니다.")
        return False
    try:
        await page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    await page.wait_for_timeout(2000)
    if "로그아웃" in (await page.inner_text("body")):
        log("[네이버] 이미 로그인되어 있습니다.")
        return True
    await page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded")
    await page.wait_for_timeout(1200)
    await _fill_and_submit(
        page,
        ["#id"],
        nid,
        ["#pw"],
        npw,
        ['button[type=submit]', ".btn_login", "#log\\.login"],
    )
    log("[네이버] 로그인 제출. 2차 인증/기기등록이 뜨면 창에서 처리하세요.")
    for _ in range(wait_seconds // 2):
        await page.wait_for_timeout(2000)
        url = page.url
        try:
            b = await page.inner_text("body")
        except Exception:
            b = ""
        if "로그아웃" in b or ("naver.com/" in url and "nid" not in url):
            log("[네이버] 로그인 성공.")
            return True
    log("[네이버] 로그인 시간 초과.")
    return False


async def login_via_cdp(
    site: str,
    on_progress: Callable[[str], None] | None = None,
    wait_seconds: int = 300,
) -> bool:
    """진짜 Chrome을 CDP로 띄워 로그인. 세션은 .userdata에 저장된다."""
    log = on_progress or (lambda _m: None)
    chrome = _chrome_path()
    if not chrome:
        log("[오류] Google Chrome 실행 파일을 찾을 수 없습니다.")
        return False

    acc = get_accounts()
    _clear_locks()

    # 로그인도 크롤과 같은 프록시로 나가도록(설정 시). 세션-크롤 IP 불일치 방지.
    proxy_args: list[str] = []
    if settings.proxy_server:
        proxy_args.append(f"--proxy-server={settings.proxy_server}")

    # 쿠팡: Akamai가 login.pang에서 자동화 브라우저를 차단하므로, 실제 크롬의
    # 봇차단 쿠키(_abck/bm_* 등, 계정 아님)를 프로필에 미리 주입해 통과시킨다.
    if site == "coupang":
        try:
            from playwright.async_api import async_playwright as _ap
            from .cookie_sync import get_bypass_cookies

            bypass = get_bypass_cookies("coupang.com")
            if bypass:
                async with _ap() as _p:
                    _ctx = await _p.chromium.launch_persistent_context(
                        USER_DATA_DIR, headless=True, channel="chrome"
                    )
                    await _ctx.clear_cookies(domain="coupang.com")
                    await _ctx.add_cookies(bypass)
                    await _ctx.close()
                log(f"[쿠팡] 봇차단 우회 쿠키 {len(bypass)}개 주입(계정 쿠키 제외)")
            else:
                log("[쿠팡] ⚠️ 실제 크롬의 쿠팡 쿠키를 읽지 못했습니다(키체인 거부/미브라우징).")
        except Exception as e:  # noqa: BLE001
            log(f"[쿠팡] 봇차단 쿠키 주입 실패: {e}")

    port = _free_port()
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={USER_DATA_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=DestroyProfileOnBrowserClose",
            *proxy_args,
            "https://www.coupang.com/" if site == "coupang" else "https://www.naver.com/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with async_playwright() as p:
            browser = None
            for _ in range(25):
                try:
                    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception:
                    await asyncio.sleep(1)
            if browser is None:
                log("[오류] Chrome CDP 연결 실패.")
                return False

            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.bring_to_front()

            if site == "coupang":
                ok = await _coupang(page, acc, log, wait_seconds)
            else:
                ok = await _naver(page, acc, log, wait_seconds)

            await page.wait_for_timeout(1500)  # 쿠키 디스크 반영 여유
            return ok
    finally:
        # 정상 종료(SIGTERM)로 쿠키 flush
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _clear_locks()
