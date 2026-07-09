"""평소 쓰는 크롬(Default 프로필)의 로그인 쿠키를 전용 프로필(.userdata)로 1회 복사.

macOS 키체인('Chrome Safe Storage')으로 복호화된 쿠키를 읽어 Playwright 지속
컨텍스트에 주입한다. 이렇게 하면 개인 크롬을 계속 켜둔 채로도, 크롤러는 로그인된
전용 프로필을 재사용해 2차 인증 없이 동작한다.
"""
from __future__ import annotations

from typing import Any, Callable

from playwright.async_api import async_playwright

from .base import USER_DATA_DIR

SYNC_DOMAINS = ("naver.com", "coupang.com")


# 개인 '계정 로그인' 쿠키. Akamai 우회 시 이것만 제외하면 개인계정으로 로그인되지 않는다.
_ACCOUNT_COOKIES = {
    "ILOGIN", "AUTH_SESSION_ID", "AUTH_SESSION_ID_LEGACY",
    "MEMBER_SRL", "memberSrl", "login", "MEMBER_ID", "CGID",
}


def get_login_cookies(domain: str = "coupang.com") -> list[dict[str, Any]]:
    """실제 크롬의 해당 도메인 '전체' 쿠키(계정 로그인 포함)를 읽어온다.

    → 크롤러가 실제 크롬의 로그인 세션을 그대로 재사용(쿠팡 리뷰 등 로그인 필요 데이터).
    """
    try:
        return _read_chrome_cookies(domain)
    except Exception:
        return []


def get_bypass_cookies(domain: str = "coupang.com") -> list[dict[str, Any]]:
    """실제 크롬에서 봇차단 통과용 쿠키를 읽어온다(계정 로그인 쿠키는 제외).

    Akamai의 _abck 검증에는 여러 쿠키가 함께 필요해 계정 쿠키만 골라 제외한다.
    → 개인계정으로 로그인되지 않으면서 봇탐지는 통과.
    """
    try:
        return [c for c in _read_chrome_cookies(domain) if c["name"] not in _ACCOUNT_COOKIES]
    except Exception:
        return []


def get_cookies_for_playwright(domains: tuple[str, ...] = SYNC_DOMAINS) -> list[dict[str, Any]]:
    """개인 크롬에서 지정 도메인 쿠키를 읽어 Playwright add_cookies 형식으로 반환.

    실패(키체인 거부 등) 시 빈 리스트. 매 크롤 시작 시 컨텍스트에 주입해 사용한다.
    """
    out: list[dict[str, Any]] = []
    for d in domains:
        try:
            out += _read_chrome_cookies(d)
        except Exception:
            pass
    return out


def _read_chrome_cookies(domain: str) -> list[dict[str, Any]]:
    import browser_cookie3 as bc

    cj = bc.chrome(domain_name=domain)
    out: list[dict[str, Any]] = []
    for c in cj:
        ck: dict[str, Any] = {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httpOnly": bool(getattr(c, "_rest", {}) and c._rest.get("HttpOnly") is not None),
        }
        if c.expires:
            ck["expires"] = float(c.expires)
        out.append(ck)
    return out


async def sync_cookies(
    domains: tuple[str, ...] = SYNC_DOMAINS,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """개인 크롬 쿠키를 읽어 .userdata 프로필에 주입하고 로그인 상태를 검증."""
    log = on_progress or (lambda _m: None)

    cookies: list[dict[str, Any]] = []
    per_domain: dict[str, int] = {}
    for d in domains:
        try:
            ck = _read_chrome_cookies(d)
            per_domain[d] = len(ck)
            cookies += ck
            log(f"[쿠키] {d}: {len(ck)}개 읽음")
        except Exception as e:  # noqa: BLE001
            per_domain[d] = 0
            log(f"[쿠키] {d} 읽기 실패: {e}")

    if not cookies:
        return {"ok": False, "error": "읽은 쿠키가 없습니다(키체인 권한 거부 가능).", "per_domain": per_domain}

    result: dict[str, Any] = {"ok": True, "per_domain": per_domain, "injected": 0, "login": {}}
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=True, channel="chrome", locale="ko-KR"
        )
        try:
            # 한번에 주입 실패 시 개별 주입으로 폴백
            try:
                await ctx.add_cookies(cookies)
                result["injected"] = len(cookies)
            except Exception:
                ok = 0
                for ck in cookies:
                    try:
                        await ctx.add_cookies([ck])
                        ok += 1
                    except Exception:
                        pass
                result["injected"] = ok
            log(f"[쿠키] 전용 프로필에 {result['injected']}개 주입")

            # 로그인 검증
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            for site, url in (("naver", "https://www.naver.com"), ("coupang", "https://www.coupang.com/")):
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1500)
                    body = await page.inner_text("body")
                    result["login"][site] = "로그아웃" in body
                except Exception:
                    result["login"][site] = False
            log(f"[쿠키] 로그인 검증 - 네이버:{result['login'].get('naver')} 쿠팡:{result['login'].get('coupang')}")
        finally:
            await ctx.close()
    return result
