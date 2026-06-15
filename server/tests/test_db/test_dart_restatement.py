"""DART 정정공시 supersede chain 오케스트레이터 — 실 SQLite end-to-end 테스트.

`DartDailyBatch._process_company` 의 write phase 가 (code, fiscal_period, ifrs_type)
그룹마다 active rcept_no 와 fetched rcept_no 를 비교해 **skip / insert / restate** 를
결정하는지, 그리고 restate 시 옛 active row 가 새 row 로 supersede 되는지를 실
SQLite 세션(citation JOIN 으로 rcept_no 도출, SAVEPOINT/flush)으로 검증한다.

§2.10 재현성이 최고위험 — byte-동일 보존(비정정 케이스)·cutoff-reproduce(정정 전
값 serve)·중복 active head 0(no-UNIQUE 가드)을 핵심으로 본다.

테스트 매트릭스 (oracle 지정):
    1. 첫 공시 insert (superseded_by None).
    2. idempotent 재실행 skip — 중복 active head 없음 assert.
    3. 단일 정정 supersede (옛→새, 값/effective_date 갱신).
    4. depth-3 chain (active head 만 supersede).
    5. dropped-account 옛 row active 유지.
    6. new-only account insert.
    7. CFS/OFS 독립 정정 (cross-ifrs supersede 안 됨).
    8. cutoff-reproduce: 중간 as_of 에서 정정-전 값 serve (load-bearing §2.10).
    9. 같은날(같은 effective_date) 정정 → successor 로 해소.
    10. effective_date regression 정정 → alert 발생 (+chain write 됨).
    11. dry_run write 0.
    12. treasury 정정.

(13. fetch_active_disclosure 다중 rcept_no corruption raise 는 Fake 단위 테스트
 tests/test_repositories/test_active_disclosure.py 가 커버.)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import IfrsType
from app.adapters.dart_adapter import DartAdapter
from app.db.converters import citation_record_to_orm, financial_record_to_orm
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.db.orm.financials import FinancialORM
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.batch_run_repository import BatchCutoff, SqlBatchRunRepository
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.pit_protocols import FinancialRecord
from app.repositories.sql_repositories import (
    SqlFinancialRepository,
    SqlTreasurySharesRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.pit_enforcer import PITDataCorruptionError
from batch.alerts import BatchAlertHandler
from batch.dart_daily import DartBatchSummary, DartDailyBatch

_CORP = "00126380"
_CODE = "005930"
_MAPPING = CorpCodeMapping.from_dict({_CODE: _CORP})


# =============================================================================
# Helpers — DART mock 응답 + 배치 실행
# =============================================================================

def _row(
    *,
    account_id: str = "ifrs-full_Assets",
    amount: str = "100",
    rcept_no: str = "20240501000123",
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "account_nm": account_id,
        "thstrm_amount": amount,
        "rcept_no": rcept_no,
        "currency": "KRW",
        "sj_div": "BS",
        "bsns_year": "2023",
        "reprt_code": "11011",
    }


def _response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "000", "message": "정상", "list": rows}


def _handler_for(
    *,
    cfs_rows: list[dict[str, Any]],
    ofs_rows: list[dict[str, Any]] | None = None,
    treasury_rcept_no: str | None = None,
    treasury_shares: int | None = None,
) -> Any:
    """fs_div / endpoint 에 따라 CFS·OFS·treasury 응답 분기하는 httpx handler.

    매 호출 시 mutable list 의 **현재 상태**를 반환하므로, 테스트가 batch run 사이에
    list 내용을 교체하면 정정 시나리오(같은 그룹 새 rcept_no 재공시)를 만든다.
    """
    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("stockTotqySttus.json"):
            if treasury_rcept_no is None:
                # treasury 미사용 시나리오 — 빈 응답.
                return httpx.Response(200, json=_response([]))
            return httpx.Response(200, json={
                "status": "000", "message": "정상",
                "list": [{
                    "se": "보통주",
                    "rcept_no": treasury_rcept_no,
                    "tesstk_co": str(treasury_shares or 0),
                    "istc_totqy": "1000000",
                }],
            })
        fs_div = request.url.params.get("fs_div", "")
        if fs_div == "CFS":
            return httpx.Response(200, json=_response(cfs_rows))
        if fs_div == "OFS":
            return httpx.Response(200, json=_response(ofs_rows or []))
        return httpx.Response(400, text=f"unknown fs_div: {fs_div}")
    return _handler


def _make_batch(
    session: Session,
    handler: Any,
    *,
    with_treasury: bool = False,
    alert_handler: BatchAlertHandler | None = None,
    ifrs_types: Any = None,
) -> DartDailyBatch:
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    adapter = DartAdapter(http_client=client, api_key="test_key")
    treasury_repo = SqlTreasurySharesRepository(session) if with_treasury else None
    kwargs: dict[str, Any] = dict(
        adapter=adapter,
        corp_mapping=_MAPPING,
        citation_repo=SqlCitationRepository(session),
        financial_repo=SqlFinancialRepository(session),
        treasury_repo=treasury_repo,
        throttle_seconds=0,
        session=session,
        batch_run_repo=SqlBatchRunRepository(session),
        alert_handler=alert_handler,
    )
    if ifrs_types is not None:
        kwargs["ifrs_types"] = ifrs_types
    return DartDailyBatch(**kwargs)


def _run(batch: DartDailyBatch, *, dry_run: bool = False) -> DartBatchSummary:
    summary = batch.run(
        stock_codes=[_CODE],
        fiscal_year=2023,
        fiscal_quarter=4,
        dry_run=dry_run,
    )
    return summary


def _active(session: Session) -> list[tuple[str, str, str]]:
    """현재 active(superseded_by IS NULL) financial 의 (account, value, ifrs) list."""
    rows = session.execute(
        select(FinancialORM).where(FinancialORM.superseded_by.is_(None))
    ).scalars().all()
    return sorted(
        (r.account, str(r.value), r.ifrs_type) for r in rows
    )


def _count_active_heads(
    session: Session, *, account: str, ifrs_type: str = "consolidated",
) -> int:
    """no-UNIQUE 가드 핵심 — 한 (code, fiscal_period, account, ifrs) 의 active 수."""
    rows = session.execute(
        select(FinancialORM).where(
            FinancialORM.code == _CODE,
            FinancialORM.fiscal_period == "2023Q4",
            FinancialORM.account == account,
            FinancialORM.ifrs_type == ifrs_type,
            FinancialORM.superseded_by.is_(None),
        )
    ).scalars().all()
    return len(rows)


class _CapturingAlert:
    """on_failure 메시지를 수집하는 테스트용 alert handler (BatchAlertHandler 구조)."""

    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []

    def on_failure(self, code: str, reason: str) -> None:
        self.failures.append((code, reason))

    def on_complete(self, summary: object) -> None:
        pass


# =============================================================================
# 1. 첫 공시 insert (superseded_by None)
# =============================================================================

def test_first_disclosure_insert(db_session: Session) -> None:
    handler = _handler_for(
        cfs_rows=[_row(account_id="ifrs-full_Assets", amount="100",
                       rcept_no="20240501000001")],
        ofs_rows=[],
    )
    summary = _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    assert summary.success_count == 1
    assert summary.total_rows_saved == 1
    assert _active(db_session) == [("total_assets", "100.0000", "consolidated")]
    assert _count_active_heads(db_session, account="total_assets") == 1


# =============================================================================
# 2. idempotent 재실행 skip — 중복 active head 없음
# =============================================================================

def test_idempotent_rerun_skips_no_duplicate_head(db_session: Session) -> None:
    handler = _handler_for(
        cfs_rows=[_row(amount="100", rcept_no="20240501000001")],
        ofs_rows=[],
    )
    batch = _make_batch(db_session, handler, ifrs_types=_cfs_only())
    s1 = _run(batch)
    assert s1.total_rows_saved == 1
    # 같은 rcept_no 재공시 → 전체 skip (citation/insert/supersede 모두 안 함).
    s2 = _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    assert s2.total_rows_saved == 0  # skip — row 미증가.
    # no-UNIQUE 가드 핵심 — 중복 active head 가 생기지 않았다.
    assert _count_active_heads(db_session, account="total_assets") == 1
    assert _active(db_session) == [("total_assets", "100.0000", "consolidated")]


# =============================================================================
# 3. 단일 정정 supersede (옛→새, 값/effective_date 갱신)
# =============================================================================

def test_single_restatement_supersedes(db_session: Session) -> None:
    cfs = [_row(amount="100", rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # 정정 — 같은 account, 새 rcept_no(더 늦은 날짜), 값 변경.
    cfs[:] = [_row(amount="150", rcept_no="20240815000009")]
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # active head 는 새 값 1건만.
    assert _count_active_heads(db_session, account="total_assets") == 1
    assert _active(db_session) == [("total_assets", "150.0000", "consolidated")]
    # 옛 row 는 superseded — restatement_history 가 2 vintage.
    repo = SqlFinancialRepository(db_session)
    hist = repo.fetch_restatement_history(_CODE, fiscal_period="2023Q4")
    assert len(hist) == 2
    superseded = [h for h in hist if h.superseded_by is not None]
    assert len(superseded) == 1
    assert str(superseded[0].value) == "100.0000"


# =============================================================================
# 4. depth-3 chain (active head 만 supersede)
# =============================================================================

def test_depth_three_chain(db_session: Session) -> None:
    cfs = [_row(amount="100", rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    cfs[:] = [_row(amount="150", rcept_no="20240601000002")]
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    cfs[:] = [_row(amount="175", rcept_no="20240701000003")]
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # active head 는 항상 1건 (최신 만).
    assert _count_active_heads(db_session, account="total_assets") == 1
    assert _active(db_session) == [("total_assets", "175.0000", "consolidated")]
    repo = SqlFinancialRepository(db_session)
    hist = repo.fetch_restatement_history(_CODE, fiscal_period="2023Q4")
    assert len(hist) == 3  # 3 vintage 누적.


# =============================================================================
# 5. dropped-account 옛 row active 유지
# =============================================================================

def test_dropped_account_old_row_stays_active(db_session: Session) -> None:
    cfs = [
        _row(account_id="ifrs-full_Assets", amount="100",
             rcept_no="20240501000001"),
        _row(account_id="ifrs-full_Equity", amount="40",
             rcept_no="20240501000001"),
    ]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # 정정 — Equity 가 정정에서 drop (Assets 만 재공시).
    cfs[:] = [_row(account_id="ifrs-full_Assets", amount="120",
                   rcept_no="20240815000009")]
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # Assets 는 새 값으로 supersede, Equity(dropped)는 옛 값 active 유지(보수적).
    assert _active(db_session) == [
        ("total_assets", "120.0000", "consolidated"),
        ("total_equity", "40.0000", "consolidated"),
    ]
    assert _count_active_heads(db_session, account="total_assets") == 1
    assert _count_active_heads(db_session, account="total_equity") == 1


# =============================================================================
# 6. new-only account insert
# =============================================================================

def test_new_only_account_inserted(db_session: Session) -> None:
    cfs = [_row(account_id="ifrs-full_Assets", amount="100",
                rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # 정정 — Assets 갱신 + Liabilities 신규(new-only).
    cfs[:] = [
        _row(account_id="ifrs-full_Assets", amount="120",
             rcept_no="20240815000009"),
        _row(account_id="ifrs-full_Liabilities", amount="80",
             rcept_no="20240815000009"),
    ]
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    assert _active(db_session) == [
        ("total_assets", "120.0000", "consolidated"),
        ("total_liabilities", "80.0000", "consolidated"),
    ]
    # new-only 도 active head 1건.
    assert _count_active_heads(db_session, account="total_liabilities") == 1


# =============================================================================
# 7. CFS/OFS 독립 정정 (cross-ifrs supersede 안 됨)
# =============================================================================

def test_cfs_ofs_independent_restatement(db_session: Session) -> None:
    cfs = [_row(account_id="ifrs-full_Assets", amount="100",
                rcept_no="20240501000001")]
    ofs = [_row(account_id="ifrs-full_Assets", amount="90",
                rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=ofs)
    _run(_make_batch(db_session, handler))  # CFS+OFS 둘 다.
    # CFS 만 정정 (OFS 는 같은 rcept_no = skip).
    cfs[:] = [_row(account_id="ifrs-full_Assets", amount="130",
                   rcept_no="20240815000009")]
    _run(_make_batch(db_session, handler))
    # CFS 는 새 값, OFS 는 옛 값 그대로 (cross-ifrs supersede 없음).
    assert _active(db_session) == [
        ("total_assets", "130.0000", "consolidated"),
        ("total_assets", "90.0000", "separate"),
    ]
    assert _count_active_heads(
        db_session, account="total_assets", ifrs_type="consolidated") == 1
    assert _count_active_heads(
        db_session, account="total_assets", ifrs_type="separate") == 1


# =============================================================================
# 8. cutoff-reproduce: 중간 as_of 에서 정정-전 값 serve (load-bearing §2.10)
# =============================================================================

def test_cutoff_reproduce_serves_pre_restatement_value(
    db_session: Session,
) -> None:
    # 첫 공시 (batch A) → 정정 (batch B). batch A 의 cutoff 로 fetch 하면 정정 전
    # 값(100)이 serve 돼야 한다 (frozen 이하 candidate 한정 + 보수 복원, C#1).
    cfs = [_row(amount="100", rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    s_a = _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    cfs[:] = [_row(amount="150", rcept_no="20240815000009")]
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))

    repo = SqlFinancialRepository(db_session)
    as_of = date(2024, 12, 31)
    # cutoff 없음 → 현재 active(정정 후 150).
    latest = repo.fetch_financials(
        _CODE, as_of=as_of, account="total_assets", ifrs_type="consolidated",
    )
    assert [str(r.value) for r in latest] == ["150.0000"]
    # batch A cutoff → 정정(batch B) row 가 candidate 에서 빠져 옛 row 가 보수
    # 복원(superseded_by 의 successor 부재) → 정정 전 값 100 재현.
    cutoff = _dart_cutoff(db_session, s_a.batch_id)
    reproduced = repo.fetch_financials(
        _CODE, as_of=as_of, account="total_assets",
        ifrs_type="consolidated", batch_cutoff=cutoff,
    )
    assert [str(r.value) for r in reproduced] == ["100.0000"]


# =============================================================================
# 9. 같은날(같은 effective_date) 정정 → successor 로 해소
# =============================================================================

def test_same_day_restatement(db_session: Session) -> None:
    # 같은 effective_date(같은 rcept_no 앞 8자리)지만 rcept_no 자체는 다름 →
    # 정정으로 처리 (active rcept_no != fetched rcept_no).
    cfs = [_row(amount="100", rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    cfs[:] = [_row(amount="150", rcept_no="20240501000077")]  # 같은날 다른 rcept.
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # successor(150)로 해소 — active head 1건.
    assert _count_active_heads(db_session, account="total_assets") == 1
    assert _active(db_session) == [("total_assets", "150.0000", "consolidated")]


# =============================================================================
# 10. effective_date regression 정정 → alert 발생 (+chain write 됨)
# =============================================================================

def test_effective_date_regression_alerts_and_writes(
    db_session: Session,
) -> None:
    cfs = [_row(amount="100", rcept_no="20240815000001")]  # eff=2024-08-15.
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    alert = _CapturingAlert()
    _run(_make_batch(db_session, handler, alert_handler=alert,
                     ifrs_types=_cfs_only()))
    # 정정 successor 의 effective_date 가 더 이르다(2024-05-01 < 2024-08-15) =
    # regression. chain 은 사실대로 write 하되 alert 발생.
    cfs[:] = [_row(amount="150", rcept_no="20240501000009")]  # eff=2024-05-01.
    alert2 = _CapturingAlert()
    _run(_make_batch(db_session, handler, alert_handler=alert2,
                     ifrs_types=_cfs_only()))
    # regression alert 가 잡혔다.
    assert any("regression" in reason for _, reason in alert2.failures)
    # chain 은 그대로 write — successor(150)가 active head.
    assert _count_active_heads(db_session, account="total_assets") == 1
    assert _active(db_session) == [("total_assets", "150.0000", "consolidated")]


# =============================================================================
# 11. dry_run write 0
# =============================================================================

def test_dry_run_writes_nothing(db_session: Session) -> None:
    # 첫 공시 적재 (정상).
    cfs = [_row(amount="100", rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # 정정인데 dry_run → write 0 (supersede 도 안 함).
    cfs[:] = [_row(amount="150", rcept_no="20240815000009")]
    s = _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()),
             dry_run=True)
    assert s.total_rows_saved == 0
    # 옛 값 그대로 — supersede 미실행.
    assert _active(db_session) == [("total_assets", "100.0000", "consolidated")]
    assert _count_active_heads(db_session, account="total_assets") == 1


# =============================================================================
# 12. treasury 정정
# =============================================================================

def test_treasury_restatement(db_session: Session) -> None:
    handler = _handler_for(
        cfs_rows=[_row(amount="100", rcept_no="20240501000001")],
        ofs_rows=[],
        treasury_rcept_no="20240501000001",
        treasury_shares=5000,
    )
    _run(_make_batch(db_session, handler, with_treasury=True,
                     ifrs_types=_cfs_only()))
    repo = SqlTreasurySharesRepository(db_session)
    rec = repo.fetch_latest_active(_CODE, as_of=date(2024, 12, 31))
    assert rec is not None and rec.shares_treasury == 5000
    # 정정 — 새 rcept_no, 새 자사주 수.
    handler2 = _handler_for(
        cfs_rows=[_row(amount="100", rcept_no="20240501000001")],
        ofs_rows=[],
        treasury_rcept_no="20240815000009",
        treasury_shares=6000,
    )
    _run(_make_batch(db_session, handler2, with_treasury=True,
                     ifrs_types=_cfs_only()))
    rec2 = repo.fetch_latest_active(_CODE, as_of=date(2024, 12, 31))
    assert rec2 is not None and rec2.shares_treasury == 6000
    # 옛 active 는 superseded — 단일 active head.
    active_rcept, active_row = repo.fetch_active_treasury_disclosure(
        _CODE, "2023Q4",
    )
    assert active_rcept == "20240815000009"
    assert active_row is not None and active_row.shares_treasury == 6000


# =============================================================================
# 13. fetch_active_disclosure 다중 rcept_no → corruption raise (SQL 경로)
# =============================================================================

def test_sql_multi_active_head_raises_corruption(db_session: Session) -> None:
    # 수동 DB corruption — 같은 (code, fiscal_period, ifrs, account-disjoint)에
    # 서로 다른 rcept_no 의 active row 2건을 직접 seed (no-UNIQUE 환경 모사).
    batch_id = UUID("00000000-0000-0000-0000-0000000000bb")
    c1 = UUID("00000000-0000-0000-0000-0000000011a1")
    c2 = UUID("00000000-0000-0000-0000-0000000011a2")
    db_session.add(BatchRunORM(
        id=batch_id, market=None, source="DART",
        started_at=datetime(2024, 5, 1, 9, 0, tzinfo=UTC),
        ended_at=datetime(2024, 5, 1, 9, 5, tzinfo=UTC),
        success_count=1, status=BATCH_STATUS_SUCCESS,
    ))
    for cid, ident in ((c1, "20240501000001"), (c2, "20240502000002")):
        db_session.add(citation_record_to_orm(SourceCitation(
            id=cid, source=SourceKind.DART, identifier=ident,
            retrieved_at=datetime(2024, 5, 1, 9, 0, tzinfo=UTC),
            effective_date=date(2024, 5, 1), adapter_version="1.0.0",
            batch_id=batch_id, url=None,
            created_at=datetime(2024, 5, 1, 9, 0, tzinfo=UTC),
        )))
    db_session.flush()
    for cid, acct in ((c1, "total_assets"), (c2, "total_equity")):
        db_session.add(financial_record_to_orm(FinancialRecord(
            id=uuid4(), code=_CODE,
            code_lineage_id=UUID("00000000-0000-0000-0000-0000000000aa"),
            effective_date=date(2024, 5, 1), fiscal_period="2023Q4",
            account=acct, value=Decimal("100"), unit="krw",
            ifrs_type="consolidated", citation_id=cid, superseded_by=None,
            created_at=datetime(2024, 5, 1, tzinfo=UTC),
        )))
    db_session.flush()

    repo = SqlFinancialRepository(db_session)
    with pytest.raises(PITDataCorruptionError, match="multiple active rcept_no"):
        repo.fetch_active_disclosure(_CODE, "2023Q4", "consolidated")


# =============================================================================
# 14. M1 가드 — 단일 fetch 내 중복 canonical account → corruption + failure 격리
# =============================================================================

def test_duplicate_account_in_single_fetch_raises_and_isolates(
    db_session: Session,
) -> None:
    """oracle M1 — 같은 fetch 결과에 같은 canonical account 가 2건이면
    생성-시점에 PITDataCorruptionError → 회사 단위 failure 로 격리되고 active
    head 중복이 애초에 생성되지 않는다.

    두 CFS row 가 같은 account_id(ifrs-full_Assets) → 둘 다 canonical
    'total_assets' 로 매핑 → db_records 에 같은 account 2건.
    """
    handler = _handler_for(
        cfs_rows=[
            _row(account_id="ifrs-full_Assets", amount="100",
                 rcept_no="20240501000001"),
            _row(account_id="ifrs-full_Assets", amount="200",
                 rcept_no="20240501000001"),
        ],
        ofs_rows=[],
    )
    summary = _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    # 회사 단위 failure 로 격리 — 배치는 raise 하지 않고 summary 로 반환.
    assert summary.failure_count == 1
    assert summary.success_count == 0
    reason = summary.failures[0][1]
    assert "duplicate canonical account" in reason
    assert "total_assets" in reason
    # SAVEPOINT ROLLBACK — bad state 가 생성되지 않았다 (active head 0건).
    assert _active(db_session) == []
    assert _count_active_heads(db_session, account="total_assets") == 0
    # financials row 자체가 전혀 영구화되지 않았다 (citation·insert 모두 롤백).
    all_rows = db_session.execute(select(FinancialORM)).scalars().all()
    assert len(all_rows) == 0


def test_duplicate_account_guard_also_on_restate(db_session: Session) -> None:
    """M1 가드는 restate 경로에도 적용 — 정정 fetch 가 중복 account 면 corruption
    이고 옛 active 는 그대로 유지된다 (supersede 미실행, ROLLBACK)."""
    cfs = [_row(account_id="ifrs-full_Assets", amount="100",
                rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    assert _active(db_session) == [("total_assets", "100.0000", "consolidated")]
    # 정정 fetch 가 같은 account 2건 (새 rcept_no) → restate 진입 전 가드 raise.
    cfs[:] = [
        _row(account_id="ifrs-full_Assets", amount="150",
             rcept_no="20240815000009"),
        _row(account_id="ifrs-full_Assets", amount="250",
             rcept_no="20240815000009"),
    ]
    summary = _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))
    assert summary.failure_count == 1
    assert "duplicate canonical account" in summary.failures[0][1]
    # 옛 active 그대로 — supersede 미실행, 새 insert 롤백.
    assert _active(db_session) == [("total_assets", "100.0000", "consolidated")]
    assert _count_active_heads(db_session, account="total_assets") == 1


# =============================================================================
# 15. M2 롤백 — restate 중간 실패 시 per-company SAVEPOINT 전체 ROLLBACK
# =============================================================================

def test_restate_mid_failure_rolls_back(
    db_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """oracle M2 — 정정(restate) 도중 update_superseded_by 가 raise 하면
    per-company SAVEPOINT(begin_nested)가 citation + 새 insert 를 전부 ROLLBACK
    → 옛 active head/row count 가 정정 시도 전과 동일하다. 배치는 raise 를 삼켜
    summary.failure_count 로 격리한다 (회사 단위 isolation 패턴).
    """
    cfs = [_row(amount="100", rcept_no="20240501000001")]
    handler = _handler_for(cfs_rows=cfs, ofs_rows=[])
    _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))

    # 정정 시도 직전 상태 스냅샷.
    before_active = _active(db_session)
    before_total = len(
        db_session.execute(select(FinancialORM)).scalars().all()
    )
    assert before_active == [("total_assets", "100.0000", "consolidated")]

    # 정정 fetch — update_superseded_by 가 chain 거는 단계에서 raise 하도록
    # SqlFinancialRepository.update_superseded_by 를 monkeypatch.
    cfs[:] = [_row(amount="150", rcept_no="20240815000009")]

    def _boom(self: Any, record_id: UUID, successor_id: UUID) -> None:
        raise RuntimeError("simulated mid-restate failure")

    monkeypatch.setattr(
        SqlFinancialRepository, "update_superseded_by", _boom,
    )
    summary = _run(_make_batch(db_session, handler, ifrs_types=_cfs_only()))

    # 배치가 raise 를 삼키고 회사 단위 failure 로 격리.
    assert summary.failure_count == 1
    assert summary.success_count == 0
    assert "simulated mid-restate failure" in summary.failures[0][1]

    # SAVEPOINT ROLLBACK — citation·새 insert 전부 되돌아가 정정 전과 동일.
    after_total = len(
        db_session.execute(select(FinancialORM)).scalars().all()
    )
    assert after_total == before_total  # 새 row 가 롤백됨.
    assert _active(db_session) == before_active  # 옛 active head 그대로(150 아님).
    assert _count_active_heads(db_session, account="total_assets") == 1


# =============================================================================
# 내부 helper
# =============================================================================

def _cfs_only() -> Any:
    return (IfrsType.CFS,)


def _dart_cutoff(session: Session, batch_id: UUID) -> Any:
    """주어진 DART batch_id 의 (started_at, id) 를 BatchCutoff 로."""
    run = session.get(BatchRunORM, batch_id)
    assert run is not None
    return BatchCutoff(started_at=run.started_at, id=batch_id)
