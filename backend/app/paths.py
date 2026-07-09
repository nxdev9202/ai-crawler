"""쓰기 가능한 데이터 경로 관리.

개발: backend 폴더에 저장. 패키지(MSI) 설치: Electron이 APP_DATA_DIR(%APPDATA%\\...)을
전달하면 그 폴더에 app.db / .userdata / .accounts.json 을 저장한다(Program Files는 쓰기 불가).
"""
from __future__ import annotations

from pathlib import Path

from .config import settings

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    d = Path(settings.app_data_dir) if settings.app_data_dir else _BACKEND_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "app.db"


def userdata_dir() -> str:
    return str(data_dir() / ".userdata")


def accounts_file() -> Path:
    return data_dir() / ".accounts.json"


def cookies_file() -> Path:
    """사용자가 붙여넣은 세션 쿠키(Playwright 형식) 저장 파일."""
    return data_dir() / ".cookies.json"


def real_chrome_user_data() -> str | None:
    """사용자가 평소 쓰는 실제 크롬 프로필 폴더(User Data). OS별 자동 감지."""
    import os
    import platform

    system = platform.system()
    if system == "Windows":
        p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
    elif system == "Darwin":
        p = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    else:
        p = os.path.expanduser("~/.config/google-chrome")
    return p if os.path.isdir(p) else None


def list_chrome_profiles() -> list[dict]:
    """실제 크롬의 프로필 목록(디렉터리명 + 표시이름/이메일). Local State에서 읽음."""
    import json as _json

    base = real_chrome_user_data()
    if not base:
        return []
    out: list[dict] = []
    try:
        ls = _json.loads((Path(base) / "Local State").read_text(encoding="utf-8"))
        cache = ls.get("profile", {}).get("info_cache", {})
        for dir_name, info in cache.items():
            out.append(
                {
                    "dir": dir_name,
                    "name": info.get("name") or dir_name,
                    "email": info.get("user_name") or "",
                }
            )
    except Exception:
        pass
    return out
