"""세금 계산 wire schemas — M3 #5 증권거래세 (ADR-0030).

설계 (portfolio / market 선례 일관):
- 입력: Pydantic v2 strict=False(JSON string → Decimal/date coercion) +
  extra=forbid + frozen. market 은 Literal — 4 시장구분만 허용(그 외 422).
- 출력: 금액·세율은 **문자열**(Decimal 직렬화 — float 잔차/정밀도 손실 차단,
  stocks.py / portfolio.py 의 str 패턴 일관).
- 인증 불요(공개 산식 — ADR-0030 D2). body 만으로 결정적 계산.

세무사법 경계 — wire 계약 가드레일(ADR-0030 D1/D2/D3):
    응답은 **증권거래세 산술 사실만**(세액·적용세율·효력일·법령출처). 양도세
    필드·개별 상황(대주주·보유기간·손익통산) 필드를 일절 두지 않는다.
    disclaimer_required 는 **상수 true** — 프론트의 디스클레이머 게이트(ADR-0030
    D3 / ADR-0007 D8.1)를 트리거해, 디스클레이머 없이 세금 결과 렌더를 차단한다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.securities_transaction_tax import TaxResult

__all__ = [
    "SecuritiesTransactionTaxIn",
    "SecuritiesTransactionTaxOut",
]

# strict=False — JSON string → Decimal/date coercion 허용(portfolio 패턴 일관).
# extra=forbid + frozen 유지.
_STRICT_MODEL_CONFIG = ConfigDict(strict=False, extra="forbid", frozen=True)


class SecuritiesTransactionTaxIn(BaseModel):
    """POST /api/tax/securities-transaction body — 증권거래세 계산 입력.

    market 은 4 시장구분 Literal — 그 외 값은 Pydantic 이 422 로 거부.
    trade_amount 는 str/number 입력 모두 Decimal coercion + 음수 차단(ge=0).
    trade_date 는 효력일 구간 매칭 기준(과거 거래일 = 그 시점 세율, ADR-0030 D4).
    """

    model_config = _STRICT_MODEL_CONFIG

    market: Literal["kospi", "kosdaq", "konex", "unlisted"]
    # 매도 거래금액(원). str/number 입력 coercion(Decimal). 음수 차단(ge=0).
    trade_amount: Decimal = Field(ge=0)
    trade_date: date


class SecuritiesTransactionTaxOut(BaseModel):
    """증권거래세 계산 응답 — 산술 사실만(ADR-0030 D2/D3).

    세액·적용세율은 문자열(Decimal 정밀 노출). effective_date·legal_source 는
    효력일 freeze + 법령 출처(ADR-0030 D4) — 표시층의 출처 표기에 사용.

    disclaimer_required: **상수 true**(ADR-0030 D3) — 프론트 디스클레이머 게이트
        트리거. 디스클레이머 없이 세금 결과 렌더 차단(ADR-0007 D8.1 패턴).
    """

    model_config = _STRICT_MODEL_CONFIG

    tax_amount: str  # Decimal → str (JSON number drift 회피)
    applied_rate: str  # Decimal → str
    effective_date: date
    legal_source: str
    # 상수 true — 프론트 게이트 트리거(ADR-0030 D3). 항상 디스클레이머 의무.
    disclaimer_required: Literal[True] = True

    @classmethod
    def from_result(cls, result: TaxResult) -> SecuritiesTransactionTaxOut:
        """service TaxResult → wire output. 세액·세율을 str 로 직렬화."""
        return cls(
            tax_amount=str(result.tax_amount),
            applied_rate=str(result.applied_rate),
            effective_date=result.effective_date,
            legal_source=result.legal_source,
        )
