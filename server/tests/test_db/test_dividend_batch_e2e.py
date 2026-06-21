"""DividendDailyBatch SQL-backed e2e — 실 SQLite 세션으로 Slice 2 통합 검증.

Fake repo 단위테스트(test_batch/test_dividend_daily.py)가 못 잡는 **실 SQL 경로**를
검증한다(운영 적재 전 통합 실버그 차단):

1. **details JSON round-trip → dedup 멱등** — corporate_actions.details 가 SQLite
   JSON 컬럼으로 직렬화/역직렬화된 뒤에도 dedup 키 `(effective_date, type)` 가
   일치해 재실행이 멱등인가(Fake 는 in-memory dict 라 round-trip 미검증). **가장
   중요한 통합 가드** — details["type"] 가 SQL 왕복에서 보존돼야 재실행 dedup 동작.
2. **citation→dividend FK 순서** — PRAGMA foreign_keys=ON 환경에서 citation 먼저
   저장이 FK(corporate_actions.citation_id → source_citations.id)를 만족하는가.
3. **batch_runs source="FSC" → collect_batch_versions** — 배치가 적재한
   batch_runs(FSC) 가 snapshot 의 dividend_batch_id freeze 로 연결되는가(end-to-end).
4. **SAVEPOINT rollback** — 한 종목 save 실패 시 그 종목의 citation+dividend 가
   partial commit 되지 않고 함께 rollback 되는가(begin_nested 격리).

`tests/test_db/conftest.py` 의 `db_session` fixture(in-memory SQLite, FK ON, 전
테이블 create_all)를 사용.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.adapters.base import FetchResult
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.pit_protocols import CorporateActionRecord
from app.repositories.sql_repositories import SqlDividendRepository
from app.services.crno_mapping import CrnoMapping
from app.services.lineage import lineage_id_for_code
from app.services.snapshot_versions import collect_batch_versions
from batch.dividend_daily import DividendDailyBatch

_CODE_A: Final[str] = "005930"
_CODE_B: Final[str] = "000660"
_CRNO_A: Final[str] = "1301110006246"
_CRNO_B: Final[str] = "1301110017990"
_NOW: Final[datetime] = datetime(2024, 1, 1, tzinfo=UTC)
_AS_OF: Final[date] = date(2024, 12, 31)
_BEGIN: Final[str] = "20200101"
_END: Final[str] = "20241231"


def _pair(
    *,
    code: str,
    ex_date: date,
    div_type: str = "현금배당",
    per_share: str = "361",
    batch_id: UUID,
) -> tuple[CorporateActionRecord, SourceCitation]:
    """(record, citation) 배당 쌍 — FscDividendAdapter 산출 모사.

    details 는 JSON-scalar only(per_share 문자열, ADR-0035 D2.2) — 실 SQL JSON
    round-trip 대상.
    """
    crno = _CRNO_A if code == _CODE_A else _CRNO_B
    citation = SourceCitation(
        id=uuid4(),
        source=SourceKind.FSC,
        identifier=f"{crno}|{ex_date.strftime('%Y%m%d')}|{div_type}",
        retrieved_at=_NOW,
        effective_date=ex_date,
        adapter_version="1.0.0",
        batch_id=batch_id,
        url="https://apis.data.go.kr/1160100/.../getDiviInfo_V2",
    )
    record = CorporateActionRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=lineage_id_for_code(code),
        action_type="cash_dividend",
        announced_date=ex_date,
        effective_date=ex_date,
        payment_date=None,
        ratio=None,
        cash_amount=Decimal(per_share),
        details={"per_share": per_share, "type": div_type, "stock_knd": "보통주"},
        citation_id=citation.id,
        superseded_by=None,
        created_at=_NOW,
    )
    return record, citation


def _result(
    pairs: list[tuple[CorporateActionRecord, SourceCitation]],
) -> FetchResult[tuple[CorporateActionRecord, ...]]:
    return FetchResult(
        data=tuple(r for r, _ in pairs),
        citations=tuple(c for _, c in pairs),
        warnings=(),
        estimated_fields=frozenset(),
    )


class _FakeFscAdapter:
    """fetch_cash_dividends 만 구현 — code 별 factory(code, batch_id) → FetchResult."""

    def __init__(self, factory) -> None:
        self._factory = factory

    def fetch_cash_dividends(
        self,
        *,
        code: str,
        crno: str,
        begin_bas_dt: str,
        end_bas_dt: str,
        batch_id: UUID,
        trading_calendar,
    ) -> FetchResult[tuple[CorporateActionRecord, ...]]:
        return self._factory(code, batch_id)

    def close(self) -> None:
        pass


def _make_batch(
    db_session: Session,
    *,
    factory,
    crno_mapping: CrnoMapping,
) -> tuple[DividendDailyBatch, SqlDividendRepository, SqlCitationRepository]:
    dividend_repo = SqlDividendRepository(db_session)
    citation_repo = SqlCitationRepository(db_session)
    batch = DividendDailyBatch(
        adapter=_FakeFscAdapter(factory),  # type: ignore[arg-type]
        crno_mapping=crno_mapping,
        citation_repo=citation_repo,
        dividend_repo=dividend_repo,
        trading_calendar=object(),  # type: ignore[arg-type] — adapter fake 가 무시.
        throttle_seconds=0.0,
        session=db_session,  # SAVEPOINT 활성.
    )
    return batch, dividend_repo, citation_repo


def _mapping_a() -> CrnoMapping:
    return CrnoMapping.from_dict({_CODE_A: _CRNO_A})


# =============================================================================
# 1. 실 SQL 영구화 + FK 순서
# =============================================================================

def test_persists_dividends_and_citations_real_sql(db_session: Session) -> None:
    """배치 run → corporate_actions + source_citations 실 SQL 영구화(FK 만족)."""
    def factory(code, batch_id):
        return _result([
            _pair(code=code, ex_date=date(2024, 3, 28), batch_id=batch_id),
            _pair(code=code, ex_date=date(2024, 6, 27), batch_id=batch_id),
        ])

    batch, drepo, crepo = _make_batch(
        db_session, factory=factory, crno_mapping=_mapping_a(),
    )
    summary = batch.run(
        stock_codes=[_CODE_A], begin_bas_dt=_BEGIN, end_bas_dt=_END,
    )
    assert summary.dividends_saved == 2
    assert summary.failure_count == 0
    # PIT fetch — effective<=as_of AND announced<=as_of 통과.
    stored = drepo.fetch_dividends(_CODE_A, as_of=_AS_OF)
    assert len(stored) == 2
    # citation FK 만족(IntegrityError 없이 저장됨) + batch_id 조회.
    assert len(crepo.fetch_by_batch(summary.batch_id)) == 2


# =============================================================================
# 2. details JSON round-trip → dedup 멱등 (핵심 통합 가드)
# =============================================================================

def test_details_json_roundtrip_dedup_idempotent(db_session: Session) -> None:
    """재실행 → 2nd run 전부 dedup skip. details["type"] 가 SQL JSON 왕복에서 보존.

    Fake 는 in-memory dict 라 round-trip 무검증. 실 SQL 에서 1st run 이 저장한
    details 를 2nd run 의 fetch_all_dividends_bulk 가 JSON 역직렬화로 읽어
    dedup 키 `(effective_date, type)` 를 동일 생성해야 멱등이 성립한다.
    """
    def factory(code, batch_id):
        return _result([
            _pair(code=code, ex_date=date(2024, 3, 28), div_type="결산배당",
                  batch_id=batch_id),
            _pair(code=code, ex_date=date(2024, 6, 27), div_type="중간배당",
                  batch_id=batch_id),
        ])

    # 1st run.
    batch1, drepo, _ = _make_batch(
        db_session, factory=factory, crno_mapping=_mapping_a(),
    )
    first = batch1.run(stock_codes=[_CODE_A], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert first.dividends_saved == 2

    # 2nd run — 같은 결과. dedup 으로 전부 skip(실 SQL details round-trip 검증).
    batch2, _, _ = _make_batch(
        db_session, factory=factory, crno_mapping=_mapping_a(),
    )
    second = batch2.run(stock_codes=[_CODE_A], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert second.dividends_saved == 0
    assert second.dedup_skipped == 2
    # DB 에는 여전히 2건(4건 아님).
    assert len(drepo.fetch_dividends(_CODE_A, as_of=_AS_OF)) == 2


# =============================================================================
# 3. batch_runs source="FSC" → collect_batch_versions dividend_batch_id
# =============================================================================

def test_batch_runs_fsc_feeds_collect_versions(db_session: Session) -> None:
    """배치가 적재한 batch_runs(source=FSC)가 dividend_batch_id 로 연결(end-to-end).

    session 주입 시 batch_run_repo 가 SqlBatchRunRepository 로 auto 구성 →
    start/finalize 가 실 SQL batch_runs row 를 남긴다. collect_batch_versions 가
    그 batch_id 를 dividend_batch_id 키로 반환해야 snapshot freeze 가 동작.
    """
    def factory(code, batch_id):
        return _result([
            _pair(code=code, ex_date=date(2024, 3, 28), batch_id=batch_id),
        ])

    batch, _, _ = _make_batch(
        db_session, factory=factory, crno_mapping=_mapping_a(),
    )
    summary = batch.run(
        stock_codes=[_CODE_A], begin_bas_dt=_BEGIN, end_bas_dt=_END,
    )
    # collect_batch_versions 는 `date(started_at_UTC) <= as_of` 인 FSC max 를 선택.
    # 배치 started_at 은 실 now(UTC)라, tz-behind 머신에서 자정 부근 실행 시 UTC
    # date 가 local today 를 넘어 flaky 할 수 있다 → +1 day 로 어느 tz 든 포함 보장
    # (FSC 배치는 본 run 1건뿐이라 미래 as_of 여도 그 batch_id 반환).
    as_of = date.today() + timedelta(days=1)
    versions = collect_batch_versions(as_of, db_session)
    assert versions["dividend_batch_id"] == str(summary.batch_id)


# =============================================================================
# 4. SAVEPOINT rollback — partial commit 차단
# =============================================================================

def test_savepoint_rollback_isolates_failed_company(db_session: Session) -> None:
    """한 종목 save 실패 → 그 종목 citation+dividend 함께 rollback, 타 종목 영구화.

    begin_nested SAVEPOINT 가 회사별 원자성 보장 — B save 중 예외 시 B 의 citation
    (먼저 저장)도 함께 SAVEPOINT rollback 되어 orphan/partial 이 남지 않는다.
    """
    def factory(code, batch_id):
        return _result([
            _pair(code=code, ex_date=date(2024, 3, 28), batch_id=batch_id),
        ])

    mapping = CrnoMapping.from_dict({_CODE_A: _CRNO_A, _CODE_B: _CRNO_B})
    batch, drepo, crepo = _make_batch(
        db_session, factory=factory, crno_mapping=mapping,
    )

    # B(000660) 의 save_dividends 만 raise 하도록 monkey-patch.
    orig_save = drepo.save_dividends

    def failing_save(records):
        if any(r.code == _CODE_B for r in records):
            raise RuntimeError("simulated B save failure")
        return orig_save(records)

    drepo.save_dividends = failing_save  # type: ignore[method-assign]

    summary = batch.run(
        stock_codes=[_CODE_A, _CODE_B], begin_bas_dt=_BEGIN, end_bas_dt=_END,
    )
    # A 성공, B 실패.
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.failures[0][0] == _CODE_B

    # A 는 영구화.
    assert len(drepo.fetch_dividends(_CODE_A, as_of=_AS_OF)) == 1
    # B 는 SAVEPOINT rollback — dividend + citation 모두 부재(partial commit 0).
    assert drepo.fetch_dividends(_CODE_B, as_of=_AS_OF) == ()
    # batch_id 의 citation 은 A 분 1건만(B citation 도 rollback 됨).
    assert len(crepo.fetch_by_batch(summary.batch_id)) == 1
