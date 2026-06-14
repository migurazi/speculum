# Speculum

> 한국 주식 시장의 **검경(speculum)**. 정량 데이터를 왜곡 없이 비추어 사용자가 시장을 능동적으로 살피게 하는 도구.

[English README](README.en.md) · [컨셉 (10 기둥)](docs/CONCEPT.md) · [로드맵](docs/ROADMAP.md) · [아키텍처](docs/ARCHITECTURE.md) · [ADR](docs/adr/)

**상태**: planning / pre-M0
**자매 프로젝트**: [Tessera](../tessera/) (DICOM 표준 준수), [Norma](../norma/) (Cephalometric 분석법 빌더)

---

## Documents

- [docs/CONCEPT.md](docs/CONCEPT.md) — 10 기둥 + 한 줄 정의 + 차별화 포지셔닝
- [docs/ROADMAP.md](docs/ROADMAP.md) — M0~M3+ 통합 timeline
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 기술 스택 + 데이터 모델 개요
- [docs/M0_PLAN.md](docs/M0_PLAN.md) — M0 v0.1.0 작업 계획서
- `docs/adr/` — Architecture Decision Records (M0 진입 전 작성)

---

## Disclaimer (IMPORTANT)

> **본 도구는 정보 제공 목적의 정량 데이터 탐색기이며, 자본시장법상 투자 자문이 아닙니다.**
> 표시되는 데이터·지표는 1 차 자료(KRX, DART)에서 산출되었으나 정확성을 보증하지 않습니다.
> 모든 투자 판단·손익은 사용자 본인의 책임입니다.

---

## 데모 실행 (dev)

빈 DB 로 실행하면 모든 factor 가 N/A 로 보입니다. dev 데모 seed 스크립트가
샘플 종목/재무/가격/시총/자사주를 심어 실제 PER/PBR/ROE/EPS/시가총액 카드 +
작동하는 Screener 를 볼 수 있게 합니다 (`as_of=2024-06-28`).

```bash
# 1) seed — server/ 에서. 기본 DB = sqlite:///./speculum_dev.db
cd server
python -m scripts.seed_demo
#   (또는 명시 URL: SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db python -m scripts.seed_demo)

# 2) 백엔드 — seed 와 동일 DB URL 로
SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db uvicorn app.main:app --reload --port 8000

# 3) 프론트 — client/ 에서
cd ../client
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm dev
```

브라우저에서 http://localhost:3000 접속 후 `as_of` 를 **2024-06-28** 로 지정합니다.

API 직접 확인:
- `GET http://localhost:8000/api/stocks/005930?as_of=2024-06-28`
- `POST http://localhost:8000/api/screen`
  `{"as_of":"2024-06-28","conditions":[{"factor":"per:ttm-consolidated-ifrs","op":"<","value":"100"}]}`

seed 스크립트는 멱등합니다 (재실행 시 같은 종목 데이터를 정리 후 재삽입). 심는
종목: 005930·000660·035420·005380·051910·035720 (KOSPI). dev 전용이며 운영
데이터는 일배치(`batch/`)가 생산합니다.

---

## 정식 데이터 (운영) — 필요한 API 키와 발급 방법

데모 seed 대신 **실제 시장 데이터**로 운영하려면 일배치(`server/batch/`)가 외부 1차
자료 출처에서 데이터를 적재합니다. 출처별로 필요한 키가 다르며, **모두 무료**지만
발급(회원가입 + 인증키 신청)은 직접 하셔야 합니다. 키 없이도 **가격·시가총액·거래량**
계열은 동작합니다 (pykrx / FinanceDataReader — 키 불필요). 재무·거시·배당 factor 는
아래 키가 있어야 N/A 를 벗어납니다.

| 출처 | 환경변수 | 채우는 데이터 | 발급처 | 비고 |
|------|----------|--------------|--------|------|
| **KRX** (pykrx / FDR) | _(불필요)_ | 가격·시가총액·거래량·이동평균·RSI·52주 신고저 | — | 라이브러리만 설치하면 동작 |
| **DART** (금융감독원 전자공시) | `DART_API_KEY` | 분기 재무제표 → PER·PBR·ROE·EPS·부채비율 등 + 종목↔corp_code 매핑 | <https://opendart.fss.or.kr> → 인증키 신청 | **재무 factor 의 필수 키.** 일 10,000 호출 한도 |
| **ECOS** (한국은행 경제통계) | `ECOS_API_KEY` | 환율(USD/KRW)·기준금리·국고채 등 거시지표 | <https://ecos.bok.or.kr> → OpenAPI 인증키 | |
| **KOSIS** (통계청 국가통계포털) | `KOSIS_API_KEY` | 고용률·실업률·산업생산지수 등 통계청 고유 거시지표 | <https://kosis.kr/openapi> → 활용신청 | |
| **FSC** (금융위원회, data.go.kr) | `FSC_API_KEY` _(또는 `DATA_GO_KR_SERVICE_KEY`)_ | 배당 (Total Return 보정) | <https://www.data.go.kr> → 활용신청 | 둘 중 아무 이름이나 인식 |

### 1) 키 설정

`server/.env` 파일을 만들거나 셸 환경변수로 주입합니다:

```bash
# server/.env (예시 — 발급받은 실제 키로 교체)
DART_API_KEY=발급받은_DART_인증키
ECOS_API_KEY=발급받은_ECOS_인증키
KOSIS_API_KEY=발급받은_KOSIS_인증키
FSC_API_KEY=발급받은_data.go.kr_서비스키
SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db
```

### 2) 일배치 실행 — 통합 scheduler

`batch.scheduler` 가 cron 호출용 통합 진입점입니다. 적재 순서는
corp-code → ecos → kosis → dart → snapshot (raw 적재 후 derived precompute).

```bash
cd server

# 전체 universe 일배치 (corp_code 전체 상장사 — 키 전부 필요, rate limit 으로 수십 분~시간)
python -m batch.scheduler --job all

# 개별 출처만 — 예: 거시지표만
python -m batch.scheduler --job ecos --observed-date 2026-06-12
python -m batch.scheduler --job kosis --observed-date 2026-06-12

# DART 재무 — 대상 종목/분기 지정 (소수 종목 빠른 적재)
python -m batch.scheduler --job dart --codes 005930 000660 035420 --fiscal-year 2024 --fiscal-quarter 1

# corpCode.xml 캐시 강제 갱신
python -m batch.scheduler --job corp-code --force-refresh-corp-code
```

`--observed-date` 미지정 시 오늘, `--fiscal-year`/`--fiscal-quarter` 미지정 시 신고기한
지난 최근 분기를 자동 추정합니다. `--codes` 미지정 시 corp_code 전체 상장사가 대상입니다.

### 3) 백엔드/프론트 기동

데모와 동일하되 `as_of` 를 적재한 거래일(최근일)로 지정합니다.

```bash
SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db uvicorn app.main:app --reload --port 8000
# 다른 터미널, client/ 에서
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm dev
```

> **Point-in-Time 주의**: `as_of` 는 적재된 데이터가 존재하는 거래일이어야 합니다.
> 재무는 공시 효력일(`effective_date`) 기준이므로, 갓 적재한 직후 최신 분기가 아직
> 공시 전이면 해당 factor 는 정상적으로 N/A 입니다.

---

## 10 핵심 기둥

1. **Fidelity** — 거울은 왜곡하지 않는다 (출처·식·시점 항상 동반)
2. **No Advice** — 거울은 추천하지 않는다 (금지 어휘 0)
3. **Active Inspection** — 사용자가 의제를 정한다 (홈 화면에 추천 위젯 X)
4. **Point-in-Time Correctness** — 거울은 멀리 보지 못한다 (look-ahead 방지)
5. **Open Data Sufficiency** — 거울은 무료다 (FDR / pykrx / DART)
6. **KRX-Native** — 한국 시장을 비춘다 (K-IFRS, 결산기, 휴장일 일급)
7. **Observation over Speculation** — Speculate 가 아닌 Spec (정량성)
8. **Conformance to Standards** — KRX / K-IFRS / DART 양식 1차 자료
9. **Temporal Continuity** — 거울은 시간을 끊지 않는다 (corporate action 보정)
10. **Reproducibility** — 거울은 어제의 자신과 같다 (Screen Run snapshot)

---

## License

**MIT** ([LICENSE](LICENSE)) — 코드에 한합니다.

**데이터 라이선스** — Speculum 이 노출하는 시장 데이터 (KRX 상장, DART 공시,
ECOS 거시 시계열 등) 는 1차 자료 제공자의 라이선스를 따릅니다. Speculum 은
원본 데이터셋을 재배포하지 않으며, 출처는 어플리케이션 footer 와
[ADR-0006 §D5](docs/adr/adr-0006-legal-review.md) 에 명시되어 있습니다.
