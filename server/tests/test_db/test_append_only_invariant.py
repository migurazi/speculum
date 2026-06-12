"""M1 T49 — financials / corporate_actions append-only 불변식 (ADR-0020).

PG 조건부 트리거 (ADR-0020 D3) 는 SQLite 미지원 → repository layer 의
`update_superseded_by` 가 동일 불변식 검증 (ADR-0020 D5). 본 테스트는 그
repository 방어선을 검증.

테스트 매트릭스 (ADR-0020 완료 기준):
    1. update_superseded_by NULL→set 1 회 허용 (financials).
    2. 이미 superseded 인 row 재변경 거부 (chain 1 회성).
    3. successor row 부재 → 거부 (self-FK 무결성).
    4. 대상 row 부재 → 거부.
    5. update_superseded_by 가 superseded_by 외 컬럼 미변경.
    6. corporate_actions 동일 (NULL→set 허용 / 재변경 거부).
    7. append-only 의도의 type-level 표현 — Repository Protocol 에 비-
       superseded_by UPDATE / DELETE method 부재.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.db.converters import (
    citation_record_to_orm,
    corporate_action_record_to_orm,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.financials import FinancialORM
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    FinancialRecord,
)
from app.repositories.sql_repositories import (
    AppendOnlyViolationError,
    SqlCorporateActionRepository,
    SqlFinancialRepository,
)

_CID = UUID("00000000-0000-0000-0000-00000000ffff")
_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_BATCH = UUID("00000000-0000-0000-0000-0000000000bb")


def _seed_fk_rows(session: Session) -> None:
    """citation_id FK + batch_id FK 만족용 dummy row.

    source_citations.batch_id 가 batch_runs FK (DEFERRABLE) 이므로 batch_runs
    row 도 seed (flush-only 테스트라 COMMIT 검사는 없으나 일관성 위해).
    """
    if session.get(BatchRunORM, _BATCH) is None:
        session.add(
            BatchRunORM(
                id=_BATCH, market="KOSPI", source="KRX",
                started_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
                ended_at=datetime(2024, 5, 20, 9, 5, tzinfo=UTC),
                success_count=1, status=BATCH_STATUS_SUCCESS,
            )
        )
    if session.get(SourceCitationORM, _CID) is None:
        session.add(citation_record_to_orm(SourceCitation(
            id=_CID,
            source=SourceKind.DART,
            identifier="20240520000001",
            retrieved_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
            effective_date=date(2024, 5, 1),
            adapter_version="1.0.0",
            batch_id=_BATCH,
            url=None,
            created_at=datetime(2024, 5, 20, 9, 0, tzinfo=UTC),
        )))
    session.flush()


def _fin(*, record_id: UUID, value: float = 100.0) -> FinancialRecord:
    return FinancialRecord(
        id=record_id,
        code="005930",
        code_lineage_id=_LINEAGE,
        effective_date=date(2024, 3, 31),
        fiscal_period="2023Q4",
        account="net_income_consolidated_ifrs",
        value=Decimal(str(value)),
        unit="krw",
        ifrs_type="consolidated",
        citation_id=_CID,
        superseded_by=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _ca(*, record_id: UUID) -> CorporateActionRecord:
    return CorporateActionRecord(
        id=record_id,
        code="005930",
        code_lineage_id=_LINEAGE,
        action_type="split",
        announced_date=date(2024, 1, 10),
        effective_date=date(2024, 2, 1),
        payment_date=None,
        ratio=Decimal("2"),
        cash_amount=None,
        details={},
        citation_id=_CID,
        superseded_by=None,
        created_at=datetime(2024, 1, 10, 9, 0, tzinfo=UTC),
    )


# =============================================================================
# 1~5. financials update_superseded_by
# =============================================================================

def test_financial_supersede_null_to_set_allowed_once(
    db_session: Session,
) -> None:
    _seed_fk_rows(db_session)
    repo = SqlFinancialRepository(db_session)
    orig_id, succ_id = uuid4(), uuid4()
    repo.save_financials([_fin(record_id=orig_id), _fin(record_id=succ_id)])

    # NULL → set 1 회 허용.
    repo.update_superseded_by(orig_id, succ_id)

    orm = db_session.get(FinancialORM, orig_id)
    assert orm is not None
    assert orm.superseded_by == succ_id


def test_financial_supersede_already_set_rejected(db_session: Session) -> None:
    """chain 1 회성 — 이미 superseded 인 row 재변경 거부 (ADR-0020 D1)."""
    _seed_fk_rows(db_session)
    repo = SqlFinancialRepository(db_session)
    orig_id, succ1, succ2 = uuid4(), uuid4(), uuid4()
    repo.save_financials([
        _fin(record_id=orig_id), _fin(record_id=succ1), _fin(record_id=succ2),
    ])
    repo.update_superseded_by(orig_id, succ1)

    with pytest.raises(AppendOnlyViolationError, match="이미 superseded"):
        repo.update_superseded_by(orig_id, succ2)


def test_financial_supersede_missing_successor_rejected(
    db_session: Session,
) -> None:
    _seed_fk_rows(db_session)
    repo = SqlFinancialRepository(db_session)
    orig_id = uuid4()
    repo.save_financials([_fin(record_id=orig_id)])

    with pytest.raises(AppendOnlyViolationError, match="successor"):
        repo.update_superseded_by(orig_id, uuid4())


def test_financial_supersede_missing_target_rejected(
    db_session: Session,
) -> None:
    _seed_fk_rows(db_session)
    repo = SqlFinancialRepository(db_session)
    with pytest.raises(AppendOnlyViolationError, match="부재"):
        repo.update_superseded_by(uuid4(), uuid4())


def test_financial_supersede_leaves_other_columns_unchanged(
    db_session: Session,
) -> None:
    _seed_fk_rows(db_session)
    repo = SqlFinancialRepository(db_session)
    orig_id, succ_id = uuid4(), uuid4()
    repo.save_financials([
        _fin(record_id=orig_id, value=100.0), _fin(record_id=succ_id),
    ])
    before = db_session.get(FinancialORM, orig_id)
    assert before is not None
    snap = (before.code, before.value, before.account, before.effective_date,
            before.fiscal_period, before.unit, before.ifrs_type,
            before.citation_id, before.created_at)

    repo.update_superseded_by(orig_id, succ_id)

    after = db_session.get(FinancialORM, orig_id)
    assert after is not None
    assert (after.code, after.value, after.account, after.effective_date,
            after.fiscal_period, after.unit, after.ifrs_type,
            after.citation_id, after.created_at) == snap


# =============================================================================
# 6. corporate_actions update_superseded_by
# =============================================================================

def test_corporate_action_supersede_null_to_set_allowed_once(
    db_session: Session,
) -> None:
    _seed_fk_rows(db_session)
    orig_id, succ_id = uuid4(), uuid4()
    db_session.add(corporate_action_record_to_orm(_ca(record_id=orig_id)))
    db_session.add(corporate_action_record_to_orm(_ca(record_id=succ_id)))
    db_session.flush()

    repo = SqlCorporateActionRepository(db_session)
    repo.update_superseded_by(orig_id, succ_id)

    from app.db.orm.corporate_actions import CorporateActionORM
    orm = db_session.get(CorporateActionORM, orig_id)
    assert orm is not None
    assert orm.superseded_by == succ_id


def test_corporate_action_supersede_already_set_rejected(
    db_session: Session,
) -> None:
    _seed_fk_rows(db_session)
    orig_id, succ1, succ2 = uuid4(), uuid4(), uuid4()
    for rid in (orig_id, succ1, succ2):
        db_session.add(corporate_action_record_to_orm(_ca(record_id=rid)))
    db_session.flush()

    repo = SqlCorporateActionRepository(db_session)
    repo.update_superseded_by(orig_id, succ1)
    with pytest.raises(AppendOnlyViolationError, match="이미 superseded"):
        repo.update_superseded_by(orig_id, succ2)


# =============================================================================
# 7. append-only type-level — 비-superseded_by UPDATE / DELETE method 부재
# =============================================================================

def test_repository_has_no_arbitrary_update_or_delete_methods() -> None:
    """ADR-0020 D4/D5 — 유일 허용 UPDATE 경로는 update_superseded_by 뿐.

    임의 update / delete / save_or_update 등의 method 가 repository 에 없음을
    검증 (append-only 의도의 type-level 표현). save_financials 는 INSERT only.
    """
    forbidden = {"update", "delete", "remove", "save_or_update", "upsert"}
    for repo_cls in (SqlFinancialRepository, SqlCorporateActionRepository):
        names = set(dir(repo_cls))
        assert not (forbidden & names), (
            f"{repo_cls.__name__} 에 임의 mutation method 존재: "
            f"{sorted(forbidden & names)}"
        )
        assert "update_superseded_by" in names
