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
    "BacktestReproduceIn",
    "BacktestReproduceOut",
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
    """freeze artifact wire — self-contained 재현 입력 (ADR-0027 D5 / ADR-0033 D2).

    M5 #3a-0: backtest reproduce(예정)를 위한 self-contained export. result_hash
    식별자뿐 아니라 **재실행에 필요한 전 입력**(pack 식별·universe conditions·
    data_versions)을 운반한다(screen `ScreenRunSnapshotOut` 패턴). client 가 이
    freeze + equity_curve(baseline)를 보관해 reproduce 요청에 그대로 사용한다.

    **불변식(ADR-0033 D3)**: 본 wire 필드 추가는 `result_hash` 입력
    (`BacktestSnapshotBuilder.build` 의 hash_input)과 **무관** — 기존 frozen
    backtest 의 result_hash 가 byte-동일하게 유지된다(frozen 재현 호환).

    Note (strict=False):
        응답 직렬화(BacktestResultOut.freeze)뿐 아니라 **reproduce 요청 입력**
        (BacktestReproduceIn.freeze)으로도 재사용된다. 입력 시 date/Literal/tuple
        이 JSON wire 문자열로 들어오므로 coercion 을 위해 non-strict. `extra=forbid`
        + `frozen=True` 는 유지 — 미지 키 차단 + immutability(self-contained 무결성).
    """

    model_config = ConfigDict(strict=False, extra="forbid", frozen=True)

    result_hash: str
    pack_content_hash: str
    # ADR-0033 D2 — pack 정본 재로드 식별자(reproduce 시 PackRegistry.resolve 입력).
    pack_slug: str
    pack_version: str
    krx_batch_id: str
    dart_batch_id: str
    rebalance: Literal["quarterly", "monthly"]
    start: date
    end: date
    cost_assumptions: CostAssumptionsOut
    # ADR-0033 D2 — self-contained 재현 입력. universe 필터 conditions(canonical
    # 정렬 — snapshot 과 동일 순서) + 전체 data_versions(정책+batch_id). 재실행이
    # 같은 모집단·데이터 시점을 재현하도록 운반(snapshot.conditions/data_versions).
    conditions: tuple[dict[str, str], ...]
    data_versions: dict[str, str]
    # ADR-0033 D3 / M1 — 산식 세대 식별자(BACKTEST_ENGINE_VERSION). reproduce 시
    # 현재 엔진 버전과 비교해 "산식 세대 bump(예: 버그 수정)로 인한 benign 불일치"를
    # 데이터 드리프트/변조와 구분(engine_version_superseded 진단). result_hash 입력엔
    # 이미 포함(snapshot hash_input) — 본 wire 노출은 hash 무영향(D3 불변식).
    # Optional(default None): 본 필드 도입 **이전**에 client 가 보관한 구 freeze 에는
    # 부재하므로 None 으로 파싱(하위호환). None 이면 세대 비교 불가 → 기존 동작.
    engine_version: str | None = None


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
    """equity curve 단일 점 — 날짜별 누적가치 (초기 1.0).

    Note (strict=False): 응답 직렬화뿐 아니라 reproduce 요청의 baseline
    equity_curve(BacktestReproduceIn.equity_curve) 입력으로도 재사용된다. date 가
    JSON wire 문자열로 들어오므로 coercion 을 위해 non-strict. extra=forbid +
    frozen 은 유지 (FreezeOut 와 동일 dual-purpose 사유).
    """

    model_config = ConfigDict(strict=False, extra="forbid", frozen=True)

    date: date
    value: str


class BacktestReproduceIn(BaseModel):
    """POST /api/backtest/reproduce body — frozen freeze + baseline equity_curve.

    screen `ReproduceIn` 의 backtest 등가물 (ADR-0033 D2~D4). self-contained freeze
    (`FreezeOut` — pack 식별·conditions·data_versions·기간·주기·거래비용) + 그와
    함께 보관된 baseline equity_curve 를 받아 frozen batch 기준 재실행 + 동일 검증.

    custom run 은 export 동봉 봉인 pack body(`custom_pack_body`)로 자기완결 재현
    (user/DB 무관 — ADR-0021 D5). 빌트인/reference run 은 None (registry 가 frozen
    slug/version 으로 정본 재로드).

    Note (strict=False):
        freeze 내부의 date / Literal 등이 JSON wire 문자열로 들어오므로 coercion 을
        위해 non-strict. `extra="forbid"` + `frozen=True` 유지 (BacktestRequestIn 동형).
    """

    model_config = ConfigDict(strict=False, extra="forbid", frozen=True)

    freeze: FreezeOut
    equity_curve: tuple[EquityPointOut, ...]
    custom_pack_body: dict | None = None


class BacktestReproduceOut(BaseModel):
    """POST /api/backtest/reproduce 응답 — 재현 동일 여부 + 재실행 equity_curve.

    Attributes:
        matches: 재실행 equity_curve == baseline (byte-동일 재현 성공). False 면
            frozen batch 이후 데이터/정책 변화 또는 재현 불가 (reproduce_note 참조).
        reproduce_note: 재현 결과 안내 — 성공/불일치/재로드 실패 사유.
        equity_curve: frozen batch cutoff 로 재실행한 equity curve (날짜별 누적가치).
    """

    model_config = _STRICT_MODEL_CONFIG

    matches: bool
    reproduce_note: str | None
    equity_curve: tuple[EquityPointOut, ...]
    # pack_tampered: pack 변조(content_hash 불일치)로 재현 불가인 경우만 True.
    # BacktestReproduceResult 와 동형(ADR-0033/M5 #3b). default False(하위호환).
    pack_tampered: bool = False
    # engine_version_superseded: freeze 의 산식 세대(engine_version)가 현재
    # BACKTEST_ENGINE_VERSION 과 다를 때 True(M1). matches 와 **독립** 신호 —
    # 분할 없는 backtest 는 세대가 달라도 equity_curve 가 byte-동일(matches=True)할
    # 수 있다. matches=False 와 함께면 "데이터 변조/드리프트가 아닌 산식 수정으로
    # 인한 benign 불일치"임을 client/운영이 구분(M5 신선도 진단 false-alarm 방지).
    # 구 freeze(engine_version 부재)는 비교 불가 → False(하위호환).
    engine_version_superseded: bool = False


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
