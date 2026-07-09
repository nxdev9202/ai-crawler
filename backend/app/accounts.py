"""전용 크롤링 계정 저장소.

앱에서 입력한 네이버/쿠팡 계정을 backend/.accounts.json(git 제외)에 저장한다.
.env의 값은 폴백으로 사용한다. (기업용: 개인 계정 대신 전용 계정 사용 권장)
"""
from __future__ import annotations

import json
from typing import Any

from .config import settings
from .paths import accounts_file

ACCOUNTS_FILE = accounts_file()

FIELDS = (
    "naver_id",
    "naver_pw",
    "coupang_email",
    "coupang_pw",
    "gemini_api_key",
    "gemini_model",
    "use_real_chrome",   # (사용 안 함) 실제 크롬 프로필 방식 — 크롬 보안으로 비활성화됨
    "chrome_profile",    # (사용 안 함)
    "proxy_enabled",     # "1"이면 크롤 시 프록시 사용(쿠팡 Akamai IP차단 우회)
    "proxy_server",      # 예: http://host:port  또는  socks5://host:port
    "proxy_username",    # 로테이팅 프록시면 user-session-{session} 형태 가능
    "proxy_password",
)


def get_accounts() -> dict[str, str]:
    """저장된 계정/키 반환. 파일 값이 우선, 없으면 .env 폴백."""
    data: dict[str, str] = {
        "naver_id": settings.naver_id or "",
        "naver_pw": settings.naver_pw or "",
        "coupang_email": settings.coupang_email or "",
        "coupang_pw": settings.coupang_pw or "",
        "gemini_api_key": settings.google_api_key or "",
        "gemini_model": settings.gemini_model or "gemini-3.5-flash",
        "use_real_chrome": "",
        "chrome_profile": "Default",
        "proxy_enabled": "",
        "proxy_server": settings.proxy_server or "",
        "proxy_username": settings.proxy_username or "",
        "proxy_password": settings.proxy_password or "",
    }
    if ACCOUNTS_FILE.exists():
        try:
            saved = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            for k in FIELDS:
                if saved.get(k):
                    data[k] = saved[k]
        except Exception:
            pass
    # .env 기본 플레이스홀더는 미설정으로 처리
    if "붙여넣기" in data.get("gemini_api_key", ""):
        data["gemini_api_key"] = ""
    return data


def save_accounts(update: dict[str, Any]) -> dict[str, str]:
    """전달된 필드만 병합 저장. 빈 문자열은 무시(기존 값 유지)."""
    current = {}
    if ACCOUNTS_FILE.exists():
        try:
            current = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    # 토글 필드는 "0"/빈값도 명시적으로 저장(꺼짐 상태가 유지되도록).
    TOGGLE = {"proxy_enabled", "use_real_chrome"}
    for k in FIELDS:
        if k not in update:
            continue
        v = update.get(k)
        if k in TOGGLE:
            current[k] = "1" if v == "1" else "0"
        elif v:
            current[k] = v
    ACCOUNTS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_accounts()


def masked_status() -> dict[str, Any]:
    """UI 표시용: 비밀번호/키는 숨기고 설정 여부만."""
    acc = get_accounts()
    key = acc.get("gemini_api_key") or ""
    return {
        "naver_id": acc["naver_id"],
        "naver_set": bool(acc["naver_id"] and acc["naver_pw"]),
        "coupang_email": acc["coupang_email"],
        "coupang_set": bool(acc["coupang_email"] and acc["coupang_pw"]),
        "gemini_set": bool(key),
        "gemini_key_hint": ("…" + key[-4:]) if key else "",
        "gemini_model": acc.get("gemini_model") or "gemini-3.5-flash",
        "use_real_chrome": acc.get("use_real_chrome") == "1",
        "chrome_profile": acc.get("chrome_profile") or "Default",
        "chrome_profiles": _list_profiles(),
        "proxy_enabled": acc.get("proxy_enabled") == "1",
        "proxy_server": acc.get("proxy_server") or "",
        "proxy_set": bool(acc.get("proxy_server")),
        "proxy_username": acc.get("proxy_username") or "",
    }


def _list_profiles() -> list:
    from .paths import list_chrome_profiles

    try:
        return list_chrome_profiles()
    except Exception:
        return []


def get_proxy_config() -> dict[str, str]:
    """크롤에 사용할 프록시 설정. proxy_enabled가 "1"이고 서버가 있을 때만 반환.

    반환: {"server": ..., "username": ..., "password": ...} 또는 {} (비활성).
    """
    acc = get_accounts()
    if acc.get("proxy_enabled") != "1":
        return {}
    server = (acc.get("proxy_server") or "").strip()
    if not server:
        return {}
    out = {"server": server}
    if acc.get("proxy_username"):
        out["username"] = acc["proxy_username"]
    if acc.get("proxy_password"):
        out["password"] = acc["proxy_password"]
    return out


def get_gemini_config() -> dict[str, str]:
    """분석에 사용할 Gemini 키/모델(파일 우선, .env 폴백)."""
    acc = get_accounts()
    return {
        "api_key": acc.get("gemini_api_key") or "",
        "model": acc.get("gemini_model") or "gemini-3.5-flash",
    }
