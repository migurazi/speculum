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

## Legal Position (short version)

Speculum, operated free of charge with Google OAuth sign-in, is **not subject to registration as a "similar investment advisory business" (유사투자자문업) under Article 101 of the Korean Capital Markets Act** — the "consideration" element is not met. Should the operating model change (paid subscription, advertising revenue, push alerts on prices), [ADR-0006](docs/adr/adr-0006-legal-review.md) must be re-issued and qualified legal counsel consulted before launch.

For non-Korean users: Speculum data is sourced from Korean Financial Supervisory Service (DART) and the Korea Exchange (KRX). Personal data (Google profile, email) is stored on overseas hosting (Vercel / Fly.io) — explicit consent is collected at sign-in.

---

## License

TBD — to be decided before M0 release. Sister projects use MIT (Norma) and Apache 2.0 (Tessera).

---

## Project Family

| Project | Domain | One-line |
|---|---|---|
| [Tessera](../tessera/) | Medical imaging (DICOM PACS) | Standards-first PACS — DIMSE + DICOMweb + HL7/FHIR + AI inference |
| [Norma](../norma/) | Orthodontics (Cephalometrics) | Open analysis-builder for cephalometric measurements |
| **Speculum** | Korean equity markets | The speculum of Korean equities — inspect, don't speculate |

The three projects share the Latin-etymology naming, the standards-first discipline, the immutable Run / self-identifying report pattern, and the milestone-end Momus conformance review.
