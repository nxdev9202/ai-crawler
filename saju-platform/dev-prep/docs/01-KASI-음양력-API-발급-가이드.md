# 01. 한국천문연구원(KASI) 음양력 API 발급 가이드

> 목적: 음양력 변환 + 일진(간지) 데이터 수집. 사주 연주(年柱)·일주(日柱) 계산의 원천 데이터이자,
> 계산엔진(ssaju) 결과의 크로스체크 기준.

## 1. 발급 절차 (공공데이터포털)

1. **공공데이터포털 가입/로그인** — https://www.data.go.kr (개인 회원이면 충분, 사업자 불필요)
2. **API 검색** — 검색창에 `한국천문연구원 음양력 정보` 입력 → "한국천문연구원_음양력 정보" 선택
   - 직접 링크: https://www.data.go.kr/data/15012679/openapi.do
3. **활용신청** 버튼 클릭
   - 활용목적: "웹 서비스 개발" 등 자유 기재 (사주 서비스라고 써도 무방)
   - 상세기능 전체 체크 (음력일정보, 양력일정보, 특정음력일정보, 율리우스적일정보)
4. **자동승인** — 신청 즉시 승인됨 (심의 없음)
5. **인증키 확인** — 마이페이지 → 오픈API → 개발계정 상세보기
   - **일반 인증키(Encoding)** 와 **일반 인증키(Decoding)** 두 가지가 있음
   - curl/브라우저에서 직접 쓸 때는 Encoding 키, SDK/라이브러리가 URL 인코딩을 해주는 경우 Decoding 키
   - ⚠️ 발급 직후에는 반영에 최대 1시간 정도 걸릴 수 있음 (SERVICE_KEY_IS_NOT_REGISTERED_ERROR 나오면 잠시 후 재시도)

## 2. 트래픽 제한

| 계정 | 일일 트래픽 |
|---|---|
| 개발계정 | 10,000건/일 |
| 운영계정 | 활용사례 등록 후 증량 신청 가능 |

- 1900~2100년 전체 일진 데이터는 약 73,000일 → **일 10,000건 제한 기준 약 8일에 걸쳐 수집**하면 로컬 DB 구축 완료.
- 수집 후에는 API를 실시간 호출하지 말고 **로컬 DB(SQLite 등)에 적재**해서 쓰는 것이 정석 (rgbitcode 블로그도 동일한 방식 권장).

## 3. 엔드포인트

Base URL: `http://apis.data.go.kr/B090041/openapi/service/LrsrCldInfoService`

| 오퍼레이션 | 용도 | 주요 파라미터 |
|---|---|---|
| `getLunCalInfo` | **양력→음력** 변환 + 간지 조회 | `solYear`, `solMonth`, `solDay` |
| `getSolCalInfo` | **음력→양력** 변환 | `lunYear`, `lunMonth`, `lunDay` |
| `getSpcifyLunCalInfo` | 특정 음력일의 양력 범위 조회 | `fromSolYear`, `toSolYear`, `lunMonth`, `lunDay`, `leapMonth` |
| `getJulDayInfo` | 율리우스적일 조회 | `solJd` |

⚠️ 참고: 포털 문서에 오퍼레이션 이름 표기가 뒤바뀐 오타가 있음 (rgbitcode 블로그 지적). `getLunCalInfo`에 **양력** 날짜를 넣으면 음력+간지가 나온다.

## 4. 호출 예시

```bash
# 양력 2024-02-04의 음력/간지 조회
curl "http://apis.data.go.kr/B090041/openapi/service/LrsrCldInfoService/getLunCalInfo?solYear=2024&solMonth=02&solDay=04&ServiceKey=발급받은_인코딩_키"
```

응답(XML) 주요 필드:

| 필드 | 의미 | 예시 |
|---|---|---|
| `lunYear/lunMonth/lunDay` | 음력 연/월/일 | 2023 / 12 / 25 |
| `lunLeapmonth` | 윤달 여부 | 평/윤 |
| `lunSecha` | **연 간지(세차)** — 연주 | 계묘(癸卯) |
| `lunWolgeon` | 월 간지 (단, 음력월 기준 — 사주 월주로 쓰면 안 됨!) | |
| `lunIljin` | **일 간지(일진)** — 일주 | |
| `solWeek` | 요일 | |
| `lunNday` | 해당 음력월 일수 | |

## 5. 사주 계산 시 주의점 (중요)

1. **`lunSecha`(연 간지)는 음력 1월 1일 기준으로 바뀌지만, 사주의 연주는 입춘 기준으로 바뀐다.**
   입춘(2월 4일경) 이전 출생자는 전년도 간지를 써야 하므로, `data/solar-terms/`의 입춘 시각과 조합해서 판단할 것.
2. **`lunWolgeon`(월 간지)도 음력월 기준이라 사주 월주로 직접 쓸 수 없다.** 월주는 반드시 절기(절입시각) 기준 — `data/solar-terms/` 데이터 사용.
3. **`lunIljin`(일진)은 그대로 일주로 사용 가능.** 단, 야자시/조자시 옵션에 따라 밤 11시 이후 출생자는 날짜 귀속이 달라짐 (→ `05-야자시-조자시-규칙.md`).
4. 이 API는 절기 시각을 주지 않는다. 같은 KASI의 **특일 정보제공 서비스**(`B090041/openapi/service/SpcdeInfoService/get24DivisionsInfo`)로 절기 "날짜"는 받을 수 있으나 **시·분 단위 절입시각은 제공하지 않으므로**, 절입시각은 본 프로젝트가 자체 생성한 `data/solar-terms/solar_terms_1900_2100.json`을 사용한다.

## 6. 수집 스크립트 설계 (개발 단계에서 구현)

```
for date in 1900-01-01 .. 2100-12-31 (일일 최대 9,900건씩 분할):
    GET getLunCalInfo(solYear, solMonth, solDay)
    → SQLite manseryeok(sol_date PK, lun_year, lun_month, lun_day, is_leap, secha, iljin, ...)
저장 후 무결성 검증: 총 행수 = 기간 일수, 일진 60갑자 순환 연속성 체크
```

- 키는 `.env`의 `KASI_SERVICE_KEY`로 관리, 저장소에 커밋 금지.
- 대안: 오픈소스 계산엔진(ssaju)이 자체적으로 KASI 데이터 기반 계산을 내장하므로, **API 수집 DB는 "검증용 정답지"로 쓰고 런타임은 ssaju 계산**으로 가는 것이 트래픽·의존성 면에서 유리 (→ `06-계산엔진-오픈소스-선정.md`).
