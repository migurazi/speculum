"""Corporate Action 가격 보정 엔진 — ADR-0001 D1~D5 + ADR-0009 D2 implementation.

본 모듈은 한국 주식 raw OHLCV 시계열을 corporate action (액면분할·무상증자·유상증자
등) 의 발효 정보로 보정하여 매끄러운 adjusted 시리즈를 생성한다. Speculum 의
§2.9 Temporal Continuity 의 backbone.

설계 원칙:

1. **단일 모듈 + dataclass 정책** — 보정 매트릭스를 `_POLICY_MATRIX` 로 데이터화.
   정책 변경 시 매트릭스만 교체, 알고리즘 코드 불변.
2. **PIT 위임** — corporate action 의 정정공시 chain 해소는 `PITEnforcer.
   filter_active_records(date_of=lambda r: r.announced_date)` 에 위임. 본 모듈은
   active 결과를 받아 effective_date 기준 보정 누적.
3. **Decimal 결정성** — 가격은 항상 `Decimal`. `localcontext(prec=28,
   ROUND_HALF_EVEN)` 으로 multi-thread 환경에서도 결정적 결과.
4. **Content hash freeze** — `(adj_policy, ca_policy, matrix, rounding, prec)` 를
   JCS canonicalize + SHA-256 → `POLICY_CONTENT_HASH`. Screen Run snapshot 의
   `data_versions.price_adjustment_policy_hash` 입력. factor_pack / krx_calendar
   와 동일 의미론 (현재는 inline JCS 복제, 별도 util 모듈 추출은 후속 사이클).
5. **Detect-only sentinel** — merger / spin_off 는 M0 에서 보정 미적용 (ADR-0009
   D7). 다만 raw 시계열의 그날 가격 점프를 `AdjustedPriceSeries.unadjusted_jumps`
   로 노출 — UI 가 ⚠ 마커 표시 가능 (oracle 자문 C2).
6. **Identity adjustment 명시** — as_of 범위에 보정 사건이 0 개면 `adjusted == raw`.
   `AdjustedPriceSeries.is_identity_adjustment` property 로 호출자가 detect 가능
   (oracle 자문 C3).

관련 ADR / 문서:
- ADR-0001 D1 (rights-only 보정, cash_dividend 제외), D2 (이론가), D4 (raw +
  adjusted 동시 보존), D5 (정책 버저닝), D6 (UI 토글)
- ADR-0009 D2 (보정 매트릭스 13 action_type), D3 (effective_date 정의), D5
  (정정공시 chain), D7 (합병/분할 — M0 detect-only), D8 (정책 버저닝), D9
  (누락 사건 alert — 본 모듈 out-of-scope, T18 일배치 책임)
- M0_PLAN T20 / AC-P-09 (Temporal Continuity 의 raw/adjusted 토글)

본 모듈의 out-of-scope:
- Volume 보정 — ADR-0001 D4 schema 가 `volume` 단일 컬럼. 후속 work-order 에서
  raw/adjusted 분리 + minor revision (oracle 자문 C1 의 ADR-0001 revision 권고).
- 누락 사건 detection — `data_quality_alerts` 는 T18 일배치 책임.
- Cross-runtime hash 검증 — factor_pack / krx_calendar 와 함께 별도 JCS util
  모듈 추출 시점.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from types import MappingProxyType
from typing import Any, Final, Literal

from app.repositories.pit_protocols import CorporateActionRecord, PriceRecord
from app.services._jcs import canonicalize_jcs as _canonicalize_jcs_shared
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "ADJUSTMENT_POLICY_VERSION",
    "CORPORATE_ACTION_POLICY_VERSION",
    "POLICY_CONTENT_HASH",
    "POLICY_LABEL",
    "AdjusterDataError",
    "AdjusterError",
    "AdjustedPriceRecord",
    "AdjustedPriceSeries",
    "AdjustmentEvent",
    "InconsistentTheoreticalPriceError",
    "PriceAdjuster",
]

# =============================================================================
# Policy versioning — content hash freeze (ADR-0001 D5 / ADR-0009 D8)
# =============================================================================

ADJUSTMENT_POLICY_VERSION: Final[str] = "1.0"
CORPORATE_ACTION_POLICY_VERSION: Final[str] = "1.0"

# 사용자에게 표시되는 정책 라벨 (ADR-0001 D6 — UI tooltip).
POLICY_LABEL: Final[str] = (
    f"v{ADJUSTMENT_POLICY_VERSION}:rights-only,theoretical"
)

# Decimal precision / rounding 정책 — 누적 곱 결정성의 기반.
_DECIMAL_PRECISION: Final[int] = 28
_DECIMAL_ROUNDING: Final[str] = "ROUND_HALF_EVEN"

# 이론가격 details vs recompute 의 허용 오차 — 0.5% (oracle 자문 결정 3).
_THEORETICAL_PRICE_TOLERANCE: Final[Decimal] = Decimal("0.005")


# =============================================================================
# Errors
# =============================================================================

class AdjusterError(Exception):
    """본 모듈의 모든 예외 base."""


class AdjusterDataError(AdjusterError):
    """입력 데이터 (action / price record) 의 invariant 위반.

    - 알 수 없는 action_type
    - 필수 details 필드 누락 (e.g., split 의 split_ratio 부재)
    - 가격 시계열의 잘못된 정렬 / 중복 일자
    """


class InconsistentTheoreticalPriceError(AdjusterError):
    """ADR-0001 D2 의 이론가격 — details 값과 recompute 결과의 차이가 tolerance 초과.

    KRX 공식 값 (details["theoretical_ex_price"]) 과 본 모듈의 산식 결과가 0.5%
    초과 차이. ADR-0009 D9 의 `data_quality_alerts` 통합 시점에서 alert 대상.

    Attributes:
        action_id: 문제 corporate_action 의 id.
        declared: details 의 값.
        recomputed: 본 모듈이 산식으로 계산한 값.
    """

    def __init__(
        self, action_id: Any, declared: Decimal, recomputed: Decimal
    ) -> None:
        diff_pct = (
            abs(declared - recomputed) / max(declared, recomputed) * 100
            if max(declared, recomputed) > 0 else Decimal("0")
        )
        super().__init__(
            f"theoretical_ex_price mismatch for action {action_id}: "
            f"declared={declared} recomputed={recomputed} "
            f"diff={diff_pct:.4f}% > tolerance {_THEORETICAL_PRICE_TOLERANCE * 100}%"
        )
        self.action_id: Final[Any] = action_id
        self.declared: Final[Decimal] = declared
        self.recomputed: Final[Decimal] = recomputed


# =============================================================================
# Internal — action_type 별 정책 매트릭스 (ADR-0009 D2)
# =============================================================================

# 분류 — UI / 로깅 / freeze hash 입력에 의미 있는 카테고리.
# 정책 base classification — `*-announced` suffix 는 본 base 에 매핑으로 derive.
# - "price-adjust": OHLC 보정 factor 적용 (split / bonus / stock_dividend /
#   rights_issue / reverse_split)
# - "ignore": 보정 대상 X (단일 주가에 영향 없음 — cash_dividend / treasury_*)
# - "detect-only-merger": M0 detect 만 — raw 시계열에 점프 남김 (D7)
# - "detect-only-spin-off": M1 본격 — M0 detect 만
BaseClassification = Literal[
    "price-adjust",
    "ignore",
    "detect-only-merger",
    "detect-only-spin-off",
]

_FactorFn = Callable[[CorporateActionRecord], Decimal]


@dataclass(frozen=True, slots=True)
class _ActionPolicy:
    """단일 action_type 의 보정 정책 — 데이터 표현.

    `classification` 은 Literal 강제 (oracle 2 차 NEW-C1) — 새 action_type 도입
    시 BaseClassification 에 먼저 등록해야 매트릭스 컴파일.
    """

    action_type: str
    classification: BaseClassification
    # 곱셈비 계산 함수. classification != "price-adjust" 이면 None.
    factor_fn: _FactorFn | None
    # M0 에서 보정 적용 가능 여부. False 인 detect-only 류는 unadjusted_jumps 에 추가.
    m0_supported: bool


# Base classification 의 announced-only 변환 — type-safe mapping (oracle 2 차 NEW-C1).
# `f"{base}-announced"` 같은 string concat 의 type bypass 차단.
_ANNOUNCED_BY_BASE: Final[Mapping[str, EventClassification]] = MappingProxyType({
    "detect-only-merger": "detect-only-merger-announced",
    "detect-only-spin-off": "detect-only-spin-off-announced",
})


# ---------------------------------------------------------------------
# Factor 산출 함수들 — ADR-0001 D1~D2 + ADR-0009 D2
# ---------------------------------------------------------------------

def _coalesce(*values: Any) -> Any:
    """첫 번째 not-None 반환. `or` 의 falsy (0 / "" / False) 회피.

    `dict.get(k) or action.ratio` 패턴은 0 입력을 None 으로 오인 → 본 helper 가
    `is None` 검사로 명시.
    """
    for v in values:
        if v is not None:
            return v
    return None


def _split_factor(action: CorporateActionRecord) -> Decimal:
    """액면분할 — 과거 가격 / split_ratio.

    details = {"split_ratio": 50} → factor = 1/50 = 0.02.
    `action.ratio` 도 사용 가능 (ADR-0009 D1 schema 의 일반 필드) — 우선순위:
    details["split_ratio"] > action.ratio.
    """
    ratio = _require_decimal(
        _coalesce(action.details.get("split_ratio"), action.ratio),
        action_id=action.id, field_name="split_ratio",
    )
    if ratio <= 0:
        raise AdjusterDataError(
            f"split_ratio must be positive (action {action.id}): got {ratio}"
        )
    return Decimal(1) / ratio


def _reverse_split_factor(
    action: CorporateActionRecord,
) -> Decimal:
    """액면병합 — 과거 가격 × merge_ratio. details 의 `merge_ratio` 또는 ratio.

    병합비 = 옛 N 주 → 새 1 주 (예: 10:1 병합 → merge_ratio=10).
    """
    ratio = _require_decimal(
        _coalesce(action.details.get("merge_ratio"), action.ratio),
        action_id=action.id, field_name="merge_ratio",
    )
    if ratio <= 0:
        raise AdjusterDataError(
            f"merge_ratio must be positive (action {action.id}): got {ratio}"
        )
    return ratio  # 곱셈비 — 과거 가격 × ratio


def _bonus_or_stock_dividend_factor(
    action: CorporateActionRecord,
) -> Decimal:
    """무상증자 / 주식배당 — 과거 가격 / (1 + 비율).

    ADR-0001 D1: "한국에선 '주식배당' 과 '무상증자' 가 회계 처리는 다르지만 가격
    보정 효과는 동일. 동일 보정 logic 적용."

    details = {"bonus_ratio": 0.2} → factor = 1/(1+0.2) = 0.8333...
    """
    bonus_ratio = _require_decimal(
        _coalesce(
            action.details.get("bonus_ratio"),
            action.details.get("dividend_ratio"),
            action.ratio,
        ),
        action_id=action.id, field_name="bonus_ratio/dividend_ratio",
    )
    if bonus_ratio < 0:
        raise AdjusterDataError(
            f"bonus/dividend ratio must be >= 0 (action {action.id}): got {bonus_ratio}"
        )
    return Decimal(1) / (Decimal(1) + bonus_ratio)


def _rights_issue_factor(
    action: CorporateActionRecord,
) -> Decimal:
    """유상증자 — ADR-0001 D2 의 이론가격 기준 보정.

    factor = theoretical_ex_price / previous_close. KRX 공식 이론가
    (details["theoretical_ex_price"]) 를 1차 source 로 사용. 산식으로 recompute
    하여 0.5% 초과 차이 시 InconsistentTheoreticalPriceError — ADR-0009 D9 alert
    과 통합 (oracle 자문 결정 3).

    Invariant — `subscription_ratio` 의 의미 (oracle 2 차 리뷰 C3):
        `subscription_ratio := shares_new / shares_old` (신주발행수 / 기존주식수).
        예: 기존 100 주당 신주 20 주 → 0.2. KRX 공시의 "신주배정비율" 과 일치.
        "기존 1 주당 신주 X 주" 같은 다른 정의로 해석하면 silent 오보정 발생 —
        호출자 (DART/KRX adapter) 가 정상화 책임.

    산식 — ADR-0001 D2 의 일반형:
        P_theoretical = (P_prev × shares_old + P_sub × shares_new) /
                        (shares_old + shares_new)

        양변을 shares_old 로 나누면 subscription_ratio = shares_new/shares_old 로:
        P_theoretical = (P_prev × 1 + P_sub × ratio) / (1 + ratio)

        본 모듈의 recompute 가 이 단순화 형태 사용.
    """
    declared_theoretical = _require_decimal(
        action.details.get("theoretical_ex_price"),
        action_id=action.id, field_name="theoretical_ex_price",
    )
    previous_close = _require_decimal(
        action.details.get("previous_close"),
        action_id=action.id, field_name="previous_close",
    )

    # Recompute cross-check
    subscription_ratio = action.details.get("subscription_ratio")
    subscription_price = action.details.get("subscription_price")
    if subscription_ratio is not None and subscription_price is not None:
        sub_ratio = _as_decimal(subscription_ratio)
        sub_price = _as_decimal(subscription_price)
        # P_theoretical = (P_prev × 1 + P_sub × ratio) / (1 + ratio)
        # (shares_old / shares_old + shares_new = 1 / (1 + ratio))
        recomputed = (
            (previous_close + sub_price * sub_ratio) / (Decimal(1) + sub_ratio)
        )
        denom = max(declared_theoretical, recomputed)
        if denom > 0:
            diff_pct = abs(declared_theoretical - recomputed) / denom
            if diff_pct > _THEORETICAL_PRICE_TOLERANCE:
                raise InconsistentTheoreticalPriceError(
                    action.id, declared_theoretical, recomputed,
                )

    if previous_close <= 0:
        raise AdjusterDataError(
            f"previous_close must be positive (action {action.id}): got {previous_close}"
        )

    return declared_theoretical / previous_close


# ---------------------------------------------------------------------
# Policy matrix — single source of truth
# ---------------------------------------------------------------------

_POLICY_MATRIX: Final[Mapping[str, _ActionPolicy]] = {
    "split": _ActionPolicy("split", "price-adjust", _split_factor, m0_supported=True),
    "reverse_split": _ActionPolicy(
        "reverse_split", "price-adjust", _reverse_split_factor, m0_supported=True),
    "bonus_issue": _ActionPolicy(
        "bonus_issue", "price-adjust", _bonus_or_stock_dividend_factor,
        m0_supported=True),
    "stock_dividend": _ActionPolicy(
        "stock_dividend", "price-adjust", _bonus_or_stock_dividend_factor,
        m0_supported=True),
    "rights_issue": _ActionPolicy(
        "rights_issue", "price-adjust", _rights_issue_factor, m0_supported=True),
    "cash_dividend": _ActionPolicy(
        "cash_dividend", "ignore", None, m0_supported=True),
    "treasury_purchase": _ActionPolicy(
        "treasury_purchase", "ignore", None, m0_supported=True),
    "treasury_cancellation": _ActionPolicy(
        "treasury_cancellation", "ignore", None, m0_supported=True),
    # M0 detect-only — ADR-0009 D7. raw 시계열에 점프 visible.
    "merger": _ActionPolicy("merger", "detect-only-merger", None, m0_supported=False),
    "merger_absorption": _ActionPolicy(
        "merger_absorption", "detect-only-merger", None, m0_supported=False),
    "spin_off_personal": _ActionPolicy(
        "spin_off_personal", "detect-only-spin-off", None, m0_supported=False),
    "spin_off_business": _ActionPolicy(
        "spin_off_business", "detect-only-spin-off", None, m0_supported=False),
    # 보정 미대상이나 metadata 변경 사건
    "par_value_change": _ActionPolicy(
        "par_value_change", "ignore", None, m0_supported=True),
    "code_change": _ActionPolicy(
        "code_change", "ignore", None, m0_supported=True),
}


# =============================================================================
# Helper — Decimal 변환 + 필수 필드 검사
# =============================================================================

def _as_decimal(value: Any) -> Decimal:
    """임의 numeric (int/float/str/Decimal) 을 Decimal 변환.

    float 입력 시 str() 후 Decimal 화 — 직접 Decimal(float) 의 IEEE 754 잔차 회피.

    Invalid 입력 (예: `"abc"`, NaN string, control character) 은
    `decimal.InvalidOperation` 을 도메인 예외 `AdjusterDataError` 로 wrap (oracle
    2 차 리뷰 C4). `from exc` 패턴 — root cause traceback 보존.
    """
    if isinstance(value, Decimal):
        return value
    try:
        if isinstance(value, (int, str)):
            return Decimal(value)
        if isinstance(value, float):
            return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AdjusterDataError(
            f"invalid numeric value {value!r} ({type(value).__name__}): {exc}"
        ) from exc
    raise AdjusterDataError(
        f"cannot convert {value!r} ({type(value).__name__}) to Decimal"
    )


def _require_decimal(value: Any, *, action_id: Any, field_name: str) -> Decimal:
    """필수 필드 — None 이면 AdjusterDataError."""
    if value is None:
        raise AdjusterDataError(
            f"required field '{field_name}' missing in action {action_id}"
        )
    return _as_decimal(value)


# =============================================================================
# Policy content hash — JCS + SHA-256 (factor_pack / krx_calendar 패턴)
# =============================================================================

def _canonicalize_jcs(value: Any) -> bytes:
    """`_jcs.canonicalize_jcs` 의 backwards-compat alias (T30 의 공유 정리)."""
    return _canonicalize_jcs_shared(value)


def _build_policy_body() -> dict:
    """정책 hash 입력 dict — 알고리즘 의미를 결정하는 모든 정보 포함."""
    return {
        "adj_policy_version": ADJUSTMENT_POLICY_VERSION,
        "ca_policy_version": CORPORATE_ACTION_POLICY_VERSION,
        "rounding": _DECIMAL_ROUNDING,
        "decimal_precision": _DECIMAL_PRECISION,
        "theoretical_price_tolerance": str(_THEORETICAL_PRICE_TOLERANCE),
        "matrix": [
            {
                "action_type": p.action_type,
                "classification": p.classification,
                "m0_supported": p.m0_supported,
            }
            for p in sorted(_POLICY_MATRIX.values(), key=lambda x: x.action_type)
        ],
    }


_POLICY_BODY: Final[dict] = _build_policy_body()
POLICY_CONTENT_HASH: Final[str] = (
    "sha256:"
    + hashlib.sha256(_canonicalize_jcs(_POLICY_BODY)).hexdigest()
)


# =============================================================================
# Result dataclass
# =============================================================================

@dataclass(frozen=True, slots=True)
class AdjustedPriceRecord:
    """단일 일자의 raw + adjusted OHLC.

    ADR-0001 D4 의 prices_daily schema 에 대응 — raw 4 컬럼 + adjusted 4 컬럼.
    volume 보정은 본 사이클 out-of-scope (ADR-0001 schema 후속 minor revision
    필요). 본 record 는 volume 을 보존하나 raw 그대로.
    """

    date: date
    code: str
    open_raw: Decimal
    high_raw: Decimal
    low_raw: Decimal
    close_raw: Decimal
    volume: int  # raw — 후속 사이클에서 raw / adjusted 분리
    open_adjusted: Decimal
    high_adjusted: Decimal
    low_adjusted: Decimal
    close_adjusted: Decimal


EventClassification = Literal[
    "price-adjust",                   # 보정 factor 적용 + effective_date <= as_of
    "announced-not-effective",        # price-adjust 정책이지만 effective_date > as_of
    "ignore",                         # 보정 미대상 (cash_dividend / treasury_* / metadata)
    "detect-only-merger",             # M0 detect-only + effective_date <= as_of
    "detect-only-merger-announced",   # detect-only + effective_date > as_of
    "detect-only-spin-off",
    "detect-only-spin-off-announced",
]


@dataclass(frozen=True, slots=True)
class AdjustmentEvent:
    """단일 corporate action 의 보정 효과 record.

    `factor == Decimal(1)` 이고 `classification != "price-adjust"` 이면 적용 미수행.
    `classification` 의 `*-announced` suffix 는 announce 됐지만 effective_date > as_of
    인 사건 — UI 가 "예고됨" 표시 가능 (oracle 2 차 리뷰 C5).
    """

    action_id: Any
    action_type: str
    effective_date: date
    factor: Decimal
    classification: EventClassification


@dataclass(frozen=True, slots=True)
class AdjustedPriceSeries:
    """보정된 가격 시계열의 결과 묶음 — Screen Run snapshot freeze 입력 단위.

    Attributes:
        code: 종목코드.
        adjusted: 보정된 OHLC + raw 보존 record tuple (오름차순, immutable).
        events: 적용 검토된 모든 action (price-adjust / ignore / detect-only 모두).
        unadjusted_jumps: detect-only (merger / spin_off) 인 사건의 effective_date
            list — UI 가 raw / adjusted 시리즈에 ⚠ 마커 표시 가능 (oracle 자문 C2).
        adj_policy_label: 사용자 표시용 정책 라벨 (예: "v1.0:rights-only,theoretical").
        policy_content_hash: 본 보정에 사용된 정책의 SHA-256 hash.
        pit_policy_version: 본 보정에 사용된 PIT 정책 version. as_of-time supersede
            chain 의미론을 결정하므로 같은 corporate_action 입력에 다른 PIT 정책을
            적용하면 결과가 달라짐 — 두 정책 모두 freeze 필요 (oracle 2 차 C6).

    Screen Run snapshot 의 `data_versions` freeze 입력 (ADR-0008 D7):
        `{"price_adjustment_policy_hash": series.policy_content_hash,
          "pit_policy_version": series.pit_policy_version, ...}`
    """

    code: str
    adjusted: Sequence[AdjustedPriceRecord]
    events: Sequence[AdjustmentEvent]
    unadjusted_jumps: Sequence[date]
    adj_policy_label: str
    policy_content_hash: str
    pit_policy_version: str

    @property
    def is_identity_adjustment(self) -> bool:
        """as_of 범위 내에 가격 보정 사건이 0 개 — adjusted == raw.

        Note (oracle 2 차 M6):
            detect-only-merger 등 가격 보정 미대상 사건이 있어도 True. UI 가
            "보정 사건 없음" 과 "보정 무관 사건 있음 (merger 등)" 을 구별하려면
            `unadjusted_jumps` 또는 `events` 의 classification 별도 확인.
        """
        return not any(e.classification == "price-adjust" for e in self.events)


# =============================================================================
# PriceAdjuster service
# =============================================================================

class PriceAdjuster:
    """Corporate Action 가격 보정 service.

    State-less but DI 친화 — `pit_enforcer` 를 주입받아 정정공시 chain 해소 위임.

    Attributes:
        pit_enforcer: 정정공시 chain 해소용. None 이면 default `PITEnforcer()`.
        policy_content_hash: 본 인스턴스가 따르는 정책 hash. Screen Run freeze.
        policy_label: UI 표시 라벨.
    """

    def __init__(self, *, pit_enforcer: PITEnforcer | None = None) -> None:
        self.pit_enforcer: Final[PITEnforcer] = pit_enforcer or PITEnforcer()
        self.policy_content_hash: Final[str] = POLICY_CONTENT_HASH
        self.policy_label: Final[str] = POLICY_LABEL

    def adjust(
        self,
        raw_prices: Sequence[PriceRecord],
        corporate_actions: Sequence[CorporateActionRecord],
        *,
        as_of: date,
    ) -> AdjustedPriceSeries:
        """Raw 시계열을 보정.

        Args:
            raw_prices: PIT 통과한 PriceRecord list. 본 모듈은 호출자가 이미
                `[start, as_of]` 범위로 fetch 했음을 전제. 동일 code, 일자 중복
                불가, sort 무관 (내부 정렬).
            corporate_actions: 같은 종목의 모든 CorporateActionRecord. PIT
                Enforcer 가 `announced_date <= as_of` 기준으로 정정공시 chain 해소.
            as_of: **AsOfPolicy 통과 후의 영업일** (ADR-0008 D6). 비영업일이
                들어와도 보정 결과는 정의되나, 호출자는 AsOfPolicy.normalize 후
                전달 권장 (oracle 2 차 M7).

        Returns:
            AdjustedPriceSeries — adjusted + events + unadjusted_jumps + 정책 hash
            + pit_policy_version.

        Raises:
            AdjusterDataError: 입력 record 의 invariant 위반 (다중 code, 중복 일자,
                알 수 없는 action_type, 잘못된 numeric).
            InconsistentTheoreticalPriceError: rights_issue 이론가격 mismatch
                (declared vs recompute 0.5% 초과).
            PITDataCorruptionError: corporate_action 의 supersede chain corruption
                — PITEnforcer 에서 전파.
        """
        if not raw_prices:
            return AdjustedPriceSeries(
                code="",
                adjusted=(),
                events=(),
                unadjusted_jumps=(),
                adj_policy_label=self.policy_label,
                policy_content_hash=self.policy_content_hash,
                pit_policy_version=self.pit_enforcer.pit_policy_version,
            )

        # 가격 시계열 정렬 검증 + code 일관성.
        code = raw_prices[0].code
        if any(p.code != code for p in raw_prices):
            raise AdjusterDataError(
                f"price records contain multiple codes (expected single {code})"
            )
        sorted_prices = sorted(raw_prices, key=lambda p: p.effective_date)
        seen_dates: set[date] = set()
        for p in sorted_prices:
            if p.effective_date in seen_dates:
                raise AdjusterDataError(
                    f"duplicate price record for {code} @ {p.effective_date}"
                )
            seen_dates.add(p.effective_date)

        # 1. PIT 위임 — 정정공시 chain 해소 (announced_date 기준).
        active_actions = self.pit_enforcer.filter_active_records(
            corporate_actions, as_of, date_of=lambda r: r.announced_date,
        )

        # 2. action_type 정책 lookup + factor 사전 계산.
        events: list[AdjustmentEvent] = []
        adjust_actions: list[tuple[CorporateActionRecord, Decimal]] = []
        unadjusted_jumps: list[date] = []

        with localcontext() as ctx:
            ctx.prec = _DECIMAL_PRECISION
            ctx.rounding = ROUND_HALF_EVEN

            for action in active_actions:
                policy = _POLICY_MATRIX.get(action.action_type)
                if policy is None:
                    raise AdjusterDataError(
                        f"unknown action_type '{action.action_type}' for action "
                        f"{action.id}. supported: {sorted(_POLICY_MATRIX.keys())}"
                    )

                if policy.classification == "price-adjust":
                    assert policy.factor_fn is not None  # invariant
                    # effective_date <= as_of 만 보정 적용 (oracle 자문 결정 6).
                    if action.effective_date <= as_of:
                        factor = policy.factor_fn(action)
                        adjust_actions.append((action, factor))
                        events.append(AdjustmentEvent(
                            action_id=action.id,
                            action_type=action.action_type,
                            effective_date=action.effective_date,
                            factor=factor,
                            classification="price-adjust",
                        ))
                    else:
                        # announce 됐지만 발효 전 — 기록만, factor 미적용.
                        events.append(AdjustmentEvent(
                            action_id=action.id,
                            action_type=action.action_type,
                            effective_date=action.effective_date,
                            factor=Decimal(1),
                            classification="announced-not-effective",
                        ))
                elif policy.classification == "ignore":
                    events.append(AdjustmentEvent(
                        action_id=action.id,
                        action_type=action.action_type,
                        effective_date=action.effective_date,
                        factor=Decimal(1),
                        classification="ignore",
                    ))
                else:
                    # detect-only-merger / detect-only-spin-off — 보정 미적용.
                    # effective_date 가 미발효 시 *-announced suffix 로 구별
                    # (oracle 2 차 리뷰 C5 / NEW-C1) — _ANNOUNCED_BY_BASE 매핑으로
                    # type-safe 분류 결정.
                    is_effective = action.effective_date <= as_of
                    if is_effective:
                        event_class: EventClassification = policy.classification
                        unadjusted_jumps.append(action.effective_date)
                    else:
                        event_class = _ANNOUNCED_BY_BASE[policy.classification]
                    events.append(AdjustmentEvent(
                        action_id=action.id,
                        action_type=action.action_type,
                        effective_date=action.effective_date,
                        factor=Decimal(1),
                        classification=event_class,
                    ))

            # 3. effective_date 오름차순 정렬 후 backward 누적 (oracle 결정 5).
            #    각 raw 일자 d 에 대해, action.effective_date > d 인 모든 action 의
            #    factor 곱이 그 일자의 adjusted factor.
            adjust_actions.sort(key=lambda x: x[0].effective_date)
            adjusted_records: list[AdjustedPriceRecord] = []
            i = len(adjust_actions)  # walk pointer (역순)
            accumulated = Decimal(1)
            # 역순 walk — raw 시리즈 끝 (최신) 부터 시작, accumulated 곱 누적.
            for p in reversed(sorted_prices):
                # action.effective_date > p.date 인 action 의 factor 를 모두 추가.
                while i > 0 and adjust_actions[i - 1][0].effective_date > p.effective_date:
                    accumulated *= adjust_actions[i - 1][1]
                    i -= 1
                # adjusted = raw × accumulated.
                adjusted_records.append(_make_adjusted_record(p, accumulated))

            # 역순으로 만들었으므로 다시 정순 정렬.
            adjusted_records.reverse()

        return AdjustedPriceSeries(
            code=code,
            adjusted=tuple(adjusted_records),
            events=tuple(events),
            unadjusted_jumps=tuple(sorted(set(unadjusted_jumps))),
            adj_policy_label=self.policy_label,
            policy_content_hash=self.policy_content_hash,
            pit_policy_version=self.pit_enforcer.pit_policy_version,
        )


def _make_adjusted_record(p: PriceRecord, factor: Decimal) -> AdjustedPriceRecord:
    """단일 PriceRecord 에 factor 적용한 AdjustedPriceRecord 생성."""
    return AdjustedPriceRecord(
        date=p.effective_date,
        code=p.code,
        open_raw=p.open_raw,
        high_raw=p.high_raw,
        low_raw=p.low_raw,
        close_raw=p.close_raw,
        volume=p.volume,
        open_adjusted=p.open_raw * factor,
        high_adjusted=p.high_raw * factor,
        low_adjusted=p.low_raw * factor,
        close_adjusted=p.close_raw * factor,
    )
