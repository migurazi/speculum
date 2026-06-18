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
from datetime import UTC, date, datetime, timedelta
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

__all__ = ["DartAdapter", "DisclosureItem", "TreasurySharesResult"]


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
# "013" = 조회된 데이터 없음. 재무제표/자사주 경로에서는 `_call_dart` 가
# AdapterError 로 raise (정상 — 그 분기 데이터가 반드시 있어야 함). 그러나
# list.json (공시목록) 은 공시가 없는 종목이 정상 case 이므로, fetch_disclosure_list
# 가 이 에러를 잡아 빈 결과로 변환 (`_call_dart` 시그니처/동작은 변경 없음).
_STATUS_NO_DATA: Final[str] = "013"

# 재무제표 구분(sj_div)별 채택 우선순위 — fnlttSinglAcntAll 응답은 한 보고서의
# BS/IS/CIS/CF/SCE 5 개 재무제표를 한 list 로 반환한다. 같은 canonical 계정이 복수
# 재무제표에 나타날 수 있어(예: net_income=ifrs-full_ProfitLoss 는 손익(IS)·포괄손익
# (CIS)·현금흐름(CF) 모두에 동일 값으로 등장) 단일 fetch 내 중복이 생긴다. PIT 무결성
# 가드("1 fetch = canonical 계정당 1 row")를 만족하도록 **재무제표 우선순위로 dedup**:
# 잔액·손익 정본인 BS > IS > CIS > CF 순으로 첫 1건만 채택(값은 statement 간 동일).
_STATEMENT_PRIORITY: Final[dict[str, int]] = {"BS": 0, "IS": 1, "CIS": 2, "CF": 3}
# 자본변동표(SCE) 는 같은 계정(자본총계·당기순이익 등)을 기초/변동/기말 컬럼마다
# 반복(period-flow)해 중복의 주원인이며, 기말 잔액은 BS·순이익은 IS 에 이미 있으므로
# **적재 대상에서 제외**한다(ADR-0005 — 적재 1차 재무제표는 BS/IS/CIS/CF, 자본변동표 외).
_EXCLUDED_STATEMENTS: Final[frozenset[str]] = frozenset({"SCE"})
# 알 수 없는/누락 sj_div 는 known 재무제표보다 후순위(dedup 시 known 우선)이나 적재는
# 유지 — 단일 statement mock·향후 신규 sj_div 에 대한 graceful 동작.
_UNKNOWN_STATEMENT_PRIORITY: Final[int] = len(_STATEMENT_PRIORITY)

# stockTotqySttus.json (주식의 총수 현황) 의 `se` (구분) 값 — 자사주 추출 규칙.
# KRX shares_outstanding (market_caps) 가 보통주 기준이므로 일관성을 위해 "보통주"
# 행의 `tesstk_co` 를 1순위 사용. "보통주" 행 부재 시 "합계" fallback.
_SE_COMMON_STOCK: Final[str] = "보통주"
_SE_TOTAL: Final[str] = "합계"


@dataclass(frozen=True, slots=True)
class TreasurySharesResult:
    """`fetch_treasury_shares` 의 data payload — 자사주 + 발행주식총수.

    DART `stockTotqySttus.json` (주식의 총수 현황) 의 "보통주" 행에서 추출.

    Attributes:
        shares_treasury: 보통주 자기주식수 (`tesstk_co`). DART 가 "-"/빈값/
            누락 또는 파싱 실패 시 None (결측 — 절대 0 으로 가정 금지, silent
            오류 회피). DbFieldProvider 의 `shares_treasury` 해소가 None 이면
            정식 N/A.
        shares_issued_total: 보통주 기말 발행주식총수 (`istc_totqy`). 결측 시
            None. (market_caps.shares_outstanding 의 cross-check 용 — 본 cycle
            은 자사주 영구화가 주목적이라 검증/대사는 별도 cycle.)
        effective_date: 공시 효력일. ADR-0012 D6 — rcept_no 도출 정밀 공시일
            (precise) 또는 신고기한 보수값 fallback. citation.effective_date 와
            동일하나, 호출자 (dart_daily) 가 effective_date_precise 와 함께 record
            로 영속화하기 위해 data payload 로도 노출.
        effective_date_precise: True 면 effective_date 가 rcept_no 도출 실 공시일
            (정밀), False 면 신고기한 보수값.
    """

    shares_treasury: int | None
    shares_issued_total: int | None
    effective_date: date
    effective_date_precise: bool


@dataclass(frozen=True, slots=True)
class DisclosureItem:
    """`fetch_disclosure_list` 의 단일 공시 항목 — 제목·접수일·원문링크만.

    ADR-0026 D1 (표시 범위 하드 제약) — DART `list.json` row 에서 **제목·접수일자·
    DART 원문 viewer URL** 3 필드만 추출. 본문/요약/자체 분류라벨 0 (§2.7 경계).

    Attributes:
        report_name: 공시 제목 (`report_nm`). 회사가 낸 외부 사실 = EXTERNAL_QUOTE
            scope (ADR-0007 D4.5) — forbidden_words 검사 대상 아님. Speculum 이
            생성/가공하지 않은 DART 원문.
        rcept_date: 접수일자 (`rcept_dt`, YYYYMMDD 파싱). PIT 필터 (ADR-0026 D4 —
            `rcept_date <= as_of`) 의 비교 키.
        rcept_no: DART 접수번호. 원문 viewer URL 생성에 사용.
        dart_url: DART 공시 viewer URL (`_DART_VIEWER_URL_TEMPLATE`). 클릭 시
            DART 페이지로 이탈 — 본문 텍스트 자체 표시 X (ADR-0026 D1).
    """

    report_name: str
    rcept_date: date
    rcept_no: str
    dart_url: str


@dataclass(frozen=True, slots=True)
class _ParsedResponse:
    """`_parse_response` 의 named return — oracle 리뷰 M4 의 의미 분리.

    Attributes:
        rows: 변환된 FinancialStatementRow tuple.
        skipped_row_count: thstrm_amount 결측/비숫자로 skip 된 row 수.
        unmapped_account_count: IFRS taxonomy ID 가 미매핑인 row 수
            (canonical key 는 `unmapped:` prefix 로 보존).
        effective_date_max: 보고서 effective_date. rcept_no 도출 성공 시 정밀
            공시일 (ADR-0012 D6), 실패 시 신고기한 보수값 (ADR-0012 D1).
        effective_date_precise: effective_date_max 가 rcept_no 도출 실 공시일이면
            True, 신고기한 보수값 fallback 이면 False.
        rcept_no: 첫 valid row 의 DART 접수번호. 모든 row 가 같은 보고서
            가정.
    """

    rows: tuple[FinancialStatementRow, ...]
    skipped_row_count: int
    unmapped_account_count: int
    effective_date_max: date
    effective_date_precise: bool
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
        # `effective_date` 는 ADR-0012 D6 — DART 응답 rcept_no 앞 8자리
        # (YYYYMMDD = 접수일자 = 공시일) 에서 직접 도출한 정밀 공시일. 도출 성공
        # 시 `estimated_fields` 비움 (정밀 — marker 불요), 실패 시 자본시장법
        # 제160조 신고기한 보수값 + `estimated_fields={"effective_date"}` fallback
        # (100% 공시 완료 보장 시점이라 look-ahead 0). precise 여부는
        # effective_date_precise 컬럼으로 영속화 (record/ORM).
        estimated_fields = (
            frozenset()
            if parsed.effective_date_precise
            else frozenset({"effective_date"})
        )
        return FetchResult(
            data=rows,
            citations=(citation,),
            warnings=tuple(warnings),
            estimated_fields=estimated_fields,
        )

    # -------------------------------------------------------------------------
    # 자사주 fetch — stockTotqySttus.json (주식의 총수 현황)
    # -------------------------------------------------------------------------

    def fetch_treasury_shares(
        self,
        *,
        code: str,
        corp_code: str,
        fiscal_year: int,
        fiscal_quarter: int,
        batch_id: UUID,
    ) -> FetchResult[TreasurySharesResult]:
        """단일 회사·연도·분기의 자사주 (보통주 자기주식수) fetch.

        DART `stockTotqySttus.json` (주식의 총수 현황) 의 "보통주" 행에서
        `tesstk_co` (자기주식수) 를 추출. KRX shares_outstanding (market_caps)
        가 보통주 기준이므로 일관성을 위해 "보통주" 행을 1순위 사용. "보통주"
        행 부재 시 "합계" 행 fallback. "-"/빈값/파싱 실패는 결측 (None) 처리 —
        절대 0 으로 가정하지 않음 (silent 오류 회피).

        `fetch_financial_statement` 패턴 미러:
            - 같은 `_call_dart` 재사용 (status code 분기 동일 — "020" retry,
              "010"/"011" auth, 그 외 비정상 AdapterError).
            - effective_date = `_disclosure_deadline` (ADR-0012 D1 보수 신고기한,
              financials 와 동일). estimated_fields = {"effective_date"}.

        Args:
            code: KRX 종목코드 (6자리). canonical schema 의 code 와 동치 (citation
                의미상 사용처는 호출자).
            corp_code: DART 8자리 회사코드. 호출자 책임 매핑.
            fiscal_year: 사업연도 (e.g., 2023).
            fiscal_quarter: 1~4 (4=연간/사업보고서).
            batch_id: 일배치 식별자 — citation batch_id 채움.

        Returns:
            FetchResult — data = `TreasurySharesResult` (자사주 + 발행주식총수,
            결측 시 각 None). citations = `(SourceCitation,)` 1 개. warnings =
            "보통주" 행 부재 fallback / 결측 안내. estimated_fields =
            `{"effective_date"}` (financials 와 동일 — 신고기한 추정 marker).

        Raises:
            AdapterError: 입력 검증 실패, API key 미설정, DART status code
                non-rate-limit 에러 (status "013" 데이터없음 포함 — `_call_dart`
                가 raise), 빈 응답.
            AdapterRetryError: DART status "020" (요청 제한) 또는 네트워크 단절.
        """
        _validate_code(code)
        _validate_corp_code(corp_code)
        _validate_fiscal_period(fiscal_year, fiscal_quarter)

        params = {
            "crtfc_key": self._resolve_api_key(),
            "corp_code": corp_code,
            "bsns_year": str(fiscal_year),
            "reprt_code": _REPRT_CODE_BY_QUARTER[fiscal_quarter],
        }

        client = self._get_http_client()
        response_json = self._call_dart(
            client=client,
            endpoint="stockTotqySttus.json",
            params=params,
            context=(
                f"fetch_treasury_shares(corp={corp_code}, "
                f"{fiscal_year}Q{fiscal_quarter})"
            ),
        )

        treasury, issued_total, rcept_no, warnings = self._parse_treasury_response(
            response_json=response_json,
            code=code,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
        )

        # ADR-0012 D6 — financials 와 동일. rcept_no 앞 8자리 (YYYYMMDD =
        # 접수일자) 에서 정밀 공시일 직접 도출. 유효 시 effective_date = 실 공시일
        # (precise), 무효/부재 (rcept_no placeholder fallback 포함) 시 자본시장법
        # 제160조 신고기한 보수값 (look-ahead 0).
        precise_date = _rcept_date(rcept_no)
        if precise_date is not None:
            effective_date = precise_date
            effective_date_precise = True
        else:
            effective_date = _disclosure_deadline(fiscal_year, fiscal_quarter)
            effective_date_precise = False
        citation = self._make_citation(
            identifier=rcept_no,
            effective_date=effective_date,
            batch_id=batch_id,
        )
        # precise 면 marker 불요, fallback 이면 {"effective_date"} (financials 동일).
        estimated_fields = (
            frozenset()
            if effective_date_precise
            else frozenset({"effective_date"})
        )
        return FetchResult(
            data=TreasurySharesResult(
                shares_treasury=treasury,
                shares_issued_total=issued_total,
                effective_date=effective_date,
                effective_date_precise=effective_date_precise,
            ),
            citations=(citation,),
            warnings=tuple(warnings),
            estimated_fields=estimated_fields,
        )

    def _parse_treasury_response(
        self,
        *,
        response_json: dict[str, Any],
        code: str,
        fiscal_year: int,
        fiscal_quarter: int,
    ) -> tuple[int | None, int | None, str, list[str]]:
        """stockTotqySttus.json list → (자사주, 발행주식총수, rcept_no, warnings).

        자사주 추출 규칙:
            1. `se == "보통주"` 행의 `tesstk_co` (자기주식수) 1순위.
            2. "보통주" 행 부재 시 `se == "합계"` 행 fallback (warning).
            3. "-"/빈값/파싱 실패는 None (결측 — 0 가정 금지).

        Raises:
            AdapterError: list 비어있음 / schema drift (필수 필드 누락).
        """
        raw_list = response_json.get("list", [])
        if not isinstance(raw_list, list) or not raw_list:
            raise AdapterError(
                f"DART empty 'list' for stockTotqySttus code={code} "
                f"{fiscal_year}Q{fiscal_quarter}"
            )

        # se → row 매핑 (마지막 등장 우선 — 통상 1 회). rcept_no 는 첫 row 채택.
        rows_by_se: dict[str, dict[str, Any]] = {}
        rcept_no = ""
        for raw in raw_list:
            if not isinstance(raw, dict):
                raise AdapterError(
                    f"DART stockTotqySttus schema drift — non-dict row for "
                    f"code={code} {fiscal_year}Q{fiscal_quarter}"
                )
            try:
                se = str(raw["se"]).strip()
            except (KeyError, TypeError) as exc:
                raise AdapterError(
                    f"DART stockTotqySttus schema drift — missing 'se' field: "
                    f"{exc}. row keys: {list(raw.keys())}"
                ) from exc
            rows_by_se[se] = raw
            if not rcept_no:
                rcept_no_raw = raw.get("rcept_no", "")
                if rcept_no_raw:
                    rcept_no = str(rcept_no_raw)

        warnings: list[str] = []
        # 1. "보통주" 1순위, 부재 시 "합계" fallback.
        target_row = rows_by_se.get(_SE_COMMON_STOCK)
        if target_row is None:
            target_row = rows_by_se.get(_SE_TOTAL)
            if target_row is not None:
                warnings.append(
                    f"DART stockTotqySttus '{_SE_COMMON_STOCK}' row 부재 — "
                    f"'{_SE_TOTAL}' 행 fallback (code={code})"
                )
        if target_row is None:
            # 보통주/합계 모두 부재 — 자사주 결측 (None). 우선주만 있는 등 edge.
            warnings.append(
                f"DART stockTotqySttus '{_SE_COMMON_STOCK}'/'{_SE_TOTAL}' 행 "
                f"모두 부재 — 자사주 결측 (code={code})"
            )
            treasury: int | None = None
            issued_total: int | None = None
        else:
            treasury = _parse_share_count(target_row.get("tesstk_co"))
            issued_total = _parse_share_count(target_row.get("istc_totqy"))
            if treasury is None:
                warnings.append(
                    f"DART stockTotqySttus tesstk_co 결측/파싱 실패 "
                    f"('-'/빈값/비숫자) — 자사주 N/A (code={code})"
                )

        # rcept_no 가 빈 string 인 edge — financials 와 달리 자사주 결측이 정상
        # 운영 case 이므로 fail 하지 않고 빈 identifier 로 citation 생성하지 않기
        # 위해 fallback. DART 응답에 rcept_no 가 없으면 corp_code 기반 placeholder.
        if not rcept_no:
            rcept_no = f"stockTotqySttus:{code}:{fiscal_year}Q{fiscal_quarter}"
            warnings.append(
                f"DART stockTotqySttus rcept_no 부재 — placeholder identifier "
                f"사용 (code={code})"
            )

        return treasury, issued_total, rcept_no, warnings

    # -------------------------------------------------------------------------
    # 공시목록 fetch — list.json (ADR-0026)
    # -------------------------------------------------------------------------

    def fetch_disclosure_list(
        self,
        *,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_count: int = 100,
    ) -> FetchResult[tuple[DisclosureItem, ...]]:
        """단일 회사의 기간 내 공시목록 on-demand fetch — ADR-0026.

        DART `list.json` (공시검색) 을 corp_code + 기간 (bgn_de~end_de) 으로 조회.
        batch/영속화 X — Stock Detail 진입 1 종목의 실시간 조회 (ADR-0026 D3).
        각 row 에서 **제목 (`report_nm`) + 접수일자 (`rcept_dt`) + 접수번호
        (`rcept_no`)** 3 필드만 추출 (ADR-0026 D1). 본문/기타 필드 무시.

        status "013" (조회된 데이터 없음) 은 공시가 없는 종목 = 정상 빈 결과.
        재무제표/자사주 경로는 `_call_dart` 가 "013" 을 AdapterError 로 raise 하나,
        공시목록은 부재가 에러가 아니므로 본 메서드가 그 AdapterError 를 잡아 빈
        list 로 변환 (`_call_dart` 시그니처/동작은 변경 없음 — 회귀 0).

        Args:
            corp_code: DART 8자리 회사코드. 호출자 책임 매핑.
            bgn_de: 조회 시작일 (YYYYMMDD).
            end_de: 조회 종료일 (YYYYMMDD).
            page_count: 페이지당 row 수 (DART 최대 100). default 100.

        Returns:
            FetchResult — data = `tuple[DisclosureItem, ...]` (rcept_date 역순 =
            최신순, ADR-0026 D3). citations = `(SourceCitation,)` 대표 1 개 (가장
            최신 공시 기준) 또는 빈 결과 시 빈 tuple. warnings = rcept_dt 파싱
            실패로 skip 한 row 안내.

        Raises:
            AdapterError: 입력 검증 실패, API key 미설정, DART status code
                non-rate-limit·non-013 에러 (auth 등).
            AdapterRetryError: DART status "020" (요청 제한) 또는 네트워크 단절.
        """
        _validate_corp_code(corp_code)
        _validate_date_yyyymmdd(bgn_de, "bgn_de")
        _validate_date_yyyymmdd(end_de, "end_de")
        if not isinstance(page_count, int) or not (1 <= page_count <= 100):
            raise AdapterError(
                f"page_count must be 1..100, got {page_count!r}"
            )

        params = {
            "crtfc_key": self._resolve_api_key(),
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_count": str(page_count),
        }

        client = self._get_http_client()
        context = (
            f"fetch_disclosure_list(corp={corp_code}, {bgn_de}~{end_de})"
        )
        try:
            response_json = self._call_dart(
                client=client,
                endpoint="list.json",
                params=params,
                context=context,
            )
        except AdapterError as exc:
            # status "013" (조회된 데이터 없음) 은 공시 미존재 종목 = 정상 빈 결과.
            # `_call_dart` 는 non-OK status 를 `"DART non-OK status={status} in
            # {context}: ..."` 메시지로 raise 한다 (시그니처/동작 변경 금지 제약).
            # 그 메시지에서 status="013" 토큰을 식별해 빈 결과로 변환 — 다른 status
            # (auth 등) 은 그대로 re-raise (회귀 0).
            if _is_no_data_error(exc):
                return FetchResult(data=(), citations=(), warnings=())
            raise

        items, skipped = _parse_disclosure_list(response_json)

        warnings: list[str] = []
        if skipped > 0:
            warnings.append(
                f"DART disclosure {skipped} rows skipped — "
                f"rcept_dt missing or non-date (YYYYMMDD parse 실패)"
            )

        if not items:
            # status "000" 이나 list 가 비거나 모든 row parse 실패한 edge.
            return FetchResult(data=(), citations=(), warnings=tuple(warnings))

        # 최신순 (rcept_date 역순) 정렬 — ADR-0026 D3 (사실 정렬 허용). 동일
        # 접수일 내 순서는 DART 응답 순서 보존 (stable sort).
        ordered = tuple(
            sorted(items, key=lambda d: d.rcept_date, reverse=True)
        )
        # 대표 citation — 가장 최신 공시 (정렬 후 첫 항목) 기준. 공시 1 종목
        # 조회라 fact 단위 citation 대신 retrieval 대표 1 개.
        latest = ordered[0]
        citation = self._make_citation(
            identifier=latest.rcept_no,
            effective_date=latest.rcept_date,
            batch_id=uuid4(),
        )
        return FetchResult(
            data=ordered,
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

        # 1차: row 데이터 수집 + 첫 valid row 의 rcept_no 채택. effective_date 는
        # rcept_no 도출 (ADR-0012 D6) 결과에 의존하므로 row 생성은 2차로 미룸.
        # 분기 내 모든 row 가 같은 보고서 = 같은 rcept_no = 같은 effective_date.
        skipped_row_count = 0
        rcept_no = ""
        # canonical_account → 후보 row list [(statement_priority, value, rcept_no,
        # currency)]. 같은 canonical 이 복수 재무제표에 등장(예: net_income 은 손익
        # (IS)·포괄손익(CIS)·현금흐름(CF) 동일 값)하면 **재무제표 우선순위(BS>IS>CIS>
        # CF)가 가장 높은 statement 의 row 만 채택** = cross-statement dedup(값 동일,
        # 무손실). 단 **같은 statement 내 같은 canonical 이 복수**면(같은 우선순위 2건+)
        # 그대로 **모두 보존** → 진짜 데이터 모순(동일 계정·동일 표·다른 값)을 저장
        # 시점 PIT 무결성 가드가 잡도록 한다(silent 삼킴 금지). 자본변동표(SCE)는
        # period-flow 반복원이라 적재 제외(_EXCLUDED_STATEMENTS).
        candidates: dict[str, list[tuple[int, Decimal, str, str]]] = {}

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

            # 적재 제외 재무제표(자본변동표 SCE) — period-flow 계정 반복이 중복원.
            sj_div = str(raw.get("sj_div") or "").strip()
            if sj_div in _EXCLUDED_STATEMENTS:
                continue

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
            priority = _STATEMENT_PRIORITY.get(sj_div, _UNKNOWN_STATEMENT_PRIORITY)
            candidates.setdefault(canonical_account, []).append(
                (priority, value, str(rcept_no_raw), str(currency))
            )
            # rcept_no 는 보통 한 보고서 단위로 동일 — 첫 valid row 채택.
            if not rcept_no:
                rcept_no = str(rcept_no_raw)

        # 미매핑 계정 수 — 고유 canonical 기준 (canonical key 가 "unmapped:..."
        # prefix). row 는 보존.
        unmapped_account_count = sum(1 for acc in candidates if is_unmapped(acc))
        # 각 canonical 의 최우선 statement(min priority) row 만 채택 — 같은 우선순위
        # (= 같은 statement) 가 복수면 모두 보존(저장 가드가 모순 탐지). cross-statement
        # 동일 계정(다른 우선순위)은 승자 1건만 → 단일 fetch 중복 0(정상 경로).
        parsed_rows: list[tuple[str, Decimal, str, str]] = []
        for acc, cands in candidates.items():
            min_priority = min(c[0] for c in cands)
            parsed_rows.extend(
                (acc, value, row_rcept_no, currency)
                for (priority, value, row_rcept_no, currency) in cands
                if priority == min_priority
            )

        if not parsed_rows:
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

        # ADR-0012 D6 — rcept_no 앞 8자리 (YYYYMMDD = 접수일자) 에서 정밀 공시일
        # 직접 도출. 유효 시 effective_date = 실 공시일 (precise=True), 무효/부재
        # 시 자본시장법 제160조 신고기한 보수값 fallback (precise=False) — 어느
        # 경우든 look-ahead 0 (보수값은 100% 공시 완료 보장 시점, ADR-0012 D1).
        precise_date = _rcept_date(rcept_no)
        if precise_date is not None:
            effective_date = precise_date
            effective_date_precise = True
        else:
            effective_date = _disclosure_deadline(fiscal_year, fiscal_quarter)
            effective_date_precise = False

        rows = tuple(
            FinancialStatementRow(
                code=code,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                effective_date=effective_date,
                account=account,
                value=value,
                unit="krw",
                ifrs_type=ifrs_type,
                rcept_no=row_rcept_no,
                currency=currency,
                effective_date_precise=effective_date_precise,
            )
            for (account, value, row_rcept_no, currency) in parsed_rows
        )

        return _ParsedResponse(
            rows=rows,
            skipped_row_count=skipped_row_count,
            unmapped_account_count=unmapped_account_count,
            effective_date_max=effective_date,
            effective_date_precise=effective_date_precise,
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


def _validate_date_yyyymmdd(value: str, field_name: str) -> None:
    """DART 기간 파라미터 (bgn_de / end_de) — 8자리 YYYYMMDD numeric + 유효 date."""
    if not isinstance(value, str):
        raise AdapterError(
            f"{field_name} must be str, got {type(value).__name__}"
        )
    if len(value) != 8 or not value.isdigit():
        raise AdapterError(
            f"{field_name} must be 8-digit YYYYMMDD, got {value[:32]!r}"
        )
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise AdapterError(
            f"{field_name} is not a valid YYYYMMDD date: {value!r}"
        ) from exc


def _is_no_data_error(exc: AdapterError) -> bool:
    """`_call_dart` 가 raise 한 AdapterError 가 DART status "013" (데이터 없음) 인지.

    `_call_dart` 의 non-OK status 메시지 형식 (`"...status={status}..."`) 에서
    status="013" 토큰을 식별. list.json 전용 — 공시 미존재 종목을 빈 결과로
    변환하기 위함 (ADR-0026). 다른 status (auth 등) 는 False → 호출자가 re-raise.
    """
    return f"status={_STATUS_NO_DATA}" in str(exc)


def _parse_disclosure_list(
    response_json: dict[str, Any],
) -> tuple[list[DisclosureItem], int]:
    """DART list.json 의 `list` → (DisclosureItem list, skipped row 수).

    각 row 에서 `report_nm` / `rcept_dt` / `rcept_no` 만 추출 (ADR-0026 D1). 본문/
    기타 필드 무시. `rcept_dt` (8자리 YYYYMMDD) 파싱은 방어적 — 실패 시 그 row
    skip + 카운트 (전체 fetch 실패시키지 않음). 정렬은 호출자 책임.

    `list` 키 부재/빈 list 는 빈 결과 ([], 0) — status "000" 이나 row 0 인 edge
    (DART 가 "013" 대신 빈 list 로 응답하는 경우도 정상 빈 결과로 처리).
    """
    raw_list = response_json.get("list", [])
    if not isinstance(raw_list, list) or not raw_list:
        return [], 0

    items: list[DisclosureItem] = []
    skipped = 0
    for raw in raw_list:
        if not isinstance(raw, dict):
            # schema drift — 방어적 skip (전체 실패 회피).
            skipped += 1
            continue
        rcept_dt_raw = raw.get("rcept_dt")
        rcept_no_raw = raw.get("rcept_no")
        report_nm_raw = raw.get("report_nm")
        # rcept_no / report_nm 부재는 viewer URL·제목 생성 불가 → skip.
        if not rcept_dt_raw or not rcept_no_raw or report_nm_raw is None:
            skipped += 1
            continue
        # rcept_dt (8자리 YYYYMMDD) 직접 파싱 — `_rcept_date` 는 14자리 rcept_no
        # 용이라 부적합. 파싱 실패 시 그 row skip + warning (방어적).
        try:
            rcept_date = datetime.strptime(
                str(rcept_dt_raw).strip(), "%Y%m%d"
            ).date()
        except ValueError:
            skipped += 1
            continue

        rcept_no = str(rcept_no_raw).strip()
        dart_url = _DART_VIEWER_URL_TEMPLATE.format(rcept_no=rcept_no)
        items.append(
            DisclosureItem(
                report_name=str(report_nm_raw).strip(),
                rcept_date=rcept_date,
                rcept_no=rcept_no,
                dart_url=dart_url,
            )
        )
    return items, skipped


def _parse_share_count(raw: object) -> int | None:
    """DART 주식수 string ("5,969,782,550") → int. 결측/파싱 실패 시 None.

    DART 의 주식수는 콤마 포함 문자열. "-"/빈값은 결측. 음수/소수는 schema
    drift 로 보고 None (자사주는 비음 정수). 절대 0 으로 가정하지 않음 (결측은
    None — silent 오류 회피).
    """
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        value = int(s)
    except (ValueError, TypeError):
        return None
    if value < 0:
        return None
    return value


def _rcept_date(rcept_no: str) -> date | None:
    """DART 접수번호 (rcept_no) → 정밀 공시일 (effective_date). 도출 실패 시 None.

    ADR-0012 D6 — DART rcept_no 는 14자리 = 앞 8자리 (YYYYMMDD = 접수일자 =
    공시일) + 6자리 일련번호. fnlttSinglAcntAll.json / stockTotqySttus.json 응답
    각 row 의 필수 필드로 이미 존재 (citation identifier + 뷰어 URL 에 사용). 별도
    list.json fetch 없이 정확한 공시일을 직접 도출 (rate-limit 무관, 더 정확).

    방어 (schema 예상 외 시 None — 호출자가 보수 신고기한 fallback):
        - 14자리 숫자가 아니면 None (placeholder identifier, 빈값, drift 포함).
        - 앞 8자리가 유효 date (YYYYMMDD) 가 아니면 None.
        - 연도가 합리적 범위 (2000~2100) 밖이면 None (오염 방어).

    Args:
        rcept_no: DART 접수번호 문자열.

    Returns:
        정밀 공시일 (date) 또는 도출 실패 시 None. None 이면 호출자가 자본시장법
        제160조 신고기한 보수값 + effective_date_precise=False 로 안전 fallback
        (look-ahead 위험 0).
    """
    if not isinstance(rcept_no, str):
        return None
    if len(rcept_no) != 14 or not rcept_no.isdigit():
        return None
    try:
        parsed = datetime.strptime(rcept_no[:8], "%Y%m%d").date()
    except ValueError:
        return None
    # 연도 합리적 범위 — _validate_fiscal_period 와 동일 가드 (오염 방어).
    if parsed.year < 2000 or parsed.year > 2100:
        return None
    return parsed


def _validate_fiscal_period(year: int, quarter: int) -> None:
    if not isinstance(year, int) or year < 2000 or year > 2100:
        raise AdapterError(f"fiscal_year out of range: {year}")
    if quarter not in _REPRT_CODE_BY_QUARTER:
        raise AdapterError(
            f"fiscal_quarter must be 1..4, got {quarter}"
        )


def _disclosure_deadline(year: int, quarter: int) -> date:
    """공시 신고기한 — ADR-0012 D1 의 effective_date 산출 보수 정책.

    자본시장법 제160조 의 신고기한을 effective_date 의 lower bound 로 적용.
    분기 종료 시점에는 보고서가 미공시 상태이므로 PIT 의미 위반 (Momus M0
    review V1). 신고기한이면 100% 회사 공시 완료 보장 → silent look-ahead
    bias 0.

    매트릭스 (자본시장법 제160조, 캘린더 +N일 산술):
        Q1 (1분기 보고서): 분기 종료 + 45일 → year-05-15
        Q2 (반기 보고서):  분기 종료 + 45일 → year-08-14
        Q3 (3분기 보고서): 분기 종료 + 45일 → year-11-14
        Q4 (사업 보고서):  사업연도 종료 + 90일 → (year+1)-03-31
                          (윤년 다음해 시 -03-30, e.g. 2023 Q4 → 2024-03-30)

    잔여 추정 — 실제 일찍 공시한 회사의 데이터는 사용 못 함 (false-negative,
    보수적 측면). 정확한 rcept_dt 는 DART list.json endpoint 별도 fetch
    (ADR-0012 D6 의 M1+ work-order).

    Returns:
        해당 분기의 신고기한 마지막 일자. PIT Enforcer 가 본 값을 effective_date
        로 사용 — `record.effective_date <= as_of` 비교 의미 보존.
    """
    quarter_end = {
        1: date(year, 3, 31),
        2: date(year, 6, 30),
        3: date(year, 9, 30),
        4: date(year, 12, 31),
    }[quarter]
    # Q4 = 사업보고서 (90일), 나머지 = 분기보고서 (45일).
    lag_days = 90 if quarter == 4 else 45
    return quarter_end + timedelta(days=lag_days)
