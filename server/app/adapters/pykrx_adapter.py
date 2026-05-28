"""PykrxAdapter — KRX 공식 데이터 1차 출처 (pykrx 라이브러리 wrapper).

ADR-0003 D2 의 canonical schema 변환 + D3 의 Source Citation 자동 채움.

다루는 도메인 (ADR-0003 D2 의 1차 출처 컬럼):
    - OHLCV (raw, 정상가 — corporate action 보정 X — ADR-0001 D1)
    - 시가총액 + 발행주식수
    - 종목 마스터 (이름·시장)
    - 영업일 캘린더 (T17 verified 캘린더와 cross-check 용)

설계 결정:

1. **pykrx 모듈 lazy import** — `pip install pykrx` 가 운영 deps 이지만 본
   모듈을 import 만 하는 시점에는 외부 라이브러리 로드 회피 (test 환경 + 빠른
   startup). 첫 fetch 호출 시 import.

2. **함수 level mocking 호환** — pykrx 의 함수를 instance attribute 으로 보유.
   생성자가 `pykrx_module` 인자 주입 받아 테스트가 mock module 전달. None 이면
   real pykrx lazy import.

3. **DataFrame → canonical schema 변환**:
   - pykrx 의 DataFrame 컬럼명은 한글 (`시가`, `종가`, `거래량`, etc.).
   - 변환은 본 adapter 의 책임 — service / repository 는 한글 컬럼명 모름.
   - `Decimal(str(value))` 패턴 — `Decimal(float)` 의 binary 부동소수 오차
     회피 (codebase 전체에서 통일).

4. **Citation 한 fetch = 한 citation**:
   - ADR-0002 D3 의 batch 단위 citation. row 마다 별 citation row 안 만듦
     (citation 폭증 방지). 한 fetch 호출 = 한 retrieval 이벤트.
   - `effective_date` 는 fetch 범위 중 가장 늦은 trade_date (운영 의미상
     batch 의 정보 가용성 시점).
   - `identifier` = `f"{code}|{fromdate}|{todate}|{method}"` — 동일 fetch
     재현 가능.

5. **rate limit / retry 는 호출자 책임** — adapter 는 단일 호출 단위 추상화.
   T18 일배치가 ADR-0003 D6 의 1.5s 간격 throttling + circuit breaker 적용.

6. **에러 분류** — pykrx 의 raise 는 ConnectionError / ValueError / etc.
   본 adapter 가 `AdapterError` 계층으로 정규화:
   - 빈 DataFrame (종목 미존재 등) → `AdapterError`.
   - 네트워크 / 일시 장애 → `AdapterRetryError`.

관련 ADR:
- ADR-0003 D1 (ABC), D2 (canonical), D3 (citation), D5 (semver), D7 (디렉토리)
- ADR-0001 D1 (raw close 우선)
- ADR-0002 D3 (Source Citation 7-tuple)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4

import pandas as pd

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    DataSourceAdapter,
    FetchResult,
    MarketCapRow,
    OHLCVRow,
    StockMaster,
)
from app.models.source_citation import SourceCitation, SourceKind

# pandas 는 운영 deps — top-level import (lazy 의미 없음).
# TYPE_CHECKING 은 다른 type-only import 시 사용 (현재는 빈 블록).
if TYPE_CHECKING:
    pass

__all__ = ["PykrxAdapter"]


# pykrx DataFrame 의 한글 컬럼명 — pykrx 1.0.x 기준. 라이브러리 version 변경 시
# 컬럼명이 바뀌면 본 상수 + ADAPTER_VERSION major bump.
_PYKRX_OHLCV_COLUMNS: Final[dict[str, str]] = {
    "open": "시가",
    "high": "고가",
    "low": "저가",
    "close": "종가",
    "volume": "거래량",
    "value": "거래대금",
}
_PYKRX_MARKET_CAP_COLUMNS: Final[dict[str, str]] = {
    "market_cap": "시가총액",
    "shares_outstanding": "상장주식수",
}

# Citation `url` 은 None — Momus M0 review W1 fix. 기존의 `kind.krx.co.kr/...`
# `isurCmpyCd={code}` 는 KIND 공시시스템의 발행회사 고유번호 (8자리) 매개
# 변수로 6자리 KRX 종목코드를 그대로 넣어 404. KRX 정보데이터시스템 (data.
# krx.co.kr) 도 form submission 기반이라 종목별 deep link 부재. FDR adapter
# 와 일관되게 None 사용 — 추후 KRX OPEN API (ADR-0018 답변 후) 합류 시 정확한
# deep link 생성 가능.


class PykrxAdapter(DataSourceAdapter):
    """pykrx 라이브러리 wrapper — KRX 공식 데이터 1차 fetch.

    Attributes (class):
        SOURCE_KIND: `"PYKRX"`. Citation 의 source enum value.
        ADAPTER_VERSION: 본 adapter 코드 semver. pykrx 라이브러리 version 변경
            시 patch+, 컬럼명 / 의미 변경 시 major bump.

    Args (생성자):
        pykrx_module: pykrx.stock 모듈 (테스트 시 mock 주입). None 이면 첫
            fetch 호출 시 real `pykrx.stock` lazy import.
    """

    SOURCE_KIND = "PYKRX"
    ADAPTER_VERSION = "1.0.0"

    def __init__(self, pykrx_module: Any | None = None) -> None:
        self._pykrx_module = pykrx_module
        self._lazy_loaded = pykrx_module is not None

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    def health_check(self) -> bool:
        """pykrx 모듈 import 가능 + 간단한 함수 호출 성공 여부.

        Returns:
            True = pykrx 정상, False = import 실패 또는 호출 실패.
        """
        try:
            module = self._get_pykrx_module()
        except AdapterError:
            return False
        try:
            # 가장 가벼운 호출 — 한 종목코드 list 가져오기.
            module.get_market_ticker_list("20240102", market="KOSPI")
            return True
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # OHLCV — 일일 종가/거래량 시계열
    # -------------------------------------------------------------------------

    def fetch_ohlcv_by_date_range(
        self,
        code: str,
        *,
        fromdate: date,
        todate: date,
        batch_id: UUID,
    ) -> FetchResult[tuple[OHLCVRow, ...]]:
        """단일 종목의 OHLCV 시계열 fetch.

        Args:
            code: KRX 종목코드 (6자리 zero-padded).
            fromdate: 시작일 (KST naive date, inclusive).
            todate: 종료일 (KST naive date, inclusive).
            batch_id: 호출자 (T18 일배치) 가 부여한 batch 식별자 — citation
                의 batch_id 채움.

        Returns:
            FetchResult — data = `tuple[OHLCVRow, ...]` (trade_date 오름차순).
            citations = `(SourceCitation,)` — 본 fetch 1 개.

        Raises:
            AdapterError: code 가 KRX 종목코드 형식 위반, pykrx 가 빈 DataFrame
                반환 (종목 미존재 등).
            AdapterRetryError: 네트워크 / 일시 장애.
        """
        _validate_code(code)
        _validate_date_range(fromdate, todate)

        module = self._get_pykrx_module()
        df = self._call_pykrx(
            lambda: module.get_market_ohlcv(
                fromdate.strftime("%Y%m%d"),
                todate.strftime("%Y%m%d"),
                code,
            ),
            context=f"get_market_ohlcv({code}, {fromdate}, {todate})",
        )
        if df is None or df.empty:
            raise AdapterError(
                f"pykrx returned empty OHLCV for code={code} "
                f"in {fromdate.isoformat()}~{todate.isoformat()}"
            )

        # DataFrame → tuple[OHLCVRow, ...] — 한글 컬럼명 + index Timestamp 변환.
        rows = tuple(
            OHLCVRow(
                code=code,
                trade_date=_index_to_date(ts),
                open=_decimal_from_value(row[_PYKRX_OHLCV_COLUMNS["open"]]),
                high=_decimal_from_value(row[_PYKRX_OHLCV_COLUMNS["high"]]),
                low=_decimal_from_value(row[_PYKRX_OHLCV_COLUMNS["low"]]),
                close=_decimal_from_value(row[_PYKRX_OHLCV_COLUMNS["close"]]),
                volume=int(row[_PYKRX_OHLCV_COLUMNS["volume"]]),
                value=_decimal_from_value(row[_PYKRX_OHLCV_COLUMNS["value"]]),
            )
            # df.iterrows() 의 (index, row) 튜플.
            for ts, row in df.iterrows()
        )
        # trade_date 오름차순 보장 — pykrx 가 이미 정렬되어 반환하지만 명시.
        rows = tuple(sorted(rows, key=lambda r: r.trade_date))
        # oracle 리뷰 C3 — 변환 후 empty 사후 검증. df.empty path 의 invariant
        # 강제 (향후 변환 logic 변경 시 silent IndexError 회피).
        if not rows:
            raise AdapterError(
                f"no valid OHLCV rows after conversion for code={code} "
                f"in {fromdate.isoformat()}~{todate.isoformat()}"
            )

        citation = self._make_citation(
            identifier=f"{code}|{fromdate.isoformat()}|{todate.isoformat()}|ohlcv",
            effective_date=rows[-1].trade_date,
            batch_id=batch_id,
            url=None,  # W1: KRX 종목 deep link 부재 — 추후 KRX OPEN API 합류 시.
        )
        return FetchResult(data=rows, citations=(citation,))

    # -------------------------------------------------------------------------
    # Market cap — 시가총액·발행주식수 시계열
    # -------------------------------------------------------------------------

    def fetch_market_cap_by_date_range(
        self,
        code: str,
        *,
        fromdate: date,
        todate: date,
        batch_id: UUID,
    ) -> FetchResult[tuple[MarketCapRow, ...]]:
        """단일 종목의 시가총액 + 발행주식수 시계열.

        pykrx 의 `get_market_cap_by_date` 는 `(시가총액, 거래량, 거래대금,
        상장주식수)` 컬럼. 거래량/거래대금은 OHLCV 와 중복이라 본 adapter
        에서는 제외.

        Args / Returns / Raises: `fetch_ohlcv_by_date_range` 와 동일 의미.

        Notes:
            `shares_treasury` 는 pykrx 미제공 — None 으로 채움. DART (T16
            adapter) 가 보강.
        """
        _validate_code(code)
        _validate_date_range(fromdate, todate)

        module = self._get_pykrx_module()
        df = self._call_pykrx(
            lambda: module.get_market_cap_by_date(
                fromdate.strftime("%Y%m%d"),
                todate.strftime("%Y%m%d"),
                code,
            ),
            context=f"get_market_cap_by_date({code}, {fromdate}, {todate})",
        )
        if df is None or df.empty:
            raise AdapterError(
                f"pykrx returned empty market_cap for code={code} "
                f"in {fromdate.isoformat()}~{todate.isoformat()}"
            )

        rows = tuple(
            MarketCapRow(
                code=code,
                trade_date=_index_to_date(ts),
                market_cap=_decimal_from_value(
                    row[_PYKRX_MARKET_CAP_COLUMNS["market_cap"]],
                ),
                shares_outstanding=int(
                    row[_PYKRX_MARKET_CAP_COLUMNS["shares_outstanding"]],
                ),
                shares_treasury=None,  # pykrx 미제공 — DART 보강 필요.
            )
            for ts, row in df.iterrows()
        )
        rows = tuple(sorted(rows, key=lambda r: r.trade_date))
        # oracle 리뷰 C3 — empty 사후 검증 (OHLCV 와 일관).
        if not rows:
            raise AdapterError(
                f"no valid market_cap rows after conversion for code={code} "
                f"in {fromdate.isoformat()}~{todate.isoformat()}"
            )

        citation = self._make_citation(
            identifier=(
                f"{code}|{fromdate.isoformat()}|{todate.isoformat()}|market_cap"
            ),
            effective_date=rows[-1].trade_date,
            batch_id=batch_id,
            url=None,  # W1: KRX 종목 deep link 부재 — 추후 KRX OPEN API 합류 시.
        )
        return FetchResult(data=rows, citations=(citation,))

    # -------------------------------------------------------------------------
    # Stock master — 단일 종목 메타정보
    # -------------------------------------------------------------------------

    def fetch_stock_master(
        self,
        code: str,
        *,
        as_of: date,
        batch_id: UUID,
    ) -> FetchResult[StockMaster]:
        """단일 종목의 마스터 정보 — 이름·시장.

        pykrx 의 단일 종목 마스터 API 는 직접 없음. 우회:
            1. `get_market_ticker_name(code)` → 종목명.
            2. KOSPI / KOSDAQ 양쪽의 `get_market_ticker_list(as_of, market)`
               에 code 포함 여부로 시장 결정.

        Notes:
            listing_date / delisting_date / sector_krx 는 pykrx 직접 API 없음 →
            None. T16 (DART) 또는 KRX 직접 API (M2+) 가 보강.
        """
        _validate_code(code)

        module = self._get_pykrx_module()
        name = self._call_pykrx(
            lambda: module.get_market_ticker_name(code),
            context=f"get_market_ticker_name({code})",
        )
        if not name or not isinstance(name, str):
            raise AdapterError(f"pykrx returned no name for code={code}")

        as_of_str = as_of.strftime("%Y%m%d")
        market = self._infer_market(module, code, as_of_str)
        if market is None:
            raise AdapterError(
                f"code={code} not found in KOSPI/KOSDAQ ticker list "
                f"as of {as_of.isoformat()}"
            )

        data = StockMaster(
            code=code,
            name=name,
            market=market,
            listing_date=None,
            delisting_date=None,
            sector_krx=None,
        )
        citation = self._make_citation(
            identifier=f"{code}|{as_of_str}|stock_master",
            effective_date=as_of,
            batch_id=batch_id,
            url=None,  # W1: KRX 종목 deep link 부재 — 추후 KRX OPEN API 합류 시.
        )
        return FetchResult(data=data, citations=(citation,))

    # -------------------------------------------------------------------------
    # Universe — KOSPI/KOSDAQ 활성 종목 list
    # -------------------------------------------------------------------------

    def fetch_universe(
        self,
        *,
        as_of: date,
        market: str,
        batch_id: UUID,
    ) -> FetchResult[tuple[str, ...]]:
        """`as_of` 시점의 `market` 활성 종목코드 set.

        Args:
            market: "KOSPI" | "KOSDAQ" — pykrx 호환 string.

        Returns:
            FetchResult — data = `tuple[str, ...]` 정렬된 종목코드 list.
        """
        if market not in ("KOSPI", "KOSDAQ"):
            raise AdapterError(f"unsupported market: {market!r}")

        module = self._get_pykrx_module()
        as_of_str = as_of.strftime("%Y%m%d")
        tickers = self._call_pykrx(
            lambda: module.get_market_ticker_list(as_of_str, market=market),
            context=f"get_market_ticker_list({as_of_str}, {market})",
        )
        if tickers is None:
            raise AdapterError(
                f"pykrx returned None for ticker_list({as_of_str}, {market})"
            )

        sorted_codes = tuple(sorted(str(t) for t in tickers))
        citation = self._make_citation(
            identifier=f"universe|{market}|{as_of_str}",
            effective_date=as_of,
            batch_id=batch_id,
            url=None,  # 시장 전체 universe — 단일 URL 없음.
        )
        return FetchResult(data=sorted_codes, citations=(citation,))

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _get_pykrx_module(self) -> Any:
        """lazy import — 첫 호출 시 pykrx.stock 로드.

        Raises:
            AdapterError: pykrx 미설치 또는 import 실패.
        """
        if self._lazy_loaded:
            return self._pykrx_module
        try:
            import pykrx.stock as _pks
        except ImportError as exc:
            raise AdapterError(
                "pykrx not installed — `pip install pykrx` required"
            ) from exc
        self._pykrx_module = _pks
        self._lazy_loaded = True
        return _pks

    def _call_pykrx(
        self,
        thunk: Any,
        *,
        context: str,
    ) -> Any:
        """pykrx 호출 wrapper — 예외를 AdapterError 계층으로 정규화.

        thunk = 인자 없는 callable. 호출자가 lambda 로 래핑.

        oracle 리뷰 C2 — 분류 순서:
            1. ConnectionError/TimeoutError → AdapterRetryError (retry 가능).
            2. AdapterError (이미 정규화됨) → re-raise (재 wrap 금지).
            3. KeyboardInterrupt/SystemExit/MemoryError → propagate
               (BaseException — runtime critical signal 흡수 X).
            4. 그 외 Exception → AdapterError (영구적 에러).
        """
        try:
            return thunk()
        except (ConnectionError, TimeoutError) as exc:
            # 네트워크 — retry 가능.
            raise AdapterRetryError(
                f"pykrx network error in {context}: {exc}"
            ) from exc
        except AdapterError:
            # 이미 정규화된 — 재 wrap 금지 (message nested 방지).
            raise
        except (KeyboardInterrupt, SystemExit, MemoryError):
            # Runtime critical signal — 흡수 X, propagate 의무.
            raise
        except Exception as exc:
            # 그 외 — 영구적 에러로 분류 (호출자가 정책 결정).
            raise AdapterError(
                f"pykrx call failed in {context}: {exc}"
            ) from exc

    def _infer_market(self, module: Any, code: str, as_of_str: str) -> str | None:
        """KOSPI / KOSDAQ 양쪽의 ticker list 에 code 포함 여부로 시장 결정.

        pykrx 의 단일 종목 시장 lookup API 없음 → 두 list 비교.

        oracle 리뷰 M6 — `contextlib.suppress(Exception)` 제거. `_call_pykrx` 가
        ConnectionError → AdapterRetryError 정확 변환하므로 transient 실패가
        영구 실패로 잘못 분류되지 않음. 호출자 (T18) 가 retry 정책 결정.
        """
        for candidate in ("KOSPI", "KOSDAQ"):
            tickers = self._call_pykrx(
                # default-arg trick — lambda closure 의 candidate late binding 회피.
                lambda c=candidate: module.get_market_ticker_list(
                    as_of_str, market=c,
                ),
                context=f"get_market_ticker_list({as_of_str}, {candidate})",
            )
            if tickers and code in tickers:
                return candidate
        return None

    def _make_citation(
        self,
        *,
        identifier: str,
        effective_date: date,
        batch_id: UUID,
        url: str | None,
    ) -> SourceCitation:
        """SourceCitation 7-tuple 채움 — ADR-0002 D3.

        retrieved_at = 호출 시점 UTC.
        source = SourceKind.PYKRX (class const SOURCE_KIND 와 동치).
        adapter_version = class const ADAPTER_VERSION.
        """
        return SourceCitation(
            id=uuid4(),
            source=SourceKind.PYKRX,
            identifier=identifier,
            retrieved_at=datetime.now(UTC),
            effective_date=effective_date,
            adapter_version=self.ADAPTER_VERSION,
            batch_id=batch_id,
            url=url,
        )


# =============================================================================
# Validation helpers — module level (단위 테스트 가능)
# =============================================================================

def _validate_code(code: str) -> None:
    """KRX 종목코드 형식 — 6 자리 numeric."""
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        raise AdapterError(
            f"code must be 6-digit numeric string, got {code!r}"
        )


def _validate_date_range(fromdate: date, todate: date) -> None:
    if fromdate > todate:
        raise AdapterError(
            f"fromdate ({fromdate.isoformat()}) > todate "
            f"({todate.isoformat()}) — inverted range"
        )


def _index_to_date(index_value: Any) -> date:
    """DataFrame index (pandas Timestamp 또는 date 또는 str) → naive date.

    pykrx 는 보통 pd.Timestamp 반환. 일부 버전에서 datetime 또는 str. 모든 path
    를 본 helper 가 정규화.
    """
    # pd.Timestamp 의 .date() — naive date.
    if hasattr(index_value, "date") and callable(index_value.date):
        result = index_value.date()
        if isinstance(result, date):
            return result
    if isinstance(index_value, date):
        # datetime 도 date subclass — 명시 변환 위해 datetime 우선 처리.
        if isinstance(index_value, datetime):
            return index_value.date()
        return index_value
    if isinstance(index_value, str):
        # "20240102" 또는 "2024-01-02" — 두 포맷 모두 시도.
        cleaned = index_value.replace("-", "")
        if len(cleaned) == 8 and cleaned.isdigit():
            return date(
                int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]),
            )
    raise AdapterError(f"cannot interpret DataFrame index value: {index_value!r}")


def _decimal_from_value(value: Any) -> Decimal:
    """pykrx DataFrame 의 수치 컬럼 (numpy int64 / float64 / Decimal) → Decimal.

    `Decimal(float)` 의 binary 부동소수 오차 회피를 위해 `str(value)` 경유.

    oracle 리뷰 C1 — NaN/NA 검사를 `pd.isna` 로 통일:
        - numpy.float NaN, pd.NA, pd.NaT, None, np.datetime64('NaT') 모두 처리.
        - 기존 `isinstance(value, float) + math.isnan` 은 numpy.float32 등에서 침묵.
        - 변환 후 `Decimal.is_finite()` 추가 방어선 — `Decimal('nan')` silent 통과
          차단.
    """
    if pd.isna(value):
        raise AdapterError(f"NaN/NA value in pykrx DataFrame: {value!r}")
    try:
        result = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise AdapterError(
            f"cannot convert value to Decimal: {value!r}"
        ) from exc
    # `Decimal('nan')` 또는 `Decimal('inf')` 차단 (`str(value)` 가 우회 path 일 때).
    if not result.is_finite():
        raise AdapterError(
            f"non-finite Decimal from pykrx value: {value!r} -> {result}"
        )
    return result
