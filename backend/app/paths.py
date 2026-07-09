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
