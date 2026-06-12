"""세전 Total Return 시계열 계산 엔진 — ADR-0035 D1/D6/D7 implementation.

본 모듈은 `PriceAdjuster` 가 산출한 corporate-action 보정 close 시계열과
이중 PIT 통과 cash_dividend 목록을 입력받아, 배당 재투자를 누적한
**세전 total return index** (TRI) 시계열을 계산한다. 첫 거래일 기준 TRI=1.0
으로 정규화한 일별 chaining index 다.

설계 원칙 (ADR-0035):

1. **별도 모듈 — `_POLICY_MATRIX` 미접촉 (D1)** — 본 엔진은 `price_adjuster.py`
   의 보정 매트릭스 / 알고리즘을 일절 변경하지 않는다. PriceAdjuster 의
   출력(`AdjustedPriceSeries`) 을 소비하기만 한다. 현금배당의 가격 보정 정책
   (cash_dividend = ignore → 미보정) 은 그대로 두고, total return 차원에서만
   배당 재투자를 별도 계산한다. 두 엔진의 정책 hash 도 독립.
2. **배당락일 재투자 (D6)** — 배당 D 의 재투자 시점은 배당락일
   (`effective_date`). 배당락일 t 의 close_adjusted[t] 는 배당락 하락이 반영된
   가격(현금배당은 미보정)이므로, 그날 D 를 받음으로써 배당락 하락이 상쇄된다.
   표준 total-return index (CRSP 류) 의미론.
3. **세전 (D7)** — 배당소득세 / 거래세 등 어떤 세금도 차감하지 않는다.
   per_share 액면 그대로 재투자. 세후 시계열은 별도 정책(미래 cycle).
4. **Decimal 결정성 (§2.10)** — 전체 누적은 `localcontext(prec=28,
   ROUND_HALF_EVEN)` 안에서 수행. price_adjuster 와 **동일 상수** 사용 →
   같은 입력 두 번이면 byte-동일 결과.
5. **Content hash freeze** — 정책 body 를 JCS canonicalize + SHA-256 →
   `POLICY_CONTENT_HASH`. backtest snapshot freeze 의 입력. 입력
   `AdjustedPriceSeries` 의 price/pit 정책 hash 도 함께 반송하여 재현 입력 전체
   를 봉인.
6. **조용한 손실 금지 (§2.1)** — 배당락일이 가격 시계열에 부재하거나, 배당금
   파싱 실패 시 그 배당을 **누락하되 반드시 warning** 으로 노출한다. 추측 매칭
   (다음 거래일 등) 은 금지 — silent 오보정 회피.

계산식 (일별 chaining TRI):
    TRI[0] = 1
    TRI[t] = TRI[t-1] * (close_adj[t] + div_adj[t]) / close_adj[t-1]   (t >= 1)
    div_adj[t] = div_nom(date[t]) × factor[t],  factor[t] = close_adj[t] / close_raw[t]
    div_nom(d) = Σ per_share of dividends whose effective_date == d

배당 단위 정합 (Critical — split × 명목배당 불일치 보정):
    `close_adj[t]` 는 backward split 보정으로 split 이전 날짜에 축소 스케일이
    걸려 있으나 (예 2:1 split 이면 split 이전 1/2), 입력 배당 `div_nom` 은 명목
    원화 (cash_amount / per_share 원본) 다. 배당락일 t 가 split **이전** 이면
    `close_adj[t]` 는 1/2 스케일인데 명목 배당은 full → `(close_adj[t] + div_nom)`
    에서 배당이 split factor 만큼 **과대 반영** 되어 TRI 가 구조적으로 틀어진다.
    그래서 각 배당락일의 배당을 그 날짜의 보정계수 `factor[t] = close_adj[t] /
    close_raw[t]` 로 스케일 (`div_adj[t]`) 하여 close_adj 와 동일 basis 로 맞춘다.
    split 없는 구간은 close_adj == close_raw → factor == 1 → 명목과 동일.

재현성 (defer 주석):
    `dividends` 입력은 batch-cutoff freeze 불가 (ADR-0035 D8) — 재현성은 배당
    미정정 가정 하. 배당 정정 재현성 마커 (#4/#5 snapshot 연결) 는 별도 cycle.

관련 ADR / 문서:
- ADR-0035 D1 (별도 모듈, _POLICY_MATRIX 미접촉), D6 (배당락일 재투자),
  D7 (세전)
- M7 #3 (TotalReturnAdjuster 엔진). backtest 연결 (#4/#5) 은 별도.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Final

from app.repositories.pit_protocols import CorporateActionRecord
from app.services._jcs import canonicalize_jcs as _canonicalize_jcs_shared
from app.services.pit_enforcer import PITEnforcer
from app.services.price_adjuster import (
    _DECIMAL_PRECISION,
    AdjustedPriceSeries,
    AdjusterDataError,
    AdjusterError,
    PriceAdjuster,
    _as_decimal,
)

__all__ = [
    "POLICY_CONTENT_HASH",
    "TOTAL_RETURN_POLICY_VERSION",
    "TotalReturnAdjusterError",
    "TotalReturnRecord",
    "TotalReturnSeries",
    "TotalReturnAdjuster",
]


# =============================================================================
# Policy versioning — content hash freeze (ADR-0035)
# =============================================================================

TOTAL_RETURN_POLICY_VERSION: Final[str] = "1.0"


def _build_policy_body() -> dict:
    """정책 hash 입력 dict — total return 의미를 결정하는 모든 정보.

    값에 **Decimal 금지** (전부 str/int/bool) — `_jcs.canonicalize_jcs` 가
    Decimal 직접 직렬화 미지원 (cross-runtime drift 회피).
    """
    return {
        "version": TOTAL_RETURN_POLICY_VERSION,
        "reinvestment_timing": "ex_dividend_date",
        "tax_treatment": "pre_tax",
        "fractional_shares": "proportional_reinvestment",
        "rounding": "ROUND_HALF_EVEN",
        "decimal_precision": 28,
        "dividend_split_adjustment": "scaled_to_adjusted_basis",
    }


_POLICY_BODY: Final[dict] = _build_policy_body()
POLICY_CONTENT_HASH: Final[str] = (
    "sha256:"
    + hashlib.sha256(_canonicalize_jcs_shared(_POLICY_BODY)).hexdigest()
)


# =============================================================================
# Errors
# =============================================================================

class TotalReturnAdjusterError(AdjusterError):
    """Total return 계산의 invariant 위반.

    가격 엔진 예외 계층 (`AdjusterError`) 하위 — 0 division 등 결정성 위협을
    조용히 통과시키지 않고 명시 raise (§2.1).
    """


# =============================================================================
# Result dataclass
# =============================================================================

@dataclass(frozen=True, slots=True)
class TotalReturnRecord:
    """단일 일자의 close_adjusted + total return index.

    Attributes:
        date: 거래일.
        code: 종목코드.
        close_adjusted: PriceAdjuster 출력 그대로 (corporate-action 보정 close).
        total_return_index: 첫 거래일 기준 1.0 시작 누적 TRI (배당 재투자 포함).
    """

    date: date
    code: str
    close_adjusted: Decimal
    total_return_index: Decimal


@dataclass(frozen=True, slots=True)
class TotalReturnSeries:
    """Total return 시계열 결과 묶음 — backtest snapshot freeze 입력 단위.

    Attributes:
        code: 종목코드.
        series: TotalReturnRecord tuple (date 오름차순, immutable).
        policy_content_hash: 본 total return 정책의 SHA-256 hash.
        price_policy_content_hash: 입력 AdjustedPriceSeries 의 가격 보정 정책 hash.
        pit_policy_version: 입력 AdjustedPriceSeries 의 PIT 정책 version.
        warnings: 조용히 누락된 배당 등에 대한 사람-읽기 가능 경고 (§2.1).
            빈 tuple 이면 모든 배당이 정상 재투자됨.
    """

    code: str
    series: tuple[TotalReturnRecord, ...]
    policy_content_hash: str
    price_policy_content_hash: str
    pit_policy_version: str
    warnings: tuple[str, ...]


# =============================================================================
# TotalReturnAdjuster service
# =============================================================================

class TotalReturnAdjuster:
    """세전 Total Return 시계열 계산 service.

    State-less + DI 친화. `price_adjuster` 는 `adjust()` wrapper 가 raw 를
    보정할 때만 사용 — `compute()` 만 호출하면 불필요. `pit_enforcer` 는 향후
    배당 PIT 필터 위임용 hook (현 cycle 에서 compute 입력은 이미 이중 PIT 통과
    한 배당으로 전제 — DividendRepository.fetch_dividends 책임).

    Attributes:
        policy_content_hash: 본 인스턴스가 따르는 total return 정책 hash.
    """

    def __init__(
        self,
        *,
        price_adjuster: PriceAdjuster | None = None,
        pit_enforcer: PITEnforcer | None = None,
    ) -> None:
        self._price_adjuster: Final[PriceAdjuster] = (
            price_adjuster or PriceAdjuster()
        )
        self._pit_enforcer: Final[PITEnforcer | None] = pit_enforcer
        self.policy_content_hash: Final[str] = POLICY_CONTENT_HASH

    def compute(
        self,
        adjusted: AdjustedPriceSeries,
        dividends: Sequence[CorporateActionRecord],
    ) -> TotalReturnSeries:
        """보정 close 시계열 + 배당 → 일별 chaining total return index.

        Args:
            adjusted: PriceAdjuster.adjust 의 출력. `adjusted.adjusted` 는 date
                오름차순 AdjustedPriceRecord tuple. corporate-action 보정 close
                를 그대로 사용 (현금배당은 미보정 — _POLICY_MATRIX ignore).
            dividends: 이중 PIT (announced<=as_of AND effective<=as_of) 통과한
                cash_dividend 목록. DividendRepository.fetch_dividends 가
                supersede chain 까지 해소한 결과를 전제. 본 엔진은 effective_date
                기준 per_share 합산만 수행.

        Returns:
            TotalReturnSeries — TRI 시계열 + 두 정책 hash + warnings.

        Raises:
            TotalReturnAdjusterError: close_adjusted[t-1] == 0 또는 close_raw[t] == 0
                (0 division 방어).
            AdjusterDataError: 입력 `adjusted.adjusted` 가 date 오름차순이 아니거나,
                중복 date 가 있거나, 단일 code 가 아님 (freeze input 불변식 위반 —
                조용히 틀린 chaining 방지, PriceAdjuster.adjust 검증과 동일 패턴).
        """
        records = tuple(adjusted.adjusted)
        warnings: list[str] = []

        # 빈 입력 방어 — 빈 series + 적절 hash 반송.
        if not records:
            return TotalReturnSeries(
                code=adjusted.code,
                series=(),
                policy_content_hash=self.policy_content_hash,
                price_policy_content_hash=adjusted.policy_content_hash,
                pit_policy_version=adjusted.pit_policy_version,
                warnings=tuple(warnings),
            )

        code = records[0].code

        # 0. 입력 불변식 검증 (freeze input 엔진 — 조용히 틀린 chaining 방지).
        #    PriceAdjuster.adjust 의 검증 패턴 (price_adjuster.py:589-600) 참고:
        #    단일 code + date 엄격 오름차순 + 중복 date 없음.
        if any(r.code != code for r in records):
            raise AdjusterDataError(
                f"adjusted price records contain multiple codes "
                f"(expected single {code})"
            )
        for prev, cur in zip(records, records[1:], strict=False):
            if cur.date < prev.date:
                raise AdjusterDataError(
                    f"adjusted price records not in ascending date order for "
                    f"{code}: {prev.date.isoformat()} followed by "
                    f"{cur.date.isoformat()}"
                )
            if cur.date == prev.date:
                raise AdjusterDataError(
                    f"duplicate adjusted price record for {code} @ "
                    f"{cur.date.isoformat()}"
                )

        with localcontext() as ctx:
            ctx.prec = _DECIMAL_PRECISION
            ctx.rounding = ROUND_HALF_EVEN

            # 1. 배당을 effective_date → Σ per_share 로 집계.
            #    같은 배당락일 복수 배당은 합산. coalesce 실패 시 누락 + warning.
            trading_dates: frozenset[date] = frozenset(r.date for r in records)
            div_by_exdate: dict[date, Decimal] = {}
            for record in dividends:
                per_share = self._dividend_per_share(record, warnings)
                if per_share is None:
                    continue  # 파싱 실패 — warning 은 helper 가 추가.
                ex_date = record.effective_date
                if ex_date not in trading_dates:
                    # 배당락일이 거래일 시계열에 부재 — 추측 매칭 금지, 누락 + warning.
                    warnings.append(
                        f"dividend {record.id} ex-date {ex_date.isoformat()} not in "
                        f"price series for {code}; reinvestment skipped "
                        f"(per_share={per_share})"
                    )
                    continue
                div_by_exdate[ex_date] = (
                    div_by_exdate.get(ex_date, Decimal(0)) + per_share
                )

            # 1b. 첫 거래일 배당락 고지 (§2.1 조용한 손실 금지).
            #     TRI[0]=1 하드코딩 + 루프 i=1 시작이라 records[0] 의 배당은
            #     재투자 불가 (직전 종가 부재). 드롭은 불가피하나 무경고 누락 금지.
            if records[0].date in div_by_exdate:
                warnings.append(
                    f"dividend on first trading day {records[0].date.isoformat()} "
                    f"for {code} cannot be reinvested (no prior close — "
                    f"ex-dividend before series start); "
                    f"div={div_by_exdate[records[0].date]} dropped"
                )

            # 2. 일별 chaining TRI. TRI[0] = 1, 이후 누적.
            #    배당은 명목 원화 → 배당락일 t 의 보정계수
            #    factor[t] = close_adj[t] / close_raw[t] 로 스케일하여 close_adj 와
            #    동일 basis 로 맞춘다 (Critical — split × 명목배당 단위 불일치 보정).
            #    split 없는 구간은 close_adj == close_raw → factor == 1 → 명목 동일.
            tri = Decimal(1)
            out: list[TotalReturnRecord] = [
                TotalReturnRecord(
                    date=records[0].date,
                    code=records[0].code,
                    close_adjusted=records[0].close_adjusted,
                    total_return_index=tri,
                )
            ]
            for i in range(1, len(records)):
                prev_close = records[i - 1].close_adjusted
                if prev_close == 0:
                    raise TotalReturnAdjusterError(
                        f"close_adjusted is zero for {code} @ "
                        f"{records[i - 1].date.isoformat()}; total return index "
                        f"undefined (division by zero)"
                    )
                cur_close = records[i].close_adjusted
                div_nom = div_by_exdate.get(records[i].date, Decimal(0))
                if div_nom != 0:
                    close_raw = records[i].close_raw
                    if close_raw == 0:
                        raise TotalReturnAdjusterError(
                            f"close_raw is zero for {code} @ "
                            f"{records[i].date.isoformat()}; cannot scale nominal "
                            f"dividend to adjusted basis (division by zero)"
                        )
                    # 배당락일 t 자신의 보정계수 (t-1 아님).
                    factor = cur_close / close_raw
                    div_adj = div_nom * factor
                else:
                    div_adj = Decimal(0)
                tri = tri * (cur_close + div_adj) / prev_close
                out.append(TotalReturnRecord(
                    date=records[i].date,
                    code=records[i].code,
                    close_adjusted=cur_close,
                    total_return_index=tri,
                ))

        return TotalReturnSeries(
            code=code,
            series=tuple(out),
            policy_content_hash=self.policy_content_hash,
            price_policy_content_hash=adjusted.policy_content_hash,
            pit_policy_version=adjusted.pit_policy_version,
            warnings=tuple(warnings),
        )

    def adjust(
        self,
        raw_prices: Sequence[Any],
        corporate_actions: Sequence[CorporateActionRecord],
        dividends: Sequence[CorporateActionRecord],
        *,
        as_of: date,
    ) -> TotalReturnSeries:
        """raw 가격 보정 후 total return 계산하는 wrapper.

        `PriceAdjuster.adjust(raw, cas, as_of=as_of)` 로 corporate-action 보정한
        뒤 `compute(adjusted, dividends)`. backtest 연결 (#4/#5) 은 별도 —
        여기선 엔진 편의 진입점만.

        이중 리스트 주의: `corporate_actions` 는 가격 보정 (split / bonus /
        rights_issue 등) 입력이고, `dividends` 는 total return 재투자 (cash_dividend)
        입력으로 **분리** 된다. cash_dividend 는 _POLICY_MATRIX 에서 ignore (가격
        미보정) 이므로 corporate_actions 에 넣어도 가격엔 영향 없고, 재투자는 오직
        `dividends` 리스트만 소비한다 — 같은 cash_dividend 를 양쪽에 넣지 말 것.

        Args:
            raw_prices: PriceAdjuster.adjust 가 받는 PriceRecord 시퀀스.
            corporate_actions: 가격 보정용 corporate action 시퀀스.
            dividends: total return 재투자 대상 cash_dividend (이중 PIT 통과).
            as_of: PIT 기준 영업일.

        Returns:
            TotalReturnSeries.
        """
        adjusted = self._price_adjuster.adjust(
            raw_prices, corporate_actions, as_of=as_of,
        )
        return self.compute(adjusted, dividends)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dividend_per_share(
        record: CorporateActionRecord, warnings: list[str],
    ) -> Decimal | None:
        """배당금 1주당 금액 coalesce — cash_amount(Decimal) 우선, details per_share fallback.

        - `record.cash_amount` (Decimal) 가 not-None 이면 우선 사용.
        - None 이면 `record.details["per_share"]` (str) 를 `_as_decimal` 파싱.
        - 둘 다 없거나 파싱 실패 → None 반환 + warning (조용한 손실 금지).
        - 음수 (per_share < 0) → 데이터 오류 / 자본감소 오분류 → skip + warning.
          0 은 정상 (no-op, 재투자 0 — 유지).
        """
        if record.cash_amount is not None:
            return _reject_if_negative(record.cash_amount, record.id, warnings)

        raw = record.details.get("per_share")
        if raw is None:
            warnings.append(
                f"dividend {record.id} has neither cash_amount nor "
                f"details['per_share']; reinvestment skipped"
            )
            return None
        try:
            parsed = _as_decimal(raw)
        except AdjusterError:
            warnings.append(
                f"dividend {record.id} per_share {raw!r} not parseable as "
                f"Decimal; reinvestment skipped"
            )
            return None
        return _reject_if_negative(parsed, record.id, warnings)


def _reject_if_negative(
    per_share: Decimal, record_id: Any, warnings: list[str],
) -> Decimal | None:
    """음수 배당 거부 — 데이터 오류 / 자본감소 오분류 silent 반영 방지 (§2.1).

    `< 0` 이면 None + warning (skip). `== 0` 은 정상 (no-op).
    """
    if per_share < 0:
        warnings.append(
            f"dividend {record_id} per_share {per_share} is negative "
            f"(data error or capital-reduction misclassification); "
            f"reinvestment skipped"
        )
        return None
    return per_share
