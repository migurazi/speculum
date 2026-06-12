"""세금 계산 endpoint — POST /api/tax/securities-transaction (M3 #5, ADR-0030).

증권거래세 계산기 backend. 매도 거래금액 + 시장구분 + 거래일 → 그 시점 효력
세율을 곱한 **산술 사실**(세액·적용세율·효력일·법령출처)을 반환한다.

설계 (stocks/market 선례 일관):
- **인증 불요** — 증권거래세는 공개 산식(ADR-0030 D2). body 만으로 결정적 계산
  (사용자별 개별 상황 입력 없음 — 그 자체가 세무 판단 회피의 핵심).
- Pydantic v2 strict input(market Literal·trade_amount ge=0) → 잘못된 입력은
  framework 가 422. service 레벨 위반(테이블 범위 밖 거래일)은 422 로 변환.

세무사법 경계 — endpoint 가드레일(ADR-0030 D1/D2/D3):
- **양도세 endpoint 미구현**(D1) — 본 모듈에 양도세 route·스텁·TODO 없음.
  증권거래세 단일 endpoint 만.
- 개별 상황 판단 0(D2) — body 에 대주주·보유기간·손익통산 입력 없음. 시장구분
  + 거래금액 + 거래일의 곱셈 사실만.
- disclaimer_required 상수 true(D3) — schema 가 강제, 프론트 디스클레이머 게이트
  트리거(디스클레이머 없이 세금 결과 렌더 차단, ADR-0007 D8.1).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.tax import (
    SecuritiesTransactionTaxIn,
    SecuritiesTransactionTaxOut,
)
from app.services.securities_transaction_tax import (
    SecuritiesTransactionTaxError,
    calculate_securities_transaction_tax,
)

router = APIRouter(prefix="/api/tax", tags=["tax"])


@router.post(
    "/securities-transaction",
    response_model=SecuritiesTransactionTaxOut,
)
async def calculate_securities_transaction(
    body: SecuritiesTransactionTaxIn,
) -> SecuritiesTransactionTaxOut:
    """증권거래세 계산 — 거래금액 × 효력일 매칭 시장구분 세율(ADR-0030 D1/D2).

    매도 거래금액에 그 시점 효력 있던 합산 세율(증권거래세 + 농어촌특별세)을 곱한
    산술 사실을 반환한다. 개별 상황 판단(대주주·보유기간·손익통산) 0(D2), 양도세
    미계산(D1). 응답의 disclaimer_required 는 상수 true(D3).

    422:
        - market 이 4 시장구분(kospi/kosdaq/konex/unlisted) 외 → Pydantic 거부.
        - trade_amount 음수 / 비숫자 → Pydantic 거부.
        - 거래일이 세율 테이블의 가장 이른 효력일보다 과거 →
          SecuritiesTransactionTaxError → 422(효력 세율 근거 없음, 보수적).
    """
    try:
        result = calculate_securities_transaction_tax(
            market=body.market,
            trade_amount=body.trade_amount,
            trade_date=body.trade_date,
        )
    except SecuritiesTransactionTaxError as exc:
        # service invariant 위반(테이블 범위 밖 거래일 등) — 422. 잘못된 입력
        # 이므로 4xx. 메시지는 입력 echo 없는 도메인 사유만.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SecuritiesTransactionTaxOut.from_result(result)
