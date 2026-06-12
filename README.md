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
