"""screen read-bypass 실 SQLite end-to-end serve==live 테스트 (ⓓ Slice 1b, oracle Low).

단위 테스트(test_api/test_screen_read_bypass.py)는 Fake repo 로 serve/live 를 검증
했으나, **producer(precompute 배치)가 실제로 쓴 snapshot 을 consumer(screen)가
byte-동일하게 serve 하는** end-to-end 경로(실 SQLite + run_snapshot_job + screen
serve/live)는 미검증이었다. 본 테스트가 그 §2.10 최강 가드를 채운다:

    1. 실 SQLite 에 stock + 4분기 basic_eps financial 시드.
    2. run_snapshot_job(pack=DEFAULT_PACK) 로 snapshot 실제 생산(producer freeze
       data_versions = collect_run_data_versions(observed_date, session, pack)).
    3. screen_active_codes 를 **serve 경로**(SqlStockSnapshotRepository + pack-aware
       current_dv)와 **live 경로**(snapshot_repo None) 양쪽 실행.
    4. **result_codes byte-동일**(§2.10) + serve 가 실제 hit(raising evaluator 미발동)
       검증 → producer/consumer fingerprint 일치(oracle Low)·byte-동일 실증.

eps 조건만 사용(financial-only — price/dividend 데이터 없이 결정적 평가). DEFAULT_PACK
사용으로 producer/consumer factor_pack_content_hash 자동 일치.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.api.routes.screen import screen_active_codes
from app.db.converters import (
    citation_record_to_orm,
    financial_record_to_orm,
    stocks_master_record_to_orm,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    FinancialRecord,
    StockMasterRecord,
)
from app.repositories.sql_repositories import (
    SqlCorporateActionRepository,
    SqlFinancialRepository,
    SqlMarketCapRepository,
    SqlPriceRepository,
    SqlStocksMasterRepository,
    SqlStockSnapshotRepository,
    SqlTreasurySharesRepository,
)
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from app.services.snapshot_versions import collect_run_data_versions
from batch.snapshot_daily import run_snapshot_job

_OBSERVED = date(2024, 5, 7)
_CITATION = UUID("00000000-0000-0000-0000-00000000ffff")
_BATCH = UUID("00000000-0000-0000-0000-0000000000bb")
_EPS_ACCOUNT = "basic_eps"
_CONSOLIDATED = "consolidated"
_EPS_FACTOR_ID = "eps:basic-ttm-consolidated-ifrs"
_CODES = ("000001", "000002", "000003")


class _RaisingEvaluator:
    """evaluate 호출 시 raise — serve 가 평가를 skip 했음을 입증."""

    def evaluate(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("evaluate 호출됨 — snapshot serve 실패(평가 미skip)")


def _seed(session: Session) -> None:
    """batch_run + DART citation + stock + 4분기 basic_eps financial(FK 순서 flush)."""
    session.add(BatchRunORM(
        id=_BATCH, market=None, source="DART",
        started_at=datetime(2024, 5, 6, 9, 0, tzinfo=UTC),
        ended_at=datetime(2024, 5, 6, 9, 5, tzinfo=UTC),
        success_count=1, status=BATCH_STATUS_SUCCESS,
    ))
    session.flush()
    session.add(citation_record_to_orm(SourceCitation(
        id=_CITATION, source=SourceKind.DART, identifier="20240506000001",
        retrieved_at=datetime(2024, 5, 6, 9, 0, tzinfo=UTC),
        effective_date=date(2024, 5, 1), adapter_version="1.0.0",
        batch_id=_BATCH, url=None,
        created_at=datetime(2024, 5, 6, 9, 0, tzinfo=UTC),
    )))
    session.flush()
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    effs = [date(2023, 5, 15), date(2023, 8, 14), date(2023, 11, 14),
            date(2024, 3, 30)]
    for i, code in enumerate(_CODES):
        session.add(stocks_master_record_to_orm(StockMasterRecord(
            id=UUID(int=i + 1), current_code=code, current_name=f"e2e{i}",
            market="KOSPI", listing_date=date(2000, 1, 1), delisting_date=None,
            fiscal_month=12,
            code_history=(CodeHistoryEntry(
                code, date(2000, 1, 1), None, "initial_listing"),),
            ifrs_preference_default="AUTO",
        )))
        # TTM eps = 분기별 (1000 + i*500) 합 → 종목별 다른 eps 로 조건 분기 검증.
        each = Decimal(1000 + i * 500)
        for fp, eff in zip(periods, effs, strict=True):
            session.add(financial_record_to_orm(FinancialRecord(
                id=uuid4(), code=code, code_lineage_id=UUID(int=i + 1),
                effective_date=eff, fiscal_period=fp, account=_EPS_ACCOUNT,
                value=each, unit="krw", ifrs_type=_CONSOLIDATED,
                citation_id=_CITATION, superseded_by=None,
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
            )))
    session.flush()


def _build_repos(session: Session) -> dict:
    return dict(
        stocks_repo=SqlStocksMasterRepository(session),
        price_repo=SqlPriceRepository(session),
        financial_repo=SqlFinancialRepository(session),
        corporate_action_repo=SqlCorporateActionRepository(session),
        market_cap_repo=SqlMarketCapRepository(session),
        treasury_repo=SqlTreasurySharesRepository(session),
    )


def test_screen_serve_byte_identical_to_live_e2e(db_session: Session) -> None:
    """실 snapshot(producer 산출)을 screen 이 serve → live 와 result_codes byte-동일."""
    _seed(db_session)
    # producer — DEFAULT_PACK 전 factor 평가 후 snapshot UPSERT(eps 는 값, 가격류 N/A).
    run_snapshot_job(session=db_session, observed_date=_OBSERVED, pack=DEFAULT_PACK)
    db_session.flush()

    # eps TTM: code0=4000, code1=6000, code2=8000. 조건 eps > 5000 → code1,2 통과.
    conditions = [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="5000")]
    repos = _build_repos(db_session)
    # producer 와 동일 호출(pack-aware) → factor_pack_content_hash + batch_id 일치.
    current_dv = dict(collect_run_data_versions(_OBSERVED, db_session, DEFAULT_PACK))

    # serve 경로 — raising evaluator: serve hit 면 evaluate 미호출(평가 skip 입증).
    served_codes = screen_active_codes(
        as_of=_OBSERVED, conditions=conditions, pack=DEFAULT_PACK,
        evaluator=_RaisingEvaluator(),  # type: ignore[arg-type]
        snapshot_repo=SqlStockSnapshotRepository(db_session),
        current_data_versions=current_dv,
        **repos,
    )
    # live 경로 — snapshot_repo None, 실 evaluator.
    live_codes = screen_active_codes(
        as_of=_OBSERVED, conditions=conditions, pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
        snapshot_repo=None, current_data_versions=None,
        **repos,
    )

    # §2.10 핵심 — serve 와 live 가 byte-동일 result_codes.
    assert served_codes == live_codes
    # 조건 분기 확인(eps>5000 → code1(6000)·code2(8000)).
    assert served_codes == ("000002", "000003")


def test_screen_serve_stale_dv_falls_back_to_live_e2e(db_session: Session) -> None:
    """current_dv 가 snapshot 과 불일치(stale)면 serve 비활성 → live(raising 발동→raise)."""
    import pytest

    _seed(db_session)
    run_snapshot_job(session=db_session, observed_date=_OBSERVED, pack=DEFAULT_PACK)
    db_session.flush()

    conditions = [ConditionIn(factor=_EPS_FACTOR_ID, op=OpEnum.GT, value="5000")]
    repos = _build_repos(db_session)
    stale_dv = {"factor_pack_content_hash": "sha256:STALE", "krx_batch_id": "x"}

    # stale dv → try_serve_snapshot_values mismatch → 전부 live → RaisingEvaluator raise.
    with pytest.raises(AssertionError, match="evaluate 호출됨"):
        screen_active_codes(
            as_of=_OBSERVED, conditions=conditions, pack=DEFAULT_PACK,
            evaluator=_RaisingEvaluator(),  # type: ignore[arg-type]
            snapshot_repo=SqlStockSnapshotRepository(db_session),
            current_data_versions=stale_dv,
            **repos,
        )
