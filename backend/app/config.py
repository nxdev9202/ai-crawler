"""환경설정 로더 (.env)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 비워두면 backend/app.db (SQLite) 사용. 서버 불필요·자체포함 배포용.
    database_url: str = ""

    google_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    crawl_max_products: int = 10
    # 상품당 수집할 리뷰 최대 개수(pageSize 20씩 페이지네이션)
    review_max: int = 300
    # 네이버는 headless를 봇으로 차단하므로 기본값 False(창 표시)
    crawl_headless: bool = False

    # 자동 로그인용 자격증명(선택). 크롤 시 로그인 안 되어 있으면 사용.
    naver_id: str = ""
    naver_pw: str = ""
    coupang_email: str = ""
    coupang_pw: str = ""

    # 크롬 프로필 선택:
    #  - 비워두면 전용 프로필(backend/.userdata)을 사용 (권장, 우리가 로그인 저장)
    #  - 평소 쓰는 크롬 프로필을 재사용하려면 아래에 경로/프로필명을 지정
    #    (그 경우 크롤링 중 평소 크롬을 완전히 종료해야 함)
    crawl_chrome_user_data_dir: str = ""   # 예: /Users/이름/Library/Application Support/Google/Chrome
    crawl_chrome_profile: str = ""          # 예: Default, "Profile 1"

    # 프록시(회사 공인 IP 보호용). residential/모바일 프록시 권장.
    # 예: http://gate.provider.com:7000  또는  socks5://host:port
    proxy_server: str = ""
    proxy_username: str = ""
    proxy_password: str = ""

    # 쿠팡 크롤 시작 시 평소 크롬의 '안티봇 쿠키(기기 지문, 계정 아님)'를 주입해
    # Akamai 봇차단을 통과한다. 계정 로그인이 아니라 정지 리스크 없음.
    crawl_use_chrome_cookies: bool = True
    crawl_min_delay_ms: int = 800
    crawl_max_delay_ms: int = 2200
    crawl_nav_timeout_ms: int = 30000

    backend_host: str = "127.0.0.1"
    backend_port: int = 8756

    # 쓰기 가능한 데이터 폴더(패키지 설치 시 Electron이 %APPDATA% 경로를 전달).
    # 비우면 backend 폴더(개발용). app.db / .userdata / .accounts.json 저장 위치.
    app_data_dir: str = ""


settings = Settings()
