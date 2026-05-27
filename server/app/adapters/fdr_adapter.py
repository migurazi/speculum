"""FdrAdapter — FinanceDataReader 라이브러리 wrapper (검증 출처).

ADR-0001 D3 / ADR-0003 D4 의 pykrx 1차 + FDR verify 패턴 구현. FDR 은 외부
source (Yahoo Finance, Naver) wrap 라 1차 자료 아님 — pykrx 와 cross-check 용.

다루는 도메인:
    - OHLCV (verify) — pykrx 의 KRX 공식과 비교.
    - 종목 마스터 (verify) — pykrx universe 와 cross-check.
    - universe — pykrx 와 cross-check.

설계 결정:

1. **canonical schema 재사용** — `OHLCVRow` / `StockMaster` 모두 T15 PykrxAdapter
   와 동일. service / repository 는 출처 무관.

2. **value (거래대금) 추정 + warning** — FDR DataFrame 은 `거래대금` 컬럼 미제공.
   canonical `OHLCVRow.value` 의무 필드를 만족하려면:
   - **추정**: `value ≈ close × volume` (정확한 KRX 거래대금 = 평균체결가 × 거래량
     이라 차이 발생). cross-check 용도이므로 정밀도 부담 적음.
   - **warning** — FetchResult.warnings 에 명시: "FDR value estimated as
     close*volume — KRX official differs".
   - 운영 처리: T18 일배치의 ConflictDetector 가 pykrx value 와 비교 시 본 추정의
     체계적 편향을 인식 (FDR value 는 threshold 검증 대상에서 제외 권장).

3. **lazy import + mock module 주입** — T15 와 동일 패턴. fdr 실제 미설치 환경
   에서도 test 통과.

4. **에러 분류** — `_call_fdr` 가 T15 의 `_call_pykrx` 와 동일 정책:
   ConnectionError → AdapterRetryError, AdapterError re-raise, BaseException
   propagate, 그 외 → AdapterError.

5. **citation URL** — FDR 은 외부 source (Yahoo/Naver) 다중 라 단일 URL 없음.
   `url=None` (ADR-0002 D3 의 nullable url 허용).

관련 ADR:
- ADR-0001 D3 — pykrx canonical / FDR verify
- ADR-0003 D2 (canonical), D3 (citation), D4 (충돌)
- ADR-0002 D3 (Source Citation 7-tuple)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid4

import pandas as pd

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    DataSourceAdapter,
    FetchResult,
    OHLCVRow,
    StockMaster,
)
from app.models.source_citation import SourceCitation, SourceKind

__all__ = ["FdrAdapter"]


# FDR DataReader 의 영문 컬럼명 — FDR 0.9.x 기준. 변경 시 ADAPTER_VERSION major
# bump.
_FDR_OHLCV_COLUMNS: Final[dict[str, str]] = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}

# FDR StockListing 의 컬럼명. KOSPI/KOSDAQ 양쪽 호환. version 변경 시 bump.
_FDR_LISTING_COLUMNS: Final[dict[str, str]] = {
    "code": "Code",
    "name": "Name",
    "market": "Market",  # "KOSPI" | "KOSDAQ" 등.
    "listing_date": "ListingDate",  # nullable.
    "sector": "Sector",  # nullable.
}

# Warning 메시지 — FetchResult.warnings 에 명시.
_VALUE_ESTIMATION_WARNING: Final[str] = (
    "FDR OHLCV value (거래대금) estimated as close*volume — "
    "differs from KRX official 평균체결가 × 거래량"
)


class FdrAdapter(DataSourceAdapter):
    """FinanceDataReader wrapper — 검증 출처 (ADR-0001 D3 의 2 순위).

    Attributes (class):
        SOURCE_KIND: `"FDR"`. Citation 의 source enum.
        ADAPTER_VERSION: 본 adapter 코드 semver. FDR 라이브러리 version 변경 시
            patch+, 컬럼명·의미 변경 시 major.

    Args (생성자):
        fdr_module: FinanceDataReader 모듈 (테스트 시 mock 주입). None 이면 첫
            fetch 호출 시 real import.
    """

    SOURCE_KIND = "FDR"
    ADAPTER_VERSION = "1.0.0"

    def __init__(self, fdr_module: Any | None = None) -> None:
        self._fdr_module = fdr_module
        self._lazy_loaded = fdr_module is not None

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    def health_check(self) -> bool:
        """FDR 모듈 import 가능 + 간단한 함수 호출 성공 여부."""
        try:
            module = self._get_fdr_module()
        except AdapterError:
            return False
        try:
            # 가장 가벼운 호출 — KOSPI listing.
            module.StockListing("KOSPI")
            return True
        except Exception:  # noqa: BLE001
            return False

    # -------------------------------------------------------------------------
    # OHLCV — 검증 시계열 (value 는 추정)
    # -------------------------------------------------------------------------

    def fetch_ohlcv_by_date_range(
        self,
        code: str,
        *,
        fromdate: date,
        todate: date,
        batch_id: UUID,
    ) -> FetchResult[tuple[OHLCVRow, ...]]:
        """단일 종목 OHLCV 시계열 — pykrx 와 cross-check 용.

        Args / Returns / Raises 의미는 PykrxAdapter.fetch_ohlcv_by_date_range
        와 동일. 단:
            - `value` (거래대금) 은 `close × volume` 추정 — FetchResult.warnings
              에 명시. 정밀 비교 시 pykrx 사용.
        """
        _validate_code(code)
        _validate_date_range(fromdate, todate)

        module = self._get_fdr_module()
        df = self._call_fdr(
            lambda: module.DataReader(
                code,
                fromdate.strftime("%Y-%m-%d"),
                todate.strftime("%Y-%m-%d"),
            ),
            context=f"DataReader({code}, {fromdate}, {todate})",
        )
        if df is None or df.empty:
            raise AdapterError(
                f"FDR returned empty OHLCV for code={code} "
                f"in {fromdate.isoformat()}~{todate.isoformat()}"
            )

        rows = tuple(
            OHLCVRow(
                code=code,
                trade_date=_index_to_date(ts),
                open=_decimal_from_value(row[_FDR_OHLCV_COLUMNS["open"]]),
                high=_decimal_from_value(row[_FDR_OHLCV_COLUMNS["high"]]),
                low=_decimal_from_value(row[_FDR_OHLCV_COLUMNS["low"]]),
                close=_decimal_from_value(row[_FDR_OHLCV_COLUMNS["close"]]),
                volume=int(row[_FDR_OHLCV_COLUMNS["volume"]]),
                # 거래대금 추정 — `close × volume`. KRX 공식과 다름 (warning).
                value=(
                    _decimal_from_value(row[_FDR_OHLCV_COLUMNS["close"]])
                    * Decimal(int(row[_FDR_OHLCV_COLUMNS["volume"]]))
                ),
            )
            for ts, row in df.iterrows()
        )
        rows = tuple(sorted(rows, key=lambda r: r.trade_date))
        if not rows:
            raise AdapterError(
                f"no valid OHLCV rows after conversion for code={code} "
                f"in {fromdate.isoformat()}~{todate.isoformat()}"
            )

        citation = self._make_citation(
            identifier=f"{code}|{fromdate.isoformat()}|{todate.isoformat()}|ohlcv",
            effective_date=rows[-1].trade_date,
            batch_id=batch_id,
        )
        return FetchResult(
            data=rows,
            citations=(citation,),
            warnings=(_VALUE_ESTIMATION_WARNING,),
            # oracle 리뷰 M1 — FDR 의 `value` 는 close × volume 추정. T18
            # ConflictDetector 가 본 필드를 pykrx 와 숫자 비교하면 체계적
            # 오차 (KRX 의 평균체결가 vs 종가) 로 false-positive alert 폭증.
            # estimated_fields 로 비교 대상 제외 신호.
            estimated_fields=frozenset({"value"}),
        )

    # -------------------------------------------------------------------------
    # Stock master — StockListing 위 단일 종목 lookup
    # -------------------------------------------------------------------------

    def fetch_stock_master(
        self,
        code: str,
        *,
        as_of: date,
        batch_id: UUID,
    ) -> FetchResult[StockMaster]:
        """단일 종목의 마스터 — Code/Name/Market/ListingDate/Sector.

        FDR 의 StockListing 은 시장별 (KOSPI/KOSDAQ) 호출. 양쪽을 모두 fetch 후
        Code 매칭 lineage 반환.

        Notes:
            FDR 의 StockListing 은 시점 무관 — `as_of` 는 citation effective_date
            용으로만 사용. 운영 의미: FDR 은 "현재 active" 종목 list (delisted
            제외). pykrx universe 와 비교 시 미세 차이 가능.
        """
        _validate_code(code)

        module = self._get_fdr_module()
        for market in ("KOSPI", "KOSDAQ"):
            df = self._call_fdr(
                lambda m=market: module.StockListing(m),
                context=f"StockListing({market})",
            )
            if df is None or df.empty:
                continue
            # Code 컬럼 매칭 — string equality.
            code_col = _FDR_LISTING_COLUMNS["code"]
            matching = df[df[code_col].astype(str) == code]
            if matching.empty:
                continue
            row = matching.iloc[0]
            data = StockMaster(
                code=code,
                name=str(row[_FDR_LISTING_COLUMNS["name"]]),
                market=market,
                listing_date=_optional_date_from_value(
                    row.get(_FDR_LISTING_COLUMNS["listing_date"]),
                ),
                delisting_date=None,  # FDR StockListing 은 active 만 반환.
                sector_krx=_optional_str_from_value(
                    row.get(_FDR_LISTING_COLUMNS["sector"]),
                ),
            )
            citation = self._make_citation(
                identifier=f"{code}|{as_of.isoformat()}|stock_master",
                effective_date=as_of,
                batch_id=batch_id,
            )
            return FetchResult(data=data, citations=(citation,))

        raise AdapterError(
            f"code={code} not found in FDR KOSPI/KOSDAQ StockListing"
        )

    # -------------------------------------------------------------------------
    # Universe — KOSPI/KOSDAQ 활성 종목코드 list
    # -------------------------------------------------------------------------

    def fetch_universe(
        self,
        *,
        as_of: date,
        market: str,
        batch_id: UUID,
    ) -> FetchResult[tuple[str, ...]]:
        """`market` 의 active 종목코드 set.

        Args:
            market: "KOSPI" | "KOSDAQ".

        Note (vs PykrxAdapter):
            FDR StockListing 은 시점 무관 (현재 active 만). PykrxAdapter 는
            `as_of` 시점의 universe. cross-check 시 시점 차이 인식 필요 —
            ConflictDetector (T18) 가 universe 비교 시 본 차이 흡수.
        """
        if market not in ("KOSPI", "KOSDAQ"):
            raise AdapterError(f"unsupported market: {market!r}")

        module = self._get_fdr_module()
        df = self._call_fdr(
            lambda: module.StockListing(market),
            context=f"StockListing({market})",
        )
        if df is None or df.empty:
            raise AdapterError(
                f"FDR returned empty StockListing for market={market}"
            )

        code_col = _FDR_LISTING_COLUMNS["code"]
        codes = tuple(sorted(df[code_col].astype(str).tolist()))
        citation = self._make_citation(
            identifier=f"universe|{market}|{as_of.isoformat()}",
            effective_date=as_of,
            batch_id=batch_id,
        )
        return FetchResult(data=codes, citations=(citation,))

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _get_fdr_module(self) -> Any:
        """lazy import — 첫 호출 시 FinanceDataReader 로드."""
        if self._lazy_loaded:
            return self._fdr_module
        try:
            import FinanceDataReader as _fdr
        except ImportError as exc:
            raise AdapterError(
                "FinanceDataReader not installed — "
                "`pip install finance-datareader` required"
            ) from exc
        self._fdr_module = _fdr
        self._lazy_loaded = True
        return _fdr

    def _call_fdr(
        self,
        thunk: Any,
        *,
        context: str,
    ) -> Any:
        """FDR 호출 wrapper — T15 PykrxAdapter._call_pykrx 와 동일 분류 정책.

        ConnectionError/TimeoutError → AdapterRetryError, AdapterError re-raise,
        KeyboardInterrupt/SystemExit/MemoryError propagate, 그 외 → AdapterError.
        """
        try:
            return thunk()
        except (ConnectionError, TimeoutError) as exc:
            raise AdapterRetryError(
                f"FDR network error in {context}: {exc}"
            ) from exc
        except AdapterError:
            # 이미 정규화 — 재 wrap 금지.
            raise
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as exc:
            raise AdapterError(
                f"FDR call failed in {context}: {exc}"
            ) from exc

    def _make_citation(
        self,
        *,
        identifier: str,
        effective_date: date,
        batch_id: UUID,
    ) -> SourceCitation:
        """SourceCitation 7-tuple — FDR 은 외부 source 다중이라 url=None."""
        return SourceCitation(
            id=uuid4(),
            source=SourceKind.FDR,
            identifier=identifier,
            retrieved_at=datetime.now(timezone.utc),
            effective_date=effective_date,
            adapter_version=self.ADAPTER_VERSION,
            batch_id=batch_id,
            url=None,
        )


# =============================================================================
# Validation / conversion helpers — module level (단위 테스트 가능)
# =============================================================================

def _validate_code(code: str) -> None:
    """KRX 종목코드 형식 — 6 자리 numeric (T15 와 동일 정책)."""
    if not isinstance(code, str):
        raise AdapterError(f"code must be str, got {type(code).__name__}")
    if len(code) != 6 or not code.isdigit():
        raise AdapterError(
            f"code must be 6-digit numeric string, got {code[:32]!r}"
        )


def _validate_date_range(fromdate: date, todate: date) -> None:
    if fromdate > todate:
        raise AdapterError(
            f"fromdate ({fromdate.isoformat()}) > todate "
            f"({todate.isoformat()}) — inverted range"
        )


def _index_to_date(index_value: Any) -> date:
    """DataFrame index → naive date — T15 의 동등 helper."""
    if hasattr(index_value, "date") and callable(index_value.date):
        result = index_value.date()
        if isinstance(result, date):
            return result
    if isinstance(index_value, date):
        if isinstance(index_value, datetime):
            return index_value.date()
        return index_value
    if isinstance(index_value, str):
        cleaned = index_value.replace("-", "")
        if len(cleaned) == 8 and cleaned.isdigit():
            return date(
                int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]),
            )
    raise AdapterError(f"cannot interpret DataFrame index value: {index_value!r}")


def _decimal_from_value(value: Any) -> Decimal:
    """numpy/pandas 수치 → Decimal — T15 와 동일 NaN/Decimal 방어선.

    `pd.isna` 로 NaN/NA/NaT 일관 처리 + `Decimal.is_finite()` 사후 검증.
    """
    if pd.isna(value):
        raise AdapterError(f"NaN/NA value in FDR DataFrame: {value!r}")
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise AdapterError(
            f"cannot convert FDR value to Decimal: {value!r}"
        ) from exc
    if not result.is_finite():
        raise AdapterError(
            f"non-finite Decimal from FDR value: {value!r} -> {result}"
        )
    return result


def _optional_date_from_value(value: Any) -> date | None:
    """nullable date — pd.NaT / None 은 None 반환.

    FDR StockListing 의 ListingDate 는 일부 종목 (오래된 종목) 에서 NaT 가능.

    oracle 리뷰 M2 — `_index_to_date` 가 인식 못 하는 값 (예: FDR 가 `"--"`
    또는 정수 `0`) 에서 AdapterError raise 가 호출자의 `_call_fdr` 외부에서
    발생하면 종목 1 개의 parsing 실패가 전체 fetch 실패로 전파. nullable
    필드 의미론상 None fallback 이 안전.
    """
    if value is None or pd.isna(value):
        return None
    try:
        return _index_to_date(value)
    except AdapterError:
        # 파싱 불가 값 — None fallback. caller (StockMaster) 가 nullable 의미
        # 로 처리. 운영 시 시계열 분석에서 listing_date 가 필요한 종목만
        # 별도 보강 (T16 DART 또는 KRX 직접).
        return None


def _optional_str_from_value(value: Any) -> str | None:
    """nullable string — FDR Sector 등 일부 종목에서 NaN/None 가능."""
    if value is None or pd.isna(value):
        return None
    result = str(value).strip()
    return result or None
