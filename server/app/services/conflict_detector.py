"""ConflictDetector — ADR-0003 D4 의 multi-source 충돌 감지.

T18 KRX 일배치 (T19 DART 일배치) 에서 pykrx (1차) vs FDR (verify) 의 같은
도메인 데이터를 비교, threshold 초과 시 ConflictReport list 반환. 본 service
자체는 alert 발송 X — 호출자 (batch orchestrator) 가 logging / Sentry 결정.

설계 결정:

1. **`FetchResult.estimated_fields` 우선 적용** — adapter 가 추정값으로 채운
   필드 (e.g., FDR 의 `value = close × volume`) 는 본 비교에서 자동 제외.
   체계적 오차 (KRX 평균체결가 ≠ FDR 종가) 로 인한 false-positive alert 폭증
   차단. T14 cycle 의 oracle M1 의 design 결정.

2. **trade_date 별 매칭** — primary 와 verify 의 시계열을 (code, trade_date)
   tuple 키로 inner join. 한쪽에만 있는 일자 (예: pykrx 영업일 인식 vs FDR
   누락) 는 `missing_in_primary` / `missing_in_verify` 별 list 로 분리.

3. **Threshold = relative diff ratio** — `abs(primary - verify) / max(|primary|,
   |verify|)`. ADR-0003 D4 의 표 (ohlcv.close = 0.001) 와 일관. 0 분모 보호.

4. **OHLCVRow 만 first cycle** — market_cap / stock_master 비교는 backlog
   (운영 의미상 OHLCV 일치가 가장 빈번한 cross-check 도메인).

5. **frozen dataclass + 결정성** — ConflictReport list 가 결정적 정렬
   (trade_date, field, code).

관련 ADR:
- ADR-0003 D4 — 출처 우선순위 + 충돌 처리 표
- ADR-0001 D3 — pykrx canonical / FDR verify (OHLCV 도메인)
- T14 oracle M1 — `estimated_fields` metadata

Out-of-scope (다음 cycle):
- DART 재무제표 vs (없음) cross-check (1차 자료 only).
- Sentry alert 직접 발송 (호출자 책임).
- 출처 우선순위 ADR-0001 D3 의 "alert + pykrx 표시" — 본 service 는 detect 만.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final

from app.adapters.base import FetchResult, OHLCVRow

__all__ = [
    "ConflictDetector",
    "ConflictReport",
    "ConflictDetectionResult",
]


# ADR-0003 D4 의 표 — relative diff threshold 별 도메인 별.
_DEFAULT_OHLCV_THRESHOLDS: Final[dict[str, Decimal]] = {
    # 가격 컬럼 — KRX 공식과 FDR 의 외부 source (Yahoo/Naver) 가 미세 차이.
    # 0.1% 초과는 schema drift 또는 출처 corruption 의심.
    "open": Decimal("0.001"),
    "high": Decimal("0.001"),
    "low": Decimal("0.001"),
    "close": Decimal("0.001"),
    # 거래량 — KRX 와 FDR 모두 KRX 공식 발표를 source 라 동일해야 정상.
    "volume": Decimal("0.001"),
    # 거래대금 — FDR 은 추정 (estimated_fields) 라 default 제외. threshold 는 사용 X.
    "value": Decimal("0.005"),
}


@dataclass(frozen=True, slots=True)
class ConflictReport:
    """단일 (code, trade_date, field) 의 차이 record.

    Attributes:
        code: KRX 종목코드.
        trade_date: 비교 일자.
        field: canonical schema 의 필드명 ("close" / "volume" 등).
        primary_value: 1 차 출처 값 (Decimal).
        verify_value: 검증 출처 값 (Decimal).
        diff_ratio: `abs(primary - verify) / max(|primary|, |verify|)`. 0 분모
            보호 — primary=verify=0 인 경우 0.0 반환.
        threshold: 적용된 threshold (운영 시 historical lookup 용).
        primary_source: 1 차 출처 식별 ("PYKRX").
        verify_source: 검증 출처 식별 ("FDR").
    """

    code: str
    trade_date: date
    field: str
    primary_value: Decimal
    verify_value: Decimal
    diff_ratio: Decimal
    threshold: Decimal
    primary_source: str
    verify_source: str


@dataclass(frozen=True, slots=True)
class ConflictDetectionResult:
    """`ConflictDetector.compare_ohlcv` 의 반환 wrapper.

    Attributes:
        conflicts: threshold 초과 차이 record list (deterministic sort).
        missing_in_primary: verify 에 있지만 primary 에 없는 trade_date list.
        missing_in_verify: primary 에 있지만 verify 에 없는 trade_date list.
        compared_field_count: 실제 비교 수행한 field 수 (estimated_fields
            제외 후). 운영 logging.
        skipped_fields: estimated_fields 로 제외된 필드 set (운영 logging).
    """

    conflicts: Sequence[ConflictReport]
    missing_in_primary: Sequence[date] = field(default_factory=tuple)
    missing_in_verify: Sequence[date] = field(default_factory=tuple)
    compared_field_count: int = 0
    skipped_fields: frozenset[str] = field(default_factory=frozenset)


class ConflictDetector:
    """multi-source 데이터 충돌 감지 — state-less service.

    Args:
        thresholds: field name → relative diff threshold. None 이면 ADR-0003
            D4 default. 운영 시 prod 와 staging 의 threshold 가 다를 수 있어
            DI.
    """

    def __init__(
        self,
        *,
        thresholds: dict[str, Decimal] | None = None,
    ) -> None:
        self._thresholds: dict[str, Decimal] = dict(
            thresholds if thresholds is not None else _DEFAULT_OHLCV_THRESHOLDS,
        )

    def compare_ohlcv(
        self,
        primary: FetchResult[tuple[OHLCVRow, ...]],
        verify: FetchResult[tuple[OHLCVRow, ...]],
        *,
        primary_source: str = "PYKRX",
        verify_source: str = "FDR",
    ) -> ConflictDetectionResult:
        """두 OHLCV FetchResult 의 trade_date 별 필드 비교.

        Args:
            primary: 1 차 출처 (e.g., pykrx) — `estimated_fields` 가 비교 대상에
                포함될 수 있는 ground truth.
            verify: 검증 출처 (e.g., FDR). 양쪽의 `estimated_fields` 합집합이
                비교에서 제외됨.
            primary_source: 운영 logging 용 출처 라벨.
            verify_source: 동일.

        Returns:
            ConflictDetectionResult — sorted conflicts + missing list.
        """
        # 양쪽 estimated_fields 의 합집합 — 어느 한쪽이라도 추정한 필드는 비교 X.
        skipped = frozenset(primary.estimated_fields | verify.estimated_fields)
        comparable_fields = tuple(
            f for f in self._thresholds if f not in skipped
        )

        # trade_date → row 의 index 구축.
        primary_by_date: dict[date, OHLCVRow] = {
            r.trade_date: r for r in primary.data
        }
        verify_by_date: dict[date, OHLCVRow] = {
            r.trade_date: r for r in verify.data
        }

        common_dates = sorted(
            set(primary_by_date) & set(verify_by_date)
        )
        missing_in_primary = tuple(
            sorted(set(verify_by_date) - set(primary_by_date))
        )
        missing_in_verify = tuple(
            sorted(set(primary_by_date) - set(verify_by_date))
        )

        conflicts: list[ConflictReport] = []
        for d in common_dates:
            p_row = primary_by_date[d]
            v_row = verify_by_date[d]
            for f in comparable_fields:
                p_val = _row_field_to_decimal(p_row, f)
                v_val = _row_field_to_decimal(v_row, f)
                diff_ratio = _relative_diff_ratio(p_val, v_val)
                threshold = self._thresholds[f]
                if diff_ratio > threshold:
                    # code 는 두 row 모두 같다고 가정 (호출자가 같은 종목 fetch).
                    conflicts.append(
                        ConflictReport(
                            code=p_row.code,
                            trade_date=d,
                            field=f,
                            primary_value=p_val,
                            verify_value=v_val,
                            diff_ratio=diff_ratio,
                            threshold=threshold,
                            primary_source=primary_source,
                            verify_source=verify_source,
                        )
                    )

        # 결정적 정렬 — (trade_date, field, code).
        conflicts.sort(key=lambda c: (c.trade_date, c.field, c.code))

        return ConflictDetectionResult(
            conflicts=tuple(conflicts),
            missing_in_primary=missing_in_primary,
            missing_in_verify=missing_in_verify,
            compared_field_count=len(comparable_fields),
            skipped_fields=skipped,
        )


def _row_field_to_decimal(row: OHLCVRow, field_name: str) -> Decimal:
    """OHLCVRow 의 field 를 Decimal 로 — volume 은 int 라 Decimal 변환.

    Raises:
        AttributeError: schema 변경으로 field 사라진 경우 (운영 fail-fast).
    """
    value = getattr(row, field_name)
    if isinstance(value, Decimal):
        return value
    # volume 등 int — str 경유 안전 변환 (codebase 일관 패턴).
    return Decimal(str(value))


def _relative_diff_ratio(primary: Decimal, verify: Decimal) -> Decimal:
    """`abs(primary - verify) / max(|primary|, |verify|)` — 0 분모 보호.

    두 값 모두 0 이면 0 반환 (동일). 한쪽만 0 이면 다른 값으로 분모 → 1 (max
    diff). 정상 운영의 OHLCV 값은 항상 양수.
    """
    abs_p = abs(primary)
    abs_v = abs(verify)
    denominator = max(abs_p, abs_v)
    if denominator == 0:
        # 둘 다 0 — 동일.
        return Decimal(0)
    return abs(primary - verify) / denominator
