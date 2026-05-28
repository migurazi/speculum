# Security Policy

본 문서는 Speculum 의 **보안 취약점 보고 채널** + **지원 버전** + **의존성
audit 정책** 의 단일 정의입니다.

---

## 1. 지원 버전

| 버전 | 지원 | 비고 |
|---|---|---|
| `0.1.x` (M0) | 예 — 보안 fix 적극 backport | M0 release 후 v0.1.0 tag 부터 |
| `< 0.1.0` | 아니오 — pre-release 단계 | develop 직접 사용 권장 |

M1 진입 시 본 표 갱신. semver patch 는 in-place upgrade 보장.

---

## 2. 취약점 보고 채널

### 2.1 보고 대상

다음 부류의 발견을 신고해주세요:

- **frontend XSS / CSRF / clickjacking** — Next.js / radix-ui / TanStack
  Query 등 의존성 또는 자체 코드.
- **backend IDOR / SQL injection / authn bypass** — FastAPI dependency
  + SQLAlchemy + NextAuth JWT 검증 영역.
- **금지 어휘 우회** — ESLint rule / FastAPI middleware / Python scanner
  세 layer 중 어느 하나라도 통과되는 의도적 우회.
- **PIT bypass** — `as_of` 무시한 historical 쿼리 path. backend 의 raw
  query 또는 cache poisoning.
- **민감 데이터 누출** — log / response body / error message 의 token /
  email / user_id echo.
- **의존성 CVE** — pnpm-lock / pip dependency 의 known CVE.

### 2.2 보고 방법

> **GitHub Issue 에 직접 보고하지 마세요.** 공개 채널은 zero-day 노출
> 위험이 있습니다.

- **GitHub Security Advisories** — 본 저장소의 Security 탭에서 "Report
  a vulnerability" (PRIVATE).
- 백업 채널 — 메인테이너 이메일 (`README.md` 또는 git history 의 author
  필드 참조).

보고 시 포함해주시면 도움됩니다:

- 영향 받는 commit hash 또는 release tag.
- 재현 절차 (가능한 최소 환경).
- 추정 영향 범위 (CVSS 등급 추정 — 제출 필수 아님).
- PoC 코드 (선택, 공개 시 안전한 변형).

---

## 3. 응답 SLA

| 단계 | SLA |
|---|---|
| 접수 확인 (acknowledge) | 72 시간 |
| 초기 분류 + severity 판정 | 7 일 |
| Critical / High fix release | 14 일 |
| Medium fix release | 30 일 |
| Low fix release | 다음 minor release |

본 표는 M0 운영 가정. 단일 메인테이너 시점 기준 — coverage 가 부족할
경우 advance notice 후 조정.

---

## 4. 의존성 Audit 정책

### 4.1 자동 검증

- **CI** — pnpm + pip lockfile 의 known CVE 는 future cycle 의 dependabot
  / renovate / GitHub Advanced Security 합류 후 자동 검사. 현 시점 (M0
  pre-release) 은 수동 검증.
- **Manual sweep** — release 직전 (T47) 에 `pnpm audit` + `pip-audit`
  실행. 결과는 RELEASE_NOTES 에 기록.

### 4.2 알려진 우려 (M0 시점)

- **NextAuth 5.0.0-beta.31** — pre-release. SNYK-JS-NEXTAUTH-13744118 같은
  email validation CVE 가 beta.22 에서 fix 되어 현 버전은 안전. M0 release
  전 GA 버전 출시 시 즉시 upgrade.
- **pykrx 의 KRX 페이지 크롤링** — KRX 의 약관 변경 시 silent breakage
  가능. T18 일배치의 circuit breaker + Sentry alert 가 1차 가드. 라이선스
  변경은 ADR-0006 D5 정책으로 우회.

### 4.3 token / secret 정책

- `.env.local` / `.env.production` 는 절대 commit 금지. `.gitignore` 강제.
- backend 의 `SPECULUM_USER_ID` / `DART_API_KEY` / `DATABASE_URL` 등은
  process env 만 사용. 코드 default 는 dev fallback 또는 RuntimeError.
- frontend 의 `NEXT_PUBLIC_*` 은 빌드 시 inline — 민감 정보 노출 금지.

---

## 5. 사용자 데이터 처리

ADR-0006 §3 의 정책:

- **저장 데이터** — Google profile (email / sub) + 사용자 정의 watchlist /
  screener set / Screen Run. 모두 cross-border (Vercel / Fly.io) 호스팅 —
  사용자가 동의 모달 (ADR-0006 D2) 에서 명시 확인.
- **민감 식별자** — user_id 는 backend `SYSTEM_USER_ID` (M0 single-user)
  또는 NextAuth JWT sub. log 출력 금지.
- **타사 API 호출** — DART / KRX / FDR / ECOS 모두 인증 없는 공개 API.
  user identity 가 외부로 전파되지 않음.

자세한 처리방침은 별도 PRIVACY.md (M1+ 합류 예정).

---

## 6. 보안 관련 ADR / 게이트

| 자료 | 관련 |
|---|---|
| ADR-0006 §D5 | 데이터 라이선스 + 개인정보 |
| ADR-0007 §D4 | 금지 어휘 강제 — file-system + middleware |
| ADR-0008 §D5 | PIT Enforcer — look-ahead 차단 |
| CI 게이트 4 종 | `tools/check_*.py` (CONTRIBUTING §4.1) |

---

## 7. License + 면책

본 정책은 [LICENSE](LICENSE) (MIT) 의 면책 조항을 보완하며, 동일하게
**무보증·무책임** 원칙을 따릅니다. 본 정책의 SLA 는 best-effort 약속이며
법적 구속력이 없습니다.
