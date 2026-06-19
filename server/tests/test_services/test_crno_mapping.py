"""CrnoMapping — 종목코드 → crno(법인등록번호) 매핑 단위 테스트.

검증:
    - from_dict 양방향 아님(단방향) + format 검증.
    - to_crno lookup + 미매핑 None.
    - N:1 허용(보통주·우선주 같은 crno 공유 — CorpCodeMapping 과의 차이).
    - format 위반(6/13자리) raise.
"""

from __future__ import annotations

import pytest

from app.services.crno_mapping import CrnoMapping, CrnoMappingError

_CRNO_SAMSUNG = "1301110006246"
_CRNO_SK = "1301110017990"


def test_from_dict_and_to_crno() -> None:
    """정상 — 종목코드 → crno lookup."""
    m = CrnoMapping.from_dict({"005930": _CRNO_SAMSUNG, "000660": _CRNO_SK})
    assert m.to_crno("005930") == _CRNO_SAMSUNG
    assert m.to_crno("000660") == _CRNO_SK
    assert m.size == 2


def test_to_crno_unmapped_returns_none() -> None:
    """미매핑 종목 → None(silent drop X — 호출자 결정)."""
    m = CrnoMapping.from_dict({"005930": _CRNO_SAMSUNG})
    assert m.to_crno("999999") is None


def test_empty_dict_allowed() -> None:
    """빈 dict 허용(운영 boot 전 / 매핑 0)."""
    m = CrnoMapping.from_dict({})
    assert m.size == 0
    assert m.to_crno("005930") is None


def test_n_to_one_crno_allowed() -> None:
    """N:1 허용 — 보통주·우선주가 같은 crno 공유(법인 식별자).

    CorpCodeMapping 은 corp_code 역중복을 1:1 위반으로 raise 했으나, crno 는
    법인 단위라 005930(보통주)·005935(우선주)가 동일 crno 를 가질 수 있다.
    """
    m = CrnoMapping.from_dict(
        {"005930": _CRNO_SAMSUNG, "005935": _CRNO_SAMSUNG}
    )
    assert m.to_crno("005930") == _CRNO_SAMSUNG
    assert m.to_crno("005935") == _CRNO_SAMSUNG
    assert m.size == 2


def test_invalid_stock_code_raises() -> None:
    """종목코드 6자리 numeric 아님 → CrnoMappingError."""
    with pytest.raises(CrnoMappingError):
        CrnoMapping.from_dict({"5930": _CRNO_SAMSUNG})
    with pytest.raises(CrnoMappingError):
        CrnoMapping.from_dict({"00593A": _CRNO_SAMSUNG})


def test_invalid_crno_raises() -> None:
    """crno 13자리 numeric 아님 → CrnoMappingError."""
    # 12자리(짧음).
    with pytest.raises(CrnoMappingError):
        CrnoMapping.from_dict({"005930": "130111000624"})
    # 하이픈 포함(검증은 정규화된 13자리 가정 — bootstrap 이 정규화 책임).
    with pytest.raises(CrnoMappingError):
        CrnoMapping.from_dict({"005930": "130111-0006246"})


def test_mapping_is_immutable() -> None:
    """stock_to_crno 는 읽기 전용(MappingProxyType) — 외부 mutation 차단."""
    m = CrnoMapping.from_dict({"005930": _CRNO_SAMSUNG})
    with pytest.raises(TypeError):
        m.stock_to_crno["000660"] = _CRNO_SK  # type: ignore[index]
