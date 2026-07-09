"""PyInstaller 백엔드 진입점.

Windows에서 python 설치 없이 백엔드를 실행하기 위한 단일 실행파일용 엔트리.
Electron이 이 exe를 spawn하고 APP_DATA_DIR/BACKEND_PORT 환경변수를 전달한다.
"""
import multiprocessing

from app.main import run

if __name__ == "__main__":
    multiprocessing.freeze_support()  # PyInstaller 다중프로세스 안전장치
    run()
