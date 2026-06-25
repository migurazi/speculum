# Speculum

> The **speculum** of Korean equities. Reflects quantitative market data without distortion so users can actively inspect the market on their own terms.

[한글 README](README.md) · [Concept (10 pillars)](docs/CONCEPT.md) · [Roadmap](docs/ROADMAP.md) · [Architecture](docs/ARCHITECTURE.md) · [ADRs](docs/adr/)

**Status**: planning / pre-M0
**Sister projects**: [Tessera](../tessera/) (DICOM standards-first PACS), [Norma](../norma/) (Cephalometric analysis builder)

---

## Tagline

Just as a *speculum* lets a physician look inside the body, Speculum lets you look inside the Korean equities market — with primary-source data, explicit formulas, and a point-in-time discipline. **It does not recommend.**

The Latin word *speculum* also gives us *speculate* — but Speculum's stance is the opposite: **observe with the instrument, do not speculate.**

---

## Documents

- [docs/CONCEPT.md](docs/CONCEPT.md) — 10 pillars + one-line definition + positioning vs. existing tools
- [docs/ROADMAP.md](docs/ROADMAP.md) — M0–M3+ integrated timeline
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Stack + data model overview
- [docs/M0_PLAN.md](docs/M0_PLAN.md) — M0 v0.1.0 work breakdown
- `docs/adr/` — Architecture Decision Records (12 ADRs accepted pre-M0)

---

## Disclaimer (IMPORTANT)

> **Speculum is an informational quantitative-data inspector and is NOT investment advice under the Korean Capital Markets Act.**
> Data and metrics displayed are derived from primary sources (KRX, DART) but no accuracy or completeness is guaranteed.
> All investment decisions and their outcomes are the sole responsibility of the user.

---

## The 10 Pillars

1. **Fidelity** — The mirror does not distort. Every value carries its source, formula, and as-of date.
2. **No Advice** — The mirror does not recommend. Zero "buy / sell / pick / outperform" vocabulary in user-visible text.
3. **Active Inspection** — The user sets the agenda. No "today's pick" widgets on the home screen.
4. **Point-in-Time Correctness** — The mirror cannot see the future. Look-ahead bias is structurally prevented.
5. **Open Data Sufficiency** — The mirror is free. Built on FinanceDataReader, pykrx, DART OpenAPI alone.
6. **KRX-Native** — The mirror reflects Korea. K-IFRS, fiscal-month diversity, KRX trading calendar are first-class.
7. **Observation over Speculation** — Numbers and definitions only. No narrative, no sentiment, no rumor.
8. **Conformance to Standards** — KRX classifications, K-IFRS accounting, DART filing formats are primary sources.
9. **Temporal Continuity** — The mirror does not cut time. Splits, rights issues, mergers, treasury actions are explicitly adjusted with versioned policy.
10. **Reproducibility** — The mirror is the same as it was yesterday. Every screen run can be frozen as a snapshot and re-executed months later with identical results.

---

## Why Speculum

Existing tools (Naver Finance, brokerage HTS apps, paid Korean quant services) show numbers without showing how the numbers were derived. Speculum makes the derivation a first-class artifact:

- **Every metric** is tagged with its `factor_id` (e.g., `per:ttm-consolidated-ifrs`), its formula AST, and the 7-tuple `SourceCitation` of where the underlying data came from.
- **The same name with different definitions** (KRX official PER vs. our TTM consolidated PER vs. forward consensus PER) gets a different ID — see the *multi-id ambiguous indicators* pattern in [CONCEPT §2.8](docs/CONCEPT.md) and [ADR-0004](docs/adr/adr-0004-market-cap-eps-per.md).
- **Look-ahead bias** is prevented by a server-side `PITEnforcer` that all repository queries must pass through; raw queries are blocked at CI.
- **Recommendations are absent by design** — there is no `<RecommendedStocks>` component in the codebase. ESLint and a FastAPI middleware (in progress) enforce a forbidden-words vocabulary.

---

## Scope

**MVP (M0)** — 4 views:

- **Screener** — Filter the KOSPI/KOSDAQ common-stock universe by quantitative conditions.
- **Stock Detail** — Metric cards + price chart + quarterly financial time series for one ticker.
- **Compare** — 2–6 tickers side by side with chart overlays.
- **Watchlist** — Folders + notes + saved Screen Runs (no price alerts in M0, by design — see [ADR-0011](docs/adr/adr-0011-watchlist-scope.md)).

**Out of scope (deliberately)**:

- Real-time intraday quotes (free-data limit).
- Foreign markets — Korea-only by design (Pillar 6).
- Algorithmic order routing — Speculum is an inspection tool, not a brokerage front-end.

---

## Tech Stack

- **Frontend**: Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query/Table + Lightweight Charts + NextAuth (Google OAuth)
- **Backend**: Python 3.12 + FastAPI + SQLAlchemy 2 + PostgreSQL 16 + APScheduler
- **Data**: FinanceDataReader + pykrx + DART OpenAPI (primary), Bank of Korea ECOS (M1+)
- **Deploy**: Vercel (frontend) + Fly.io/Railway (backend)

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data-flow diagram.

---

## Running with Real Data — API Keys and How to Get Them

To run against **real market data**, the
daily batches (`server/batch/`) ingest from primary sources. Required keys differ
by source and are **all free**, but you must sign up and request an auth key
yourself. Price / market-cap / volume series work **without any key** (pykrx /
FinanceDataReader); financial, macro, and dividend factors stay N/A until the keys
below are present.

| Source | Env var | Data it fills | Where to get it | Notes |
|--------|---------|---------------|-----------------|-------|
| **KRX** (pykrx / FDR) | _(none)_ | Price, market cap, volume, moving averages, RSI, 52-week high/low | — | Works with just the library installed |
| **DART** (Financial Supervisory Service) | `DART_API_KEY` | Quarterly financial statements → PER, PBR, ROE, EPS, debt ratio, etc. + ticker↔corp_code mapping | <https://opendart.fss.or.kr> → request auth key | **Required for financial factors.** 10,000 calls/day limit |
| **ECOS** (Bank of Korea) | `ECOS_API_KEY` | FX (USD/KRW), base rate, treasury yields, macro series | <https://ecos.bok.or.kr> → OpenAPI auth key | |
| **KOSIS** (Statistics Korea) | `KOSIS_API_KEY` | Employment rate, unemployment, industrial production — Statistics-Korea-only macro | <https://kosis.kr/openapi> → apply for use | |
| **FSC** (Financial Services Commission, data.go.kr) | `FSC_API_KEY` _(or `DATA_GO_KR_SERVICE_KEY`)_ | Dividends → dividend yield, Total Return (dividend reinvestment) | <https://www.data.go.kr> → apply for use | Either env name is recognized. The dividend batch resolves ticker→crno via DART `company.json` first, so **a DART key is also required** |

### 1) Configure keys

Create `server/.env` or inject the variables into your shell:

```bash
# server/.env (example — replace with your issued keys)
DART_API_KEY=your_DART_auth_key
ECOS_API_KEY=your_ECOS_auth_key
KOSIS_API_KEY=your_KOSIS_auth_key
FSC_API_KEY=your_data.go.kr_service_key
SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db
```

### 2) Run the batches — unified scheduler

`batch.scheduler` is the unified entry point (meant for cron). Ingest order is
corp-code → krx → ecos → kosis → dart → dividend → snapshot (raw first, then derived
precompute). Dividend runs after dart — the ticker→crno mapping is built on top of the
corp_code mapping, and dividends feed the Total Return factor.

```bash
cd server

# Full universe (all listed companies via corp_code — krx needs no key, financials/macro do;
# tens of minutes to hours due to rate limits)
python -m batch.scheduler --job all

# Price / market cap only — ingest with no key at all (KRX = pykrx/FDR). Full universe takes hours.
python -m batch.scheduler --job krx --observed-date 2026-06-12

# Fast smoke — single market + a few tickers (seconds). End-to-end check without keys or full universe.
python -m batch.scheduler --job krx --market KOSPI --codes 005930 000660 --observed-date 2026-06-12

# A single source — e.g. macro only
python -m batch.scheduler --job ecos --observed-date 2026-06-12
python -m batch.scheduler --job kosis --observed-date 2026-06-12

# DART financials — specific tickers / quarter (fast partial ingest)
python -m batch.scheduler --job dart --codes 005930 000660 035420 --fiscal-year 2024 --fiscal-quarter 1

# Dividends (FSC) — resolves ticker→crno (DART company.json) first, then ingests cash
# dividends. Lookback window is the 5 years before observed-date. Needs FSC + DART keys.
python -m batch.scheduler --job dividend --codes 005930 000660 --observed-date 2026-06-12

# Force-refresh the corpCode.xml cache
python -m batch.scheduler --job corp-code --force-refresh-corp-code

# --dry-run — validate fetch + mapping + conversion WITHOUT any DB write (pre-load sanity
# check). Confirms keys, corp_code/crno mapping, adapter reachability, conversion errors.
python -m batch.scheduler --job all --dry-run
```

`--observed-date` defaults to today; `--fiscal-year`/`--fiscal-quarter` default to
the most recent quarter past its filing deadline. `--codes` is shared by KRX, DART, and
dividend — when omitted, KRX ingests the full market universe and DART/dividend cover all
companies in corp_code. `--market` (KOSPI|KOSDAQ) is KRX-only and ingests both
markets when omitted.

**`--dry-run`** is for pre-load validation: dart/dividend/krx run fetch+mapping+conversion
with no DB write, while ecos/kosis/snapshot are skipped (dry_run unsupported) to avoid DB
writes (logged as WARNING). The corp-code/crno mapping bootstrap is read-only (disk cache;
on a cache miss it fetches from DART — crno can take tens of minutes via per-ticker
`company.json` calls), so it still runs under dry-run to validate mapping resolution.

### 3) Start backend / frontend

Set `as_of` to an ingested trading day.

```bash
SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db uvicorn app.main:app --reload --port 8000
# In another terminal, from client/
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm dev
```

> **Point-in-Time note**: `as_of` must be a trading day for which data exists.
> Financials are keyed by disclosure effective date (`effective_date`), so right
> after ingest the latest quarter may legitimately be N/A if it has not been
> filed yet.

---

## Legal Position (short version)

Speculum, operated free of charge with Google OAuth sign-in, is **not subject to registration as a "similar investment advisory business" (유사투자자문업) under Article 101 of the Korean Capital Markets Act** — the "consideration" element is not met. Should the operating model change (paid subscription, advertising revenue, push alerts on prices), [ADR-0006](docs/adr/adr-0006-legal-review.md) must be re-issued and qualified legal counsel consulted before launch.

For non-Korean users: Speculum data is sourced from Korean Financial Supervisory Service (DART) and the Korea Exchange (KRX). Personal data (Google profile, email) is stored on overseas hosting (Vercel / Fly.io) — explicit consent is collected at sign-in.

---

## License

**MIT** ([LICENSE](LICENSE)) — applies to the source code.

**Data licensing** — the market data exposed through Speculum (KRX listings,
DART filings, ECOS macro series) remains subject to the licenses of the
original providers. Speculum does not redistribute the underlying data set.
Per-source posture is documented in
[ADR-0006 §D5](docs/adr/adr-0006-legal-review.md) and rendered in the
application footer.

---

## Project Family

| Project | Domain | One-line |
|---|---|---|
| [Tessera](../tessera/) | Medical imaging (DICOM PACS) | Standards-first PACS — DIMSE + DICOMweb + HL7/FHIR + AI inference |
| [Norma](../norma/) | Orthodontics (Cephalometrics) | Open analysis-builder for cephalometric measurements |
| **Speculum** | Korean equity markets | The speculum of Korean equities — inspect, don't speculate |

The three projects share the Latin-etymology naming, the standards-first discipline, the immutable Run / self-identifying report pattern, and the milestone-end Momus conformance review.
