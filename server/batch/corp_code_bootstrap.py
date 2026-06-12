"""DART corpCode bootstrap — corpCode.xml fetch + 디스크 캐시 → CorpCodeMapping.

CorpCodeMapping (app/services/corp_code_mapping.py) 은 순수 도메인 (in-memory
dict + 양방향 lookup) 으로, 운영 매핑 source 인 DART corpCode.xml 의 fetch/파싱은
docstring 에 "별도 cycle" 로 미뤄져 있었다. 본 모듈이 그 1 마일을 채운다:

    DART `corpCode.xml` endpoint (전체 회사 list ZIP) → 디스크 캐시 → XML 파싱
    → `{KRX 6자리 stock_code → DART 8자리 corp_code}` dict → CorpCodeMapping.

DartDailyBatch 가 `CorpCodeMapping.to_corp_code(stock_code)` 로 종목코드 →
corp_code 를 해소하므로, 운영 부트스트랩에서 본 모듈이 만든 매핑을 주입하면
하드코딩 없이 전체 상장사 재무 fetch 가 가능해진다.

설계 결정:
1. **도메인 순수성 보존** — CorpCodeMapping (순수 dict) 에 httpx/zipfile 의존을
   넣지 않고 본 배치 모듈에 격리. CorpCodeMapping.from_dict 를 재사용.
2. **디스크 캐시 + TTL** — corpCode.xml 은 일 단위로도 거의 불변 (회사 신규
   상장/폐지만 반영). 매 배치 fetch (수 MB ZIP) 는 낭비 — cache_path 에 ZIP 을
   저장하고 max_age_days 내면 재사용. DART rate limit (일 10,000 호출) 절약.
3. **api_key 정책 일관** — 명시 인자 > `DART_API_KEY` env > AdapterError
   (DartAdapter._resolve_api_key 동일).
4. **에러 응답 방어** — corpCode 정상 응답은 binary ZIP. api_key 오류 등은
   DART 가 XML (`<result><status>...`) 로 응답 → zipfile.BadZipFile →
   AdapterError 로 변환 (응답 앞부분을 메시지에 포함, 단 api_key 누출 없음).
5. **중복/비상장 격리** — corpCode.xml 은 회사당 1 row, 상장사만 stock_code
   6자리 보유 (비상장은 공백). 비상장·이상 format·중복은 skip + 통계 집계
   (절대 silent drop 0 — 카운트로 가시화). 깨끗한 dict 만 from_dict 에 전달해
   from_dict 의 1:1 invariant raise 를 회피.

관련 ADR:
- ADR-0003 D2 (DART corp_code canonical 외부)
- ADR-0002 D3 (citation identifier 와 무관 — corpCode 는 매핑 layer)
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

import httpx

from app.adapters.base import AdapterError, AdapterRetryError
from app.services.corp_code_mapping import CorpCodeMapping

__all__ = [
    "CorpCodeBootstrap",
    "CorpCodeParseResult",
    "extract_corpcode_xml",
    "parse_corpcode_xml",
]

logger = logging.getLogger(__name__)

# DART corpCode 전체 list endpoint (ZIP 반환).
_DART_CORPCODE_URL: Final[str] = "https://opendart.fss.or.kr/api/corpCode.xml"
# ZIP 내부 멤버 파일명 (DART 규약 — 고정).
_CORPCODE_XML_MEMBER: Final[str] = "CORPCODE.xml"
# 기본 캐시 TTL (일). corpCode.xml 은 거의 불변 — 1 일이면 충분.
_DEFAULT_MAX_AGE_DAYS: Final[float] = 1.0
# 기본 캐시 파일명 (cache_path 미지정 시 시스템 temp 디렉토리에 생성).
_DEFAULT_CACHE_FILENAME: Final[str] = "speculum_dart_corpcode.zip"


@dataclass(frozen=True, slots=True)
class CorpCodeParseResult:
    """corpCode.xml 파싱 결과 — 매핑 dict + 처리 통계.

    Attributes:
        stock_to_corp: KRX 6자리 → DART 8자리 (상장사·유효·중복제거 후).
        total_entries: XML 의 전체 `<list>` 항목 수 (상장 + 비상장).
        listed_count: format-valid 상장사 수 (중복 skip 전). 감사 항등식:
            `listed_count = len(stock_to_corp) + skipped_duplicate_stock
            + skipped_duplicate_corp`.
        skipped_unlisted: stock_code 가 빈 값인 비상장사 수 (정상 skip).
        skipped_invalid_format: stock_code/corp_code format 위반 skip 수.
        skipped_duplicate_stock: 같은 stock_code 중복 등장 skip 수 (첫 항목 우선).
        skipped_duplicate_corp: 같은 corp_code 가 다른 stock_code 에 중복 매핑 —
            1:1 invariant 보호를 위해 skip 한 수.
    """

    stock_to_corp: dict[str, str]
    total_entries: int
    listed_count: int
    skipped_unlisted: int
    skipped_invalid_format: int
    skipped_duplicate_stock: int
    skipped_duplicate_corp: int


def extract_corpcode_xml(zip_bytes: bytes) -> bytes:
    """DART corpCode ZIP bytes → 내부 CORPCODE.xml bytes.

    DART corpCode endpoint 정상 응답은 단일 멤버 (CORPCODE.xml) ZIP. api_key
    오류 등 비정상 응답은 ZIP 이 아닌 XML 본문 → BadZipFile.

    Args:
        zip_bytes: corpCode endpoint 응답 본문 (binary).

    Returns:
        CORPCODE.xml 의 raw bytes.

    Raises:
        AdapterError: ZIP 이 아님 (에러 응답일 가능성) 또는 CORPCODE.xml 멤버
            부재. api_key 누출 방지를 위해 본문 일부만 메시지에 포함하되,
            요청 URL (api_key 포함) 은 절대 포함하지 않는다.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            # 멤버명이 정확히 CORPCODE.xml 이 아닐 edge — 첫 .xml 멤버 fallback.
            member = (
                _CORPCODE_XML_MEMBER
                if _CORPCODE_XML_MEMBER in names
                else next((n for n in names if n.lower().endswith(".xml")), None)
            )
            if member is None:
                raise AdapterError(
                    f"DART corpCode ZIP 에 XML 멤버 없음 — members={names}"
                )
            return zf.read(member)
    except zipfile.BadZipFile as exc:
        # 정상은 ZIP. 에러 응답 (XML 본문) 이면 여기로. api_key 가 본문에 없으므로
        # 앞부분만 진단용으로 노출 (요청 URL 은 미포함 — 키 누출 0).
        preview = zip_bytes[:200].decode("utf-8", errors="replace")
        raise AdapterError(
            f"DART corpCode 응답이 ZIP 이 아님 (api_key 오류 등 가능): {preview}"
        ) from exc


def parse_corpcode_xml(xml_bytes: bytes) -> CorpCodeParseResult:
    """CORPCODE.xml bytes → CorpCodeParseResult (매핑 dict + 통계).

    XML 구조 (DART 규약):
        <result>
          <list>
            <corp_code>00126380</corp_code>
            <corp_name>삼성전자</corp_name>
            <stock_code>005930</stock_code>   # 상장사만, 비상장은 공백/빈값.
            <modify_date>20170630</modify_date>
          </list>
          ...
        </result>

    상장사 (stock_code 6자리 numeric) 만 매핑. 비상장·이상 format·중복은 skip
    하되 통계로 카운트 (silent drop 0). corpCode.xml 은 회사당 1 row 라 보통
    stock_code/corp_code 모두 유일하나, 방어적으로 중복도 격리한다.

    Args:
        xml_bytes: CORPCODE.xml raw bytes (UTF-8).

    Returns:
        CorpCodeParseResult — stock_to_corp dict + 처리 통계.

    Raises:
        AdapterError: XML 파싱 실패 (schema drift / 손상).
    """
    try:
        # 표준 ElementTree — DART 신뢰 출처 XML (외부 entity 없음). Python ET 는
        # 외부 DTD/entity 를 기본 비활성 (XXE 표면 최소).
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AdapterError(
            f"DART CORPCODE.xml 파싱 실패 (손상/schema drift): {exc}"
        ) from exc

    stock_to_corp: dict[str, str] = {}
    seen_corp: set[str] = set()
    total = 0
    listed = 0
    skipped_unlisted = 0
    skipped_invalid = 0
    skipped_dup_stock = 0
    skipped_dup_corp = 0

    for list_el in root.iter("list"):
        total += 1
        corp_code = (list_el.findtext("corp_code") or "").strip()
        stock_code = (list_el.findtext("stock_code") or "").strip()

        # 비상장사 — stock_code 빈 값 (DART 가 공백 " " 로 채우는 경우 포함). 정상.
        if not stock_code:
            skipped_unlisted += 1
            continue

        # format 검증 — CorpCodeMapping._validate_* 와 동일 정책 (6/8자리 numeric).
        if (
            len(stock_code) != 6
            or not stock_code.isdigit()
            or len(corp_code) != 8
            or not corp_code.isdigit()
        ):
            skipped_invalid += 1
            logger.debug(
                "corpCode format 위반 skip: stock_code=%r corp_code=%r",
                stock_code[:16], corp_code[:16],
            )
            continue

        listed += 1

        # stock_code 중복 — 첫 항목 우선 (corpCode.xml 은 통상 유일하나 방어).
        if stock_code in stock_to_corp:
            skipped_dup_stock += 1
            continue
        # corp_code 역중복 — 같은 회사가 두 stock_code 로 등장 (보통주/우선주가
        # 같은 corp_code 를 공유하는 edge). 1:1 invariant 보호 위해 skip.
        if corp_code in seen_corp:
            skipped_dup_corp += 1
            continue

        stock_to_corp[stock_code] = corp_code
        seen_corp.add(corp_code)

    return CorpCodeParseResult(
        stock_to_corp=stock_to_corp,
        total_entries=total,
        listed_count=listed,
        skipped_unlisted=skipped_unlisted,
        skipped_invalid_format=skipped_invalid,
        skipped_duplicate_stock=skipped_dup_stock,
        skipped_duplicate_corp=skipped_dup_corp,
    )


class CorpCodeBootstrap:
    """DART corpCode.xml fetch + 디스크 캐시 → CorpCodeMapping 부트스트랩.

    Args:
        api_key: DART OpenAPI 키. None 이면 `DART_API_KEY` env. 둘 다 없으면
            load() 시 (캐시 미스 path 에서) AdapterError.
        http_client: httpx.Client (테스트 시 mock_transport 주입). None 이면
            lazy default Client 생성 (owned — close() 시 정리).
        cache_path: ZIP 캐시 경로. None 이면 시스템 temp 디렉토리의
            `speculum_dart_corpcode.zip`.
        max_age_days: 캐시 유효 기간 (일). 캐시 mtime 이 이보다 오래되면 재fetch.
        timeout_seconds: HTTP timeout. corpCode ZIP 은 수 MB → default 60s.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_client: httpx.Client | None = None,
        cache_path: Path | str | None = None,
        max_age_days: float = _DEFAULT_MAX_AGE_DAYS,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._explicit_api_key = api_key
        self._http_client = http_client
        self._owned_client = http_client is None
        self._cache_path = (
            Path(cache_path)
            if cache_path is not None
            else Path(_default_cache_path())
        )
        self._max_age_days = max_age_days
        self._timeout = timeout_seconds

    def load(self, *, force_refresh: bool = False) -> CorpCodeMapping:
        """corpCode 매핑 로드 — 신선한 캐시 우선, 없으면 fetch + 캐시 갱신.

        Args:
            force_refresh: True 면 캐시 무시하고 무조건 재fetch (운영 강제 갱신).

        Returns:
            CorpCodeMapping — 전체 상장사 stock_code ↔ corp_code 양방향 매핑.

        Raises:
            AdapterError: api_key 미설정 (캐시 미스 시), ZIP/XML 파싱 실패.
            AdapterRetryError: 네트워크/서버 일시 장애.
        """
        zip_bytes: bytes | None = None
        if not force_refresh:
            zip_bytes = self._read_fresh_cache()

        if zip_bytes is None:
            # 캐시 미스/만료/강제 — fetch 후 캐시 갱신.
            zip_bytes = self._fetch_zip()
            self._write_cache(zip_bytes)

        xml_bytes = extract_corpcode_xml(zip_bytes)
        parsed = parse_corpcode_xml(xml_bytes)

        logger.info(
            "corpCode bootstrap: total=%d listed=%d mapped=%d "
            "(unlisted=%d invalid=%d dup_stock=%d dup_corp=%d)",
            parsed.total_entries, parsed.listed_count,
            len(parsed.stock_to_corp), parsed.skipped_unlisted,
            parsed.skipped_invalid_format, parsed.skipped_duplicate_stock,
            parsed.skipped_duplicate_corp,
        )
        # L-3: 매핑 0 종목 — XML schema drift / 잘못된 멤버 파싱 의심. 빈 매핑은
        # 모든 DART 종목을 skip 시키므로 (silent 0 처리) 경고로 가시화.
        if not parsed.stock_to_corp:
            logger.warning(
                "corpCode bootstrap: 매핑 0 종목 — XML schema drift 또는 잘못된 "
                "멤버 파싱 의심 (total_entries=%d). DART 배치가 전 종목 skip 됩니다.",
                parsed.total_entries,
            )
        # 파싱 단계에서 중복을 이미 제거했으므로 from_dict 1:1 invariant 안전.
        return CorpCodeMapping.from_dict(parsed.stock_to_corp)

    def close(self) -> None:
        """owned httpx.Client 정리 (DartAdapter.close 패턴)."""
        if self._owned_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    # =========================================================================
    # 내부 helpers
    # =========================================================================

    def _resolve_api_key(self) -> str:
        """명시 인자 > env var > AdapterError (DartAdapter 동일 정책)."""
        if self._explicit_api_key:
            return self._explicit_api_key
        env_key = os.environ.get("DART_API_KEY", "").strip()
        if env_key:
            return env_key
        raise AdapterError(
            "DART API key not configured — set DART_API_KEY env var or "
            "pass api_key= to CorpCodeBootstrap()"
        )

    def _get_http_client(self) -> httpx.Client:
        """lazy http client 생성 (DartAdapter 패턴)."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _read_fresh_cache(self) -> bytes | None:
        """캐시 파일이 존재 + TTL 내면 ZIP bytes 반환, 아니면 None.

        손상/읽기 실패는 None (재fetch 유도) — 캐시 미스로 안전 degrade.
        """
        path = self._cache_path
        try:
            if not path.is_file():
                return None
            age_seconds = time.time() - path.stat().st_mtime
            if age_seconds > self._max_age_days * 86400.0:
                logger.debug(
                    "corpCode 캐시 만료 (age=%.1fh > %.1fd): %s",
                    age_seconds / 3600.0, self._max_age_days, path,
                )
                return None
            data = path.read_bytes()
            if not data:
                return None
            return data
        except OSError as exc:
            logger.warning("corpCode 캐시 읽기 실패 (재fetch): %s — %s", path, exc)
            return None

    def _write_cache(self, zip_bytes: bytes) -> None:
        """ZIP bytes 를 캐시 파일에 원자적 저장 (temp → replace).

        쓰기 실패는 치명적이지 않음 (다음 run 재fetch) — warning 후 진행.
        """
        path = self._cache_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 원자적 교체 — 부분 쓰기 중 다른 reader 가 손상 파일 읽는 것 방지.
            tmp.write_bytes(zip_bytes)
            tmp.replace(path)
        except OSError as exc:
            logger.warning(
                "corpCode 캐시 저장 실패 (다음 run 재fetch): %s — %s", path, exc,
            )
            # M-3: replace 실패 시 남은 .tmp 정리 (디스크 부족 시 누적 방지).
            # replace 성공 시엔 이미 path 로 이동해 tmp 부재 → missing_ok 로 무해.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _fetch_zip(self) -> bytes:
        """DART corpCode endpoint GET → ZIP bytes.

        에러 분류 (DartAdapter._call_dart 정책 일관):
            - httpx.TimeoutException / ConnectError → AdapterRetryError.
            - HTTP 5xx → AdapterRetryError (서버 일시 장애).
            - HTTP 4xx → AdapterError.
            - 그 외 예외 → AdapterError.

        Note:
            반환 본문이 ZIP 인지 검증은 호출자 (extract_corpcode_xml) 책임 —
            HTTP 200 이어도 DART 가 XML 에러 본문을 줄 수 있음 (api_key 오류 등).
        """
        api_key = self._resolve_api_key()
        client = self._get_http_client()
        params = {"crtfc_key": api_key}
        try:
            response = client.get(_DART_CORPCODE_URL, params=params)
        except httpx.TimeoutException as exc:
            raise AdapterRetryError(
                f"DART corpCode timeout: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise AdapterRetryError(
                f"DART corpCode connection failure: {exc}"
            ) from exc
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"DART corpCode call failed: {exc}") from exc

        if response.status_code >= 500:
            raise AdapterRetryError(
                f"DART corpCode HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        if response.status_code >= 400:
            raise AdapterError(
                f"DART corpCode HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        return response.content


def _default_cache_path() -> str:
    """기본 캐시 경로 — 시스템 temp 디렉토리 (cross-platform).

    SPECULUM_CORPCODE_CACHE env 로 override 가능 (운영 배포 시 영속 볼륨 지정).
    """
    override = os.environ.get("SPECULUM_CORPCODE_CACHE", "").strip()
    if override:
        return override
    return str(Path(tempfile.gettempdir()) / _DEFAULT_CACHE_FILENAME)
