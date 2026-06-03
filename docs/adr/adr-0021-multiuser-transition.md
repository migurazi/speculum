# ADR-0021: 멀티유저 전환 — NextAuth + user 격리 + M1 데이터 마이그레이션

| | |
|---|---|
| **Status** | PROPOSED (M2 Phase 0 선결 — T68/T69 착수 전 확정 필요) |
| **Date** | 2026-06-01 |
| **Deciders** | 사용자 |
| **Related** | [[adr-0011-watchlist-scope]](watchlist scope·default folder), [[adr-0008-as-of-date]](screen_runs/data_versions), `docs/CONCEPT.md` §2.3 Active Inspection / §2.10 Reproducibility / §2.4 PIT; `docs/work-orders/m2-milestone.md` T68/T69·R2/R2-bis/C3; oracle 분석(2026-06-01) |

## Context

M0~M1 은 `auth.py` 의 `SYSTEM_USER_ID` placeholder(실 인증 없음, 단일 사용자)로 동작했다. M2 는 NextAuth Google OAuth 실 사용자로 전환하며, user 소유 데이터(watchlist / notes / screener_sets / custom factor pack)를 격리한다. **fact(가격/재무/매크로)는 user 무관 공용.**

**oracle 분석의 결정적 발견**: 코드베이스가 이 전환에 대해 **이미 옳게 설계**돼 있다.
- `result_hash` 입력 = `{query, as_of, result_codes, data_versions}` — **`user_id` 는 hash 입력이 아니다**(`screen_run.py:318-327`). `reproduce_run` 도 user_id 를 안 읽는다(`reproduce.py:111-189`). 즉 **마이그레이션이 재현성을 깨지 않는다**(R2-bis 의 "computed_by 불변" 전제는 현 구현에서 거짓 — `computed_by` 컬럼 자체가 없고 `user_id`(소유자, hash 무관)만 있음).
- 미인증 경계가 이미 라우트 분리로 구현됨: `CurrentUserDep` 를 쓰는 라우트는 `runs`/`screener_sets`/`watchlists` 3개뿐. `screen`·fact 라우트는 인증 불필요(`screen.py:61`).
- 실제 리스크는 ① `users` 테이블/FK 부재(migration 0002 deferred) ② `SqlScreenRunRepository.save` 의 owner 미검증 IDOR ③ "computed_by" 용어 함정.

## Decision

### D1. 인증 경로 (NextAuth JWT)
- NextAuth v5 Google OAuth + `session.strategy = "jwt"`, **HS256(AUTH_SECRET backend 공유)**. 비대칭(RS256)은 단일 배포에 과잉.
- `get_current_user`(`auth.py`) 본문만 JWT decode → `sub` 클레임 → `users` upsert → `UserContext(user_id, is_system=False)` 로 교체. **signature 보존**(라우트 무변경 — scaffold 활용).
- **user 프로비저닝**: Google 첫 로그인 시 `users` row + default "내 관심 종목" 폴더(ADR-0011 D7, is_default=True) 생성. 마이그레이션이 아닌 신규 가입 플로우.
- 어떤 로그인 세션도 `is_system=True` 를 받지 않음(SYSTEM_USER_ID 는 마이그레이션된 run 의 소유자로만 잔존).

#### D1.1 JWT 전달·검증·미인증 fallback (T68 골격 결정 — 2026-06-02)
- **JWT 전달 = Authorization Bearer (모델 B).** NextAuth v5 의 기본 세션 토큰은 **JWE(암호화)** 라 PyJWT HS256 으로 직접 복호 불가(NextAuth 내부 HKDF 파생에 강결합). → NextAuth `jwt` callback 에서 별도 **HS256 JWS 액세스 토큰**(claims: `sub`=google_sub, `email`) 발급 → client `fetchJson` 이 `Authorization: Bearer <token>` 첨부 → backend `PyJWT.decode(token, AUTH_SECRET, ["HS256"])`. D1 의 "HS256 AUTH_SECRET 공유" 를 이 형태로 구체화(cookie/JWE 경로 기각 — backend 복호 취약).
- **AUTH_SECRET 게이팅 fallback (회귀 0 의 핵심).** `get_current_user` 는 `AUTH_SECRET` 유무로 분기: **미설정**(개발/CI/테스트 기본) → SYSTEM_USER_ID fallback(기존 M0 동작 유지 — 무토큰 테스트 회귀 0, `SPECULUM_USER_ID` env override 도 유지). **설정**(운영) → JWT 필수(user-scoped 무토큰 → 401). 운영 배포는 AUTH_SECRET 을 필수로 강제(`config` 검증)하므로 인증 우회가 아니라 **single-user 개발 모드의 명시적 선택**. 401 게이트·JWT 검증 테스트는 AUTH_SECRET 설정 fixture + mock JWT 로 수행.
- **users OAuth 메타.** `users` 에 `google_sub`(unique, 문자열)·`email` 컬럼을 T68 migration 으로 추가(Google sub 는 UUID 아님 → 별도 컬럼). JWT `sub`(google_sub) → users 조회 → 있으면 그 `id`(UUID), 없으면 **JIT provision**(uuid4 + default "내 관심 종목" 폴더). sentinel(`…-0001`)은 JIT 대상 아님(google_sub NULL). 신규 `UserRepository`(get_by_google_sub / provision).
- **credential 분리.** JWT 검증·미인증 정책·라우트 가드·IDOR·재현성·migration·frontend NextAuth 구조는 **credential 없이 구현·테스트**(mock JWT). 실 Google OAuth 로그인 E2E·실 client id/secret 만 deferred(M2.1, work-order §8 AC-M2-F-01).

### D2. user 격리 메커니즘
- **repo-레벨 `user_id` keyword-only owner-check 를 일급 격리로 유지**(미들웨어/PG RLS 아님 — RLS 는 SQLite 테스트에서 안 돌고 repo 가 이미 단일 통로). **인증=미들웨어(누구인가), 격리=repo(무엇을 보나)** 분리.
- ownership mismatch 는 404 통일(미존재와 구별 안 함 — 정보 누출 차단). body 에 user_id 미수용(IDOR 차단).
- **users 테이블 신설 + `watchlists/screener_sets/screen_runs.user_id` 에 FK 추가**(현재 FK 부재). 
- **갭 A 폐쇄**: `SqlScreenRunRepository.save`(`sql_user_repositories.py:421-434`)가 user_id 검증 없이 `merge` overwrite → 타 user run overwrite 가능(IDOR). **screen_runs 를 append-only 로 전환**(M0 overwrite 정책 폐기 — `screen_run.py:16-17` 이 예고) 또는 owner-mismatch 거부. append-only 권고(재현 자산 보존 + 격리).
- **negative test(AC-M2-C-04)**: user B 토큰으로 user A 의 folder/set/run id 직접 호출 → 404. save overwrite 경로 포함.

### D3. M1 데이터 마이그레이션 — system-owned 유지
- 기존 `SYSTEM_USER_ID` run/watchlist/screener_set 의 `user_id` **UPDATE 0건**(system-owned 유지). 재귀속(첫 실유저)·폐기 모두 거부(전자=의미 오염·비결정, 후자=§2.10 freeze 위반).
- `users` 에 **system sentinel row**(`00000000-…-0001`) 추가로 기존 row 의 FK 만족. 마이그레이션 순서: **users 생성 → system row insert → FK 추가**(순서 어기면 FK 위반).
- **운영 DB 한정, dev/demo 비대상**(seed_demo 는 fact 만 심고 run/watchlist 미생성 — 마이그레이션 대상 없음. dev/CI 는 fresh schema).
- **불변식 교정(함정 C)**: 마이그레이션 불변식 = `result_hash`/`data_versions`/`as_of`/`result_codes` **byte 불변**이지 "user_id 보존"이 아니다. user_id 는 재현 무관(hash 입력 아님). **"computed_by 를 result_hash 입력에 추가" 금지** — 그 순간 기존 run hash 붕괴 + 재현의 user 종속화(D5 불변식 붕괴).

### D4. 미인증 사용자 정책
- **공용 fact 익명 허용**: 가격/재무/매크로/`/api/screen` 평가/market-overview/stock detail/compare — 로그인 없이 시장 탐색(§2.3 Active Inspection "누구나 탐색"). 현 라우트 분리와 일치 — **`screen` 에 `CurrentUserDep` 추가 금지**(함정 D, §2.3 위반).
- **인증 요구**: watchlist/notes/screener_sets/custom pack/Save Run 저장·조회.
- 미인증 = 읽기 전용 탐색. 영속화(저장) 액션만 401 → 로그인 유도(평가 결과 자체는 익명 표시).

### D5. 재현성 불변식 (명문화)
> **저장 run 은 user 무관 재현**. 임의 저장 run R 에 대해 `reproduce_run(R)` 은 `R.user_id` 를 입력받지 않으며, fact 조회는 batch_cutoff(공용)만으로 결정된다.
- fact 테이블(prices/financials/macro/market_caps/treasury)에 user_id 컬럼 없음 — 멀티유저 전환이 fact 스키마 무변경.
- **회귀 테스트(AC-M2-C-05)**: M1 SYSTEM_USER_ID 저장 run 을 마이그레이션 후 실유저 세션에서 재현 → `matches=True` + `result_hash` byte 동일.

## Rationale

- `user_id` 가 hash 무관이라 마이그레이션 옵션이 재현을 안 깬다. 그럼에도 system-owned 유지를 택하는 건 ① 재귀속의 비결정·의미오염 ② 폐기의 freeze 위반 회피 + ③ fixed UUID sentinel 로 UPDATE 0건의 가장 보수적 해석.
- 격리는 repo 가 이미 단일 통로이므로 미들웨어/RLS 과잉. 인증/격리 책임 분리 유지.
- 미인증 정책은 현 라우트 분리가 §2.3 과 정확히 일치 — 정책만 못 박으면 됨.

## Consequences

### Positive
- 마이그레이션 UPDATE 0건 + 재현 byte 불변 → §2.10 freeze 보존.
- Factor Lab 저장(T75)·Notes(T76)·watchlist/screener_sets 의 user-scoped 기반 확보.
- 미인증 탐색 허용 → §2.3 Active Inspection.

### Negative / 비용
- users 테이블 + FK migration(순서 주의), screen_runs append-only 전환(save 정책 변경).
- JWT 검증·user 프로비저닝·negative test 추가.

## "이대로 가면 깨지는 지점" (ADR 이 닫는 갭)
- **갭 A**: `SqlScreenRunRepository.save` owner 미검증 IDOR → append-only/owner 거부(D2).
- **갭 B**: users 테이블/FK 실제 부재 → 신설 + sentinel row 선행(D3 순서).
- **함정 C**: "computed_by 불변" 용어 → `result_hash byte 불변` 교정(D3). computed_by 를 hash 입력화 금지.
- **함정 D**: `screen` 에 인증 추가 = §2.3 위반(D4).

## 미해결 (T68/T69 착수 시)
- **[T68 결정 — 2026-06-02]** JWT 전달·검증·미인증 fallback: D1.1 로 확정(Authorization Bearer HS256 JWS, AUTH_SECRET 게이팅 fallback, users OAuth 메타, JIT provision). JWT 만료 = NextAuth jwt callback `exp` + backend exp 검증(만료 401). refresh = NextAuth 자동(정밀화는 M2.1).
- **[T76 결정완료]** notes scope: USER_PRIVATE 전용(ADR-0007 D4.5).
- multi-device session — M2.1/M3(현 JWT stateless 라 다중 기기 동시 로그인은 자연 허용, 세션 무효화/로그아웃 전파만 deferred).
