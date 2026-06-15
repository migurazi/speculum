"""FscDividendAdapter — 금융위원회 공공데이터 주식 배당 정보 1차 자료.

ADR-0035 D3 (배당 데이터 출처 = 금융위 공공데이터 GetStocDiviInfoService) +
ADR-0035 D6 (이중 PIT 축 — announced_date / effective_date) 의 adapter 구현.
M7 #2 의 코어. 배당 데이터를 `CorporateActionRecord(action_type="cash_dividend")`
로 변환해 #1 `DividendRepository.save_dividends` 의 writer 입력을 생산한다.

본 cycle 범위 = adapter 코어 + mock 테스트. **crno 종목 매핑 (KRX code ↔
법인등록번호 crno) 과 배치 cycle 은 #2 후속** — 본 adapter 는 code 와 crno 를
호출자로부터 둘 다 받는다 (DartAdapter 가 code/corp_code 둘 다 받는 패턴 일관).

설계 결정 (DartAdapter 패턴 미러 — 추측 금지, 기존 동형):

1. **httpx.Client (sync)** — codebase 정책 일관. 공공데이터포털은 외부 REST.
   타임아웃 + retry 는 호출자 책임 (배치).

2. **API key 환경변수** — `FSC_API_KEY` 우선, 공공데이터포털 관행
   `DATA_GO_KR_SERVICE_KEY` fallback. 생성자 명시 주입도 허용 (테스트). 미설정
   + 첫 fetch 호출 시 명시 `AdapterError` (DartAdapter `_resolve_api_key` 동형).

3. **공공데이터포털 resultCode 분기** (DART status code 분기 동형):
   - "00" → 정상.
   - 요청 한도 초과성 (LIMITED_NUMBER_OF_SERVICE_REQUESTS 등) → AdapterRetryError.
   - 인증 실패 (SERVICE_KEY_IS_NOT_REGISTERED_ERROR 등) → AdapterError.
   - 정상 無자료 (resultCode "00" 인데 빈 items) → 빈 결과 + warning (에러 아님).

4. **현금배당만 변환** — `stckGnrlDvdnAmt` (주당현금배당금) 가 비었거나 "0" 이면
   현금배당이 아니므로 skip. 우선주는 본 cycle 범위 외 → skip + warning (보통주만).

5. **effective_date = 배당기준일(`dvdnBasDt`) 직전 거래일** — `trading_calendar`
   주입으로 산출. 배당기준일은 그 날까지 주주명부에 등재돼야 배당 권리를 받는
   날이며, 배당락(권리락)은 그 직전 거래일에 발생한다. 캘린더에 해당 날짜
   정보가 없으면 (verified 범위 밖) warning + skip — 추측 역산 금지.

6. **announced_date = effective_date** (ADR-0035 D6) — 금융위 배당 API 는 공시일
   필드를 제공하지 않는다. 배당락일(effective_date)은 look-ahead 안전 하한이다:
   배당락이 실제로 발생한 시점에는 그 배당이 이미 공시되어 알 수 있었음이 보장되며
   (배당 결의 공시 → 배당기준일 → 배당락), announced_date 를 그보다 이르게 잡으면
   look-ahead 위험이 생긴다. announced_date = effective_date 로 두면 이중 PIT 의
   두 축 (announced ≤ as_of AND effective ≤ as_of) 이 한 점으로 수렴하여 PIT 정합을
   보존한다. (정밀 공시일은 #2 후속에서 DART 배당 결의 공시 cross-ref 로 보강.)

7. **details = JSON-scalar only** (ADR-0035 D2.2) — `per_share` 같은 금액은 문자열로
   저장 (Decimal 금지 — Fake/SQL round-trip 비대칭 방지, DividendRepository
   save_dividends contract).

8. **superseded_by = None** (ADR-0035 D2.2) — save_dividends 는 insert 시
   superseded_by=NULL 강제. supersede 는 INSERT 가 아닌 update_superseded_by.

재현성 caveat (H3/M1 deferrable — DartAdapter 와 동일 trade-off):
- `record.id` (uuid4) / `record.created_at` (wall-clock) / `citation.id` (uuid4) /
  `citation.retrieved_at` (wall-clock) 는 비결정적이다. fact-level 재현성은 본
  adapter 가 아니라 repository / snapshot layer 에서 보장한다 (DartAdapter 와
  동일 — adapter 는 변환 순수성만, 영속 ID 안정성은 writer 책임).
- `announced_date = effective_date` 는 announce-time tie-break 정밀도를 의도적
  으로 포기한 것이다 (금융위 API 가 공시일 필드를 제공하지 않음). 정밀 공시일은
  #2 후속의 DART 배당 결의 공시 cross-ref 로 보강한다.

관련 ADR:
- ADR-0035 D2.2 (details JSON-scalar, superseded_by insert NULL), D3 (출처),
  D6 (이중 PIT 축 / announced=effective)
- ADR-0002 D3 (Source Citation 7-tuple)
- ADR-0009 D1 (corporate_actions schema), D2 (action_type)
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Protocol, runtime_checkable
from uuid import UUID, uuid4

import httpx

from app.adapters.base import (
    AdapterError,
    AdapterRetryError,
    DataSourceAdapter,
    FetchResult,
)
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.pit_protocols import CorporateActionRecord
from app.services.krx_calendar import CalendarRangeError
from app.services.lineage import lineage_id_for_code

__all__ = [
    "DATA_GAP_PREFIX",
    "FscDividendAdapter",
    "TradingCalendarProtocol",
]


# 공공데이터포털 GetStocDiviInfoService — getDiviInfo (주식배당정보 조회).
_FSC_DIVI_URL: Final[str] = (
    "https://apis.data.go.kr/1160100/service/GetStocDiviInfoService/getDiviInfo"
)

# 공공데이터포털 resultCode 분류 (DART status code 분기 동형).
_RESULT_OK: Final[str] = "00"
# 요청 한도 초과성 — 재시도 권장 (AdapterRetryError). 공공데이터포털 표준 에러코드
# (LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR = "22").
_RESULT_RATE_LIMIT: Final[frozenset[str]] = frozenset({"22"})
# 인증 실패 — config 문제 (AdapterError). "30" = SERVICE_KEY_IS_NOT_REGISTERED,
# "31" = DEADLINE_HAS_EXPIRED, "32" = UNREGISTERED_IP.
_RESULT_AUTH_FAILURE: Final[frozenset[str]] = frozenset({"30", "31", "32"})

# 보통주 종류명 (stckKndNm) — 본 cycle 은 보통주만. 우선주 등은 skip + warning.
_STOCK_KND_COMMON: Final[str] = "보통주"

# 페이징 — numOfRows 상한 (공공데이터포털 관행 999). totalCount 기준 루프.
_NUM_OF_ROWS: Final[int] = 100
# 페이지 폭주 방어 상한 (무한 루프 차단 — schema drift / totalCount 오염 대비).
_MAX_PAGES: Final[int] = 1000

# 기계 판독 가능한 데이터 갭 신호 prefix (oracle H1/M3). benign skip (우선주·
# 0배당·배당기준일 부재) 과 달리, 본 prefix 가 붙은 warning 은 **재수집·격리
# (quarantine) 판단이 필요한 데이터 손실** 을 의미한다. #2 후속 배치가 본 prefix
# 로 fail-loud / quarantine 분기 가능하도록 모듈 상수로 노출 (배치가 참조).
# 현재 두 종류:
#   - 캘린더 verified 범위 밖 → 배당락일(effective_date) 산출 불가 (배당 드롭).
#   - totalCount drift 의심 → 마지막 페이지 tail 누락 가능.
DATA_GAP_PREFIX: Final[str] = "[DATA_GAP]"


@runtime_checkable
class TradingCalendarProtocol(Protocol):
    """직전 거래일 산출을 위한 거래일 캘린더 contract — 주입 대상.

    `app.services.krx_calendar.TradingCalendar` 가 구조적으로 본 Protocol 을
    만족한다 (`previous_business_day(d) -> date`, `is_business_day(d) -> bool`).
    캘린더는 외부 (KRX) 데이터이므로 adapter 가 직접 구현하지 않고 주입받는다
    (db_field_provider / batch / as_of_policy 의 TradingCalendar 주입 패턴 일관).

    `previous_business_day` 는 입력일 자체를 제외한 strictly 직전 영업일을 반환
    하고, 입력/결과가 verified 범위 밖이면 예외를 raise 한다 (TradingCalendar
    계약). adapter 는 그 예외를 잡아 해당 배당 row 를 skip + warning 처리한다
    (추측 역산 금지).
    """

    def previous_business_day(self, d: date) -> date:
        """`d` 보다 strictly 이전의 마지막 영업일."""
        ...


class FscDividendAdapter(DataSourceAdapter):
    """금융위 공공데이터 주식 배당 정보 wrapper — 현금배당 1차 자료.

    Attributes (class):
        SOURCE_KIND: `"FSC"` (SourceKind.FSC value).
        ADAPTER_VERSION: 본 adapter 코드 semver. API 응답 schema 변경 시 bump.

    Args (생성자):
        http_client: httpx.Client 인스턴스 (테스트 시 mock_transport 주입). None
            이면 default Client lazy 생성 (호출자가 close).
        api_key: 공공데이터포털 serviceKey. None 이면 `FSC_API_KEY` →
            `DATA_GO_KR_SERVICE_KEY` env var 순으로 fallback. 셋 다 없으면 첫
            fetch 호출 시 AdapterError.
        timeout_seconds: HTTP 호출 timeout. default 10.0.
    """

    SOURCE_KIND = SourceKind.FSC.value
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
        self._owned_client = http_client is None

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    def health_check(self) -> bool:
        """API key 설정 여부만 검사 — 외부 호출 X (rate limit 절약).

        실제 도달 가능성은 첫 fetch 호출에서 검증 (DartAdapter 동형).
        """
        try:
            self._resolve_api_key()
            return True
        except AdapterError:
            return False

    # -------------------------------------------------------------------------
    # 현금배당 fetch
    # -------------------------------------------------------------------------

    def fetch_cash_dividends(
        self,
        *,
        code: str,
        crno: str,
        begin_bas_dt: str,
        end_bas_dt: str,
        batch_id: UUID,
        trading_calendar: TradingCalendarProtocol,
    ) -> FetchResult[tuple[CorporateActionRecord, ...]]:
        """단일 법인(crno)의 기간 내 현금배당 → CorporateActionRecord tuple.

        getDiviInfo 를 crno + 배당기준일 기간 (beginBasDt~endBasDt) 으로 페이징
        조회. 각 item 을 현금배당 record 로 변환 (보통주만 — 우선주는 후속 cycle).

        변환 규칙 (ADR-0035 D6):
            - 현금배당 필터: `stckGnrlDvdnAmt` (주당현금배당금) 비었거나 "0" 이면
              skip (현금배당 아님).
            - 보통주만: `stckKndNm != "보통주"` 면 skip + warning.
            - `effective_date` = `trading_calendar` 로 구한 `dvdnBasDt` (배당기준일)
              직전 거래일 = 배당락일. 캘린더 범위 밖이면 warning + skip (역산 금지).
            - `announced_date` = `effective_date` (금융위 API 공시일 부재 —
              배당락일이 look-ahead 안전 하한, 이중 PIT 두 축 수렴).
            - `payment_date` = `cshDvdnPayDt` 파싱 (없으면 None).
            - `cash_amount` = Decimal(stckGnrlDvdnAmt).
            - `details` = JSON-scalar only (per_share 문자열).

        Args:
            code: KRX 종목코드 (6자리). record 의 code 채움. crno ↔ code 매핑은
                호출자 책임 (#2 후속).
            crno: 법인등록번호 (공공데이터포털 crno 파라미터).
            begin_bas_dt: 배당기준일 조회 시작 (YYYYMMDD).
            end_bas_dt: 배당기준일 조회 종료 (YYYYMMDD).
            batch_id: 일배치 식별자 — citation batch_id 채움.
            trading_calendar: 직전 거래일 산출 캘린더 (주입 — 외부 KRX 데이터).

        Returns:
            FetchResult — data = `tuple[CorporateActionRecord, ...]` (응답 순서).
            citations = record 가 있으면 동반 citation tuple (record 마다 1 개),
            없으면 빈 tuple. warnings = skip 사유 (우선주/배당기준일 부재/캘린더
            범위 밖/0배당 등). estimated_fields = frozenset() (역산 추정 marker 는
            skip 으로 처리 — 추정값 미생산).

        Raises:
            AdapterError: 입력 검증 실패, API key 미설정, resultCode 인증 실패,
                HTTP 4xx, JSON parse 실패.
            AdapterRetryError: resultCode 요청 한도 초과, HTTP 5xx, 네트워크 단절.
        """
        _validate_code(code)
        _validate_crno(crno)
        _validate_date_yyyymmdd(begin_bas_dt, "begin_bas_dt")
        _validate_date_yyyymmdd(end_bas_dt, "end_bas_dt")

        service_key = self._resolve_api_key()
        client = self._get_http_client()

        all_items: list[dict[str, Any]] = []
        page_warnings: list[str] = []
        page_no = 1
        # M3 — totalCount 종료 판정 대신 **빈/부분(full 미만) 페이지를 볼 때까지**
        # 페이징. 서버 totalCount 가 실제보다 작으면 (drift) totalCount 종료는
        # tail 을 조용히 드롭한다. full 페이지 (_NUM_OF_ROWS 만큼) 가 계속 오면
        # 끝까지 읽고, totalCount 와 누적이 어긋나면 drift 를 [DATA_GAP] 로 고지.
        while page_no <= _MAX_PAGES:
            params = {
                "serviceKey": service_key,
                "resultType": "json",
                "crno": crno,
                "beginBasDt": begin_bas_dt,
                "endBasDt": end_bas_dt,
                "pageNo": str(page_no),
                "numOfRows": str(_NUM_OF_ROWS),
            }
            context = (
                f"fetch_cash_dividends(crno={crno}, "
                f"{begin_bas_dt}~{end_bas_dt}, page={page_no})"
            )
            payload = self._call_fsc(
                client=client, params=params, context=context,
            )
            items, total_count = _parse_body(payload, context=context)
            all_items.extend(items)
            # 無자료 / 빈 페이지 → 종료 (정상, 에러 아님). schema drift 로 빈
            # 페이지가 totalCount>0 와 함께 와도 더 못 받으므로 종료 (무한루프 방어).
            if not items:
                break
            # 부분 페이지 (full 미만) → 마지막 페이지 — 종료. tail 누락 없음.
            if len(items) < _NUM_OF_ROWS:
                # full 페이지가 아닌데 totalCount 가 누적보다 큰 의심 drift —
                # 단, 부분 페이지로 끝났으니 실제 tail 은 모두 받았다. 다만
                # totalCount 가 더 작다고 주장하면 (under-count) 고지.
                if 0 < total_count < len(all_items):
                    page_warnings.append(
                        f"{DATA_GAP_PREFIX} totalCount drift 의심 — "
                        f"서버 totalCount={total_count} < 누적 수신="
                        f"{len(all_items)} (crno={crno})"
                    )
                break
            # full 페이지인데 totalCount 기준으로는 이미 종료했어야 하면 (서버
            # totalCount under-count) tail 이 더 있을 수 있음을 고지하고 계속.
            if 0 < total_count <= len(all_items):
                page_warnings.append(
                    f"{DATA_GAP_PREFIX} totalCount drift 의심 — tail 누락 "
                    f"가능 (서버 totalCount={total_count} 이나 page={page_no} 가 "
                    f"full {_NUM_OF_ROWS}건 반환 — 계속 페이징, crno={crno})"
                )
            page_no += 1
        else:
            # _MAX_PAGES guard 소진 — 페이징이 정상 종료되지 못함 (drift 폭주).
            page_warnings.append(
                f"{DATA_GAP_PREFIX} 페이징 _MAX_PAGES={_MAX_PAGES} 상한 도달 — "
                f"tail 누락 가능 (crno={crno})"
            )

        records, warnings = self._convert_items(
            items=all_items,
            code=code,
            crno=crno,
            batch_id=batch_id,
            trading_calendar=trading_calendar,
        )
        warnings = page_warnings + warnings
        # record 가 있으면 citation 동반 (FetchResult.citations 비면 빌드 게이트
        # 차단). record 0 이면 빈 citations (정상 無배당 / 전부 skip).
        citations = tuple(c for _, c in records)
        data = tuple(r for r, _ in records)
        return FetchResult(
            data=data,
            citations=citations,
            warnings=tuple(warnings),
            estimated_fields=frozenset(),
        )

    # -------------------------------------------------------------------------
    # Resource cleanup
    # -------------------------------------------------------------------------

    def close(self) -> None:
        """owned httpx.Client 정리 (DartAdapter 동형 — 주입 client 는 호출자 책임)."""
        if self._owned_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _resolve_api_key(self) -> str:
        """명시 인자 > FSC_API_KEY > DATA_GO_KR_SERVICE_KEY > AdapterError."""
        if self._explicit_api_key:
            return self._explicit_api_key
        for env_name in ("FSC_API_KEY", "DATA_GO_KR_SERVICE_KEY"):
            env_key = os.environ.get(env_name, "").strip()
            if env_key:
                return env_key
        raise AdapterError(
            "FSC API key not configured — set FSC_API_KEY or "
            "DATA_GO_KR_SERVICE_KEY env var or pass api_key= to "
            "FscDividendAdapter()"
        )

    def _get_http_client(self) -> httpx.Client:
        """lazy http client 생성 (DartAdapter 동형)."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _call_fsc(
        self,
        *,
        client: httpx.Client,
        params: dict[str, str],
        context: str,
    ) -> dict[str, Any]:
        """getDiviInfo GET + HTTP status 검증 + JSON parse + resultCode 분기.

        에러 분류 (DartAdapter `_call_dart` 동일 정책):
            - httpx.TimeoutException / ConnectError → AdapterRetryError.
            - HTTP 5xx → AdapterRetryError (서버 일시 장애).
            - HTTP 4xx → AdapterError.
            - JSON parse 실패 → AdapterError.
            - resultCode 요청 한도 초과 → AdapterRetryError.
            - resultCode 인증 실패 / 그 외 비정상 → AdapterError.
            - resultCode "00" → payload 반환.
            - AdapterError re-raise (재 wrap 금지).
        """
        try:
            response = client.get(_FSC_DIVI_URL, params=params)
        except httpx.TimeoutException as exc:
            raise AdapterRetryError(
                f"FSC timeout in {context}: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise AdapterRetryError(
                f"FSC connection failure in {context}: {exc}"
            ) from exc
        except AdapterError:
            raise
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(
                f"FSC call failed in {context}: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise AdapterRetryError(
                f"FSC {response.status_code} in {context}: {response.text[:200]}"
            )
        if response.status_code >= 400:
            raise AdapterError(
                f"FSC {response.status_code} in {context}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"FSC non-JSON response in {context}: {exc}"
            ) from exc

        result_code = _extract_result_code(payload)
        if result_code == _RESULT_OK:
            return payload
        if result_code in _RESULT_RATE_LIMIT:
            raise AdapterRetryError(
                f"FSC rate limit (resultCode={result_code}) in {context}: "
                f"{_extract_result_msg(payload)}"
            )
        if result_code in _RESULT_AUTH_FAILURE:
            raise AdapterError(
                f"FSC auth failure (resultCode={result_code}) — check "
                f"service key. context: {context}"
            )
        raise AdapterError(
            f"FSC non-OK resultCode={result_code} in {context}: "
            f"{_extract_result_msg(payload)}"
        )

    def _convert_items(
        self,
        *,
        items: list[dict[str, Any]],
        code: str,
        crno: str,
        batch_id: UUID,
        trading_calendar: TradingCalendarProtocol,
    ) -> tuple[list[tuple[CorporateActionRecord, SourceCitation]], list[str]]:
        """getDiviInfo item list → (record, citation) pairs + warnings.

        보통주 현금배당만 변환. 우선주/0배당/배당기준일 부재/캘린더 범위 밖은
        skip + warning. effective_date = dvdnBasDt 직전 거래일 (배당락),
        announced_date = effective_date (ADR-0035 D6).
        """
        lineage_id = lineage_id_for_code(code)
        pairs: list[tuple[CorporateActionRecord, SourceCitation]] = []
        warnings: list[str] = []
        # H1 — 캘린더 verified 범위 밖으로 드롭한 배당 수. >0 이면 요약 1줄을
        # [DATA_GAP] 로 추가 (배치가 fail-loud / quarantine 판단 가능하도록).
        out_of_range_drops = 0

        for item in items:
            if not isinstance(item, dict):
                warnings.append(f"FSC non-dict item skipped (crno={crno})")
                continue

            # 1. 종류 — 보통주만 (우선주 등은 후속 cycle).
            stock_knd = str(item.get("stckKndNm", "")).strip()
            if stock_knd != _STOCK_KND_COMMON:
                warnings.append(
                    f"FSC non-common stock skipped — stckKndNm="
                    f"{stock_knd!r} (보통주만 지원, code={code})"
                )
                continue

            # 2. 현금배당 필터 — 주당현금배당금 비었거나 "0" 이면 현금배당 아님.
            amount_str = str(item.get("stckGnrlDvdnAmt", "")).strip().replace(
                ",", "",
            )
            if not amount_str or amount_str == "-":
                warnings.append(
                    f"FSC empty stckGnrlDvdnAmt skipped (현금배당 아님, "
                    f"code={code})"
                )
                continue
            try:
                cash_amount = Decimal(amount_str)
            except (InvalidOperation, ValueError):
                warnings.append(
                    f"FSC non-numeric stckGnrlDvdnAmt skipped "
                    f"({amount_str!r}, code={code})"
                )
                continue
            if cash_amount == 0:
                warnings.append(
                    f"FSC zero stckGnrlDvdnAmt skipped (현금배당 아님, "
                    f"code={code})"
                )
                continue
            # 음수 배당은 무의미 — DartAdapter `_parse_share_count` 가 음수를
            # schema drift 로 거부하는 패턴과 일관 (silent 통과 금지).
            if cash_amount < 0:
                warnings.append(
                    f"FSC negative stckGnrlDvdnAmt skipped "
                    f"({amount_str!r} — 음수 배당 무의미, code={code})"
                )
                continue

            # 3. 배당기준일 — 없으면 skip (effective_date 산출 불가).
            bas_dt_raw = str(item.get("dvdnBasDt", "")).strip()
            bas_date = _parse_yyyymmdd(bas_dt_raw)
            if bas_date is None:
                warnings.append(
                    f"FSC missing/invalid dvdnBasDt skipped "
                    f"({bas_dt_raw!r}, code={code})"
                )
                continue

            # 4. effective_date = 배당기준일 직전 거래일 (배당락). 캘린더 verified
            #    범위 밖이면 skip (추측 역산 금지). H2 — CalendarRangeError 만
            #    범위밖 데이터 갭으로 처리하고, 그 외 예외 (AttributeError 등
            #    프로그래밍 버그) 는 silent 손실로 숨기지 않고 propagate.
            try:
                effective_date = trading_calendar.previous_business_day(bas_date)
            except CalendarRangeError as exc:
                # H1 — 기계 판독 prefix + 재수집 고지. benign skip 과 구별.
                out_of_range_drops += 1
                warnings.append(
                    f"{DATA_GAP_PREFIX} 캘린더 범위 밖 배당락일 산출 불가 — "
                    f"crno={crno} dvdnBasDt={bas_dt_raw} (재수집 필요) — "
                    f"{exc} (code={code})"
                )
                continue

            # 5. announced_date = effective_date (ADR-0035 D6 — 공시일 부재,
            #    배당락일이 look-ahead 안전 하한, 이중 PIT 두 축 수렴).
            announced_date = effective_date

            # 6. payment_date — 없으면 None.
            payment_date = _parse_yyyymmdd(
                str(item.get("cshDvdnPayDt", "")).strip()
            )

            # 7. 배당종류 (dvdnRcdNm) — 결산/중간/특별 등. C1: identifier 의
            #    유일성 키 일부이자 details["type"].
            dvdn_rcd_nm = str(item.get("dvdnRcdNm", "")).strip()

            # 8. details — JSON-scalar only (Decimal/date 금지, per_share 문자열).
            details: dict[str, object] = {
                "per_share": str(cash_amount),
                "type": dvdn_rcd_nm,
                "stock_knd": stock_knd,
            }

            # 9. citation — 안정 식별자 (crno|배당기준일|배당종류). C1: 같은 crno·
            #    같은 배당기준일에 결산+중간+특별 배당이 공존 가능 (다른 dvdnRcdNm)
            #    → 배당종류를 포함하지 않으면 identifier 충돌로 #1 supersede/정정
            #    매칭이 깨진다 (배당 소실 또는 stale 잔존, §2.1). dvdnRcdNm 이 빈
            #    문자열이면 여전히 충돌 가능 → 조용히 병합하지 않고 warning 으로
            #    식별 불가를 고지 (§2.1 — silent merge 금지).
            identifier = f"{crno}|{bas_dt_raw}|{dvdn_rcd_nm}"
            if not dvdn_rcd_nm:
                warnings.append(
                    f"FSC empty dvdnRcdNm — citation identifier 비유일 가능 "
                    f"(같은 crno·배당기준일 다종 배당 시 충돌 위험, "
                    f"identifier={identifier!r}, code={code})"
                )

            # X1 — sanity assert (defense-in-depth, sql_repositories
            # `assert_no_lookahead` 정신). 배당락일은 배당기준일보다 엄격히
            # 이르다 — buggy 캘린더가 미래 날짜를 반환하면 §2.4 look-ahead 가
            # 생기므로 조용히 통과시키지 않고 fail-loud.
            if not effective_date < bas_date:
                raise AdapterError(
                    f"FSC invariant 위반 — effective_date {effective_date} "
                    f"가 배당기준일 {bas_date} 보다 이르지 않음 (배당락일은 "
                    f"배당기준일보다 엄격히 이르러야 함, look-ahead 방지). "
                    f"crno={crno} dvdnBasDt={bas_dt_raw} code={code}"
                )
            if payment_date is not None and not (payment_date >= effective_date):
                raise AdapterError(
                    f"FSC invariant 위반 — payment_date {payment_date} 가 "
                    f"effective_date {effective_date} 보다 이름 (지급일은 "
                    f"배당락일 이상이어야 함). crno={crno} "
                    f"dvdnBasDt={bas_dt_raw} code={code}"
                )

            citation = self._make_citation(
                identifier=identifier,
                effective_date=announced_date,
                batch_id=batch_id,
            )

            record = CorporateActionRecord(
                id=uuid4(),
                code=code,
                code_lineage_id=lineage_id,
                action_type="cash_dividend",
                announced_date=announced_date,
                effective_date=effective_date,
                payment_date=payment_date,
                ratio=None,
                cash_amount=cash_amount,
                details=details,
                citation_id=citation.id,
                superseded_by=None,
                created_at=datetime.now(UTC),
            )
            pairs.append((record, citation))

        # H1 — out-of-range 드롭 요약 1줄 (배치가 prefix 로 집계 가능).
        if out_of_range_drops > 0:
            warnings.append(
                f"{DATA_GAP_PREFIX} out-of-range 드롭 {out_of_range_drops}건 "
                f"(crno={crno} — 캘린더 verified 범위 확장 후 재수집 필요)"
            )

        return pairs, warnings

    def _make_citation(
        self,
        *,
        identifier: str,
        effective_date: date,
        batch_id: UUID,
    ) -> SourceCitation:
        """SourceCitation — source=FSC, url=getDiviInfo 조회 URL."""
        return SourceCitation(
            id=uuid4(),
            source=SourceKind.FSC,
            identifier=identifier,
            retrieved_at=datetime.now(UTC),
            effective_date=effective_date,
            adapter_version=self.ADAPTER_VERSION,
            batch_id=batch_id,
            url=_FSC_DIVI_URL,
        )


# =============================================================================
# Validation / parsing helpers — module level
# =============================================================================

def _validate_code(code: str) -> None:
    """KRX 종목코드 — 6자리 numeric (DartAdapter `_validate_code` 동형)."""
    if not isinstance(code, str):
        raise AdapterError(f"code must be str, got {type(code).__name__}")
    if len(code) != 6 or not code.isdigit():
        raise AdapterError(
            f"code must be 6-digit numeric string, got {code[:32]!r}"
        )


def _validate_crno(crno: str) -> None:
    """법인등록번호 (crno) — 비어있지 않은 string. 형식은 공공데이터포털 의존이라
    엄격 검사 대신 minimal invariant (whitespace-only 거부)."""
    if not isinstance(crno, str):
        raise AdapterError(f"crno must be str, got {type(crno).__name__}")
    if not crno.strip():
        raise AdapterError("crno must be non-empty and not whitespace-only")


def _validate_date_yyyymmdd(value: str, field_name: str) -> None:
    """기간 파라미터 (beginBasDt / endBasDt) — 8자리 YYYYMMDD numeric + 유효 date.

    DartAdapter `_validate_date_yyyymmdd` 동형.
    """
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


def _parse_yyyymmdd(value: str) -> date | None:
    """8자리 YYYYMMDD string → date. 빈값/"-"/파싱 실패/비 8자리 → None.

    item 의 dvdnBasDt / cshDvdnPayDt 파싱용 — 방어적 (실패 시 None, raise X).
    """
    s = value.strip().replace("-", "")
    if not s or len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _extract_result_code(payload: dict[str, Any]) -> str:
    """공공데이터포털 응답에서 resultCode 추출 (response.header.resultCode).

    JSON 구조: {"response": {"header": {"resultCode": "00", ...}, "body": {...}}}.
    구조 부재 시 "" 반환 (비정상 → 호출자가 non-OK 로 분류).
    """
    response = payload.get("response")
    if not isinstance(response, dict):
        return ""
    header = response.get("header")
    if not isinstance(header, dict):
        return ""
    return str(header.get("resultCode", "")).strip()


def _extract_result_msg(payload: dict[str, Any]) -> str:
    """resultMsg 추출 (에러 메시지 — 가능한 경우만)."""
    response = payload.get("response")
    if not isinstance(response, dict):
        return ""
    header = response.get("header")
    if not isinstance(header, dict):
        return ""
    return str(header.get("resultMsg", "")).strip()


def _parse_body(
    payload: dict[str, Any], *, context: str,
) -> tuple[list[dict[str, Any]], int]:
    """response.body 에서 (items.item list, totalCount) 추출.

    `items.item` 은 단건 object / 다건 array 둘 다 처리 (공공데이터포털 관행 —
    1 건이면 array 가 아닌 단일 dict). totalCount 는 페이징 종료 판정에 사용.

    빈 items / items None 은 정상 빈 결과 ([], totalCount) — 에러 아님.

    Raises:
        AdapterError: response.body 구조 부재 (schema drift).
    """
    response = payload.get("response")
    if not isinstance(response, dict):
        raise AdapterError(
            f"FSC schema drift — missing 'response' object in {context}"
        )
    body = response.get("body")
    if not isinstance(body, dict):
        raise AdapterError(
            f"FSC schema drift — missing 'response.body' in {context}"
        )

    total_count = _parse_int(body.get("totalCount"))

    items_container = body.get("items")
    # 無자료 — items 가 "" / None / 빈 dict 인 경우 (공공데이터포털 관행).
    if not isinstance(items_container, dict):
        return [], total_count
    item = items_container.get("item")
    if item is None:
        return [], total_count
    if isinstance(item, dict):
        # 단건 object — 다건 array 로 정규화.
        return [item], total_count
    if isinstance(item, list):
        return [i for i in item if isinstance(i, dict)], total_count
    # 예상 외 타입 — 빈 결과 (방어).
    return [], total_count


def _parse_int(raw: object) -> int:
    """totalCount string → int. 파싱 실패 시 0 (페이징 종료 유도)."""
    if raw is None:
        return 0
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return 0
