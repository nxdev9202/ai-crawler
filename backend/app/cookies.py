"""사용자가 직접 export한 세션 쿠키를 저장/주입한다.

크롬 자동 추출은 ABE(App-Bound Encryption)로 막혀 있지만, 사용자가 본인 크롬에서
Cookie-Editor 같은 확장으로 직접 export하면 평문으로 나온다. 그 JSON을 붙여넣으면
Playwright 형식으로 정규화해 저장하고, 크롤 시 컨텍스트에 주입한다.
(로그인/2차인증 세션이 그대로 넘어옴. 단, Akamai IP차단은 쿠키로 못 뚫음 → 프록시 필요)
"""
from __future__ import annotations

import json
from typing import Any

from .paths import cookies_file

COOKIES_FILE = cookies_file()

# Playwright sameSite는 Strict/Lax/None만 허용
_SAMESITE = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": "Lax",
    "": "Lax",
}


def _normalize_one(c: dict[str, Any]) -> dict[str, Any] | None:
    """Cookie-Editor/Chrome/Playwright 쿠키 하나를 Playwright add_cookies 형식으로."""
    name = c.get("name")
    value = c.get("value")
    domain = c.get("domain")
    if not name or value is None or not domain:
        return None

    out: dict[str, Any] = {
        "name": str(name),
        "value": str(value),
        "domain": str(domain),
        "path": c.get("path") or "/",
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", False)),
    }

    # 만료: expirationDate(초, float) 또는 expires. 없거나 세션이면 -1(세션 쿠키)
    exp = c.get("expirationDate", c.get("expires"))
    if c.get("session") or exp in (None, "", -1):
        out["expires"] = -1
    else:
        try:
            out["expires"] = float(exp)
        except (TypeError, ValueError):
            out["expires"] = -1

    # sameSite 정규화. None인데 secure가 아니면 Playwright가 거부 → Lax로 낮춤
    ss_raw = str(c.get("sameSite", "")).lower()
    ss = _SAMESITE.get(ss_raw, "Lax")
    if ss == "None" and not out["secure"]:
        ss = "Lax"
    out["sameSite"] = ss
    return out


def parse_cookies(raw: str) -> list[dict[str, Any]]:
    """붙여넣은 텍스트(JSON 배열 / storage_state / Cookie-Editor)를 파싱·정규화."""
    raw = (raw or "").strip()
    if not raw:
        return []
    data = json.loads(raw)  # 실패하면 상위에서 처리
    # 암호화 백업 형식(hotcleaner Cookie Manager 등): {"url":..,"version":..,"data":"<암호문>"}
    if (
        isinstance(data, dict)
        and isinstance(data.get("data"), str)
        and "cookies" not in data
        and ("version" in data or "url" in data)
    ):
        raise ValueError(
            "암호화된 백업 형식이라 읽을 수 없습니다. 'Cookie-Editor'(cookie-editor.com) 확장의 "
            "Export(JSON)를 사용하세요 — 평문 JSON을 줍니다."
        )
    # storage_state 형식: {"cookies":[...]}
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        data = data["cookies"]
    if not isinstance(data, list):
        raise ValueError("쿠키 JSON은 배열이거나 {\"cookies\": [...]} 형식이어야 합니다.")
    out = []
    for c in data:
        if isinstance(c, dict):
            n = _normalize_one(c)
            if n:
                out.append(n)
    return out


# 크롤 대상 사이트만 저장(전체 export를 붙여넣어도 구글/Gmail 등 다른 세션은 버림 → 프라이버시)
TARGET_KEYWORDS = ("coupang", "naver")


def _is_target(domain: str) -> bool:
    d = (domain or "").lower()
    return any(k in d for k in TARGET_KEYWORDS)


def save_cookies(raw: str) -> dict[str, Any]:
    """붙여넣은 쿠키를 정규화 + 쿠팡/네이버만 걸러 저장. 반환: {ok, count, domains}."""
    parsed = parse_cookies(raw)
    cookies = [c for c in parsed if _is_target(c.get("domain", ""))]
    if not cookies:
        if parsed:
            return {
                "ok": False, "count": 0, "domains": {},
                "error": "쿠팡/네이버 쿠키가 없습니다. 해당 사이트에 로그인 후 그 사이트의 쿠키를 export하세요.",
            }
        return {"ok": False, "count": 0, "domains": {}, "error": "유효한 쿠키가 없습니다."}
    COOKIES_FILE.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "count": len(cookies), "domains": _domain_counts(cookies)}


def load_cookies() -> list[dict[str, Any]]:
    """저장된 쿠키(Playwright 형식) 반환. 없으면 빈 리스트."""
    if not COOKIES_FILE.exists():
        return []
    try:
        data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def clear_cookies() -> None:
    try:
        COOKIES_FILE.unlink()
    except FileNotFoundError:
        pass


def _domain_counts(cookies: list[dict[str, Any]]) -> dict[str, int]:
    """주요 사이트별 쿠키 개수 요약(UI 표시용)."""
    out: dict[str, int] = {}
    for c in cookies:
        d = c.get("domain", "")
        key = "coupang" if "coupang" in d else "naver" if "naver" in d else "기타"
        out[key] = out.get(key, 0) + 1
    return out


def status() -> dict[str, Any]:
    cookies = load_cookies()
    return {"count": len(cookies), "domains": _domain_counts(cookies)}
