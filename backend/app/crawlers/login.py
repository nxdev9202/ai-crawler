"""네이버 로그인 세션 관리.

지속 프로필(.userdata)에 로그인 쿠키를 심어두면 이후 크롤링에서 봇 차단을 통과한다.
자동 로그인 시 캡차/2단계 인증이 뜨면 열린 창에서 사용자가 직접 처리해야 하므로,
로그인은 반드시 headful(창 표시)로 수행한다.
"""
from __future__ import annotations

from typing import Callable

from playwright.async_api import async_playwright

from .base import open_persistent


async def check_login() -> bool:
    """현재 프로필이 네이버에 로그인되어 있는지 확인(headless 가능)."""
    async with async_playwright() as p:
        ctx = await open_persistent(p, headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://www.naver.com", wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
            body = await page.inner_text("body")
            return "로그아웃" in body or "MyView" in (await page.content())
        finally:
            await ctx.close()


async def login(
    naver_id: str,
    naver_pw: str,
    on_progress: Callable[[str], None] | None = None,
    wait_seconds: int = 300,
) -> bool:
    """네이버 로그인 수행(headful). 캡차/2FA/2차 비밀번호는 열린 창에서 직접 처리.

    2차 인증(기기 등록/2차 비밀번호)이 뜰 수 있으므로 기본 최대 5분간 성공을 폴링한다.
    """
    log = on_progress or (lambda _m: None)
    async with async_playwright() as p:
        ctx = await open_persistent(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://www.naver.com", wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
            body = await page.inner_text("body")
            if "로그아웃" in body:
                log("[로그인] 이미 로그인되어 있습니다.")
                return True

            await page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded")
            await page.wait_for_timeout(1000)
            await page.click("#id")
            await page.keyboard.type(naver_id, delay=90)
            await page.wait_for_timeout(400)
            await page.click("#pw")
            await page.keyboard.type(naver_pw, delay=90)
            await page.wait_for_timeout(300)
            try:
                if await page.locator("#keep").count() > 0:
                    await page.check("#keep")
            except Exception:
                pass
            await page.click('button[type=submit], .btn_login, #log\\.login')
            log("[로그인] 제출함. 캡차/2단계 인증이 뜨면 열린 창에서 직접 처리하세요.")

            for i in range(wait_seconds // 2):
                await page.wait_for_timeout(2000)
                url = page.url
                b = ""
                try:
                    b = await page.inner_text("body")
                except Exception:
                    pass
                if "로그아웃" in b or ("naver.com/" in url and "nid" not in url):
                    log("[로그인] 성공.")
                    return True
            log("[로그인] 시간 초과. 캡차/2FA 미완료일 수 있습니다.")
            return False
        finally:
            await ctx.close()


async def coupang_check_login() -> bool:
    """쿠팡 로그인 상태 확인(headless 가능)."""
    async with async_playwright() as p:
        ctx = await open_persistent(p, headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://www.coupang.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            body = await page.inner_text("body")
            return "로그아웃" in body  # '마이쿠팡'은 로그아웃 상태에도 있어 판별 불가
        except Exception:
            return False
        finally:
            await ctx.close()


async def _has_email_input(page) -> bool:
    for s in ["#login-email-input", 'input[name="email"]']:
        try:
            if await page.locator(s).count() > 0:
                return True
        except Exception:
            pass
    return False


async def _goto_coupang_login(page, log) -> bool:
    """쿠팡 홈에서 로그인 링크 클릭으로 Akamai 통과 후 로그인 폼으로 이동.

    직접 login.pang 접근은 Access Denied가 뜨므로, 먼저 login.coupang.com 도메인에
    자연스럽게 진입(쿠키 확보)한 뒤 login.pang로 이동한다.
    """
    # 1) 로그인 관련 링크 클릭 (login.pang 우선, 없으면 login.coupang.com 아무거나)
    for sel in ['a[href*="login.pang"]', 'a[href*="login.coupang.com"]', 'a:has-text("로그인")']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click(timeout=4000)
                break
        except Exception:
            pass
    await page.wait_for_timeout(2500)

    # 2) 로그인 폼이 아니면(회원가입 등) login.pang로 직접 이동 - 이제 Akamai 쿠키 유효
    if not await _has_email_input(page):
        try:
            await page.goto(
                "https://login.coupang.com/login/login.pang",
                wait_until="domcontentloaded",
                referer="https://www.coupang.com/",
            )
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    body = ""
    try:
        body = await page.inner_text("body")
    except Exception:
        pass
    if "Access Denied" in body or "don't have permission" in body:
        return False
    return await _has_email_input(page)


async def coupang_login(
    email: str,
    password: str,
    on_progress: Callable[[str], None] | None = None,
    wait_seconds: int = 300,
) -> bool:
    """쿠팡 로그인 수행(headful). 2차 인증/캡차는 열린 창에서 직접 처리.

    쿠팡은 봇 차단이 강하고 2차 인증(문자/기기)이 자주 뜨므로 기본 최대 5분 대기.
    """
    log = on_progress or (lambda _m: None)
    async with async_playwright() as p:
        ctx = await open_persistent(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://www.coupang.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)  # Akamai 센서 통과 대기
            body = await page.inner_text("body")
            if "로그아웃" in body:
                log("[쿠팡 로그인] 이미 로그인되어 있습니다.")
                return True

            # 직접 login.pang 접근은 Access Denied → 홈의 '로그인' 링크를 클릭해 이동
            if not await _goto_coupang_login(page, log):
                log("[쿠팡 로그인] 로그인 페이지 진입 실패(Access Denied 가능).")
                return False
            # 이메일/비밀번호 입력 (셀렉터 폴백)
            for sel in ['#login-email-input', 'input[name="email"]', 'input[type="text"]']:
                if await page.locator(sel).count() > 0:
                    await page.click(sel)
                    await page.keyboard.type(email, delay=90)
                    break
            await page.wait_for_timeout(400)
            for sel in ['#login-password-input', 'input[name="password"]', 'input[type="password"]']:
                if await page.locator(sel).count() > 0:
                    await page.click(sel)
                    await page.keyboard.type(password, delay=90)
                    break
            await page.wait_for_timeout(300)
            for sel in ['button.login__button', 'button[type="submit"]', 'button:has-text("로그인")']:
                if await page.locator(sel).count() > 0:
                    await page.click(sel)
                    break
            log("[쿠팡 로그인] 제출함. 2차 인증(문자/기기)/캡차가 뜨면 열린 창에서 직접 처리하세요.")

            for i in range(wait_seconds // 2):
                await page.wait_for_timeout(2000)
                url = page.url
                b = ""
                try:
                    b = await page.inner_text("body")
                except Exception:
                    pass
                if "로그아웃" in b:
                    log("[쿠팡 로그인] 성공.")
                    return True
            log("[쿠팡 로그인] 시간 초과. 2차 인증 미완료일 수 있습니다.")
            return False
        finally:
            await ctx.close()
