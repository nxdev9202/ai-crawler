#!/usr/bin/env bash
# 개발용 실행 (macOS/Linux). DB는 SQLite(backend/app.db)라 별도 서버 불필요.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Electron이 백엔드(FastAPI)를 자동 기동
cd "$ROOT/electron"
npm start
