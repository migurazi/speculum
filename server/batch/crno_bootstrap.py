"""crno bootstrap — CorpCodeMapping + DART company.json → CrnoMapping.

CrnoMapping(app/services/crno_mapping.py)은 순수 도메인(in-memory dict + 단방향
lookup)으로, 운영 매핑 source 인 종목코드 → crno(법인등록번호) 구축은 외부 fetch
(DART company.json)가 필요하다. 본 모듈이 그 부트스트랩을 채운다:

    CorpCodeMapping(종목코드 → corp_code) 의 각 corp_code 에 대해 DART
    `company.json` 의 `jurir_no`(crno) 를 fetch → `{종목코드 → crno}` dict →
    CrnoMapping. 결과 dict 는 디스크 JSON 캐시(TTL).

M7 #2 배당 배치(후속 cycle)가 `CrnoMapping.to_crno(stock_code)` 로 종목코드 →
crno 를 해소하면, FscDividendAdapter.fetch_cash_dividends(crno=...) 로 전체 상장사
배당 fetch 가 하드코딩 없이 가능해진다.

설계 결정 (CorpCodeBootstrap 패턴 미러 — 일관성):

1. **도메인 순수성 보존** — CrnoMapping(순수 dict)에 httpx/IO 의존을 넣지 않고
   본 배치 모듈에 격리. CrnoMapping.from_dict 재사용.

2. **N회 호출 + 결과 dict 캐시** — corpCode.xml(ZIP 1회)과 달리 company.json 은
   corp_code 별 1회씩 N회 호출이라, ZIP 이 아니라 **산출 dict(JSON)** 을 캐시한다.
   crno 는 법인등록번호로 사실상 불변(법인 재설립 시에만 변경) → TTL 길게(기본
   30일). 매 배치 N회 호출(DART 일 10,000 한도)을 피한다.

3. **api_key 정책 일관** — adapter 미주입 시 DartAdapter(api_key) lazy 생성.
   명시 인자 > `DART_API_KEY` env(DartAdapter._resolve_api_key) > 첫 호출 시
   AdapterError.

4. **per-stock 격리 + 통계(silent drop 0)** — 한 종목의 company.json 실패
   (AdapterError — 폐지/이상 corp_code 등)는 skip + 카운트하고 나머지 진행.
   단 **AdapterRetryError(rate limit / 네트워크)는 re-raise** — 일시 장애 중
   부분 crno 매핑을 캐시에 굳히면 영구 결손이 되므로 전체 빌드를 중단(다음 run
   재시도). jurir_no 부재/format 위반도 skip + 카운트(절대 silent 0).

5. **에러 분류 순서 주의** — AdapterRetryError 는 AdapterError 의 하위이므로
   except 절에서 **AdapterRetryError 를 먼저** 잡아 re-raise 한 뒤 AdapterError
   를 skip 처리한다(순서 뒤바뀌면 rate limit 도 skip 돼 조용히 결손).

관련 ADR:
- ADR-0035 D3 (배당 출처 = 금융위, crno 입력 키)
- ADR-0002 D3 (citation 무관 — crno 는 매핑 layer)
- ADR-0003 D2 (DART corp_code canonical 외부)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.adapters.base import AdapterError, AdapterRetryError
from app.adapters.dart_adapter import DartAdapter
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.crno_mapping import CrnoMapping

__all__ = ["CrnoBootstrap", "CrnoBootstrapResult"]

logger = logging.getLogger(__name__)

# 기본 캐시 TTL(일). crno 는 사실상 불변 → 길게.
_DEFAULT_MAX_AGE_DAYS: Final[float] = 30.0
# 기본 캐시 파일명 (cache_path 미지정 시 시스템 temp 디렉토리에 생성).
_DEFAULT_CACHE_FILENAME: Final[str] = "speculum_dart_crno.json"


@dataclass(frozen=True, slots=True)
class CrnoBootstrapResult:
    """crno 빌드 결과 — 매핑 dict + 처리 통계.

    Attributes:
        stock_to_crno: KRX 6자리 → crno 13자리 (유효 jurir_no 보유 종목만).
        total_corps: 입력 CorpCodeMapping 의 corp_code 수(=시도 종목 수). 감사
            항등식: `total_corps = len(stock_to_crno) + skipped_no_jurir
            + skipped_adapter_error`.
        skipped_no_jurir: company.json 응답의 jurir_no 가 빈 값/13자리 위반이라
            skip 한 수(정상 — 일부 회사 jurir_no 미기재).
        skipped_adapter_error: company.json 호출이 AdapterError(폐지/이상
            corp_code·013 데이터없음 등)로 실패해 skip 한 수.
    """

    stock_to_crno: dict[str, str]
    total_corps: int
    skipped_no_jurir: int
    skipped_adapter_error: int


class CrnoBootstrap:
    """CorpCodeMapping + DART company.json → CrnoMapping 부트스트랩.

    Args:
        corp_mapping: 종목코드 → corp_code 매핑(CorpCodeBootstrap.load 산출).
            본 부트스트랩이 corp_code 별 company.json 을 호출해 crno 로 보강한다.
        adapter: DartAdapter(테스트 시 mock transport 주입). None 이면 api_key 로
            lazy 생성(owned — close() 시 정리).
        api_key: DART OpenAPI 키. adapter 미주입 시 DartAdapter 에 전달. None 이면
            `DART_API_KEY` env(DartAdapter 정책).
        cache_path: 결과 dict JSON 캐시 경로. None 이면 시스템 temp 디렉토리의
            `speculum_dart_crno.json`(SPECULUM_CRNO_CACHE env override).
        max_age_days: 캐시 유효 기간(일). 기본 30(crno 사실상 불변).
    """

    def __init__(
        self,
        *,
        corp_mapping: CorpCodeMapping,
        adapter: DartAdapter | None = None,
        api_key: str | None = None,
        cache_path: Path | str | None = None,
        max_age_days: float = _DEFAULT_MAX_AGE_DAYS,
    ) -> None:
        self._corp_mapping = corp_mapping
        self._adapter = adapter
        self._owned_adapter = adapter is None
        self._api_key = api_key
        self._cache_path = (
            Path(cache_path)
            if cache_path is not None
            else Path(_default_cache_path())
        )
        self._max_age_days = max_age_days

    def load(self, *, force_refresh: bool = False) -> CrnoMapping:
        """crno 매핑 로드 — 신선한 캐시 우선, 없으면 DART fetch + 캐시 갱신.

        Args:
            force_refresh: True 면 캐시 무시하고 무조건 재빌드(운영 강제 갱신).

        Returns:
            CrnoMapping — 종목코드 → crno 단방향 매핑.

        Raises:
            AdapterError: api_key 미설정(빌드 path), 캐시 손상은 재빌드로 degrade.
            AdapterRetryError: company.json 호출 중 rate limit / 네트워크 단절
                (부분 캐시 방지 위해 전체 중단 — 다음 run 재시도).
        """
        if not force_refresh:
            cached = self._read_fresh_cache()
            if cached is not None:
                logger.info(
                    "crno bootstrap: 캐시 적중 — mapped=%d (%s)",
                    len(cached), self._cache_path,
                )
                return CrnoMapping.from_dict(cached)

        result = self.build()
        # 빌드 성공 시에만 캐시 갱신(부분 실패는 build 가 re-raise 하므로 도달 X).
        self._write_cache(result.stock_to_crno)

        logger.info(
            "crno bootstrap: total_corps=%d mapped=%d "
            "(no_jurir=%d adapter_error=%d)",
            result.total_corps, len(result.stock_to_crno),
            result.skipped_no_jurir, result.skipped_adapter_error,
        )
        # 매핑 0 종목 — 전 종목 company.json 실패/jurir 부재. 빈 매핑은 배당 배치가
        # 전 종목 skip 시키므로(silent 0) 경고로 가시화(CorpCodeBootstrap 동형).
        if not result.stock_to_crno:
            logger.warning(
                "crno bootstrap: 매핑 0 종목 — 전 종목 jurir_no 부재 또는 "
                "company.json 실패(total_corps=%d). 배당 배치가 전 종목 skip 됩니다.",
                result.total_corps,
            )
        return CrnoMapping.from_dict(result.stock_to_crno)

    def build(self) -> CrnoBootstrapResult:
        """캐시 무시하고 DART company.json 으로 crno 매핑 dict 빌드 + 통계.

        corp_mapping 의 각 (stock_code, corp_code) 에 대해 company.json 을 호출해
        jurir_no(crno)를 해소한다. per-stock AdapterError 는 skip + 카운트,
        AdapterRetryError 는 re-raise(전체 중단). load() 가 캐시 wrapping 에 사용.

        Returns:
            CrnoBootstrapResult — 매핑 dict + 처리 통계.

        Raises:
            AdapterError: api_key 미설정(첫 호출 시).
            AdapterRetryError: rate limit / 네트워크 단절(전체 빌드 중단).
        """
        adapter = self._get_adapter()
        stock_to_crno: dict[str, str] = {}
        total = 0
        skipped_no_jurir = 0
        skipped_adapter_error = 0

        for stock_code, corp_code in self._corp_mapping.stock_to_corp.items():
            total += 1
            try:
                info = adapter.fetch_company_info(corp_code=corp_code)
            except AdapterRetryError:
                # rate limit / 네트워크 — 부분 캐시 방지 위해 전체 중단(re-raise).
                # AdapterError 보다 먼저 잡아야 함(하위 클래스라 순서 중요).
                raise
            except AdapterError as exc:
                # 폐지/이상 corp_code·013 데이터없음 등 — 해당 종목만 skip + 카운트.
                skipped_adapter_error += 1
                logger.debug(
                    "crno bootstrap: company.json 실패 skip "
                    "(stock=%s corp=%s): %s",
                    stock_code, corp_code, exc,
                )
                continue

            if not info.jurir_no:
                # jurir_no 부재/13자리 위반(adapter 가 빈 문자열로 정규화) — skip.
                skipped_no_jurir += 1
                logger.debug(
                    "crno bootstrap: jurir_no 부재 skip (stock=%s corp=%s)",
                    stock_code, corp_code,
                )
                continue

            stock_to_crno[stock_code] = info.jurir_no

        return CrnoBootstrapResult(
            stock_to_crno=stock_to_crno,
            total_corps=total,
            skipped_no_jurir=skipped_no_jurir,
            skipped_adapter_error=skipped_adapter_error,
        )

    def close(self) -> None:
        """owned DartAdapter 정리(주입 adapter 는 호출자 책임)."""
        if self._owned_adapter and self._adapter is not None:
            self._adapter.close()
            self._adapter = None

    # =========================================================================
    # 내부 helpers
    # =========================================================================

    def _get_adapter(self) -> DartAdapter:
        """lazy DartAdapter 생성(api_key 전달, owned)."""
        if self._adapter is None:
            self._adapter = DartAdapter(api_key=self._api_key)
        return self._adapter

    def _read_fresh_cache(self) -> dict[str, str] | None:
        """캐시 파일이 존재 + TTL 내 + 유효 JSON dict 면 stock_to_crno 반환.

        손상/format 위반/읽기 실패는 None(재빌드 유도) — 캐시 미스로 안전 degrade.
        """
        path = self._cache_path
        try:
            if not path.is_file():
                return None
            age_seconds = time.time() - path.stat().st_mtime
            if age_seconds > self._max_age_days * 86400.0:
                logger.debug(
                    "crno 캐시 만료 (age=%.1fd > %.1fd): %s",
                    age_seconds / 86400.0, self._max_age_days, path,
                )
                return None
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError) as exc:
            logger.warning("crno 캐시 읽기/파싱 실패 (재빌드): %s — %s", path, exc)
            return None
        # 손상 방어 — dict[str, str] 가 아니면 재빌드(from_dict 가 format 재검증하나,
        # 비-dict/비-str value 는 여기서 차단해 TypeError 노출 회피).
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            logger.warning("crno 캐시 구조 위반 (재빌드): %s", path)
            return None
        # format 위반 방어(oracle Low-E) — 자기 캐시는 13자리만 저장하나, 외부
        # 수동 편집/부분 손상으로 6/13자리 위반 값이 들어오면 load() 의
        # CrnoMapping.from_dict 가 CrnoMappingError(독립 예외)를 raise 해 호출자에게
        # 전파된다(배당 배치가 AdapterError 만 처리 시 500). 캐시 적중 경로에서
        # 조용히 재빌드로 degrade 하도록 여기서 format 을 사전 검증한다.
        if not all(
            len(k) == 6 and k.isdigit() and len(v) == 13 and v.isdigit()
            for k, v in data.items()
        ):
            logger.warning("crno 캐시 format 위반 (재빌드): %s", path)
            return None
        return data

    def _write_cache(self, stock_to_crno: dict[str, str]) -> None:
        """결과 dict 를 JSON 캐시 파일에 원자적 저장(temp → replace).

        쓰기 실패는 치명적이지 않음(다음 run 재빌드) — warning 후 진행.
        """
        path = self._cache_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(stock_to_crno, ensure_ascii=True, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            logger.warning(
                "crno 캐시 저장 실패 (다음 run 재빌드): %s — %s", path, exc,
            )
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _default_cache_path() -> str:
    """기본 캐시 경로 — 시스템 temp 디렉토리(cross-platform).

    SPECULUM_CRNO_CACHE env 로 override 가능(운영 배포 시 영속 볼륨 지정).
    """
    override = os.environ.get("SPECULUM_CRNO_CACHE", "").strip()
    if override:
        return override
    return str(Path(tempfile.gettempdir()) / _DEFAULT_CACHE_FILENAME)
