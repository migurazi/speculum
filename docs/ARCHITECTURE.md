# Speculum — Architecture

> **본 문서는 v0.1 스켈레톤.** 구체 schema·테이블 정의는 별도 `DATA_MODEL.md` (M0 진입 시 작성).

---

## 1. 시스템 구성

```
┌─────────────────────────────────────────────────────────────┐
│                      Browser (Next.js 14)                    │
│  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌─────────────┐│
│  │ Screener   │ │ StockDetail│ │ Compare  │ │  Watchlist  ││
│  └────────────┘ └────────────┘ └──────────┘ └─────────────┘│
│       ▼              ▼               ▼              ▼        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  TanStack Query (서버 상태) + Zustand (로컬 상태)    │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTPS + JWT (NextAuth Google)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI (Python 3.12)                           │
│  /api/screen     /api/stock/{code}    /api/compare           │
│  /api/watchlist  /api/factor (M2)     /api/sector (M1)       │
│       ▼                                                       │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Service layer — 8기둥 lint, PIT enforcer, factor   │    │
│  └────────────────────────┬────────────────────────────┘    │
│                           ▼                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Repository — SQLAlchemy 2                          │    │
│  └────────────────────────┬────────────────────────────┘    │
└──────────────────────────┬┴──────────────────────────────────┘
                           ▼
       ┌──────────────────────────────────────┐
       │  PostgreSQL 16                       │
       │   - stocks_master                    │
       │   - prices (월별 partition)          │
       │   - financials (JSONB + effective_date) │
       │   - users / watchlist / factors      │
       └──────────────────────────────────────┘
                           ▲
                           │ 일배치
       ┌──────────────────────────────────────┐
       │  Batch Workers (APScheduler / cron)  │
       │   - 16:30 KST  KRX/pykrx 일배치      │
       │   - 03:00 KST  DART 신규 공시 수집   │
       └──────────────────────────────────────┘
                           ▲
                           │
       ┌───────────────┬───┴──────────┬──────────────┐
       ▼               ▼              ▼              ▼
   FinanceDataReader  pykrx       DART OpenAPI   한국은행 ECOS
   (가격·종목 마스터) (KRX 공식)   (재무제표)    (M1 — 환율·금리)
```

---

## 2. Frontend

### 2.1 스택

- **Next.js 14** App Router + TypeScript strict
- **Tailwind CSS** + **shadcn/ui**
- **TanStack Query** (서버 상태)
- **TanStack Table** (가상화 — 1만 종목 결과 60fps)
- **Lightweight Charts** (TradingView 오픈소스)
- **Zustand** (Watchlist 임시 상태 등 로컬)
- **NextAuth.js** (Google OAuth)

### 2.2 디렉토리

```
client/
  app/                      # Next.js App Router
    (auth)/                 # 로그인 페이지
    (app)/                  # 인증 필요 영역
      screener/
      stock/[code]/
      compare/
      watchlist/
    api/                    # Route Handlers (backend proxy)
    layout.tsx              # 동의 모달 + footer disclaimer
  components/
    ui/                     # shadcn/ui 기본
    charts/                 # 차트 wrapper
    screener/               # 조건 빌더, 결과 테이블
    stock-detail/           # 지표 카드, 시계열
    compare/                # 비교 그리드
    watchlist/              # 폴더 트리
  lib/
    api.ts                  # FastAPI 클라이언트
    formatters.ts           # 통화·% 포맷
    forbidden-words.ts      # 금지 어휘 lint (8기둥 §2.2)
  state/                    # Zustand stores
  styles/
```

### 2.3 8기둥 강제

- **Fidelity (§2.1)**: 지표 표시 component 는 `value` + `source` + `formula` + `as_of` props 를 반드시 받음. TypeScript level 강제.
- **No Advice (§2.2)**: ESLint rule + 빌드 시 정적 검사 (사용자 visible 텍스트에 금지 어휘 0).
- **Active Inspection (§2.3)**: 홈 화면 default = Watchlist 또는 Market Overview (의제 없는 fact). "추천 위젯" 컴포넌트 자체가 존재하지 않음.

---

## 3. Backend

### 3.1 스택

- **Python 3.12** + **FastAPI** + **Uvicorn**
- **SQLAlchemy 2** (Async)
- **PostgreSQL 16**
- **Redis** (M1+ — 캐시, rate limiting)
- **APScheduler** (M0) → **Celery + Redis Broker** (M1+ — 배치 분리)

### 3.2 디렉토리

```
server/
  app/
    api/                    # FastAPI routers
      screener.py
      stocks.py
      compare.py
      watchlist.py
    services/               # 비즈니스 로직
      screener_service.py
      pit_enforcer.py       # Point-in-Time 강제 (§2.4)
      factor_engine.py      # M2 — DAG 평가
      forbidden_words.py    # 금지 어휘 검사 (request/response)
    repositories/           # SQLAlchemy
    schemas/                # Pydantic
    models/                 # SQLAlchemy ORM
    core/
      auth.py               # NextAuth JWT 검증
      config.py
      logging.py
  batch/
    krx_daily.py            # 16:30 KST
    dart_daily.py           # 03:00 KST
    corporate_action.py     # 액면분할·권리락 등 보정
  tests/
```

### 3.3 핵심 service: PIT Enforcer

8기둥 §2.4 의 핵심 — 모든 historical 쿼리에 `as_of` 파라미터 강제.

```python
# 예시
class PITEnforcer:
    def filter_financials(self, query, as_of: date):
        return query.where(
            Financial.effective_date <= as_of
        ).order_by(
            Financial.effective_date.desc()
        )
```

쿼리 layer 에서 강제하지 않으면 UI 가 알 수 없는 look-ahead 가 발생. Repository 가 raw query 를 노출하지 않는 게 원칙.

---

## 4. 데이터 모델 (개요)

### 4.1 핵심 테이블

| 테이블 | 핵심 컬럼 | 메모 |
|---|---|---|
| `stocks_master` | code, name, market, sector_krx, listing_date, delisting_date, fiscal_month, ifrs_preference | 종목 마스터 + history (재상장·합병 시) |
| `prices_daily` | code, date, open, high, low, close, volume, value, market_cap, shares_outstanding | 월별 partition |
| `financials` | code, fiscal_year, fiscal_quarter, ifrs_type (CFS/OFS), effective_date, data (JSONB) | DART 원문 JSONB. `effective_date` 가 PIT 의 핵심 |
| `corporate_actions` | code, action_type, ex_date, ratio, ... | 액면분할·무상증자 등 |
| `users` | id, google_sub, email, name, image_url, created_at | Google OAuth |
| `watchlists` | id, user_id, name, parent_id (폴더링), order | 트리 구조 |
| `watchlist_items` | watchlist_id, code, order, note | |
| `screener_sets` | id, user_id, name, conditions (JSONB), created_at | 조건셋 저장 |
| `factors` | id, owner_user_id, name, formula, version, hash, ... | M2 |

### 4.2 8기둥 §2.4 PIT 의 effective_date

- DART 보고서 접수일 (`rcept_dt`) 을 `effective_date` 로 사용
- 정정공시는 **새 record 로 추가** — 기존 record 변경 X (history 보존)
- 쿼리 시 `MAX(effective_date) WHERE effective_date <= as_of` 패턴

### 4.3 8기둥 §2.6 KRX-Native 의 종목코드 변경

- `stocks_master` 의 row 자체는 종목 단위가 아니라 **종목 lineage 단위**
- `code_history` JSONB 컬럼에 `[(code, valid_from, valid_to), ...]`
- 합병상장·재상장·종목코드 변경 시 새 row 가 아닌 history append

---

## 5. 배치 잡

### 5.1 KRX 일배치 (16:30 KST)

```
1. pykrx.get_market_ohlcv (KOSPI + KOSDAQ) → prices_daily insert
2. pykrx.get_market_cap → prices_daily.market_cap, shares_outstanding update
3. pykrx.get_market_trading_value_by_investor → (M1) 외인·기관 매매동향
4. 신규 상장 종목 감지 → stocks_master insert
5. 상장폐지 감지 → stocks_master.delisting_date update (row 삭제 X)
6. 휴장일이면 skip
```

### 5.2 DART 일배치 (03:00 KST)

```
1. 어제 접수된 공시 목록 fetch (rate limit 고려)
2. 분기·연간 재무제표인 것만 필터
3. XBRL 또는 HTML 파싱 → K-IFRS 표준 계정과목으로 정규화
4. financials insert (정정이면 새 row, 기존 row 변경 X)
5. corporate action 공시 (액면분할·합병 등) → corporate_actions 테이블
```

### 5.3 DART rate limit 정책

- DART OpenAPI 는 키당 일 10,000 요청 (2026 기준 — 확인 필요)
- 분 단위 throttling 으로 안전 마진 확보
- 실패 시 exponential backoff + retry
- 일배치 실패 → Sentry alert + 다음 날 재시도

---

## 6. 인증 / 권한

- **Frontend**: NextAuth.js Google Provider → JWT
- **Backend**: NextAuth JWT 검증 (PUBLIC_KEY 공유) → `request.state.user`
- **Row-level**: 모든 사용자 데이터 (watchlist, screener_sets, factors) 는 `user_id` 컬럼 + repository 에서 자동 필터링
- **Admin**: 별도 admin role (M2+) — 사용자 통계, 데이터 파이프라인 상태 모니터링

---

## 7. 배포

### 7.1 Frontend (Vercel)

- Next.js + serverless functions
- 환경변수: `NEXTAUTH_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `API_BASE_URL`

### 7.2 Backend (Fly.io 또는 Railway)

- Docker 이미지 (multi-stage build)
- PostgreSQL: Fly Postgres / Railway Postgres
- 배치 워커: 별도 process (FastAPI 와 분리)

### 7.3 모니터링

- **Sentry**: 에러 (frontend + backend)
- **Plausible**: 익명 analytics (개인정보 우려로 GA 미사용)
- **Healthcheck**: `/healthz` endpoint (DB 연결 + 마지막 배치 시각)

---

## 8. CI / 빌드 게이트

자매 프로젝트 Norma 의 CI 게이트 패턴:

| 게이트 | 검사 |
|---|---|
| `check-forbidden-words` | 금지 어휘 (§2.2 No Advice) 코드/콘텐츠 모두 검사 |
| `check-pit-correctness` | PIT enforcer 우회 가능한 raw query path 차단 |
| `check-disclaimer-coverage` | 모든 페이지 footer 에 disclaimer component 포함 |
| `check-source-attribution` | 지표 component 가 source/formula/as_of props 누락 시 빌드 실패 |
| `check-i18n-keys` | 한국어 텍스트 누락 검사 |

---

## 9. 보안

- HTTPS 강제 (HSTS)
- CSP — script src 제한
- CSRF — NextAuth 기본
- Rate limiting — Redis (M1+)
- DART API 키, OAuth secret 등 모두 환경변수, 절대 코드 commit X
- 개인정보 (Google OAuth 이메일·이름) 은 별도 테이블, 분석용 ID 와 분리 (Tessera PHI 분리 패턴)

---

## 10. 솔직한 trade-off

- **PostgreSQL vs ClickHouse**: 시계열 분석 위주면 ClickHouse 가 빠르지만, 소규모 공개 + 사용자 데이터 transaction 필요 → PostgreSQL JSONB + partition 으로 충분.
- **FastAPI vs Django**: 데이터 파이프라인 + REST API 만 → FastAPI 가 가볍고 적합. admin UI 필요해지면 (M2+) Django admin 검토.
- **TanStack Table vs AG Grid**: 가상화 + 정렬·필터 충분 → TanStack Table (오픈소스, 가벼움). AG Grid 의 enterprise 기능 불필요.
- **장중 실시간 vs 종가 배치**: 실시간 = 증권사 OpenAPI 필요 + 인프라 부담 + 8기둥 §2.7 와의 거리. **종가 배치만 — 충분**.
