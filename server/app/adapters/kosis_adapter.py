"""KosisAdapter — 통계청 KOSIS(국가통계포털) 거시지표 1차 자료.

ADR-0036 D2 (canonical) + D3 (citation/vintage=observed_date 근사) +
D4 (단일 objL — AdapterError 재시도 없음) 구현.
MacroIndicatorRow 를 canonical Row 로 반환; DB 영속화(MacroIndicatorRecord) 와
배치 cycle 은 M9 후속 범위.

설계 결정:

1. **EcosAdapter 구조 모방** — httpx.Client 주입, `__init__`, `_resolve_api_key`
   (env), `_get_http_client`, `_call_kosis`(err 분기/에러 분류),
   `_make_citation`, FetchResult[tuple[MacroIndicatorRow, ...]] 반환.

2. **Query param 기반 API** — KOSIS 통계자료 조회는 `Param/statisticsParameterData.do`
   에 path segment 아닌 query parameter 로 인자를 포함. `?method=getList&apiKey=...&
   orgId=...` 형태. ECOS URL path 방식과 다른 핵심 차이. (주의: 평범한
   `statisticsData.do?method=getList` 는 동일 파라미터로도 err=20 — 메타 전용 경로.)

3. **vintage_date = 관측 시점 근사** — KOSIS API 는 공표일/개정일 필드를 별도
   제공하지 않는다(LST_CHN_DE=최종수정일이나 vintage 의미로 부적합). 따라서
   batch fetch 일자(observed_date, 호출자 주입)를 "우리가 이 값을 관측한 시점"
   으로 vintage_date 에 채운다. ADR-0036 D3.

4. **err 응답 분기** (ECOS RESULT.CODE 분기 대응):
   - 응답이 dict이고 `err` 키 있음 → 에러 응답.
   - KOSIS JSON err 코드(10/13/20/30 등)는 전부 영구 설정·요청 오류 → AdapterError.
     rate-limit 코드는 공식 명세 미확인이라 retry set 비움(보수적).
     일시 재시도는 HTTP 5xx/timeout/connect만.
   - 정상은 JSON array.

5. **PRD_DE 파싱 방어** — prd_se 별로 PRD_DE 형식이 다름(Y=YYYY, H=YYYYHH,
   Q=YYYYQQ, M=YYYYMM, D=YYYYMMDD). 파싱 실패 시 해당 row skip + warning (절대
   AdapterError 미발생 — 연속 배치의 일부 이상 row 가 전체를 죽이지 않도록).

6. **DT 결측 skip** — "-" / 빈 string 은 결측으로 간주, skip + warning.
   절대 0 으로 가정하지 않음 (silent 오류 회피). 음수 허용(거시지표 음수 정상).

7. **indicator_id 규약** — `f"kosis/{org_id}/{tbl_id}/{itm_id}"`.
   MacroIndicatorRecord.indicator_id 와 1:1.

8. **보안 — citation.url 에 apiKey 절대 미포함** — KOSIS API 는 query param 에
   apiKey 를 포함하므로 fetch URL 을 그대로 citation 에 저장하면 DB 에 키가 노출.
   citation.url 은 키 없는 공개 통계표 뷰어 링크만 저장.

관련 ADR:
- ADR-0036 D2 (canonical), D3 (citation/vintage 근사), D4 (단일 objL)
- ADR-0002 D3 (Source Citation 7-tuple)
- T62 MacroIndicatorRecord (vintage 이중 시간축)
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

__all__ = ["KosisAdapter"]

logger = logging.getLogger(__name__)

# KOSIS 통계자료(getList) API endpoint — **Param/statisticsParameterData.do**.
# Query param 기반 — path segment 아닌 query parameter 에 인자 포함.
# ⚠ 주의(2026-06-18 라이브 실측): orgId/tblId/itmId/objL1/prdSe 조합의 데이터
# 조회는 이 `Param/statisticsParameterData.do` 가 정본이다. 평범한
# `statisticsData.do?method=getList` 는 동일 파라미터로도 **err=20(필수요청변수
# 누락)** 을 반환한다(메타 getMeta 만 그 경로에서 동작) — 과거 이 경로를 써서
# 모든 KOSIS getList 가 실패했다.
_KOSIS_API_ENDPOINT: Final[str] = (
    "https://kosis.kr/openapi/Param/statisticsParameterData.do"
)

# KOSIS JSON err 코드(10/13/20/30 등)는 전부 영구 설정·요청 오류 → AdapterError.
# rate-limit 코드는 공식 명세 미확인이라 retry set 비움(보수적).
# 일시 재시도는 HTTP 5xx/timeout/connect만.
_ERR_RETRY_CODES: Final[frozenset[str]] = frozenset()

# KOSIS 통계표 뷰어 URL — orgId + tblId 로 공개 통계표 조회 링크 구성.
# apiKey 미포함 (보안).
_KOSIS_VIEWER_URL_TEMPLATE: Final[str] = (
    "https://kosis.kr/statHtml/statHtml.do"
    "?orgId={org_id}&tblId={tbl_id}"
)

# PRD_SE 별 PRD_DE 허용 길이 (KOSIS 공식 개발가이드 기준).
# Y=YYYY(4), H=YYYYHH(6, HH=01/02), Q=YYYYQQ(6, QQ=01~04),
# M=YYYYMM(6), D=YYYYMMDD(8).
# 주: "S"(반반기) 는 KOSIS 공식 명세에 없음 — 제거.
# 주: 반기(H)/분기(Q) 형식은 운영 KOSIS_API_KEY 실 응답으로 최종 확정 필요
#     (거시 핵심지표 대부분이 월(M)이라 H/Q 는 보조 수록주기).
_PRD_SE_LENGTHS: Final[dict[str, int]] = {
    "Y": 4,
    "H": 6,  # YYYYHH — HH=01(1반기)/02(2반기).
    "Q": 6,  # YYYYQQ — QQ=01(1분기)~04(4분기).
    "M": 6,
    "D": 8,
}

# 분기 → 시작 월.
_QUARTER_START_MONTH: Final[dict[int, int]] = {1: 1, 2: 4, 3: 7, 4: 10}


class KosisAdapter(DataSourceAdapter):
    """KOSIS(통계청 국가통계포털) wrapper — 거시지표 1차 자료.

    Attributes (class):
        SOURCE_KIND: `"KOSIS"`.
        ADAPTER_VERSION: 본 adapter 코드 semver. KOSIS API 응답 schema 변경 시
            major bump.

    Args (생성자):
        http_client: httpx.Client 인스턴스 (테스트 시 mock_transport 주입).
            None 이면 default Client 생성 (lifespan 단위 reuse 권장).
        api_key: KOSIS API 키. None 이면 `KOSIS_API_KEY` env var 사용. 둘 다
            없으면 첫 fetch 호출 시 AdapterError.
        timeout_seconds: HTTP 호출 timeout. default 30.0.
    """

    SOURCE_KIND = "KOSIS"
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
        org_id: str,
        tbl_id: str,
        itm_id: str,
        obj_l: str,
        prd_se: str,
        start_prd: str,
        end_prd: str,
        batch_id: UUID,
        observed_date: date,
    ) -> FetchResult[tuple[MacroIndicatorRow, ...]]:
        """단일 KOSIS 통계표·항목의 지표 시계열 fetch.

        Args:
            org_id: 기관 ID (예: "101" — 통계청).
            tbl_id: 통계표 ID (예: "DT_1B040A3").
            itm_id: 항목 ID (예: "T10").
            obj_l: 분류 코드 단일값 (ADR-0036 D4 단일 objL 정책).
                불일치 시 KOSIS err 20 → AdapterError (재시도 없음).
            prd_se: 수록주기 — "Y"(연), "H"(반기), "Q"(분기), "M"(월), "D"(일).
                "S" 는 KOSIS 공식 명세에 없으므로 지원하지 않음.
            start_prd: 시작 기간 (PRD_SE 형식에 맞는 string, 예: "202401" for M).
            end_prd: 종료 기간 (start_prd 와 같은 형식).
            batch_id: 배치 식별자 — citation batch_id 채움.
            observed_date: batch fetch 일자. KOSIS API 가 공표일 미제공이므로
                이 값이 vintage_date 로 주입된다 (관측 시점 근사, ADR-0036 D3).

        Returns:
            FetchResult — data = `tuple[MacroIndicatorRow, ...]`. citations =
            `(SourceCitation,)` 1 개 (빈 array 시도 빈 tuple 반환 — raise 아님).
            warnings = 결측 DT skip 수 + PRD_DE 파싱 실패 수.

        Raises:
            AdapterError: API key 미설정·빈 필수 파라미터·알 수 없는 prd_se,
                인증 오류, 통계표 ID/objL 오류 — KOSIS JSON err 전부 포함(영구).
            AdapterRetryError: HTTP 5xx·TimeoutException·ConnectError (일시성).
                KOSIS JSON err 는 공식 명세상 전부 영구 → AdapterError.
        """
        # 입력 검증 — 설정 오류 silent skip 방지 (EcosAdapter _validate_* 대응).
        if prd_se not in _PRD_SE_LENGTHS:
            raise AdapterError(
                f"KosisAdapter: 알 수 없는 prd_se={prd_se!r} "
                f"(허용: {sorted(_PRD_SE_LENGTHS)})"
            )
        if not org_id.strip():
            raise AdapterError("KosisAdapter: org_id 가 빈 문자열")
        if not tbl_id.strip():
            raise AdapterError("KosisAdapter: tbl_id 가 빈 문자열")
        if not itm_id.strip():
            raise AdapterError("KosisAdapter: itm_id 가 빈 문자열")

        api_key = self._resolve_api_key()
        client = self._get_http_client()

        params = {
            "method": "getList",
            "apiKey": api_key,
            "orgId": org_id,
            "tblId": tbl_id,
            "itmId": itm_id,
            "objL1": obj_l,
            "prdSe": prd_se,
            "startPrdDe": start_prd,
            "endPrdDe": end_prd,
            "format": "json",
            "jsonVD": "Y",
        }

        context = (
            f"fetch_statistic(org={org_id}, tbl={tbl_id}, itm={itm_id}, "
            f"objL={obj_l}, prd_se={prd_se}, {start_prd}~{end_prd})"
        )

        items = self._call_kosis(
            client=client,
            params=params,
            context=context,
        )

        # 빈 array → 빈 FetchResult.
        if not items:
            return FetchResult(
                data=(),
                citations=(),
                warnings=(
                    f"KOSIS 빈 array 응답: 해당 기간 데이터 없음 "
                    f"(org={org_id}, tbl={tbl_id}, itm={itm_id}, "
                    f"{start_prd}~{end_prd})",
                ),
            )

        rows, missing_count, parse_fail_count = self._parse_rows(
            items=items,
            org_id=org_id,
            tbl_id=tbl_id,
            itm_id=itm_id,
            prd_se=prd_se,
            observed_date=observed_date,
        )

        citation = self._make_citation(
            org_id=org_id,
            tbl_id=tbl_id,
            itm_id=itm_id,
            effective_date=observed_date,
            batch_id=batch_id,
        )

        warnings: list[str] = []
        if missing_count > 0:
            warnings.append(
                f"KOSIS {missing_count} rows skipped — "
                f"DT 결측 (\"-\" 또는 빈 string) "
                f"(org={org_id}, tbl={tbl_id}, itm={itm_id})"
            )
        if parse_fail_count > 0:
            warnings.append(
                f"KOSIS {parse_fail_count} rows skipped — "
                f"PRD_DE 파싱 실패 (prd_se={prd_se} 형식 불일치) "
                f"(org={org_id}, tbl={tbl_id}, itm={itm_id})"
            )

        citations = (citation,) if rows else ()
        return FetchResult(
            data=rows,
            citations=citations,
            warnings=tuple(warnings),
        )

    # -------------------------------------------------------------------------
    # Resource cleanup
    # -------------------------------------------------------------------------

    def close(self) -> None:
        """owned httpx.Client 정리. lifespan shutdown 에서 호출 권장."""
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
        env_key = os.environ.get("KOSIS_API_KEY", "").strip()
        if env_key:
            return env_key
        raise AdapterError(
            "KOSIS API key not configured — set KOSIS_API_KEY env var or "
            "pass api_key= to KosisAdapter()"
        )

    def _get_http_client(self) -> httpx.Client:
        """lazy http client 생성."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _call_kosis(
        self,
        *,
        client: httpx.Client,
        params: dict[str, str],
        context: str,
    ) -> list[dict[str, Any]]:
        """KOSIS GET + err 응답 분기 + JSON parse.

        에러 분류:
            - httpx.TimeoutException → AdapterRetryError.
            - httpx.ConnectError → AdapterRetryError.
            - HTTP 5xx → AdapterRetryError (서버 일시 장애).
            - HTTP 4xx → AdapterError.
            - JSON parse 실패 → AdapterError.
            - dict + `err` 키 있음 → KOSIS JSON err 전부 AdapterError (영구).
              KOSIS 공식 err(10/13/20/30 등)는 설정·인증·요청 오류 — 재시도 무의미.
            - 정상 응답 = JSON array → list[dict] 반환.

        Returns:
            정상 응답 시 items list (빈 list 포함).
        """
        try:
            response = client.get(_KOSIS_API_ENDPOINT, params=params)
        except httpx.TimeoutException as exc:
            raise AdapterRetryError(
                f"KOSIS timeout in {context}: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise AdapterRetryError(
                f"KOSIS connection failure in {context}: {exc}"
            ) from exc
        except AdapterError:
            raise
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(
                f"KOSIS call failed in {context}: {exc}"
            ) from exc

        if response.status_code >= 500:
            # response.text 미포함 — KOSIS 에러 응답이 apiKey query param echo 가능.
            raise AdapterRetryError(
                f"KOSIS HTTP {response.status_code} in {context}"
            )
        if response.status_code >= 400:
            raise AdapterError(
                f"KOSIS HTTP {response.status_code} in {context}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"KOSIS non-JSON response in {context}: {exc}"
            ) from exc

        # KOSIS 에러 응답 — dict 이고 `err` 키 있음.
        # KOSIS JSON err(10/13/20/30 등)는 전부 영구 설정·요청 오류 → AdapterError.
        # _ERR_RETRY_CODES 는 비어있음(보수적) — rate-limit 공식 코드 미확인.
        # ADR-0036 D4: objL 불일치(err 20), 인증(err 10/13), 기타 → 재시도 없음.
        if isinstance(payload, dict) and "err" in payload:
            err_code = str(payload.get("err", "")).strip()
            err_msg = str(payload.get("errMsg", "")).strip()
            raise AdapterError(
                f"KOSIS error (err={err_code}) in {context}: {err_msg}"
            )

        # 정상 응답 — JSON array.
        if not isinstance(payload, list):
            raise AdapterError(
                f"KOSIS unexpected response schema in {context}: "
                f"expected list or error dict, got {type(payload).__name__}. "
                f"keys: {list(payload.keys()) if isinstance(payload, dict) else '(not dict)'}"
            )

        return payload  # type: ignore[return-value]

    def _parse_rows(
        self,
        *,
        items: list[dict[str, Any]],
        org_id: str,
        tbl_id: str,
        itm_id: str,
        prd_se: str,
        observed_date: date,
    ) -> tuple[tuple[MacroIndicatorRow, ...], int, int]:
        """KOSIS items list → (rows, missing_count, parse_fail_count).

        Args:
            items: _call_kosis 가 반환한 item list.
            org_id: 기관 ID.
            tbl_id: 통계표 ID.
            itm_id: 항목 ID.
            prd_se: 수록주기 (PRD_DE 파싱 분기).
            observed_date: vintage_date 주입값 (관측 시점 근사).

        Returns:
            (rows, missing_count, parse_fail_count) 3-tuple.
        """
        indicator_id = f"kosis/{org_id}/{tbl_id}/{itm_id}"
        missing_count = 0
        parse_fail_count = 0
        rows: list[MacroIndicatorRow] = []

        for item in items:
            if not isinstance(item, dict):
                parse_fail_count += 1
                logger.warning(
                    "KOSIS item 이 dict 아님 (skip): org=%s tbl=%s itm=%s type=%s",
                    org_id, tbl_id, itm_id, type(item).__name__,
                )
                continue

            # DT 결측 검사 — "-" / 빈 string 은 결측. 음수 허용.
            raw_dt = item.get("DT")
            dt_str = str(raw_dt).strip() if raw_dt is not None else ""
            if not dt_str or dt_str == "-":
                missing_count += 1
                logger.debug(
                    "KOSIS DT 결측 (skip): org=%s tbl=%s itm=%s prd_de=%s",
                    org_id, tbl_id, itm_id, item.get("PRD_DE"),
                )
                continue

            # 천단위 콤마 제거 ("1,234.5" → "1234.5") — fsc_adapter 패턴.
            dt_str_clean = dt_str.replace(",", "")
            if not dt_str_clean or dt_str_clean == "-":
                missing_count += 1
                logger.debug(
                    "KOSIS DT 콤마 제거 후 결측 (skip): org=%s tbl=%s itm=%s prd_de=%s",
                    org_id, tbl_id, itm_id, item.get("PRD_DE"),
                )
                continue

            try:
                value = Decimal(dt_str_clean)
            except InvalidOperation:
                missing_count += 1
                logger.warning(
                    "KOSIS DT 비숫자 (skip): org=%s tbl=%s itm=%s dt=%r",
                    org_id, tbl_id, itm_id, dt_str_clean,
                )
                continue

            # PRD_DE 파싱 — prd_se 별 형식. 파싱 실패 시 해당 row skip + warning.
            prd_de_str = str(item.get("PRD_DE", "")).strip()
            reference_date = _parse_prd_de(prd_de_str, prd_se)
            if reference_date is None:
                parse_fail_count += 1
                logger.warning(
                    "KOSIS PRD_DE 파싱 실패 (skip): org=%s tbl=%s itm=%s "
                    "prd_se=%s prd_de=%r",
                    org_id, tbl_id, itm_id, prd_se, prd_de_str,
                )
                continue

            unit = str(item.get("UNIT_NM", "")).strip()

            rows.append(
                MacroIndicatorRow(
                    indicator_id=indicator_id,
                    reference_date=reference_date,
                    value=value,
                    unit=unit,
                    # vintage_date = 관측 시점 근사 (KOSIS 가 공표일 미제공).
                    vintage_date=observed_date,
                )
            )

        return tuple(rows), missing_count, parse_fail_count

    def _make_citation(
        self,
        *,
        org_id: str,
        tbl_id: str,
        itm_id: str,
        effective_date: date,
        batch_id: UUID,
    ) -> SourceCitation:
        """SourceCitation — KOSIS 뷰어 링크 + org_id/tbl_id/itm_id identifier.

        **보안 — citation.url 에 apiKey 절대 미포함**: KOSIS API 는 query param 에
        apiKey 를 포함하므로 fetch URL 을 그대로 저장하면 source_citations DB 에
        키가 영구 노출된다. citation.url 은 키 없는 공개 통계표 뷰어 링크만 저장.
        """
        viewer_url = _KOSIS_VIEWER_URL_TEMPLATE.format(
            org_id=org_id, tbl_id=tbl_id
        )
        return SourceCitation(
            id=uuid4(),
            source=SourceKind.KOSIS,
            identifier=f"{org_id}/{tbl_id}/{itm_id}",
            retrieved_at=datetime.now(UTC),
            effective_date=effective_date,
            adapter_version=self.ADAPTER_VERSION,
            batch_id=batch_id,
            url=viewer_url,
        )


# =============================================================================
# PRD_DE 파싱 helper — module level
# =============================================================================

def _parse_prd_de(prd_de: str, prd_se: str) -> date | None:
    """KOSIS PRD_DE 문자열 → reference_date. 파싱 실패 / 형식 불일치 시 None.

    PRD_SE 별 PRD_DE 형식 (KOSIS 공식 개발가이드 기준):
        Y (연간):   YYYY     (4자리) → 해당 연도 1월 1일.
        H (반기):   YYYYHH   (6자리, HH=01/02) → 01=1월 1일, 02=7월 1일.
                     예: "202401"=2024 1반기, "202402"=2024 2반기.
        Q (분기):   YYYYQQ   (6자리, QQ=01~04) → 분기 시작월 1일.
                     예: "202401"=Q1(1월), "202402"=Q2(4월),
                         "202403"=Q3(7월), "202404"=Q4(10월).
        M (월별):   YYYYMM   (6자리) → 해당 월 1일.
        D (일별):   YYYYMMDD (8자리) → 해당 일.
        "S" 는 KOSIS 공식 명세에 없으므로 지원하지 않음.

    주: 반기(H)/분기(Q) 형식은 운영 KOSIS_API_KEY 실 응답으로 최종 확정 필요
        (거시 핵심지표 대부분이 월(M)이라 H/Q 는 보조 수록주기).

    방어 정책:
        - prd_de 가 비어있거나 PRD_SE 의 허용 길이와 불일치 → None.
        - 전체 자리가 isdigit 아님(알파 혼입 — "2024H1" 등) → None.
        - 연도가 합리적 범위 (1900~2100) 밖 → None (오염 방어).
        - 분기 번호가 01~04 범위 밖 → None.
        - 반기 번호가 01~02 범위 밖 → None.
        - date 생성 시 ValueError (잘못된 날짜) → None.
        - 알 수 없는 PRD_SE → None.

    Args:
        prd_de: KOSIS 응답의 PRD_DE 필드 값 (strip 된 문자열).
        prd_se: "Y" / "H" / "Q" / "M" / "D".

    Returns:
        reference_date (date) 또는 파싱 실패 시 None.
    """
    if not prd_de:
        return None

    expected_len = _PRD_SE_LENGTHS.get(prd_se)
    if expected_len is None:
        return None

    if len(prd_de) != expected_len:
        return None

    # 전체 자리 숫자 검증 — "2024H1" 같은 알파 혼입 거부.
    if not prd_de.isdigit():
        return None

    try:
        year = int(prd_de[:4])
    except ValueError:
        return None

    # 연도 합리적 범위 — 오염 방어.
    if year < 1900 or year > 2100:
        return None

    try:
        if prd_se == "Y":
            # YYYY → 해당 연도 1월 1일.
            return date(year, 1, 1)

        if prd_se == "H":
            # YYYYHH (6자리, HH=01/02) → 01=1월 1일, 02=7월 1일.
            hh = prd_de[4:6]
            if hh == "01":
                return date(year, 1, 1)
            if hh == "02":
                return date(year, 7, 1)
            return None

        if prd_se == "Q":
            # YYYYQQ (6자리, QQ=01~04) → 분기 시작월 1일.
            qq = int(prd_de[4:6])
            start_month = _QUARTER_START_MONTH.get(qq)
            if start_month is None:
                return None
            return date(year, start_month, 1)

        if prd_se == "M":
            # YYYYMM → 해당 월 1일.
            return datetime.strptime(prd_de + "01", "%Y%m%d").date()

        if prd_se == "D":
            # YYYYMMDD → 해당 일.
            return datetime.strptime(prd_de, "%Y%m%d").date()

    except (ValueError, TypeError):
        return None

    return None  # pragma: no cover
