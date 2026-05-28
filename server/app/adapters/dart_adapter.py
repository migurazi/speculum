"""DartAdapter — DART OpenAPI 1차 자료 (재무제표).

ADR-0003 D2 (canonical) + ADR-0005 (K-IFRS CFS/OFS) + ADR-0002 D3 (citation)
implementation. M0 cycle scope: fetch_financial_statement 만 — corporate
action 은 별도 cycle.

설계 결정:

1. **httpx.Client (sync)** — codebase 정책 일관. DART OpenAPI 는 외부 REST.
   타임아웃 + retry 는 호출자 책임 (T19 일배치).

2. **API key 환경변수 `DART_API_KEY`** — 생성자에 명시 주입도 허용 (테스트).
   미설정 + 첫 fetch 호출 시 명시 `AdapterError`.

3. **corp_code 호출자 책임** — DART 의 8자리 corp_code 와 KRX 6자리 stock code
   의 매핑은 별도 layer (T19 일배치에서 stocks_master 와 corp_code mapping
   table 활용). 본 adapter 는 둘 다 받음.

4. **DART status code 분기**:
   - "000" → 정상.
   - "020" (요청 제한) → AdapterRetryError.
   - "010" / "011" (인증 실패) → AdapterError (config 문제).
   - 그 외 → AdapterError.

5. **CFS / OFS 분리 fetch** — `fs_div=CFS` 와 `fs_div=OFS` 별도 호출. ADR-0005
   D1 의 default 선택 logic 은 service layer 책임. adapter 는 어느 하나만
   fetch.

6. **dart_account_mapper 적용 + 미매핑 row 도 보존** — silent drop X.
   FetchResult.warnings 에 미매핑 계정 수 명시.

관련 ADR:
- ADR-0003 D2 (canonical), D3 (citation), D6 (rate limit)
- ADR-0005 D1 (CFS/OFS 선택)
- ADR-0002 D3 (Source Citation)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid4

import httpx

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    DataSourceAdapter,
    FetchResult,
    FinancialStatementRow,
    IfrsType,
)
from app.adapters.dart_account_mapper import is_unmapped, map_ifrs_account
from app.models.source_citation import SourceCitation, SourceKind

__all__ = ["DartAdapter"]


# DART OpenAPI base URL (2026 기준 — opendart.fss.or.kr).
_DART_API_BASE: Final[str] = "https://opendart.fss.or.kr/api"
# DART 공시 viewer URL — citation 의 url 채움.
_DART_VIEWER_URL_TEMPLATE: Final[str] = (
    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
)

# 분기 → DART reprt_code 매핑 (사업보고서/반기/분기보고서).
_REPRT_CODE_BY_QUARTER: Final[dict[int, str]] = {
    1: "11013",  # 1분기보고서
    2: "11012",  # 반기보고서
    3: "11014",  # 3분기보고서
    4: "11011",  # 사업보고서 (연간 = 4분기).
}

# IfrsType → DART fs_div 매핑.
_FS_DIV_BY_IFRS_TYPE: Final[dict[IfrsType, str]] = {
    IfrsType.CFS: "CFS",
    IfrsType.OFS: "OFS",
}

# DART status code 분류.
_STATUS_OK: Final[str] = "000"
_STATUS_RATE_LIMIT: Final[str] = "020"
_STATUS_AUTH_FAILURE: Final[frozenset[str]] = frozenset({"010", "011"})


@dataclass(frozen=True, slots=True)
class _ParsedResponse:
    """`_parse_response` 의 named return — oracle 리뷰 M4 의 의미 분리.

    Attributes:
        rows: 변환된 FinancialStatementRow tuple.
        skipped_row_count: thstrm_amount 결측/비숫자로 skip 된 row 수.
        unmapped_account_count: IFRS taxonomy ID 가 미매핑인 row 수
            (canonical key 는 `unmapped:` prefix 로 보존).
        effective_date_max: 분기말 date (보고서 효력일 근사). M3 backlog.
        rcept_no: 첫 valid row 의 DART 접수번호. 모든 row 가 같은 보고서
            가정.
    """

    rows: tuple[FinancialStatementRow, ...]
    skipped_row_count: int
    unmapped_account_count: int
    effective_date_max: date
    rcept_no: str


class DartAdapter(DataSourceAdapter):
    """DART OpenAPI wrapper — 재무제표 1차 자료.

    Attributes (class):
        SOURCE_KIND: `"DART"`.
        ADAPTER_VERSION: 본 adapter 코드 semver. DART API 응답 schema 변경 시
            major bump.

    Args (생성자):
        http_client: httpx.Client 인스턴스 (테스트 시 mock_transport 주입).
            None 이면 default Client 생성 (lifespan 단위 reuse 권장 — 호출자
            가 close).
        api_key: DART OpenAPI 키. None 이면 `DART_API_KEY` env var 사용. 둘 다
            없으면 첫 fetch 호출 시 AdapterError.
        timeout_seconds: HTTP 호출 timeout. default 10.0.
    """

    SOURCE_KIND = "DART"
    ADAPTER_VERSION = "1.0.0"

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._http_client = http_client
        self._explicit_api_key = api_key
        self._timeout = timeout_seconds
        # _owned_client = True 면 dispose() 시 close.
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
    # 재무제표 fetch
    # -------------------------------------------------------------------------

    def fetch_financial_statement(
        self,
        *,
        code: str,
        corp_code: str,
        fiscal_year: int,
        fiscal_quarter: int,
        ifrs_type: IfrsType,
        batch_id: UUID,
    ) -> FetchResult[tuple[FinancialStatementRow, ...]]:
        """단일 회사·연도·분기·CFS|OFS 의 전체 재무제표 fetch.

        Args:
            code: KRX 종목코드 (6자리). canonical schema 의 code 채움.
            corp_code: DART 8자리 회사코드. 호출자 책임 매핑.
            fiscal_year: 사업연도 (e.g., 2023).
            fiscal_quarter: 1~4 (4=연간/사업보고서).
            ifrs_type: CFS (연결) 또는 OFS (별도).
            batch_id: 일배치 식별자 — citation batch_id 채움.

        Returns:
            FetchResult — data = `tuple[FinancialStatementRow, ...]` (정렬 X —
            DART 응답 순서). citations = `(SourceCitation,)` 1 개 (한 fetch =
            한 retrieval). warnings = 미매핑 계정 수 안내.

        Raises:
            AdapterError: 입력 검증 실패, API key 미설정, DART status code
                non-rate-limit 에러, 빈 응답.
            AdapterRetryError: DART status "020" (요청 제한) 또는 네트워크 단절.
        """
        _validate_code(code)
        _validate_corp_code(corp_code)
        _validate_fiscal_period(fiscal_year, fiscal_quarter)
        if ifrs_type not in _FS_DIV_BY_IFRS_TYPE:
            raise AdapterError(f"unsupported ifrs_type: {ifrs_type!r}")

        params = {
            "crtfc_key": self._resolve_api_key(),
            "corp_code": corp_code,
            "bsns_year": str(fiscal_year),
            "reprt_code": _REPRT_CODE_BY_QUARTER[fiscal_quarter],
            "fs_div": _FS_DIV_BY_IFRS_TYPE[ifrs_type],
        }

        client = self._get_http_client()
        response_json = self._call_dart(
            client=client,
            endpoint="fnlttSinglAcntAll.json",
            params=params,
            context=(
                f"fetch_financial_statement(corp={corp_code}, "
                f"{fiscal_year}Q{fiscal_quarter}, {ifrs_type.value})"
            ),
        )

        parsed = self._parse_response(
            response_json=response_json,
            code=code,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            ifrs_type=ifrs_type,
        )
        rows = parsed.rows
        rcept_no = parsed.rcept_no
        effective_date_max = parsed.effective_date_max

        citation = self._make_citation(
            identifier=rcept_no,
            effective_date=effective_date_max,
            batch_id=batch_id,
        )
        # oracle 리뷰 M4 — 결측 row skip 과 미매핑 계정 분리.
        warnings: list[str] = []
        if parsed.skipped_row_count > 0:
            warnings.append(
                f"DART {parsed.skipped_row_count} rows skipped — "
                f"thstrm_amount missing ('' or '-') or non-numeric"
            )
        if parsed.unmapped_account_count > 0:
            warnings.append(
                f"DART {parsed.unmapped_account_count} unmapped IFRS accounts — "
                f"dart_account_mapper extension required"
            )
        # oracle 리뷰 M3 — `effective_date` 가 분기말 근사 (실제 rcept_dt 가 아닌).
        # 분기말은 실제 공시일 (보통 분기말 + 45~60 일) 보다 빠른 시점이라
        # PIT 시 silently 미존재 데이터 노출 위험. T18 ConflictDetector / PIT
        # enforcer 가 본 필드를 estimated 로 인식하여 추가 검증 가능. 정확한
        # rcept_dt 는 DART list endpoint 별도 fetch (별도 cycle backlog).
        return FetchResult(
            data=rows,
            citations=(citation,),
            warnings=tuple(warnings),
            estimated_fields=frozenset({"effective_date"}),
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
        env_key = os.environ.get("DART_API_KEY", "").strip()
        if env_key:
            return env_key
        raise AdapterError(
            "DART API key not configured — set DART_API_KEY env var or "
            "pass api_key= to DartAdapter()"
        )

    def _get_http_client(self) -> httpx.Client:
        """lazy http client 생성."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _call_dart(
        self,
        *,
        client: httpx.Client,
        endpoint: str,
        params: dict[str, str],
        context: str,
    ) -> dict[str, Any]:
        """DART GET + status code 검증 + JSON parse.

        에러 분류 (T15 PykrxAdapter 동일 정책):
            - httpx.ConnectError / ReadTimeout / PoolTimeout → AdapterRetryError.
            - HTTP 5xx → AdapterRetryError (서버 일시 장애).
            - HTTP 4xx (auth 등) → AdapterError.
            - JSON parse 실패 → AdapterError.
            - DART status "020" → AdapterRetryError.
            - DART status 그 외 비정상 → AdapterError.
            - BaseException (KeyboardInterrupt 등) propagate.
            - AdapterError re-raise (재 wrap 금지).
        """
        url = f"{_DART_API_BASE}/{endpoint}"
        try:
            response = client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise AdapterRetryError(
                f"DART timeout in {context}: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise AdapterRetryError(
                f"DART connection failure in {context}: {exc}"
            ) from exc
        except AdapterError:
            raise
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(
                f"DART call failed in {context}: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise AdapterRetryError(
                f"DART {response.status_code} in {context}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise AdapterError(
                f"DART {response.status_code} in {context}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"DART non-JSON response in {context}: {exc}"
            ) from exc

        status = payload.get("status", "")
        if status == _STATUS_OK:
            return payload
        if status == _STATUS_RATE_LIMIT:
            raise AdapterRetryError(
                f"DART rate limit (status=020) in {context}: "
                f"{payload.get('message', '')}"
            )
        if status in _STATUS_AUTH_FAILURE:
            raise AdapterError(
                f"DART auth failure (status={status}) — check API key. "
                f"context: {context}"
            )
        raise AdapterError(
            f"DART non-OK status={status} in {context}: "
            f"{payload.get('message', '')}"
        )

    def _parse_response(
        self,
        *,
        response_json: dict[str, Any],
        code: str,
        fiscal_year: int,
        fiscal_quarter: int,
        ifrs_type: IfrsType,
    ) -> _ParsedResponse:
        """DART JSON list → FinancialStatementRow tuple + skipped/unmapped 카운트.

        oracle 리뷰 M4 — `skipped_row_count` (결측/parse 실패) 와
        `unmapped_account_count` (미매핑 IFRS 계정 — row 는 보존) 분리.

        Raises:
            AdapterError: list 비어있음, schema drift, 변환 후 0 valid row.
        """
        raw_list = response_json.get("list", [])
        if not isinstance(raw_list, list) or not raw_list:
            raise AdapterError(
                f"DART empty 'list' for code={code} "
                f"{fiscal_year}Q{fiscal_quarter} {ifrs_type.value}"
            )

        # 분기말 date 로 일괄 설정 (M3: 분기 내 모든 row 가 같은 effective_date).
        effective_date = _fiscal_quarter_end(fiscal_year, fiscal_quarter)

        rows: list[FinancialStatementRow] = []
        skipped_row_count = 0
        unmapped_account_count = 0
        rcept_no = ""

        for raw in raw_list:
            # 필수 필드 검증 — schema drift fail-fast.
            try:
                ifrs_account_id = raw["account_id"]
                thstrm_amount_raw = raw["thstrm_amount"]
                rcept_no_raw = raw["rcept_no"]
                currency = raw.get("currency", "KRW") or "KRW"
            except (KeyError, TypeError) as exc:
                raise AdapterError(
                    f"DART schema drift — missing field in row: {exc}. "
                    f"row keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}"
                ) from exc

            # thstrm_amount 가 string ("455905830000000") 또는 빈 string.
            # 빈 string / "-" 는 결측 — skipped_row_count.
            amount_str = str(thstrm_amount_raw).strip().replace(",", "")
            if not amount_str or amount_str == "-":
                skipped_row_count += 1
                continue
            try:
                value = Decimal(amount_str)
            except Exception:  # noqa: BLE001
                # 비숫자 — schema drift. row skip.
                skipped_row_count += 1
                continue

            canonical_account = map_ifrs_account(ifrs_account_id)
            if is_unmapped(canonical_account):
                # 미매핑 — row 는 보존 (canonical key 가 "unmapped:..." prefix).
                unmapped_account_count += 1

            rows.append(
                FinancialStatementRow(
                    code=code,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    effective_date=effective_date,
                    account=canonical_account,
                    value=value,
                    unit="krw",
                    ifrs_type=ifrs_type,
                    rcept_no=str(rcept_no_raw),
                    currency=str(currency),
                )
            )
            # rcept_no 는 보통 한 보고서 단위로 동일 — 첫 valid row 채택.
            if not rcept_no:
                rcept_no = str(rcept_no_raw)

        if not rows:
            raise AdapterError(
                f"DART parsed 0 valid rows for code={code} "
                f"{fiscal_year}Q{fiscal_quarter} {ifrs_type.value}"
            )
        # oracle 리뷰 L8 — 모든 row 의 rcept_no 가 빈 string 인 edge case 방어.
        if not rcept_no:
            raise AdapterError(
                f"DART rcept_no empty for all rows — schema drift? "
                f"code={code} {fiscal_year}Q{fiscal_quarter}"
            )

        return _ParsedResponse(
            rows=tuple(rows),
            skipped_row_count=skipped_row_count,
            unmapped_account_count=unmapped_account_count,
            effective_date_max=effective_date,
            rcept_no=rcept_no,
        )

    def _make_citation(
        self,
        *,
        identifier: str,
        effective_date: date,
        batch_id: UUID,
    ) -> SourceCitation:
        """SourceCitation 7-tuple — DART 공시 viewer URL 포함."""
        url = _DART_VIEWER_URL_TEMPLATE.format(rcept_no=identifier)
        return SourceCitation(
            id=uuid4(),
            source=SourceKind.DART,
            identifier=identifier,
            retrieved_at=datetime.now(UTC),
            effective_date=effective_date,
            adapter_version=self.ADAPTER_VERSION,
            batch_id=batch_id,
            url=url,
        )


# =============================================================================
# Validation / helpers — module level
# =============================================================================

def _validate_code(code: str) -> None:
    """KRX 종목코드 형식 — T15 / T14 와 동일."""
    if not isinstance(code, str):
        raise AdapterError(f"code must be str, got {type(code).__name__}")
    if len(code) != 6 or not code.isdigit():
        raise AdapterError(
            f"code must be 6-digit numeric string, got {code[:32]!r}"
        )


def _validate_corp_code(corp_code: str) -> None:
    """DART corp_code — 8자리 numeric."""
    if not isinstance(corp_code, str):
        raise AdapterError(
            f"corp_code must be str, got {type(corp_code).__name__}"
        )
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise AdapterError(
            f"corp_code must be 8-digit numeric, got {corp_code[:32]!r}"
        )


def _validate_fiscal_period(year: int, quarter: int) -> None:
    if not isinstance(year, int) or year < 2000 or year > 2100:
        raise AdapterError(f"fiscal_year out of range: {year}")
    if quarter not in _REPRT_CODE_BY_QUARTER:
        raise AdapterError(
            f"fiscal_quarter must be 1..4, got {quarter}"
        )


def _fiscal_quarter_end(year: int, quarter: int) -> date:
    """분기말 date — 본 cycle MVP 의 effective_date 근사.

    정확한 rcept_dt 는 DART list endpoint 별도 fetch (M0+ backlog). 본 함수는
    분기말 (3-31, 6-30, 9-30, 12-31) 로 근사 — PIT 의미상 가장 보수적.
    """
    # quarter=1 → 3-31, Q=2 → 6-30, Q=3 → 9-30, Q=4 → 12-31.
    month_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[quarter]
    return date(year, month_day[0], month_day[1])
