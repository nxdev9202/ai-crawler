# AI 분석 크롤러

네이버쇼핑 / 쿠팡 검색 1페이지의 상품들을 각 **상세페이지까지 진입**해 스펙·리뷰를 크롤링하고,
자연어 + JSONB로 PostgreSQL에 세션 단위로 저장한 뒤, **Google AI(Gemini)** 로 분석하는 데스크톱 앱.

## 구성
- **Electron** — 데스크톱 UI (검색 / 진행상황 / 결과 / 분석)
- **FastAPI (Python)** — 백엔드 API, 백그라운드 크롤링 오케스트레이션
- **Playwright** — 네이버쇼핑·쿠팡 크롤링 (스텔스 + 지연으로 봇차단 완화)
- **PostgreSQL (JSONB)** — 세션 / 상품(스펙·리뷰) / 분석결과 저장
- **Gemini** — 세션 데이터 자연어 분석

## 데이터 모델 (JSONB)
- `crawl_sessions` — 검색어 1회 실행 단위
- `products` — 상품별 `spec_json`(구조화) + `spec_text`(자연어) + `reviews_json`
- `analyses` — Gemini 분석 결과

## 설치 & 실행

### 1) PostgreSQL (이미 설치/실행됨)
```bash
brew services start postgresql@16
createdb ai_crawler   # 최초 1회
```

### 2) 백엔드
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env      # 그리고 GOOGLE_API_KEY 입력
```

### 3) 프론트엔드
```bash
cd electron
npm install
```

### 4) 실행
프로젝트 루트에서:
```bash
./run.sh
```
또는 Electron이 백엔드를 자동 기동:
```bash
cd electron && npm start
```

## 참고
- 네이버쇼핑·쿠팡은 봇 차단이 있습니다. 대량/고속 요청 시 차단될 수 있으니 `.env`의
  `CRAWL_MAX_PRODUCTS`, `CRAWL_*_DELAY_MS` 를 보수적으로 두세요.
- 차단이 잦으면 `.env`에서 `CRAWL_HEADLESS=false`로 두고 창을 띄워 크롤링하면 통과율이 올라갑니다.
- 사이트 구조 변경 시 `backend/app/crawlers/*.py` 의 셀렉터 튜닝이 필요할 수 있습니다.
