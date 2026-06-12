"""DividendRepository (Fake + SQL) 이중 PIT contract 단위 테스트 — M7 #1.

이중 PIT 설계 (ADR-0035 D6):
    1. announced_date <= as_of  (정보 가용성, supersede chain 해소 포함)
    2. effective_date <= as_of  (배당락 사건 발생 — total return 재투자 대상)

테스트 매트릭스:
    1. announced_date PIT 경계 (announced > as_of 제외, == 포함)
    2. effective_date 경계 (effective > as_of 제외 = 미발생 배당락, == 포함)
    3. 이중 축 교차: announced <= as_of 이지만 effective > as_of 인 배당 → 제외
       (핵심 케이스 — §2.4 PIT 위반 차단)
    4. supersede chain (announced 축) 해소
    5. cash_dividend 아닌 action_type (split 등) 미반환
    6. Fake / SQL 동등성 (parametrize)
    7. save_dividends 후 fetch round-trip
    8. action_type != "cash_dividend" save 시 ValueError
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
from app.db.orm.source_citations import SourceCitationORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.fakes import FakeDividendRepository
from app.repositories.pit_protocols import CorporateActionRecord
from app.repositories.sql_repositories import SqlDividendRepository

_CID = UUID("00000000-0000-0000-0000-00000000ffff")
_LINEAGE = UUID("00000000-0000-0000-0000-0000000000aa")
_BATCH = UUID("00000000-0000-0000-0000-0000000000bb")

_CODE = "005930"


def _seed_fk_rows(session: Session) -> None:
    """citation_id FK + batch_id FK 만족용 dummy row."""
    if session.get(BatchRunORM, _BATCH) is None:
        session.add(
            BatchRunORM(
                id=_BATCH, market=None, source="DART",
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


def _rec(
    *,
    record_id: UUID | None = None,
    code: str = _CODE,
    action_type: str = "cash_dividend",
    announced_date: date,
    effective_date: date,
    payment_date: date | None = None,
    cash_amount: Decimal | None = None,
    superseded_by: UUID | None = None,
    created_at: datetime | None = None,
) -> CorporateActionRecord:
    # code 는 기본 _CODE(005930). 다종목 격리 테스트에서만 다른 값을 넘긴다.
    return CorporateActionRecord(
        id=record_id or uuid4(),
        code=code,
        code_lineage_id=_LINEAGE,
        action_type=action_type,
        announced_date=announced_date,
        effective_date=effective_date,
        payment_date=payment_date,
        ratio=None,
        cash_amount=cash_amount or Decimal("500"),
        details={"per_share": "500", "type": "ordinary"},
        citation_id=_CID,
        superseded_by=superseded_by,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


# =============================================================================
# Repo factory helpers for parametrize
# =============================================================================

def _fake_from_records(
    records: list[CorporateActionRecord],
    _session: Session | None = None,
) -> FakeDividendRepository:
    return FakeDividendRepository(records=records)


def _sql_from_records(
    records: list[CorporateActionRecord],
    session: Session,
) -> SqlDividendRepository:
    repo = SqlDividendRepository(session)
    repo.save_dividends(records)
    return repo


# =============================================================================
# 1. announced_date PIT 경계
# =============================================================================

class TestAnnouncedDateBoundary:
    """announced_date <= as_of 경계 — 당일 포함, 미래 제외."""

    def test_fake_announced_equal_as_of_included(self) -> None:
        r = _rec(
            announced_date=date(2024, 3, 1),
            effective_date=date(2024, 3, 1),
        )
        repo = FakeDividendRepository(records=[r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == r.id

    def test_fake_announced_after_as_of_excluded(self) -> None:
        r = _rec(
            announced_date=date(2024, 3, 2),
            effective_date=date(2024, 3, 2),
        )
        repo = FakeDividendRepository(records=[r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert result == []

    def test_sql_announced_equal_as_of_included(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        r = _rec(
            announced_date=date(2024, 3, 1),
            effective_date=date(2024, 3, 1),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == r.id

    def test_sql_announced_after_as_of_excluded(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        r = _rec(
            announced_date=date(2024, 3, 2),
            effective_date=date(2024, 3, 2),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert not result


# =============================================================================
# 2. effective_date 경계
# =============================================================================

class TestEffectiveDateBoundary:
    """effective_date <= as_of 경계 — 당일 포함, 미래(미발생 배당락) 제외."""

    def test_fake_effective_equal_as_of_included(self) -> None:
        r = _rec(
            announced_date=date(2024, 2, 1),
            effective_date=date(2024, 3, 1),
        )
        repo = FakeDividendRepository(records=[r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == r.id

    def test_fake_effective_after_as_of_excluded(self) -> None:
        r = _rec(
            announced_date=date(2024, 2, 1),
            effective_date=date(2024, 3, 2),
        )
        repo = FakeDividendRepository(records=[r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert result == []

    def test_sql_effective_equal_as_of_included(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        r = _rec(
            announced_date=date(2024, 2, 1),
            effective_date=date(2024, 3, 1),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1

    def test_sql_effective_after_as_of_excluded(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        r = _rec(
            announced_date=date(2024, 2, 1),
            effective_date=date(2024, 3, 2),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert not result


# =============================================================================
# 3. 이중 축 교차 핵심 케이스 — announced <= as_of 이지만 effective > as_of
# =============================================================================

class TestDualAxisCrossCase:
    """announced 는 이미 공시됐으나 배당락이 미발생인 배당 → 제외.

    §2.4 PIT 위반 차단의 핵심 케이스: announced_date 만 체크하면 effective > as_of
    인 배당이 total return 재투자에 포함되어 미래 이익을 현시점에 실현한 look-ahead
    오염이 발생한다. 이중 축이 이 케이스를 정확히 차단해야 한다.
    """

    def test_fake_announced_before_effective_after_excluded(self) -> None:
        # announced_date(2024-02-01) <= as_of(2024-03-01) 만족
        # effective_date(2024-04-01) > as_of(2024-03-01) → 배당락 미발생 → 제외
        r = _rec(
            announced_date=date(2024, 2, 1),
            effective_date=date(2024, 4, 1),
        )
        repo = FakeDividendRepository(records=[r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert result == [], (
            "announced ≤ as_of 이지만 effective > as_of 인 배당은 "
            "배당락 미발생이므로 total return 재투자 대상 외 (§2.4 PIT 위반 차단)"
        )

    def test_fake_both_conditions_met_included(self) -> None:
        # 두 조건 모두 만족하면 포함
        r = _rec(
            announced_date=date(2024, 2, 1),
            effective_date=date(2024, 3, 1),
        )
        repo = FakeDividendRepository(records=[r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1

    def test_sql_announced_before_effective_after_excluded(
        self, db_session: Session,
    ) -> None:
        _seed_fk_rows(db_session)
        r = _rec(
            announced_date=date(2024, 2, 1),
            effective_date=date(2024, 4, 1),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert not result

    def test_multiple_dividends_partial_exclusion(self) -> None:
        """여러 배당 중 이중 축 통과하는 것만 반환."""
        included = _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        excluded_effective = _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 4, 1),  # effective > as_of
        )
        excluded_announced = _rec(
            announced_date=date(2024, 4, 1),  # announced > as_of
            effective_date=date(2024, 4, 15),
        )
        repo = FakeDividendRepository(records=[included, excluded_effective, excluded_announced])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == included.id


# =============================================================================
# 4. supersede chain (announced 축) 해소
# =============================================================================

class TestSupersededChain:
    """정정공시 — superseded 옛 row 제외, 후속 row 반환 (announced 축 chain 해소)."""

    def test_fake_superseded_row_excluded(self) -> None:
        orig_id, succ_id = uuid4(), uuid4()
        orig = _rec(
            record_id=orig_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("500"),
            superseded_by=succ_id,
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        succ = _rec(
            record_id=succ_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("600"),  # 정정 금액
            superseded_by=None,
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        repo = FakeDividendRepository(records=[orig, succ])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == succ_id
        assert result[0].cash_amount == Decimal("600")

    def test_sql_superseded_row_excluded(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        orig_id, succ_id = uuid4(), uuid4()
        succ = _rec(
            record_id=succ_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("600"),
            superseded_by=None,
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        orig = _rec(
            record_id=orig_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("500"),
            superseded_by=None,  # insert 시 NULL (update_superseded_by 로 set)
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        # insert 순서 무관 — superseded_by=NULL 로 둘 다 저장 후 별도 update.
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([succ, orig])
        repo.update_superseded_by(orig_id, succ_id)
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == succ_id

    def test_sql_save_with_superseded_by_raises(self, db_session: Session) -> None:
        """insert 시 superseded_by non-NULL 이면 ValueError (update_superseded_by 사용)."""
        _seed_fk_rows(db_session)
        succ_id = uuid4()
        orig = _rec(
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            superseded_by=succ_id,  # insert 에 직접 superseded_by — 금지
        )
        repo = SqlDividendRepository(db_session)
        with pytest.raises(ValueError, match="superseded_by"):
            repo.save_dividends([orig])

    def test_fake_save_with_superseded_by_raises(self) -> None:
        succ_id = uuid4()
        orig = _rec(
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            superseded_by=succ_id,
        )
        repo = FakeDividendRepository(records=[])
        with pytest.raises(ValueError, match="superseded_by"):
            repo.save_dividends([orig])

    def test_fake_update_superseded_by(self) -> None:
        orig_id, succ_id = uuid4(), uuid4()
        orig = _rec(
            record_id=orig_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("500"),
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        succ = _rec(
            record_id=succ_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("600"),
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        repo = FakeDividendRepository(records=[])
        repo.save_dividends([orig, succ])  # insert 순서 무관, 둘 다 superseded_by=NULL
        repo.update_superseded_by(orig_id, succ_id)
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == succ_id
        assert result[0].cash_amount == Decimal("600")


# =============================================================================
# 5. cash_dividend 아닌 action_type 미반환
# =============================================================================

class TestActionTypeFilter:
    """cash_dividend 가 아닌 action_type(split 등)은 fetch_dividends 에서 반환 안 됨."""

    def test_fake_non_dividend_action_type_excluded(self) -> None:
        split = _rec(
            action_type="split",
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        dividend = _rec(
            action_type="cash_dividend",
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        # Fake 는 init records 에서 non-cash_dividend 도 받을 수 있으나
        # fetch_dividends 는 cash_dividend 만 반환.
        repo = FakeDividendRepository(records=[split, dividend])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].action_type == "cash_dividend"
        assert result[0].id == dividend.id

    def test_sql_save_non_dividend_raises(self, db_session: Session) -> None:
        """save_dividends 에 non-cash_dividend 입력 시 ValueError (방어)."""
        _seed_fk_rows(db_session)
        repo = SqlDividendRepository(db_session)
        split = _rec(
            action_type="split",
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        with pytest.raises(ValueError, match="cash_dividend"):
            repo.save_dividends([split])

    def test_fake_save_non_dividend_raises(self) -> None:
        repo = FakeDividendRepository(records=[])
        split = _rec(
            action_type="split",
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        with pytest.raises(ValueError, match="cash_dividend"):
            repo.save_dividends([split])


# =============================================================================
# 6. Fake / SQL 동등성
# =============================================================================

@pytest.mark.parametrize("make_repo", [
    pytest.param("fake", id="fake"),
    pytest.param("sql", id="sql"),
])
def test_fake_sql_equivalence_basic(
    make_repo: str,
    db_session: Session,
) -> None:
    """Fake 와 SQL 이 동일 입력에서 동일 결과를 반환한다."""
    _seed_fk_rows(db_session)
    records = [
        _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("500"),
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        _rec(
            announced_date=date(2024, 3, 1),
            effective_date=date(2024, 4, 1),
            cash_amount=Decimal("600"),
            created_at=datetime(2024, 3, 1, tzinfo=UTC),
        ),
    ]

    fake_repo = FakeDividendRepository(records=records)
    sql_repo = SqlDividendRepository(db_session)
    sql_repo.save_dividends(records)

    as_of = date(2024, 5, 1)
    fake_result = list(fake_repo.fetch_dividends(_CODE, as_of=as_of))
    sql_result = list(sql_repo.fetch_dividends(_CODE, as_of=as_of))

    assert len(fake_result) == len(sql_result) == 2
    assert [r.id for r in fake_result] == [r.id for r in sql_result]
    assert [r.cash_amount for r in fake_result] == [r.cash_amount for r in sql_result]


@pytest.mark.parametrize("make_repo", [
    pytest.param("fake", id="fake"),
    pytest.param("sql", id="sql"),
])
def test_fake_sql_equivalence_dual_axis_exclusion(
    make_repo: str,
    db_session: Session,
) -> None:
    """Fake 와 SQL 모두 이중 축 교차 케이스에서 동일하게 제외한다."""
    _seed_fk_rows(db_session)
    included = _rec(
        announced_date=date(2024, 1, 1),
        effective_date=date(2024, 2, 1),
        cash_amount=Decimal("500"),
    )
    excluded = _rec(
        announced_date=date(2024, 1, 1),
        effective_date=date(2024, 4, 1),  # effective > as_of → 제외
        cash_amount=Decimal("600"),
    )

    fake_repo = FakeDividendRepository(records=[included, excluded])
    sql_repo = SqlDividendRepository(db_session)
    sql_repo.save_dividends([included, excluded])

    as_of = date(2024, 3, 1)
    fake_result = list(fake_repo.fetch_dividends(_CODE, as_of=as_of))
    sql_result = list(sql_repo.fetch_dividends(_CODE, as_of=as_of))

    assert len(fake_result) == len(sql_result) == 1
    assert fake_result[0].id == sql_result[0].id == included.id


# =============================================================================
# 7. save_dividends 후 fetch round-trip
# =============================================================================

class TestSaveAndFetchRoundTrip:
    """save_dividends 후 fetch_dividends 가 저장된 record 를 정확히 반환한다."""

    def test_fake_save_and_fetch(self) -> None:
        repo = FakeDividendRepository(records=[])
        r = _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("750"),
        )
        repo.save_dividends([r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].cash_amount == Decimal("750")

    def test_sql_save_and_fetch(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        repo = SqlDividendRepository(db_session)
        r = _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("750"),
        )
        repo.save_dividends([r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == r.id
        assert result[0].cash_amount == Decimal("750")
        assert result[0].action_type == "cash_dividend"
        assert result[0].details["per_share"] == "500"  # _rec default

    def test_sql_multiple_save_round_trip(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        repo = SqlDividendRepository(db_session)
        r1 = _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
            cash_amount=Decimal("500"),
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        r2 = _rec(
            announced_date=date(2024, 6, 1),
            effective_date=date(2024, 7, 1),
            cash_amount=Decimal("600"),
            created_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        repo.save_dividends([r1, r2])

        # as_of = 2024-07-15 → 두 배당 모두 이중 축 통과
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 7, 15))
        assert len(result) == 2
        # 정렬: effective_date 기준
        assert result[0].effective_date <= result[1].effective_date

    def test_fake_empty_store(self) -> None:
        repo = FakeDividendRepository(records=[])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert result == []

    def test_sql_empty_store(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        repo = SqlDividendRepository(db_session)
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert list(result) == []

    def test_fake_save_empty_sequence_noop(self) -> None:
        """빈 시퀀스 save 는 no-op — 기존 store 불변."""
        r = _rec(announced_date=date(2024, 1, 1), effective_date=date(2024, 2, 1))
        repo = FakeDividendRepository(records=[r])
        repo.save_dividends([])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1


# =============================================================================
# 8. 다종목 격리 — code 필터가 다른 종목 배당을 섞지 않음
# =============================================================================

class TestCodeIsolation:
    """fetch_dividends(code) 는 해당 종목의 배당만 반환 (code WHERE 격리)."""

    _OTHER = "000660"  # SK하이닉스 — _CODE(삼성전자)와 다른 종목

    def test_fake_other_code_not_leaked(self) -> None:
        mine = _rec(
            code=_CODE,
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        other = _rec(
            code=self._OTHER,
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        repo = FakeDividendRepository(records=[mine, other])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].id == mine.id
        assert result[0].code == _CODE

    def test_sql_other_code_not_leaked(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        mine = _rec(
            code=_CODE,
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        other = _rec(
            code=self._OTHER,
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([mine, other])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1
        assert result[0].code == _CODE


# =============================================================================
# 9. 정렬 결정성 — 동일 effective_date 시 (created_at, id) tie-break
# =============================================================================

class TestSortDeterminism:
    """동일 effective_date 의 다수 배당은 (effective_date, created_at, str(id)) 로
    결정적 정렬 — 입력 순서·DB 반환 순서와 무관하게 재현 가능(§2.10).
    """

    def test_fake_same_effective_date_tie_break_by_created_at(self) -> None:
        # 동일 effective_date, created_at 이 다름 → created_at 오름차순.
        eff = date(2024, 2, 1)
        later = _rec(
            announced_date=date(2024, 1, 20),
            effective_date=eff,
            created_at=datetime(2024, 1, 20, tzinfo=UTC),
        )
        earlier = _rec(
            announced_date=date(2024, 1, 10),
            effective_date=eff,
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        # 입력 순서를 일부러 뒤집어 정렬이 입력 비의존임을 보인다.
        repo = FakeDividendRepository(records=[later, earlier])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert [r.id for r in result] == [earlier.id, later.id]

    def test_sql_same_effective_date_deterministic(self, db_session: Session) -> None:
        _seed_fk_rows(db_session)
        eff = date(2024, 2, 1)
        later = _rec(
            announced_date=date(2024, 1, 20),
            effective_date=eff,
            created_at=datetime(2024, 1, 20, tzinfo=UTC),
        )
        earlier = _rec(
            announced_date=date(2024, 1, 10),
            effective_date=eff,
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([later, earlier])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert [r.id for r in result] == [earlier.id, later.id]


# =============================================================================
# 10. payment_date 는 PIT 축이 아님 (ADR-0035 D6)
# =============================================================================

class TestPaymentDateNotPitAxis:
    """배당 지급일(payment_date)은 PIT 기준이 아니다 — 배당락(effective_date)이
    기준. payment_date 가 as_of 이후(미지급)여도 effective ≤ as_of 면 포함한다.
    배당락일에 주가가 조정되므로 total return 재투자 시점은 배당락이지 지급일이
    아니다(ADR-0035 D6).
    """

    def test_fake_payment_date_after_as_of_still_included(self) -> None:
        r = _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),   # 배당락 발생 ≤ as_of
            payment_date=date(2024, 5, 1),     # 지급일은 as_of 이후 (미지급)
        )
        repo = FakeDividendRepository(records=[r])
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert len(result) == 1, (
            "payment_date 가 as_of 이후여도 effective ≤ as_of 면 포함 — "
            "지급일은 PIT 축이 아님(ADR-0035 D6)"
        )
        assert result[0].payment_date == date(2024, 5, 1)


# =============================================================================
# 11. supersede chain + effective 미래 교차 — chain 해소 후 effective 필터
# =============================================================================

class TestSupersedeAndEffectiveCross:
    """정정 후속본(succ)의 effective 가 as_of 이후면, announced 축에서 active 로
    선택돼도 effective 필터에서 제외된다(이중 축이 chain 해소 *후* 적용됨).
    """

    def test_fake_active_successor_with_future_effective_excluded(self) -> None:
        orig_id, succ_id = uuid4(), uuid4()
        orig = _rec(
            record_id=orig_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        succ = _rec(
            record_id=succ_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 4, 1),  # 정정본 배당락은 as_of 이후 (미발생)
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        repo = FakeDividendRepository(records=[])
        repo.save_dividends([orig, succ])
        repo.update_superseded_by(orig_id, succ_id)  # orig 은 succ 로 정정됨
        # as_of=2024-03-01: announced 축은 succ(active) 선택, orig 제외.
        #   succ.effective(2024-04-01) > as_of → effective 필터에서도 제외.
        #   → 결과 비어야 함 (orig 은 superseded 라 부활 안 함).
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert result == [], (
            "정정 후속본의 배당락이 미발생(effective > as_of)이면 "
            "superseded 원본도 부활하지 않고 결과는 비어야 함"
        )

    def test_sql_active_successor_with_future_effective_excluded(
        self, db_session: Session,
    ) -> None:
        _seed_fk_rows(db_session)
        orig_id, succ_id = uuid4(), uuid4()
        orig = _rec(
            record_id=orig_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        succ = _rec(
            record_id=succ_id,
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 4, 1),
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([orig, succ])
        repo.update_superseded_by(orig_id, succ_id)
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert not result


# =============================================================================
# 12. cross-action_type chain — cash_dividend(orig) → split(succ) supersede
#
# 수정 1(Critical) 직접 검증: chain 해소는 전체 corporate_action 집합에서 수행되고
# cash_dividend 필터는 chain 해소 *후* 적용된다(fetch_actions 정합). cash_dividend
# 가 non-cash_dividend(split) successor 로 superseded 되면 —
#   orig: superseded 라 미반환.
#   succ(split): cash_dividend 가 아니라 미반환.
#   → 결과 빈.
# 수정 전(action_type 을 chain 해소 *전* 적용)에는 succ 가 record_by_id 에 부재하여
# orig 이 보수적 active 로 부활(§2.4 위반)했을 케이스다.
# =============================================================================

class TestCrossActionTypeChainTruncation:
    """cash_dividend(orig)가 split(succ, non-cash_dividend)로 superseded → 결과 빈."""

    def test_fake_cash_dividend_superseded_by_split_excluded(self) -> None:
        orig_id, succ_id = uuid4(), uuid4()
        # Fake __init__ 은 검증 없이 records 를 그대로 buckets 에 보관하므로
        # cross-action_type chain(orig.superseded_by=split.id)을 직접 구성한다.
        orig = _rec(
            record_id=orig_id,
            action_type="cash_dividend",
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            superseded_by=succ_id,           # split 로 정정됨 (cross-action_type)
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        split = _rec(
            record_id=succ_id,
            action_type="split",
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        repo = FakeDividendRepository(records=[orig, split])
        # as_of=2024-03-01: split 의 announced ≤ as_of → orig 은 already superseded.
        #   orig: superseded → 미반환. split: cash_dividend 아님 → 미반환.
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert result == [], (
            "cash_dividend 가 split successor 로 superseded 되면 orig 은 부활하지 "
            "않고 split 은 cash_dividend 가 아니라 결과는 비어야 함 — chain 은 전체 "
            "corporate_action 집합에서 해소되고 cash_dividend 필터는 그 후 적용(수정 1)"
        )

    def test_sql_cash_dividend_superseded_by_split_excluded(
        self, db_session: Session,
    ) -> None:
        _seed_fk_rows(db_session)
        orig_id, succ_id = uuid4(), uuid4()
        # split(succ) 는 save_dividends 가 거부하므로 ORM 직접 insert.
        split = _rec(
            record_id=succ_id,
            action_type="split",
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        db_session.add(corporate_action_record_to_orm(split))
        db_session.flush()
        # cash_dividend(orig) insert (superseded_by=NULL) 후 update 로 split 지정.
        orig = _rec(
            record_id=orig_id,
            action_type="cash_dividend",
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        repo = SqlDividendRepository(db_session)
        repo.save_dividends([orig])
        repo.update_superseded_by(orig_id, succ_id)  # cross-action_type successor
        result = repo.fetch_dividends(_CODE, as_of=date(2024, 3, 1))
        assert list(result) == [], (
            "cross-action_type chain 에서 orig 부활 금지 + split 미반환 → 결과 빈. "
            "SQL fetch 가 code-only 로 전체 chain 을 받아 해소 후 cash_dividend 필터함"
        )

    def test_fake_sql_cross_action_chain_equivalence(
        self, db_session: Session,
    ) -> None:
        """Fake 와 SQL 모두 cross-action_type chain 에서 동일하게 빈 결과."""
        _seed_fk_rows(db_session)
        orig_id, succ_id = uuid4(), uuid4()
        orig = _rec(
            record_id=orig_id,
            action_type="cash_dividend",
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            superseded_by=succ_id,
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        split = _rec(
            record_id=succ_id,
            action_type="split",
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        fake_repo = FakeDividendRepository(records=[orig, split])

        db_session.add(corporate_action_record_to_orm(split))
        db_session.flush()
        orig_null = _rec(
            record_id=orig_id,
            action_type="cash_dividend",
            announced_date=date(2024, 1, 10),
            effective_date=date(2024, 2, 1),
            created_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        sql_repo = SqlDividendRepository(db_session)
        sql_repo.save_dividends([orig_null])
        sql_repo.update_superseded_by(orig_id, succ_id)

        as_of = date(2024, 3, 1)
        fake_result = list(fake_repo.fetch_dividends(_CODE, as_of=as_of))
        sql_result = list(sql_repo.fetch_dividends(_CODE, as_of=as_of))
        assert fake_result == sql_result == []


# =============================================================================
# 13. details JSON-scalar round-trip contract anchor (수정 3)
#
# details 값은 JSON-scalar(str/int/float/bool/None)만 — Decimal/date 금지. per_share
# 는 문자열 관행. Fake/SQL fetch 후 details 동등 비교로 round-trip contract 고정.
# =============================================================================

class TestDetailsJsonScalarContract:
    """details 가 JSON-safe 값일 때 Fake/SQL fetch round-trip 이 동등하다."""

    def test_fake_sql_details_round_trip_equivalence(
        self, db_session: Session,
    ) -> None:
        _seed_fk_rows(db_session)
        r = _rec(
            announced_date=date(2024, 1, 1),
            effective_date=date(2024, 2, 1),
        )
        # _rec 기본 details = {"per_share": "500", "type": "ordinary"} — JSON-scalar.
        fake_repo = FakeDividendRepository(records=[r])
        sql_repo = SqlDividendRepository(db_session)
        sql_repo.save_dividends([r])

        as_of = date(2024, 3, 1)
        fake_result = list(fake_repo.fetch_dividends(_CODE, as_of=as_of))
        sql_result = list(sql_repo.fetch_dividends(_CODE, as_of=as_of))
        assert len(fake_result) == len(sql_result) == 1
        # JSON-scalar round-trip contract anchor — Fake/SQL details 동등.
        assert dict(fake_result[0].details) == dict(sql_result[0].details)
        assert dict(sql_result[0].details) == {
            "per_share": "500", "type": "ordinary",
        }
        # per_share 는 문자열로 저장 (Decimal 금지 — JSON round-trip 비대칭 방지).
        assert sql_result[0].details["per_share"] == "500"
