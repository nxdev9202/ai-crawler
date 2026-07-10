# 00. MASTER — 개발 실행 프롬프트

> 사용법: 개발을 시작할 때 아래 프롬프트 블록 전체를 Claude Code에 그대로 붙여넣는다.
> (이 폴더 `saju-platform/` 루트에서 세션을 열 것)

---

```
사주풀이 플랫폼 개발을 시작한다. dev-prep/ 폴더에 준비된 문서와 데이터를 기반으로 구현해라.

## 0. 문서 읽기 순서 (구현 전 필독)

1. dev-prep/docs/06-계산엔진-오픈소스-선정.md  ← 전체 아키텍처와 파이프라인이 여기 있다
2. dev-prep/docs/02-24절기-절입시각-데이터.md  ← 월주/연주 경계 규칙
3. dev-prep/docs/03-서머타임-기간표.md          ← 시각 정규화 파이프라인 순서
4. dev-prep/docs/04-진태양시-보정-계산식.md     ← 경도 보정, 도시 테이블
5. dev-prep/docs/05-야자시-조자시-규칙.md       ← 날짜 귀속 옵션
6. dev-prep/docs/07-LLM-해석-프롬프트-설계.md   ← Opus 4.8 호출 규칙 (구식 API 문법 금지 사항 포함)
7. dev-prep/docs/01-KASI-음양력-API-발급-가이드.md ← 검증용 DB (사용자가 키 발급 후)

데이터 파일:
- dev-prep/data/solar-terms/solar_terms_1900_2100.json (절입시각, 검증 완료: KASI 공표값 ±4초)
- dev-prep/data/summer-time/korea_dst_periods.json (서머타임 12개 기간)
- dev-prep/data/summer-time/korea_timezone_history.json (표준시 자오선 이력)

## 1. 기술 스택 (확정)

- Next.js (App Router) + TypeScript
- 계산엔진: ssaju (npm i ssaju) — 문서 06의 선정 근거 참조
- LLM: Claude Opus 4.8 (claude-opus-4-8), @anthropic-ai/sdk
  - thinking: {type:"adaptive"}, output_config: {effort:"high"}, 스트리밍 필수
  - temperature/top_p/budget_tokens는 이 모델에서 400 에러 — 절대 넣지 말 것
- API 키는 .env (ANTHROPIC_API_KEY, KASI_SERVICE_KEY), 커밋 금지

## 2. 개발 순서 (Phase별, 각 Phase 완료 시 테스트 통과 후 다음으로)

### Phase 1 — 시각 정규화 모듈 (lib/time-normalize.ts)
입력(시계 시각, 출생도시) → 정규화된 UTC 시각.
순서: 서머타임 −1h → 시대별 표준시 오프셋 → 진태양시 경도 보정(도시→경도 테이블).
korea_dst_periods.json, korea_timezone_history.json을 그대로 로드해 사용.
단위 테스트: 문서 03·04의 예시 케이스(1955-08-08 16:20 서울, 1990-03-15 15:05 서울) 그대로.

### Phase 2 — 계산엔진 통합 (lib/myeongsik.ts)
ssaju 설치 → Phase 1 결과를 입력으로 명식 계산.
ssaju가 서머타임/구표준시/야자시를 자체 처리하는지 먼저 확인하고, 중복 보정되지 않게 래퍼 설계.
야자시 모드 옵션(traditional | yajasi) 구현 (문서 05).
골든 테스트 5종(문서 06 하단 케이스)을 만세력닷컴/데이사주 결과와 대조해 통과시킬 것.
월주·연주 경계는 solar_terms_1900_2100.json으로 이중 검증.

### Phase 3 — 검증 도구 (선택, KASI 키 발급 후)
문서 01의 수집 스크립트로 일진 DB 구축 → ssaju 일주와 전수 대조하는 스크립트 작성.
불일치 발견 시 보고만 하고 임의 수정하지 말 것.

### Phase 4 — LLM 해석 API (app/api/saju/route.ts)
문서 07의 시스템 프롬프트와 호출 스켈레톤 사용.
- POST 입력: {birth: {...}, gender, maritalStatus, scope: "preview"|"full"}
- 파이프라인: 입력 검증 → Phase1 → Phase2 → 명식 JSON → Opus 4.8 스트리밍 → SSE로 클라이언트 전달
- 시스템 프롬프트는 바이트 고정 + cache_control ephemeral (캐시 히트 확인: usage.cache_read_input_tokens)
- 미리보기는 총평+성정만, 전체는 8개 섹션 (문서 07 구조)

### Phase 5 — 프론트 최소 플로우
입력 폼(생년월일, 양/음력+윤달, 시간(모름 허용), 성별, 혼인여부, 출생도시)
→ 미리보기 표시 → (결제는 이번 범위 아님, 버튼 자리만) → 전체 풀이 표시.
UI 디자인/씬 연출은 사용자가 나중에 별도 지시할 예정이므로 기능 뼈대만.

## 3. 하지 말 것

- LLM에게 사주 계산(간지 산출)을 시키지 않는다 — 계산은 전부 코드.
- 결제/운영/트래킹 구현은 이번 범위 아님.
- 문서와 다른 임의 판단이 필요하면 진행 전에 해당 문서에 결정 사항을 추기하고 이유를 남길 것.

## 4. 완료 기준

- Phase 1~2: 골든 테스트 전부 통과 (경계 케이스 포함)
- Phase 4: 실제 Opus 4.8 호출로 명식 1건의 미리보기+전체 생성 성공, 캐시 히트 확인
- README.md에 실행 방법과 테스트 방법 기록
```

---

## 부록: 준비 폴더 구성 요약

```
saju-platform/
└── dev-prep/
    ├── docs/
    │   ├── 00-MASTER-개발-실행-프롬프트.md   ← 본 문서
    │   ├── 01-KASI-음양력-API-발급-가이드.md
    │   ├── 02-24절기-절입시각-데이터.md
    │   ├── 03-서머타임-기간표.md
    │   ├── 04-진태양시-보정-계산식.md
    │   ├── 05-야자시-조자시-규칙.md
    │   ├── 06-계산엔진-오픈소스-선정.md
    │   └── 07-LLM-해석-프롬프트-설계.md
    └── data/
        ├── solar-terms/
        │   ├── solar_terms_1900_2100.json   (4,824건, KASI 공표값 ±4초 검증)
        │   ├── solar_terms_1900_2100.csv
        │   └── generate_solar_terms.py      (재생성 스크립트, pyephem 필요)
        └── summer-time/
            ├── korea_dst_periods.json       (서머타임 12개 기간)
            ├── korea_timezone_history.json  (표준시 자오선 5개 시대)
            └── raw/rgbitcode_blog43_원문크롤.txt (Wayback 스냅샷 원문)
```
