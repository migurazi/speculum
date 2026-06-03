"""Backtest endpoint wire schemas — POST /api/backtest (ADR-0027).

설계 (screen.py 의 ConditionIn / strict 패턴 미러):
- BacktestConditionIn strict — factor canonical_id regex (text injection 차단).
- 응답은 wire snake_case — frontend(Backtest/) 가 그대로 의존.
- disclaimer_required: true 고정 — 과거성과 디스클레이머 게이트 (ADR-0027 D4).
- survivorship_complete / missing_price_ratio — survivorship 디스클로저 (D3).
- 평가 라벨/등급 0 — 사실 통계만 (D6).
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.screen import OpEnum

__all__ = [
    "BacktestConditionIn",
    "BacktestRequestIn",
    "BacktestResultOut",
    "CostAssumptionsOut",
    "EquityPointOut",
    "FreezeOut",
    "RebalanceEnum",
    "StatsOut",
]

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)

# screen.py 와 동일 factor canonical_id 형식 — text injection 차단.
_FACTOR_ID_PATTERN: Final[str] = r"^[a-z][a-z0-9-]*(:[a-z][a-z0-9-]*)+$"
_VALUE_MAX_LENGTH: Final[int] = 200

# pack 식별자 형식 — screen.py CustomScreenRunQueryIn 와 일관.
_PACK_SLUG_PATTERN: Final[str] = r"^[a-z][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?$"
_PACK_VERSION_PATTERN: Final[str] = r"^[0-9]+\.[0-9]+\.[0-9]+$"

# 거래비용 bps 상한 — 비현실적 입력 차단 (1000 bps = 10%). ADR-0027 D2 는 0 으로
# 숨길 수 없으나 0 입력 자체는 허용(명시 노출되므로). 음수 거부.
_MAX_COST_BPS: Final[float] = 1000.0


class RebalanceEnum(str, Enum):
    """rebalance 주기 — 분기말 / 월말 (ADR-0027 D1)."""

    QUARTERLY = "quarterly"
    MONTHLY = "monthly"


class BacktestConditionIn(BaseModel):
    """백테스트 universe 필터 조건 — screen.py ConditionIn 동형.

    Attributes:
        factor: factor canonical_id (regex 강제).
        op: OpEnum 연산자.
        value: 비교값 (str — 호출자 Decimal 변환).
    """

    model_config = ConfigDict(strict=False, extra="forbid", frozen=True)

    factor: str = Field(
        pattern=_FACTOR_ID_PATTERN, min_length=3, max_length=200,
        description="Factor canonical_id (예: 'per:ttm-consolidated-ifrs').",
    )
    op: OpEnum
    value: str = Field(min_length=1, max_length=_VALUE_MAX_LENGTH)


class BacktestRequestIn(BaseModel):
    """POST /api/backtest body — pack 식별자 + 기간 + 주기 + (옵션) 거래비용.

    pack 은 registry.resolve(slug, version, user_id=current_user, ...) 로 해소 —
    빌트인/reference/custom 모두. start <= end 강제.

    Attributes:
        pack_slug / pack_version: pack 식별자.
        conditions: universe 필터 조건 (1~32 개).
        start / end: 백테스트 기간.
        rebalance: "quarterly" | "monthly".
        cost_commission_bps / cost_tax_bps: 거래비용 override (옵션). None 이면
            엔진 default (DEFAULT_COMMISSION_BPS / DEFAULT_TAX_BPS). ADR-0027 D2 —
            0 으로 숨길 수 없으나 결과에 명시 노출되므로 0 입력 허용.

    Note (strict=False):
        date / RebalanceEnum 은 JSON wire 문자열("2022-01-01" / "quarterly") 로
        들어오므로 coercion 을 위해 non-strict. `extra="forbid"` + `frozen=True`
        는 유지 — 미지 키 차단 + immutability (ConditionIn 패턴 동형).
    """

    model_config = ConfigDict(strict=False, extra="forbid", frozen=True)

    pack_slug: str = Field(
        pattern=_PACK_SLUG_PATTERN, min_length=1, max_length=128,
    )
    pack_version: str = Field(
        pattern=_PACK_VERSION_PATTERN, min_length=5, max_length=32,
    )
    conditions: list[BacktestConditionIn] = Field(min_length=1, max_length=32)
    start: date
    end: date
    rebalance: RebalanceEnum
    cost_commission_bps: float | None = Field(
        default=None, ge=0.0, le=_MAX_COST_BPS,
    )
    cost_tax_bps: float | None = Field(default=None, ge=0.0, le=_MAX_COST_BPS)

    @field_validator("end")
    @classmethod
    def _validate_period(cls, v: date, info) -> date:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError("end 가 start 보다 빠릅니다 (start <= end 강제).")
        return v


# =============================================================================
# 응답 — wire snake_case (frontend 의존)
# =============================================================================

class CostAssumptionsOut(BaseModel):
    """적용된 거래비용 가정 — ADR-0027 D2 강제 노출."""

    model_config = _STRICT_MODEL_CONFIG

    tax_bps: str
    commission_bps: str


class FreezeOut(BaseModel):
    """freeze artifact wire — 재현 식별자 (ADR-0027 D5)."""

    model_config = _STRICT_MODEL_CONFIG

    result_hash: str
    pack_content_hash: str
    krx_batch_id: str
    dart_batch_id: str
    rebalance: Literal["quarterly", "monthly"]
    start: date
    end: date
    cost_assumptions: CostAssumptionsOut


class StatsOut(BaseModel):
    """사실 통계 — 평가 라벨/등급 0 (ADR-0027 D6).

    Decimal 은 str 로 직렬화 (정밀도 보존 — float drift 회피). None 가능 통계는
    null (외삽 금지 — 계산 불가 시 사실대로 null).
    """

    model_config = _STRICT_MODEL_CONFIG

    cagr: str | None
    cumulative_return: str
    mdd: str
    volatility: str | None
    turnover: str


class EquityPointOut(BaseModel):
    """equity curve 단일 점 — 날짜별 누적가치 (초기 1.0)."""

    model_config = _STRICT_MODEL_CONFIG

    date: date
    value: str


class BacktestResultOut(BaseModel):
    """POST /api/backtest 응답 — 사실 통계 + survivorship 디스클로저 + 디스클레이머 게이트.

    Attributes:
        freeze: 재현 식별자 (result_hash / pack_content_hash / batch_id / 기간 /
            주기 / 거래비용 가정).
        stats: 사실 통계 (cagr / cumulative_return / mdd / volatility / turnover).
        equity_curve: 날짜별 누적가치.
        survivorship_complete: 가격 누락 0 이면 True. False 면 UI 가 survivorship
            bias 경고 강제 (ADR-0027 D3).
        missing_price_ratio: survivorship 측정 — 가격 누락 비율 (0~1).
        disclaimer_required: 항상 true — 과거성과 디스클레이머 게이트 (ADR-0027
            D4). disclaimer 없는 백테스트 렌더 차단 (ADR-0007 D8.1).
    """

    model_config = _STRICT_MODEL_CONFIG

    freeze: FreezeOut
    stats: StatsOut
    equity_curve: tuple[EquityPointOut, ...]
    survivorship_complete: bool
    missing_price_ratio: str
    disclaimer_required: Literal[True] = True
