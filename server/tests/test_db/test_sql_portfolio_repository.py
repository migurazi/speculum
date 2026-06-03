"""SqlPortfolioRepository + FakePortfolioRepository 단위 테스트 — M3 #4.

테스트 매트릭스 — Fake 와 동일 contract:
    1. add + list_transactions (CRUD happy path, 결정적 정렬)
    2. list_transactions 격리(P0) — user_id 필터(전 사용자 누출 게이트)
    3. code_lineage_id 선택 필터 — 특정 종목만 / 전 종목
    4. delete owner-check — 타 user 거래 delete → False(IDOR)
    5. side 값 invariant — 'buy'|'sell' 외 → PortfolioDataError
    6. quantity/unit_price/fee invariant — 음수/0 → PortfolioDataError
    7. append-only — UPDATE 메서드 부재(역분개 정신, delete 만)
    8. Fake/SQL contract 동등성
    9. ORM create_all 의 portfolio_transactions 컬럼 / 인덱스 검증
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.repositories.portfolio_repository import (
    FakePortfolioRepository,
    PortfolioDataError,
    PortfolioRepository,
)
from app.repositories.sql_portfolio_repository import SqlPortfolioRepository

# conftest 가 users 에 seed 한 표준 user_id (FK 충족).
_USER_A = UUID("00000000-0000-0000-0000-00000000000a")
_USER_B = UUID("00000000-0000-0000-0000-00000000000b")

_CODE_A = UUID("11111111-1111-1111-1111-111111111111")
_CODE_B = UUID("22222222-2222-2222-2222-222222222222")


def _sql_repo(db_session: Session) -> SqlPortfolioRepository:
    return SqlPortfolioRepository(db_session)


def _fake_repo(_db_session: Session) -> FakePortfolioRepository:
    return FakePortfolioRepository()


_REPO_FACTORIES = (_sql_repo, _fake_repo)


# =============================================================================
# CRUD happy path
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_add_and_list(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    repo.add_transaction(
        user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
        quantity=100, unit_price=Decimal("1000"), trade_date=date(2024, 2, 1),
    )
    repo.add_transaction(
        user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
        quantity=50, unit_price=Decimal("2000"), trade_date=date(2024, 1, 1),
        fee=Decimal("100"),
    )
    txns = repo.list_transactions(user_id=_USER_A, code_lineage_id=_CODE_A)
    # trade_date asc 정렬 — 1/1 거래가 먼저.
    assert [t.trade_date for t in txns] == [date(2024, 1, 1), date(2024, 2, 1)]
    assert txns[0].quantity == 50
    assert txns[0].fee == Decimal("100")
    assert txns[1].quantity == 100


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_list_code_lineage_filter(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    repo.add_transaction(
        user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
        quantity=10, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
    )
    repo.add_transaction(
        user_id=_USER_A, code_lineage_id=_CODE_B, side="buy",
        quantity=20, unit_price=Decimal("500"), trade_date=date(2024, 1, 1),
    )
    # 특정 종목만.
    a_only = repo.list_transactions(user_id=_USER_A, code_lineage_id=_CODE_A)
    assert {t.code_lineage_id for t in a_only} == {_CODE_A}
    # 전 종목.
    all_txns = repo.list_transactions(user_id=_USER_A)
    assert {t.code_lineage_id for t in all_txns} == {_CODE_A, _CODE_B}


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_delete(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    tx = repo.add_transaction(
        user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
        quantity=10, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
    )
    assert repo.delete_transaction(tx.id, user_id=_USER_A) is True
    assert repo.list_transactions(user_id=_USER_A) == ()


# =============================================================================
# user 격리 (IDOR P0)
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_list_user_isolation_p0(db_session: Session, factory) -> None:
    """list_transactions 는 user_id 필터 (P0 누출 게이트).

    A·B 가 같은 종목에 거래 — 각자 list 는 자기 것만. user_id predicate 누락 시
    전 사용자 거래 누출(가장 위험한 회귀).
    """
    repo: PortfolioRepository = factory(db_session)
    repo.add_transaction(
        user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
        quantity=10, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
    )
    repo.add_transaction(
        user_id=_USER_B, code_lineage_id=_CODE_A, side="buy",
        quantity=99, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
    )
    a_txns = repo.list_transactions(user_id=_USER_A)
    assert [t.quantity for t in a_txns] == [10]
    b_txns = repo.list_transactions(user_id=_USER_B)
    assert [t.quantity for t in b_txns] == [99]


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_delete_owner_mismatch_returns_false(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    tx = repo.add_transaction(
        user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
        quantity=10, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
    )
    # B 가 A 의 거래 삭제 시도 → False(IDOR 차단).
    assert repo.delete_transaction(tx.id, user_id=_USER_B) is False
    # A 의 거래 불변.
    assert len(repo.list_transactions(user_id=_USER_A)) == 1


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_delete_unknown_returns_false(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    assert repo.delete_transaction(uuid4(), user_id=_USER_A) is False


# =============================================================================
# invariant — side / 수량 / 단가 / 수수료
# =============================================================================

@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_invalid_side_raises(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    with pytest.raises(PortfolioDataError, match="side"):
        repo.add_transaction(
            user_id=_USER_A, code_lineage_id=_CODE_A, side="short",
            quantity=10, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
        )


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_non_positive_quantity_raises(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    with pytest.raises(PortfolioDataError, match="quantity"):
        repo.add_transaction(
            user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
            quantity=0, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
        )


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_negative_unit_price_raises(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    with pytest.raises(PortfolioDataError, match="unit_price"):
        repo.add_transaction(
            user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
            quantity=10, unit_price=Decimal("-1"), trade_date=date(2024, 1, 1),
        )


@pytest.mark.parametrize("factory", _REPO_FACTORIES)
def test_negative_fee_raises(db_session: Session, factory) -> None:
    repo: PortfolioRepository = factory(db_session)
    with pytest.raises(PortfolioDataError, match="fee"):
        repo.add_transaction(
            user_id=_USER_A, code_lineage_id=_CODE_A, side="buy",
            quantity=10, unit_price=Decimal("1000"), trade_date=date(2024, 1, 1),
            fee=Decimal("-5"),
        )


# =============================================================================
# append-only — UPDATE 부재
# =============================================================================

def test_repository_has_no_update_method() -> None:
    """append-only 정신 — repository 에 update/edit 메서드 없음(역분개로 정정)."""
    methods = set(dir(FakePortfolioRepository))
    assert "add_transaction" in methods
    assert "list_transactions" in methods
    assert "delete_transaction" in methods
    # UPDATE 류 부재(거래 정정은 역분개 append, 입력 실수는 delete).
    assert not any(m for m in methods if m.startswith("update"))
    assert not any(m for m in methods if m.startswith("edit"))


# =============================================================================
# ORM create_all — 컬럼 / 인덱스 검증
# =============================================================================

def test_orm_create_all_includes_portfolio_transactions() -> None:
    """ORM create_all 의 portfolio_transactions 컬럼/인덱스 set (migration drift 차단)."""
    from app.db import orm  # noqa: F401  ← Base.metadata 등록 side-effect
    from app.db.base import Base

    engine: Engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("portfolio_transactions")}
        assert {
            "id", "user_id", "code_lineage_id", "side", "quantity",
            "unit_price", "trade_date", "fee", "created_at",
        } == cols
        # 세금 컬럼 부재(D6 세전만 — 세금 필드 0).
        assert "tax" not in cols
        assert "withholding" not in cols

        index_names = {ix["name"] for ix in insp.get_indexes("portfolio_transactions")}
        assert "ix_portfolio_transactions_user_id" in index_names
        assert "ix_portfolio_transactions_user_code" in index_names

        composite = next(
            ix for ix in insp.get_indexes("portfolio_transactions")
            if ix["name"] == "ix_portfolio_transactions_user_code"
        )
        assert composite["column_names"] == ["user_id", "code_lineage_id"]
    finally:
        engine.dispose()
