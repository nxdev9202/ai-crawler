# Windows MSI 빌드 가이드

Docker 없이 **MSI 설치파일 하나**로 앱 + 백엔드 + DB(SQLite)를 설치·실행합니다.
사용자 PC엔 **Python도 PostgreSQL도 필요 없습니다.** (Google Chrome만 설치돼 있으면 됨)

> ⚠️ **빌드는 반드시 Windows 머신(또는 Windows CI)에서 수행**해야 합니다.
> MSI/Windows exe는 macOS에서 만들 수 없습니다. 아래는 Windows 빌드 PC 기준입니다.

## 사전 준비 (빌드 PC, 1회)
- Python 3.12 (3.14 아님 — 일부 휠 미지원)
- Node.js 18+ / npm
- Google Chrome
- WiX Toolset 3.x (electron-builder MSI 타깃이 사용) — https://wixtoolset.org/

## 1) 백엔드를 단일 실행파일로 번들 (PyInstaller)
```bat
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
python -m playwright install chromium
pyinstaller ai-crawler-backend.spec
```
결과물: `backend\dist\ai-crawler-backend\` (백엔드 exe + 의존성 폴더)

동작 확인:
```bat
backend\dist\ai-crawler-backend\ai-crawler-backend.exe
:: http://127.0.0.1:8756/health 응답 확인 후 종료
```

## 2) Electron 앱 빌드 + MSI 생성
```bat
cd electron
npm install
npm run dist        :: --win msi (WiX 필요)
:: 또는 NSIS(.exe 설치본):  npm run dist:nsis
```
결과물: `electron\release\AI분석크롤러-Setup-1.0.0.msi`

electron-builder가 `backend\dist\ai-crawler-backend` 폴더를 설치본의
`resources\backend\` 로 동봉하고, 실행 시 `main.js`가 그 exe를 자동 기동합니다.

## 설치 후 데이터 위치
- DB / 로그인 프로필 / 계정: `%APPDATA%\AI분석크롤러\` (app.db, .userdata, .accounts.json)
- Program Files엔 쓰지 않으므로 권한 문제 없음.

## 설정(.env)
번들 백엔드는 환경변수/기본값으로 동작합니다. 사용자별 설정(Gemini 키·프록시)은:
- 앱 실행 후 넣거나,
- `%APPDATA%\AI분석크롤러\` 에 `.env` 를 두면 백엔드가 읽습니다.
  (또는 설치 시 기본 .env 동봉 구성 가능)

## 체크리스트
- [ ] Chrome 설치돼 있어야 크롤링/로그인 동작(channel=chrome)
- [ ] 첫 실행 시 방화벽에서 127.0.0.1:8756 허용
- [ ] Gemini 키 입력 시 분석 기능 활성화
- [ ] (선택) 프록시 설정 시 회사 IP 보호

## CI로 자동화(선택)
GitHub Actions `windows-latest` 러너에서 위 1)·2)를 순차 실행하면
푸시할 때마다 MSI가 자동 빌드됩니다. 필요 시 워크플로 예시 제공 가능.
