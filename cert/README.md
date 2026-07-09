# 코드서명 & 사내 배포 (SmartScreen 경고 제거)

앱은 자체 서명 인증서로 서명됩니다. 사내 PC에서 "알 수 없는 게시자 / SmartScreen 경고"를
없애려면 아래 **공개 인증서를 사내 PC에 배포**하면 됩니다. (개인키 pfx는 GitHub Secret에만 있음)

- 공개 인증서: `NXaiCrawler-publisher.cer` (게시자 = NX)
- 서명 자동화: 매 빌드마다 CI가 `WIN_CSC_LINK`/`WIN_CSC_KEY_PASSWORD` 시크릿으로 자동 서명

---

## IT팀: GPO로 사내 전체 배포 (권장)

**그룹 정책 관리 편집기** → Computer Configuration → Policies → Windows Settings →
Security Settings → Public Key Policies 에서 `NXaiCrawler-publisher.cer` 를 **두 곳**에 가져오기:

1. **Trusted Root Certification Authorities** (신뢰할 수 있는 루트 인증 기관)
2. **Trusted Publishers** (신뢰할 수 있는 게시자)

→ 정책 적용된 사내 PC에서 "알 수 없는 게시자" 경고가 사라집니다.

## 개별 PC 수동 등록 (테스트용, 관리자 CMD)
```bat
certutil -addstore Root NXaiCrawler-publisher.cer
certutil -addstore TrustedPublisher NXaiCrawler-publisher.cer
```

---

## SmartScreen(파란 화면)에 대한 정확한 안내

파란 "Windows의 PC 보호 / SmartScreen" 창은 **인터넷에서 다운로드한 파일(Mark-of-the-Web)**
중 평판이 없는 것에 뜹니다. 위 인증서 배포는 "게시자 경고"를 없애지만, SmartScreen의
**다운로드 평판 검사는 별개 계층**이라 아래 중 하나가 추가로 필요합니다:

- **(가장 확실) 사내 배포 채널 이용**: Intune / SCCM / 네트워크 공유 / GPO 소프트웨어 설치로
  배포하면 Mark-of-the-Web가 없어 SmartScreen이 뜨지 않습니다.
- **MOTW 제거**: 다운로드한 msi에 대해 `Unblock-File .\NXaiCrawler-Setup-1.0.0.msi` (PowerShell)
- **GPO로 SmartScreen 정책 조정**: Computer Config → Administrative Templates →
  Windows Components → File Explorer → "Configure Windows Defender SmartScreen"
  (관리 대상 기기 한정)

> 요약: **사내 관리 PC에 (1) 인증서 GPO 배포 + (2) Intune/SCCM/네트워크 공유로 설치** 하면
> 게시자 경고·SmartScreen 모두 뜨지 않습니다. 불특정 외부 배포까지 즉시 없애려면 EV 인증서가 필요합니다.

---

## 인증서 정보
- 주체(Subject): CN=NX, O=NX, C=KR
- 용도: Code Signing (EKU 1.3.6.1.5.5.7.3.3)
- 유효기간: 5년
- 개인키(pfx)는 저장소에 없음 — GitHub Secret `WIN_CSC_LINK`(base64)/`WIN_CSC_KEY_PASSWORD`
- 재발급/교체 시: 새 pfx를 만들어 두 시크릿을 갱신하고, 새 .cer 를 재배포
