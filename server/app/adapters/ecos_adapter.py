"""EcosAdapter — 한국은행 ECOS(경제통계시스템) 거시지표 1차 자료.

ADR-0003 D2 (canonical) + D3 (citation) + D8 (fixture test) 구현.
MacroIndicatorRow 를 canonical Row 로 반환; DB 영속화(MacroIndicatorRecord) 와
배치(ecos_daily) 는 T64 범위.

설계 결정:

1. **DartAdapter 구조 모방** — httpx.Client 주입, `__init__`, `_resolve_api_key`
   (env), `_get_http_client`, `_call_ecos`(RESULT.CODE 분기/에러 분류),
   `_make_citation`, FetchResult[tuple[MacroIndicatorRow, ...]] 반환.

2. **URL path 기반 API** — ECOS StatisticSearch 는 query param 이 아닌 path
   segment 에 인자를 포함. `{API_KEY}/json/kr/{startRow}/{endRow}/...` 형태로
   httpx GET 호출.

3. **vintage_date = 관측 시점 근사** — ECOS API 는 공표일/개정일 필드 없이 항상
   최신값만 반환. 따라서 batch fetch 일자(observed_date, 호출자 주입) 를 "우리가
   이 값을 관측한 시점" 으로 vintage_date 에 채운다. 정확한 한국은행 공표일이 아닌
   관측 근사임을 docstring + disclaimer 대상으로 명시.

4. **RESULT.CODE 분기**:
   - INFO-200 → 빈 FetchResult (데이터없음 — DART 의 "013" 처리와 같이 raise 하지
     않고 빈 결과 반환. 정상 운영 범위 내의 "해당 기간 데이터 없음").
   - ERROR-100 → AdapterError (인증키 오류 — config 문제, 재시도 불가).
   - ERROR-500 / ERROR-600 → AdapterRetryError (서버/DB 일시 장애 — 재시도 가능).
   - 그 외 ERROR-* → AdapterError.
   - 응답 JSON 에 RESULT key 가 있으면 에러 응답.

5. **TIME 파싱 방어** — CYCLE 별로 TIME 형식이 다름(D=YYYYMMDD, M=YYYYMM,
   Q=YYYYQ, A=YYYY, S=YYYYS). 형식 불일치 시 해당 row skip + warning (절대
   AdapterError 미발생 — 연속 배치의 일부 이상 row 가 전체를 죽이지 않도록).

6. **DATA_VALUE 결측 skip** — 빈 string / null 은 결측으로 간주, skip + warning.
   절대 0 으로 가정하지 않음 (silent 오류 회피).

7. **indicator_id 규약** — `make_indicator_id(stat_code, item_code)` helper 가
   "STAT_CODE/ITEM_CODE1" 조합 생성. MacroIndicatorRecord.indicator_id 와 1:1.

관련 ADR:
- ADR-0003 D2 (canonical), D3 (citation), D8 (fixture test)
- ADR-0002 D3 (Source Citation 7-tuple)
- T62 MacroIndicatorRecord (vintage 이중 시간축)
- T64 ecos_daily 배치 (batch 통합 — 본 T63 범위 아님)
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from uuid import UUID, uuid4

import httpx

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    DataSourceAdapter,
    FetchResult,
    MacroIndicatorRow,
)
from app.models.source_citation import SourceCitation, SourceKind

__all__ = ["EcosAdapter", "make_indicator_id"]

logger = logging.getLogger(__name__)

# ECOS StatisticSearch API base URL.
# URL path 기반 — query param 이 아닌 path segment 에 인자 포함.
_ECOS_API_BASE: Final[str] = "https://ecos.bok.or.kr/api"

# fetch_statistic 1회 호출의 최대 조회 row 수. ECOS 는 startRow/endRow 로 페이징.
# 배치(T64)가 필요 시 더 큰 범위로 여러 번 호출할 수 있으나, 본 adapter 는 단일
# 범위 요청 — 호출자 책임으로 범위 조정.
_DEFAULT_START_ROW: Final[int] = 1
_DEFAULT_END_ROW: Final[int] = 10000  # ECOS 단일 응답 한계 충분히 커버

# ECOS RESULT.CODE 분류.
_RESULT_INFO_NO_DATA: Final[str] = "INFO-200"   # 해당 데이터 없음 (정상 범위).
_RESULT_ERROR_AUTH: Final[str] = "ERROR-100"    # 인증키 오류 → AdapterError.
_RESULT_ERROR_SERVER: Final[frozenset[str]] = frozenset(
    {"ERROR-500", "ERROR-600"}  # 서버/DB 장애 → AdapterRetryError.
)

# CYCLE 별 TIME 문자열 길이.
# D=YYYYMMDD(8), M=YYYYMM(6), Q=YYYYQ(5), A=YYYY(4), S=YYYYS(6).
_CYCLE_TIME_LENGTHS: Final[dict[str, int]] = {
    "D": 8,
    "M": 6,
    "Q": 5,
    "A": 4,
    "S": 6,
}

# 분기 → 시작 월 (reference_date 에서 "분기 기준 시점" = 분기 시작일을 사용).
# ECOS Q cycle TIME 끝자리 숫자가 분기 번호.
_QUARTER_START_MONTH: Final[dict[int, int]] = {1: 1, 2: 4, 3: 7, 4: 10}

# ECOS 공시 뷰어 URL — STAT_CODE + 기간으로 ECOS 통계 조회 링크 구성.
_ECOS_VIEWER_URL_TEMPLATE: Final[str] = (
    "https://ecos.bok.or.kr/#/통계검색/{stat_code}"
)


def make_indicator_id(stat_code: str, item_code: str) -> str:
    """indicator_id = STAT_CODE + "/" + ITEM_CODE1 조합.

    T62 MacroIndicatorRecord.indicator_id 와 동일 규약 — 배치(T64) 가 Row → Record
    변환 시 추가 변환 없이 1:1 복사.

    Args:
        stat_code: ECOS 통계표코드 (예: "722Y001").
        item_code: ECOS 항목코드1 (예: "0101000").

    Returns:
        "722Y001/0101000" 형태의 복합 identifier.
    """
    return f"{stat_code}/{item_code}"


class EcosAdapter(DataSourceAdapter):
    """ECOS(한국은행 경제통계시스템) wrapper — 거시지표 1차 자료.

    Attributes (class):
        SOURCE_KIND: `"ECOS"`.
        ADAPTER_VERSION: 본 adapter 코드 semver. ECOS API 응답 schema 변경 시
            major bump.

    Args (생성자):
        http_client: httpx.Client 인스턴스 (테스트 시 mock_transport 주입).
            None 이면 default Client 생성 (lifespan 단위 reuse 권장).
        api_key: ECOS API 키. None 이면 `ECOS_API_KEY` env var 사용. 둘 다 없으면
            첫 fetch 호출 시 AdapterError.
        timeout_seconds: HTTP 호출 timeout. default 30.0 (ECOS 응답이 DART 보다
            느릴 수 있음).
    """

    SOURCE_KIND = "ECOS"
    ADAPTER_VERSION = "1.0.0"

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._http_client = http_client
        self._explicit_api_key = api_key
        self._timeout = timeout_seconds
        # _owned_client = True 면 close() 시 close.
        self._owned_client = http_client is None

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    def health_check(self) -> bool:
        """API key 설정 여부만 검사 — 외부 호출 X (rate limit 절약).

        실제 도달 가능성은 첫 fetch 호출에서 검증.
        """
        try:
            self._resolve_api_key()
            return True
        except AdapterError:
            return False

    # -------------------------------------------------------------------------
    # 거시지표 fetch
    # -------------------------------------------------------------------------

    def fetch_statistic(
        self,
        *,
        stat_code: str,
        item_code: str,
        cycle: str,
        start: str,
        end: str,
        batch_id: UUID,
        observed_date: date,
    ) -> FetchResult[tuple[MacroIndicatorRow, ...]]:
        """단일 ECOS 통계표·항목 코드의 지표 시계열 fetch.

        Args:
            stat_code: ECOS 통계표코드 (예: "722Y001" — 기준금리).
            item_code: ECOS 항목코드1 (예: "0101000" — 한국은행 기준금리).
            cycle: 시계열 주기 — "D"(일), "M"(월), "Q"(분기), "A"(연), "S"(반기).
            start: 조회 시작일 (CYCLE 형식에 맞는 string, 예: "20240101" for D,
                "202401" for M).
            end: 조회 종료일 (start 와 같은 형식).
            batch_id: 배치 식별자 — citation batch_id 채움.
            observed_date: batch fetch 일자. ECOS API 가 공표일/개정일을 제공하지
                않으므로 이 값이 vintage_date 로 주입된다 (관측 시점 근사 — 정확한
                한국은행 공표일 아님, T64 disclaimer 대상).

        Returns:
            FetchResult — data = `tuple[MacroIndicatorRow, ...]`. citations =
            `(SourceCitation,)` 1 개 (INFO-200 데이터없음 시 빈 tuple 반환 — raise
            하지 않음). warnings = 결측 DATA_VALUE skip 수 + TIME 파싱 실패 수.

        Raises:
            AdapterError: 입력 검증 실패, API key 미설정, ERROR-100 (인증),
                그 외 ERROR-* (형식오류 등).
            AdapterRetryError: ERROR-500/600 (서버/DB 장애) 또는 네트워크 단절.
        """
        _validate_stat_code(stat_code)
        _validate_item_code(item_code)
        _validate_cycle(cycle)

        api_key = self._resolve_api_key()
        client = self._get_http_client()

        # URL path 구성: {base}/{API_KEY}/json/kr/{startRow}/{endRow}/
        # {STAT_CODE}/{CYCLE}/{startDate}/{endDate}/{ITEM_CODE1}
        url = (
            f"{_ECOS_API_BASE}/StatisticSearch"
            f"/{api_key}/json/kr"
            f"/{_DEFAULT_START_ROW}/{_DEFAULT_END_ROW}"
            f"/{stat_code}/{cycle}/{start}/{end}/{item_code}"
        )

        context = (
            f"fetch_statistic(stat={stat_code}, item={item_code}, "
            f"cycle={cycle}, {start}~{end})"
        )

        payload = self._call_ecos(client=client, url=url, context=context)

        # INFO-200 응답은 _call_ecos 가 None 반환 (데이터 없음 — 정상 범위).
        if payload is None:
            return FetchResult(
                data=(),
                citations=(),
                warnings=(
                    f"ECOS INFO-200: 해당 기간 데이터 없음 "
                    f"(stat={stat_code}, item={item_code}, {start}~{end})",
                ),
            )

        rows, missing_value_count, parse_failure_count = self._parse_rows(
            payload=payload,
            stat_code=stat_code,
            item_code=item_code,
            cycle=cycle,
            observed_date=observed_date,
        )

        # citation — 최신 reference_date 또는 observed_date 를 effective_date 로.
        # ECOS 는 발표일을 별도 제공하지 않으므로 observed_date 가 effective_date.
        # vintage = 관측 근사이므로 citation.effective_date 도 관측일 사용.
        effective_date = observed_date
        if rows:
            # 마지막 row (시계열 최신) 의 reference_date — ECOS 가 시간 순으로 반환.
            effective_date = rows[-1].reference_date

        citation = self._make_citation(
            stat_code=stat_code,
            item_code=item_code,
            effective_date=effective_date,
            batch_id=batch_id,
        )

        warnings: list[str] = []
        if missing_value_count > 0:
            warnings.append(
                f"ECOS {missing_value_count} rows skipped — "
                f"DATA_VALUE 결측 (빈 string / null) "
                f"(stat={stat_code}, item={item_code})"
            )
        if parse_failure_count > 0:
            warnings.append(
                f"ECOS {parse_failure_count} rows skipped — "
                f"TIME 파싱 실패 (cycle={cycle} 형식 불일치) "
                f"(stat={stat_code}, item={item_code})"
            )

        return FetchResult(
            data=rows,
            citations=(citation,),
            warnings=tuple(warnings),
        )

    # -------------------------------------------------------------------------
    # Resource cleanup
    # -------------------------------------------------------------------------

    def close(self) -> None:
        """owned httpx.Client 정리. lifespan shutdown 에서 호출 권장.

        외부에서 주입한 http_client 는 close 안 함 (호출자 책임).
        """
        if self._owned_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _resolve_api_key(self) -> str:
        """명시 인자 > env var > AdapterError."""
        if self._explicit_api_key:
            return self._explicit_api_key
        env_key = os.environ.get("ECOS_API_KEY", "").strip()
        if env_key:
            return env_key
        raise AdapterError(
            "ECOS API key not configured — set ECOS_API_KEY env var or "
            "pass api_key= to EcosAdapter()"
        )

    def _get_http_client(self) -> httpx.Client:
        """lazy http client 생성."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _call_ecos(
        self,
        *,
        client: httpx.Client,
        url: str,
        context: str,
    ) -> dict[str, Any] | None:
        """ECOS GET + RESULT.CODE 검증 + JSON parse.

        에러 분류 (DartAdapter._call_dart 정책 일관):
            - httpx.TimeoutException → AdapterRetryError.
            - httpx.ConnectError → AdapterRetryError.
            - HTTP 5xx → AdapterRetryError (서버 일시 장애).
            - HTTP 4xx → AdapterError.
            - JSON parse 실패 → AdapterError.
            - RESULT.CODE INFO-200 (데이터없음) → None 반환 (raise 아님).
            - RESULT.CODE ERROR-100 (인증) → AdapterError.
            - RESULT.CODE ERROR-500/600 (서버/DB) → AdapterRetryError.
            - RESULT.CODE 그 외 ERROR-* → AdapterError.
            - AdapterError re-raise (재 wrap 금지).

        Returns:
            정상 응답 시 StatisticSearch dict (payload["StatisticSearch"]).
            INFO-200 (데이터없음) 시 None — 호출자가 빈 FetchResult 반환.
        """
        try:
            response = client.get(url)
        except httpx.TimeoutException as exc:
            raise AdapterRetryError(
                f"ECOS timeout in {context}: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise AdapterRetryError(
                f"ECOS connection failure in {context}: {exc}"
            ) from exc
        except AdapterError:
            raise
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(
                f"ECOS call failed in {context}: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise AdapterRetryError(
                f"ECOS HTTP {response.status_code} in {context}: "
                f"{response.text[:200]}"
            )
        if response.status_code >= 400:
            raise AdapterError(
                f"ECOS HTTP {response.status_code} in {context}: "
                f"{response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"ECOS non-JSON response in {context}: {exc}"
            ) from exc

        # ECOS 에러 응답은 최상위 "RESULT" key 존재.
        if "RESULT" in payload:
            result_obj = payload["RESULT"]
            code = str(result_obj.get("CODE", "")).strip()
            message = str(result_obj.get("MESSAGE", "")).strip()

            if code == _RESULT_INFO_NO_DATA:
                # INFO-200 = 해당 기간 데이터 없음 — 정상 범위, raise 아닌 None.
                logger.debug(
                    "ECOS INFO-200 (데이터없음) in %s: %s", context, message
                )
                return None

            if code == _RESULT_ERROR_AUTH:
                raise AdapterError(
                    f"ECOS auth failure (code=ERROR-100) — ECOS_API_KEY 확인. "
                    f"context: {context}"
                )

            if code in _RESULT_ERROR_SERVER:
                raise AdapterRetryError(
                    f"ECOS server error (code={code}) in {context}: {message}"
                )

            # 그 외 ERROR-* (예: ERROR-101 형식오류) → AdapterError.
            raise AdapterError(
                f"ECOS error (code={code}) in {context}: {message}"
            )

        # 정상 응답 — "StatisticSearch" 최상위 키.
        if "StatisticSearch" not in payload:
            raise AdapterError(
                f"ECOS unexpected response schema in {context}: "
                f"'StatisticSearch' key 없음. keys: {list(payload.keys())}"
            )

        return payload["StatisticSearch"]

    def _parse_rows(
        self,
        *,
        payload: dict[str, Any],
        stat_code: str,
        item_code: str,
        cycle: str,
        observed_date: date,
    ) -> tuple[tuple[MacroIndicatorRow, ...], int, int]:
        """StatisticSearch dict → (rows, missing_value_count, parse_failure_count).

        Args:
            payload: _call_ecos 가 반환한 StatisticSearch dict.
            stat_code: ECOS 통계표코드.
            item_code: ECOS 항목코드1.
            cycle: 시계열 주기 (TIME 파싱 분기).
            observed_date: vintage_date 주입값 (관측 시점 근사).

        Returns:
            (rows, missing_value_count, parse_failure_count) 3-tuple.
            missing_value_count: DATA_VALUE 결측으로 skip 된 row 수.
            parse_failure_count: TIME 파싱 실패로 skip 된 row 수.
        """
        raw_list = payload.get("row", [])
        if not isinstance(raw_list, list):
            raise AdapterError(
                f"ECOS 'row' 가 list 아님 in "
                f"(stat={stat_code}, item={item_code}): "
                f"got {type(raw_list).__name__}"
            )

        indicator_id = make_indicator_id(stat_code, item_code)
        missing_value_count = 0
        parse_failure_count = 0
        rows: list[MacroIndicatorRow] = []

        for raw in raw_list:
            if not isinstance(raw, dict):
                # schema drift — row 자체가 dict 아님. skip + parse_failure.
                parse_failure_count += 1
                logger.warning(
                    "ECOS row 가 dict 아님 (skip): stat=%s item=%s type=%s",
                    stat_code, item_code, type(raw).__name__,
                )
                continue

            # DATA_VALUE 결측 검사 — 빈 string / null 은 결측. 절대 0 가정 금지.
            raw_value = raw.get("DATA_VALUE")
            if raw_value is None or str(raw_value).strip() == "":
                missing_value_count += 1
                logger.debug(
                    "ECOS DATA_VALUE 결측 (skip): stat=%s item=%s time=%s",
                    stat_code, item_code, raw.get("TIME"),
                )
                continue

            try:
                value = Decimal(str(raw_value).strip())
            except InvalidOperation:
                # 비숫자 DATA_VALUE — 결측 처리.
                missing_value_count += 1
                logger.warning(
                    "ECOS DATA_VALUE 비숫자 (skip): stat=%s item=%s value=%r",
                    stat_code, item_code, raw_value,
                )
                continue

            # TIME 파싱 — CYCLE 별 형식. 파싱 실패 시 해당 row skip + warning.
            time_str = str(raw.get("TIME", "")).strip()
            reference_date = _parse_ecos_time(time_str, cycle)
            if reference_date is None:
                parse_failure_count += 1
                logger.warning(
                    "ECOS TIME 파싱 실패 (skip): stat=%s item=%s "
                    "cycle=%s time=%r",
                    stat_code, item_code, cycle, time_str,
                )
                continue

            unit = str(raw.get("UNIT_NAME", "")).strip()

            rows.append(
                MacroIndicatorRow(
                    indicator_id=indicator_id,
                    reference_date=reference_date,
                    value=value,
                    unit=unit,
                    # vintage_date = 관측 시점 근사 (ECOS 가 공표일 미제공).
                    # 정확한 한국은행 공표일이 아니며, batch fetch 일자가 곧 vintage.
                    vintage_date=observed_date,
                )
            )

        return tuple(rows), missing_value_count, parse_failure_count

    def _make_citation(
        self,
        *,
        stat_code: str,
        item_code: str,
        effective_date: date,
        batch_id: UUID,
    ) -> SourceCitation:
        """SourceCitation 7-tuple — ECOS 뷰어 링크 + stat_code/item_code identifier.

        identifier = make_indicator_id(stat_code, item_code) — indicator_id 규약
        과 동일. DartAdapter._make_citation 패턴.

        **보안 — citation.url 에 API 키 금지**: fetch 호출 URL 은 path 에 API_KEY
        를 포함하므로(`/StatisticSearch/{API_KEY}/...`), 그것을 citation.url 로
        저장하면 source_citations DB 에 키가 **영구 노출**된다. 따라서 citation.url
        은 키 없는 공개 ECOS 통계 조회 페이지 링크(`_ECOS_VIEWER_URL_TEMPLATE`)만
        저장한다. 실제 호출 URL 은 citation 에 보존하지 않는다.
        """
        viewer_url = _ECOS_VIEWER_URL_TEMPLATE.format(stat_code=stat_code)
        return SourceCitation(
            id=uuid4(),
            source=SourceKind.ECOS,
            identifier=make_indicator_id(stat_code, item_code),
            retrieved_at=datetime.now(UTC),
            effective_date=effective_date,
            adapter_version=self.ADAPTER_VERSION,
            batch_id=batch_id,
            url=viewer_url,
        )


# =============================================================================
# TIME 파싱 helper — module level
# =============================================================================

def _parse_ecos_time(time_str: str, cycle: str) -> date | None:
    """ECOS TIME 문자열 → reference_date. 파싱 실패 / 형식 불일치 시 None.

    CYCLE 별 TIME 형식:
        D (일별):  YYYYMMDD (8자리) → 해당 일.
        M (월별):  YYYYMM   (6자리) → 해당 월 1일.
        Q (분기별): YYYYQ   (5자리, 끝자리 = 분기 1~4) → 분기 시작월 1일.
        A (연간):  YYYY     (4자리) → 해당 연도 1월 1일.
        S (반기):  YYYYS    (6자리, 끝자리 = 반기 1/2) → S1=1월 1일, S2=7월 1일.

    방어 정책:
        - time_str 이 비어있거나 CYCLE 의 기대 길이와 불일치 → None.
        - 연도가 합리적 범위 (1900~2100) 밖 → None (오염 방어).
        - 분기 번호가 1~4 범위 밖 → None.
        - 반기 번호가 1~2 범위 밖 → None.
        - date 생성 시 ValueError (잘못된 날짜) → None.
        - 알 수 없는 CYCLE → None.

    Args:
        time_str: ECOS 응답의 TIME 필드 값 (strip 된 문자열).
        cycle: "D" / "M" / "Q" / "A" / "S".

    Returns:
        reference_date (date) 또는 파싱 실패 시 None.
    """
    if not time_str:
        return None

    expected_len = _CYCLE_TIME_LENGTHS.get(cycle)
    if expected_len is None:
        # 알 수 없는 CYCLE — None (방어).
        return None

    if len(time_str) != expected_len:
        return None

    if not time_str[:4].isdigit():
        return None

    try:
        year = int(time_str[:4])
    except ValueError:
        return None

    # 연도 합리적 범위 — 오염 방어.
    if year < 1900 or year > 2100:
        return None

    try:
        if cycle == "D":
            # YYYYMMDD — 해당 일.
            return datetime.strptime(time_str, "%Y%m%d").date()

        if cycle == "M":
            # YYYYMM — 해당 월 1일.
            return datetime.strptime(time_str + "01", "%Y%m%d").date()

        if cycle == "Q":
            # YYYYQ (5자리, 끝자리 = 분기 번호 1~4) — 분기 시작월 1일.
            if not time_str[4].isdigit():
                return None
            quarter = int(time_str[4])
            start_month = _QUARTER_START_MONTH.get(quarter)
            if start_month is None:
                # 분기 번호가 1~4 범위 밖.
                return None
            return date(year, start_month, 1)

        if cycle == "A":
            # YYYY — 해당 연도 1월 1일.
            return date(year, 1, 1)

        if cycle == "S":
            # YYYYS (6자리, 끝자리 = 반기 번호 1/2) — S1=1월 1일, S2=7월 1일.
            if not time_str[5].isdigit():
                return None
            half = int(time_str[5])
            if half == 1:
                return date(year, 1, 1)
            if half == 2:
                return date(year, 7, 1)
            # 반기 번호가 1/2 범위 밖.
            return None

    except (ValueError, TypeError):
        # date 생성 실패 (잘못된 날짜 조합 등).
        return None

    # 도달 불가 — 방어 fallback.
    return None  # pragma: no cover


# =============================================================================
# Validation helpers — module level
# =============================================================================

def _validate_stat_code(stat_code: str) -> None:
    """ECOS 통계표코드 기본 검증 — 비어있지 않고 str 인지."""
    if not isinstance(stat_code, str):
        raise AdapterError(
            f"stat_code must be str, got {type(stat_code).__name__}"
        )
    if not stat_code.strip():
        raise AdapterError("stat_code must not be empty")


def _validate_item_code(item_code: str) -> None:
    """ECOS 항목코드1 기본 검증 — 비어있지 않고 str 인지."""
    if not isinstance(item_code, str):
        raise AdapterError(
            f"item_code must be str, got {type(item_code).__name__}"
        )
    if not item_code.strip():
        raise AdapterError("item_code must not be empty")


def _validate_cycle(cycle: str) -> None:
    """CYCLE 이 지원 범위 내인지 검증."""
    if cycle not in _CYCLE_TIME_LENGTHS:
        raise AdapterError(
            f"cycle must be one of {sorted(_CYCLE_TIME_LENGTHS)}, got {cycle!r}"
        )
