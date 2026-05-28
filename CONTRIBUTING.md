# Contributing

Speculum 에 기여해주셔서 감사합니다. 본 문서는 **저장소 운영 정책 + 기여
패턴** 의 요약입니다. 정확한 결정 사유는 [`docs/adr/`](docs/adr/), 작업
단위는 [`docs/M0_PLAN.md`](docs/M0_PLAN.md) 참조.

---

## 1. 시작하기 전에

### 1.1 무엇이 받아들여지는가

| 환영 | 신중 |
|---|---|
| 1차 자료 (KRX / DART / ECOS) 기반 factor 정의 | 추정 / 합의 / 외부 인용 |
| ADR 결정 일관 fix | ADR 무시한 우회 패치 |
| PIT 일관성 + Source Citation 동반 | "값만 표시" 우회 |
| K-IFRS 결산기 / 휴장일 명시 처리 | 미국 시장 가정 (Speculum 은 KRX-only) |

10 기둥 (CONCEPT §2) 과 12 개 ADR 위반은 PR 단계에서 거부 사유. **기능
구현 전 관련 ADR 확인 필수**.

### 1.2 보안·정합성 위반 보고

보안 취약점 (의존성 CVE, XSS, IDOR 등) 는 GitHub Issue 가 아닌 별도 보안
채널로 보고. SECURITY.md 합류 전까지는 메인테이너 이메일로.

---

## 2. 개발 환경

### 2.1 요구 사항

- **Node 20** + pnpm 9 (frontend)
- **Python 3.12** (backend)
- **PostgreSQL 16** (운영 — SQLite 도 단위 테스트 OK)

### 2.2 setup

```bash
# 1. 환경변수 template 복사 후 값 채우기 (DART_API_KEY / AUTH_SECRET 등).
cp .env.example .env
# server 환경변수 (host 실행):
#   export $(grep -v '^#' .env | xargs)            # bash
#   Get-Content .env | ForEach-Object { ... }      # PowerShell — README 참조
# client 는 .env.local 로 복사 — Next.js 자동 로드.
cp .env.example client/.env.local

# 2. Postgres 16 (dev 의존성 service) — docker compose.
docker compose up -d                # postgres 5432 띄움
docker compose ps                   # health=healthy 대기 (~5s)

# 3. Alembic migration — schema 0001~0003 적용.
cd server && python -m pip install -e ".[test]"
alembic upgrade head

# 4. FastAPI (별도 터미널).
uvicorn app.main:app                # http://localhost:8000

# 5. Next.js (별도 터미널).
cd client && pnpm install
pnpm dev                            # http://localhost:3000
```

선택: `docker compose --profile tools up -d` 로 Adminer (DB 시각 도구) 추가 —
http://localhost:8080. M0 운영 deploy 의 컨테이너화는 별도 cycle (현 compose 는
dev 의존성 service 만).

---

## 3. 기여 워크플로우

### 3.1 Git 운영 (squash 정책)

본 저장소는 **squash merge to develop** 패턴. PR 미사용. 개인 feature
branch 에서 작업 후 develop 으로 squash.

```bash
# 1. 시작
git fetch origin
git checkout develop && git pull --ff-only
git checkout -b feature/<작업명-kebab>

# 2. 자유롭게 checkpoint commit
git add -A && git commit -m "..."

# 3. 완료 시 develop 에 squash
git checkout develop
git merge --squash feature/<작업명-kebab>
git commit -m "..."     # 본 commit 이 사용자 가시 history
git push origin develop
git branch -D feature/<작업명-kebab>
```

**금지**:

- develop / main 직접 commit
- force push (본인 미공유 feature 외)
- non-squash merge
- 기존 develop commit 재작성

### 3.2 Commit 메시지

```
type(scope): <한 줄 요약 — 한글 OK>

본문 (선택, 한글 OK) — 의도·결정 사유·oracle 리뷰 반영 등.

관련 ADR / 문서.

Co-Authored-By: ...
```

**type** — `feat` / `fix` / `refactor` / `ci` / `docs` / `chore`.
**scope** — `client` / `server` / `T<N>` (M0_PLAN 작업 번호).

예시:

```
feat(client): T37 Stock Detail 뷰 — MetricCard (SourceAttribution wrap) + CodeHistory
```

### 3.3 PR / Code Review (해당 시)

GitHub 외부 사용자의 기여는 일반 PR. 메인테이너가 squash merge.

---

## 4. 코드 품질 요구

### 4.1 모든 변경이 통과해야 하는 CI 게이트

| 게이트 | tool | 목적 |
|---|---|---|
| forbidden words | `tools/check_forbidden_words.py` | ADR-0007 D4 (No Advice) |
| disclaimer coverage | `tools/check_disclaimer_coverage.py` | ADR-0006 D2 + ADR-0007 D2 |
| source attribution | `tools/check_source_attribution.py` | ADR-0007 D2 (Fidelity UI) |
| PIT bypass | `tools/check_pit_bypass.py` | ADR-0008 D5 + ADR-0002 D3 |
| client typecheck/lint/test/build | `pnpm` | TypeScript strict + ESLint flat |
| server pytest/ruff | `pytest` + `ruff` | unit + 정적 분석 |

로컬에서 PR 전 검증:

```bash
# 모든 정적 가드 (1초 미만)
python tools/check_forbidden_words.py .
python tools/check_disclaimer_coverage.py
python tools/check_source_attribution.py
python tools/check_pit_bypass.py

# frontend
cd client && pnpm typecheck && pnpm lint && pnpm test && pnpm build

# backend
cd server && python -m ruff check . && python -m pytest -m "not integration"
```

#### Integration tests (선택, 외부 호출)

Adapter (pykrx / FDR / DART) 의 실 호출 smoke 는 `server/tests/integration/`.
일반 PR 검증에 미포함 — CI 의 `integration-nightly.yml` (KST 02:00) 또는
수동 workflow_dispatch 에서만 실행.

```bash
cd server
# pykrx + FinanceDataReader 미설치 환경에서는 자동 skip.
python -m pytest tests/integration -m integration -v

# DART 도 함께 실 호출 (API key 필요) — bash
DART_API_KEY=<your-key> python -m pytest tests/integration -m integration -v

# 동일, PowerShell
$env:DART_API_KEY = "<your-key>"; python -m pytest tests/integration -m integration -v
```

#### E2E tests (Playwright, client/)

4 뷰 + 동의 모달 사용자 흐름은 `client/tests/e2e/` (M0_PLAN T42). CI 의
`client-e2e.yml` 이 push/PR `client/**` 시 + KST 03:00 nightly +
workflow_dispatch 에서 실행. chromium 1 개만 (M0).

```bash
cd client
# 최초 1 회 — chromium download (~150MB, Windows/macOS/Linux 자동).
pnpm e2e:install

# 실 브라우저 실행 (next dev 자동 spawn).
pnpm e2e

# UI 모드 (개발 디버깅).
pnpm e2e:ui
```

Windows 노트: PowerShell 에서도 동일. `pnpm e2e:install` 가 chromium binary
를 `~\AppData\Local\ms-playwright\` 에 cache. CI 와 동일 hash 사용.

### 4.2 테스트 작성

- **단위 테스트 우선** — 외부 의존 (KRX / DART / 네트워크) 없이 실행.
  Adapter 의 실 호출은 `@pytest.mark.integration` 별도 marker.
- Backend = `server/tests/`, frontend = `client/**/__tests__/`.
- Pydantic / dataclass / 순수 함수 가장 우선.

### 4.3 주석 정책

- WHY 만 작성 — WHAT 은 잘 이름 지은 식별자가 설명.
- ADR / oracle 리뷰 인용 — "(ADR-0008 D7)", "(oracle T40 C1)".
- 미래 cycle 의 의도된 분리 — "Out-of-scope (별도 cycle): ...".

---

## 5. ADR (Architecture Decision Record)

큰 결정은 ADR 로 기록. `docs/adr/` 의 기존 12 ADR 형식 따름:

1. **Title** — `ADR-XXXX: 결정 한 줄`
2. **Status** — `PROPOSED` → `ACCEPTED` / `REJECTED` / `SUPERSEDED`
3. **Context** — 왜 결정이 필요한가
4. **Decision** — D1, D2, ... 명시적 결정 list
5. **Consequences** — positive / negative 양쪽

ADR 무시한 PR 은 거부 후 ADR 재논의 단계로.

---

## 6. License

기여하면 코드는 [MIT 라이선스](LICENSE) 로 배포됩니다. Pull request 제출은
본인이 라이선스 권한 보유함의 확인입니다.

데이터 라이선스는 별도 — Speculum 은 1차 자료 (KRX / DART 등) 를
재배포하지 않으며 출처 정책은 [ADR-0006 §D5](docs/adr/adr-0006-legal-review.md) 참조.
