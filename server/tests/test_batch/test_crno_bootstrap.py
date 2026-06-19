"""CrnoBootstrap — CorpCodeMapping + company.json → CrnoMapping 단위 테스트.

Fake DartAdapter(fetch_company_info 만 구현)로 DART 호출을 격리. 라이브 호출 0.

검증:
    - build: 정상 매핑 + 통계.
    - per-stock AdapterError skip + 카운트(다른 종목 진행).
    - AdapterRetryError re-raise(rate limit — 전체 중단, 부분 캐시 방지).
    - jurir_no 부재 skip + 카운트.
    - load: 캐시 적중(adapter 미호출) / 빌드 후 캐시 기록 / force_refresh / 손상 캐시 재빌드.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.base import AdapterError, AdapterRetryError
from app.adapters.dart_adapter import CompanyInfo
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.crno_mapping import CrnoMapping
from batch.crno_bootstrap import CrnoBootstrap

_CRNO_SAMSUNG = "1301110006246"
_CRNO_SK = "1301110017990"

# 종목코드 → corp_code (CorpCodeMapping 입력).
_STOCK_TO_CORP = {"005930": "00126380", "000660": "00164779"}
# corp_code → crno (Fake adapter 가 반환할 정답).
_CORP_TO_CRNO = {"00126380": _CRNO_SAMSUNG, "00164779": _CRNO_SK}


class _FakeDartAdapter:
    """fetch_company_info 만 구현한 DartAdapter 대역.

    corp_to_jurir 매핑으로 jurir_no 반환. raise_for 에 등록된 corp_code 는 지정
    예외를 raise(에러 경로 검증). close() no-op. call_log 로 호출 추적.
    """

    def __init__(
        self,
        *,
        corp_to_jurir: dict[str, str] | None = None,
        raise_for: dict[str, Exception] | None = None,
    ) -> None:
        self._corp_to_jurir = corp_to_jurir or {}
        self._raise_for = raise_for or {}
        self.call_log: list[str] = []
        self.closed = False

    def fetch_company_info(self, *, corp_code: str) -> CompanyInfo:
        self.call_log.append(corp_code)
        if corp_code in self._raise_for:
            raise self._raise_for[corp_code]
        return CompanyInfo(
            corp_code=corp_code,
            corp_name="테스트회사",
            stock_code="",
            jurir_no=self._corp_to_jurir.get(corp_code, ""),
        )

    def close(self) -> None:
        self.closed = True


def _corp_mapping() -> CorpCodeMapping:
    return CorpCodeMapping.from_dict(_STOCK_TO_CORP)


# =============================================================================
# build
# =============================================================================

def test_build_maps_all_stocks() -> None:
    """정상 — 모든 종목이 crno 로 매핑 + 통계."""
    adapter = _FakeDartAdapter(corp_to_jurir=_CORP_TO_CRNO)
    boot = CrnoBootstrap(corp_mapping=_corp_mapping(), adapter=adapter)
    result = boot.build()
    assert result.stock_to_crno == {
        "005930": _CRNO_SAMSUNG,
        "000660": _CRNO_SK,
    }
    assert result.total_corps == 2
    assert result.skipped_no_jurir == 0
    assert result.skipped_adapter_error == 0
    # 감사 항등식.
    assert result.total_corps == (
        len(result.stock_to_crno)
        + result.skipped_no_jurir
        + result.skipped_adapter_error
    )


def test_build_skips_missing_jurir() -> None:
    """jurir_no 부재 종목 skip + 카운트(다른 종목 진행)."""
    adapter = _FakeDartAdapter(
        corp_to_jurir={"00126380": _CRNO_SAMSUNG, "00164779": ""},
    )
    boot = CrnoBootstrap(corp_mapping=_corp_mapping(), adapter=adapter)
    result = boot.build()
    assert result.stock_to_crno == {"005930": _CRNO_SAMSUNG}
    assert result.skipped_no_jurir == 1
    assert result.skipped_adapter_error == 0


def test_build_skips_adapter_error() -> None:
    """company.json AdapterError(폐지/이상 corp_code) skip + 카운트."""
    adapter = _FakeDartAdapter(
        corp_to_jurir={"00126380": _CRNO_SAMSUNG},
        raise_for={"00164779": AdapterError("status=013 no data")},
    )
    boot = CrnoBootstrap(corp_mapping=_corp_mapping(), adapter=adapter)
    result = boot.build()
    assert result.stock_to_crno == {"005930": _CRNO_SAMSUNG}
    assert result.skipped_adapter_error == 1
    assert result.skipped_no_jurir == 0


def test_build_reraises_retry_error() -> None:
    """AdapterRetryError(rate limit) → 전체 중단(re-raise, 부분 캐시 방지).

    AdapterRetryError 는 AdapterError 하위이나 except 순서상 먼저 잡혀 re-raise
    되어야 함(skip 으로 조용히 삼키면 결손).
    """
    adapter = _FakeDartAdapter(
        corp_to_jurir={"00126380": _CRNO_SAMSUNG},
        raise_for={"00164779": AdapterRetryError("status=020 rate limit")},
    )
    boot = CrnoBootstrap(corp_mapping=_corp_mapping(), adapter=adapter)
    with pytest.raises(AdapterRetryError):
        boot.build()


# =============================================================================
# load — 캐시
# =============================================================================

def test_load_builds_and_writes_cache(tmp_path: Path) -> None:
    """캐시 미스 → 빌드 + JSON 캐시 기록 → CrnoMapping."""
    cache = tmp_path / "crno.json"
    adapter = _FakeDartAdapter(corp_to_jurir=_CORP_TO_CRNO)
    boot = CrnoBootstrap(
        corp_mapping=_corp_mapping(), adapter=adapter, cache_path=cache,
    )
    mapping = boot.load()
    assert isinstance(mapping, CrnoMapping)
    assert mapping.to_crno("005930") == _CRNO_SAMSUNG
    # 캐시 파일 기록됨.
    assert cache.is_file()
    written = json.loads(cache.read_text(encoding="utf-8"))
    assert written == {"005930": _CRNO_SAMSUNG, "000660": _CRNO_SK}


def test_load_cache_hit_skips_adapter(tmp_path: Path) -> None:
    """신선한 캐시 적중 → adapter 미호출(call_log 빈 채로 매핑 반환)."""
    cache = tmp_path / "crno.json"
    cache.write_text(
        json.dumps({"005930": _CRNO_SAMSUNG}), encoding="utf-8",
    )
    adapter = _FakeDartAdapter(corp_to_jurir=_CORP_TO_CRNO)
    boot = CrnoBootstrap(
        corp_mapping=_corp_mapping(), adapter=adapter, cache_path=cache,
    )
    mapping = boot.load()
    assert mapping.to_crno("005930") == _CRNO_SAMSUNG
    # 캐시 적중이라 DART 호출 0.
    assert adapter.call_log == []


def test_load_force_refresh_ignores_cache(tmp_path: Path) -> None:
    """force_refresh=True → 캐시 무시하고 재빌드(adapter 호출됨)."""
    cache = tmp_path / "crno.json"
    cache.write_text(
        json.dumps({"005930": "9999999999999"}), encoding="utf-8",
    )
    adapter = _FakeDartAdapter(corp_to_jurir=_CORP_TO_CRNO)
    boot = CrnoBootstrap(
        corp_mapping=_corp_mapping(), adapter=adapter, cache_path=cache,
    )
    mapping = boot.load(force_refresh=True)
    # 재빌드 → stale 캐시값 무시, fresh jurir.
    assert mapping.to_crno("005930") == _CRNO_SAMSUNG
    assert adapter.call_log  # 호출됨.


def test_load_corrupt_cache_rebuilds(tmp_path: Path) -> None:
    """손상 캐시(JSON 아님) → 재빌드(degrade)."""
    cache = tmp_path / "crno.json"
    cache.write_text("not json {{{", encoding="utf-8")
    adapter = _FakeDartAdapter(corp_to_jurir=_CORP_TO_CRNO)
    boot = CrnoBootstrap(
        corp_mapping=_corp_mapping(), adapter=adapter, cache_path=cache,
    )
    mapping = boot.load()
    assert mapping.to_crno("005930") == _CRNO_SAMSUNG
    assert adapter.call_log  # 손상 → 재빌드 호출.


def test_load_cache_wrong_structure_rebuilds(tmp_path: Path) -> None:
    """구조 위반 캐시(list / 비-str value) → 재빌드."""
    cache = tmp_path / "crno.json"
    cache.write_text(json.dumps(["005930", _CRNO_SAMSUNG]), encoding="utf-8")
    adapter = _FakeDartAdapter(corp_to_jurir=_CORP_TO_CRNO)
    boot = CrnoBootstrap(
        corp_mapping=_corp_mapping(), adapter=adapter, cache_path=cache,
    )
    mapping = boot.load()
    assert mapping.to_crno("005930") == _CRNO_SAMSUNG
    assert adapter.call_log


def test_load_cache_invalid_crno_format_rebuilds(tmp_path: Path) -> None:
    """구조는 dict[str,str]이나 crno format 위반(13자리 아님) 캐시 → 재빌드(degrade).

    oracle Low-E — 외부 손상/수동 편집으로 잘못된 crno 가 캐시에 있으면 load()의
    from_dict 가 CrnoMappingError 로 500 전파되는 대신 조용히 재빌드해야 함.
    """
    cache = tmp_path / "crno.json"
    # 구조는 맞으나 crno 가 13자리 아님(format 위반).
    cache.write_text(
        json.dumps({"005930": "BADVALUE"}), encoding="utf-8",
    )
    adapter = _FakeDartAdapter(corp_to_jurir=_CORP_TO_CRNO)
    boot = CrnoBootstrap(
        corp_mapping=_corp_mapping(), adapter=adapter, cache_path=cache,
    )
    # CrnoMappingError 전파 없이 재빌드 → 정상 매핑.
    mapping = boot.load()
    assert mapping.to_crno("005930") == _CRNO_SAMSUNG
    assert adapter.call_log  # 재빌드 호출됨.


def test_load_empty_mapping_warns(tmp_path: Path, caplog) -> None:
    """매핑 0 종목(전 종목 jurir 부재) → warning 가시화 + 빈 CrnoMapping."""
    cache = tmp_path / "crno.json"
    # 모든 corp 가 빈 jurir → 매핑 0.
    adapter = _FakeDartAdapter(corp_to_jurir={})
    boot = CrnoBootstrap(
        corp_mapping=_corp_mapping(), adapter=adapter, cache_path=cache,
    )
    import logging

    with caplog.at_level(logging.WARNING):
        mapping = boot.load()
    assert mapping.size == 0
    assert any("매핑 0 종목" in rec.message for rec in caplog.records)
