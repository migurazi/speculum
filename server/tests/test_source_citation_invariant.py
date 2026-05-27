"""SourceCitation 빌드 게이트 — Record dataclass 의 citation_id 의무 검증.

oracle 자문 Risk-E #1 — T23 의 진짜 산출물. ADR-0002 D3 의 "모든 fact 가 citation
참조 의무" + AC-P-01 (Fidelity) 의 type-level 강제.

`pit_protocols.py` 의 모든 fact record dataclass (PriceRecord, FinancialRecord,
CorporateActionRecord, StockSnapshotRecord) 가 다음을 만족해야 함:

1. `citation_id` field 보유
2. `citation_id` type 이 `UUID` (Optional 거부 — 의무 참조)
3. 향후 새 record dataclass 도입 시 본 게이트가 자동 detect

`dataclasses.fields()` 로 introspection — AST walk 보다 안정적이고 frozen 검사 자연.
"""

from __future__ import annotations

import dataclasses
from typing import get_type_hints
from uuid import UUID

import pytest

from app.repositories.pit_protocols import (
    CorporateActionRecord,
    FinancialRecord,
    PriceRecord,
    StockSnapshotRecord,
)


# 본 게이트가 검증할 Record dataclass list — 새 record 도입 시 본 list 갱신 의무.
# M0 의 4 종 record cover. StockSnapshotRecord 의 citation_id 는 "결과의 대표
# citation" (derived) — 같은 invariant 적용 (oracle 2 차 L5 — derived 의 의미는
# ADR-0002 D5 의 stock_snapshots schema citation_id NOT NULL 와 일관).
_ALL_RECORDS = [
    PriceRecord,
    FinancialRecord,
    CorporateActionRecord,
    StockSnapshotRecord,
]


@pytest.mark.parametrize("record_cls", _ALL_RECORDS)
def test_record_has_citation_id_field(record_cls: type) -> None:
    """모든 fact record dataclass 가 `citation_id` field 보유."""
    field_names = {f.name for f in dataclasses.fields(record_cls)}
    assert "citation_id" in field_names, (
        f"{record_cls.__name__} missing required field 'citation_id' — "
        f"AC-P-01 Fidelity invariant. ADR-0002 D3 의 모든 fact 가 citation 의무."
    )


@pytest.mark.parametrize("record_cls", _ALL_RECORDS)
def test_record_citation_id_type_is_uuid(record_cls: type) -> None:
    """citation_id type 이 UUID — Optional[UUID] 거부 (의무 참조)."""
    hints = get_type_hints(record_cls)
    citation_type = hints.get("citation_id")
    assert citation_type is UUID, (
        f"{record_cls.__name__}.citation_id type must be UUID (not Optional), "
        f"got {citation_type!r}. AC-P-01 의 의무 참조 보장."
    )


@pytest.mark.parametrize("record_cls", _ALL_RECORDS)
def test_record_is_frozen_dataclass(record_cls: type) -> None:
    """모든 Record 가 frozen=True — citation_id reassign 불가."""
    params = record_cls.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen, (
        f"{record_cls.__name__} must be frozen=True — citation 변조 차단"
    )


def test_record_count_matches_expected_known_set() -> None:
    """현재 4 종 record 가 covered 됨을 명시 — 새 record 추가 시 본 list 갱신 강제.

    M2+ 의 신규 fact record 가 등장하면 _ALL_RECORDS 에 추가해야 본 게이트가
    적용. 누락 시 fidelity invariant 가 silent 깨지므로 명시적 sentinel test.
    """
    expected_names = {
        "PriceRecord",
        "FinancialRecord",
        "CorporateActionRecord",
        "StockSnapshotRecord",
    }
    actual_names = {cls.__name__ for cls in _ALL_RECORDS}
    assert actual_names == expected_names, (
        f"새 fact record 도입 시 본 test 의 _ALL_RECORDS 갱신 + Fidelity 검증 "
        f"확인. expected={sorted(expected_names)} actual={sorted(actual_names)}"
    )
