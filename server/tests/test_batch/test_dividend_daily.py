"""DividendDailyBatch — 배당 일배치 orchestrator 단위 테스트 (M7 #2 Slice 2).

Fake FscDividendAdapter + FakeDividendRepository/FakeCitationRepository 로 외부
호출·DB 를 격리. 핵심 검증 = **재실행 멱등성(dedup)** + crno 매핑 skip + 데이터
갭 가시화 + failure isolation + dry_run.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid4

from app.adapters.base import AdapterError, FetchResult
from app.adapters.fsc_dividend_adapter import DATA_GAP_PREFIX
from app.models.source_citation import SourceCitation, SourceKind
from app.repositories.citation_repository import FakeCitationRepository
from app.repositories.fakes import FakeDividendRepository
from app.repositories.pit_protocols import CorporateActionRecord
from app.services.crno_mapping import CrnoMapping
from app.services.lineage import lineage_id_for_code
from batch.alerts import BatchAlertHandler
from batch.dividend_daily import DividendDailyBatch

_CODE: Final[str] = "005930"
_CRNO: Final[str] = "1301110006246"
_NOW: Final[datetime] = datetime(2024, 1, 1, tzinfo=UTC)


def _pair(
    *,
    code: str = _CODE,
    ex_date: date,
    div_type: str = "현금배당",
    per_share: str = "361",
    batch_id: UUID,
) -> tuple[CorporateActionRecord, SourceCitation]:
    """(CorporateActionRecord, SourceCitation) 배당 쌍 — adapter 산출 모사."""
    citation = SourceCitation(
        id=uuid4(),
        source=SourceKind.FSC,
        identifier=f"{_CRNO}|{ex_date.strftime('%Y%m%d')}|{div_type}",
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
    *,
    warnings: tuple[str, ...] = (),
) -> FetchResult[tuple[CorporateActionRecord, ...]]:
    return FetchResult(
        data=tuple(r for r, _ in pairs),
        citations=tuple(c for _, c in pairs),
        warnings=warnings,
        estimated_fields=frozenset(),
    )


class _FakeFscAdapter:
    """fetch_cash_dividends 만 구현한 FscDividendAdapter 대역.

    results[code] 의 FetchResult 를 반환(batch_id 는 호출 시 주입되므로, 본 fake 는
    호출 시점에 pair 를 생성하는 factory 를 받는다). raise_for 등록 code 는 예외.
    """

    def __init__(
        self,
        *,
        factory=None,
        raise_for: dict[str, Exception] | None = None,
    ) -> None:
        # factory: (code, batch_id) -> FetchResult. None 이면 빈 결과.
        self._factory = factory
        self._raise_for = raise_for or {}
        self.call_log: list[str] = []
        self.closed = False

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
        self.call_log.append(code)
        if code in self._raise_for:
            raise self._raise_for[code]
        if self._factory is None:
            return _result([])
        return self._factory(code, batch_id)

    def close(self) -> None:
        self.closed = True


class _RecordingAlert(BatchAlertHandler):
    """on_failure / on_complete 호출을 기록."""

    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []
        self.completed = 0

    def on_failure(self, stock_code: str, reason: str) -> None:
        self.failures.append((stock_code, reason))

    def on_complete(self, summary) -> None:
        self.completed += 1


class _FakeBatchRunRepo:
    """batch_runs start/finalize 호출 기록 (영속화 검증용)."""

    def __init__(self) -> None:
        self.started: list[tuple] = []
        self.finalized: list[tuple] = []

    def start(self, *, run_id, market, source, started_at) -> None:
        self.started.append((run_id, market, source, started_at))

    def finalize(self, *, run_id, ended_at, success_count, status) -> None:
        self.finalized.append((run_id, ended_at, success_count, status))


_CAL = object()  # adapter 가 무시(fake) — trading_calendar placeholder.
_BEGIN = "20200101"
_END = "20241231"


def _batch(
    *,
    adapter: _FakeFscAdapter,
    crno_mapping: CrnoMapping,
    dividend_repo: FakeDividendRepository | None = None,
    citation_repo: FakeCitationRepository | None = None,
    alert: BatchAlertHandler | None = None,
    batch_run_repo=None,
) -> tuple[DividendDailyBatch, FakeDividendRepository, FakeCitationRepository]:
    drepo = dividend_repo or FakeDividendRepository(records=())
    crepo = citation_repo or FakeCitationRepository()
    batch = DividendDailyBatch(
        adapter=adapter,  # type: ignore[arg-type]
        crno_mapping=crno_mapping,
        citation_repo=crepo,
        dividend_repo=drepo,
        trading_calendar=_CAL,  # type: ignore[arg-type]
        throttle_seconds=0.0,
        alert_handler=alert,
        batch_run_repo=batch_run_repo,
    )
    return batch, drepo, crepo


def _mapping() -> CrnoMapping:
    return CrnoMapping.from_dict({_CODE: _CRNO})


# =============================================================================
# 정상 적재
# =============================================================================

def test_saves_new_dividends() -> None:
    """배당 2건 → 저장 + summary 집계."""
    def factory(code, batch_id):
        return _result([
            _pair(ex_date=date(2024, 3, 28), per_share="361", batch_id=batch_id),
            _pair(ex_date=date(2024, 6, 27), per_share="361", batch_id=batch_id),
        ])

    adapter = _FakeFscAdapter(factory=factory)
    batch, drepo, crepo = _batch(adapter=adapter, crno_mapping=_mapping())
    summary = batch.run(
        stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END,
    )
    assert summary.dividends_saved == 2
    assert summary.dedup_skipped == 0
    assert summary.success_count == 1
    assert summary.failure_count == 0
    # 실제 저장 확인 (PIT 무관 raw).
    stored = drepo.fetch_all_dividends_bulk((_CODE,))[_CODE]
    assert len(stored) == 2
    # citation 도 저장.
    assert len(crepo.fetch_by_batch(summary.batch_id)) == 2


def test_empty_fetch_is_success_zero_saved() -> None:
    """배당 0건(빈 fetch) → 종목 success, 저장 0."""
    adapter = _FakeFscAdapter(factory=lambda c, b: _result([]))
    batch, drepo, _ = _batch(adapter=adapter, crno_mapping=_mapping())
    summary = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert summary.success_count == 1
    assert summary.dividends_saved == 0


# =============================================================================
# 재실행 멱등성 (dedup) — 핵심
# =============================================================================

def test_idempotent_rerun_dedups() -> None:
    """같은 배당을 두 번 적재 → 2nd run 전부 dedup skip(중복 0).

    record.id 가 uuid4 라 순진 insert 면 중복되나, (effective_date, 배당종류)
    자연키 dedup 으로 first-wins. 재투자 이중계산(§2.10) 방지.
    """
    def factory(code, batch_id):
        return _result([
            _pair(ex_date=date(2024, 3, 28), per_share="361", batch_id=batch_id),
            _pair(ex_date=date(2024, 6, 27), per_share="361", batch_id=batch_id),
        ])

    adapter = _FakeFscAdapter(factory=factory)
    drepo = FakeDividendRepository(records=())
    batch, _, _ = _batch(
        adapter=adapter, crno_mapping=_mapping(), dividend_repo=drepo,
    )
    first = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert first.dividends_saved == 2

    # 2nd run — 같은 결과. dedup 으로 전부 skip.
    second = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert second.dividends_saved == 0
    assert second.dedup_skipped == 2
    # DB 에는 여전히 2건(4건 아님).
    assert len(drepo.fetch_all_dividends_bulk((_CODE,))[_CODE]) == 2


def test_restatement_amount_change_first_wins() -> None:
    """같은 (배당락일, 종류) 인데 per_share 변경(정정) → first-wins skip.

    M7 §1 known-limit — 배당 정정 chain 미구현. dedup 키에 per_share 미포함이라
    금액이 바뀌어도 skip(이중계산 방지, 정정분 미반영은 알려진 한계).
    """
    drepo = FakeDividendRepository(records=())
    a1 = _FakeFscAdapter(factory=lambda c, b: _result([
        _pair(ex_date=date(2024, 3, 28), per_share="361", batch_id=b),
    ]))
    batch1, _, _ = _batch(adapter=a1, crno_mapping=_mapping(), dividend_repo=drepo)
    batch1.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)

    # 같은 배당락일·종류, 금액만 363 으로 정정.
    a2 = _FakeFscAdapter(factory=lambda c, b: _result([
        _pair(ex_date=date(2024, 3, 28), per_share="363", batch_id=b),
    ]))
    batch2, _, _ = _batch(adapter=a2, crno_mapping=_mapping(), dividend_repo=drepo)
    summary = batch2.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert summary.dividends_saved == 0
    assert summary.dedup_skipped == 1
    stored = drepo.fetch_all_dividends_bulk((_CODE,))[_CODE]
    assert len(stored) == 1
    # first-wins — 원래 361 유지.
    assert stored[0].cash_amount == Decimal("361")


def test_dedup_within_single_fetch() -> None:
    """같은 fetch 내 (배당락일, 종류) 중복 2건 → 1건만 저장(방어적 dedup)."""
    def factory(code, batch_id):
        return _result([
            _pair(ex_date=date(2024, 3, 28), div_type="현금배당", batch_id=batch_id),
            _pair(ex_date=date(2024, 3, 28), div_type="현금배당", batch_id=batch_id),
        ])

    adapter = _FakeFscAdapter(factory=factory)
    batch, drepo, _ = _batch(adapter=adapter, crno_mapping=_mapping())
    summary = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert summary.dividends_saved == 1
    assert summary.dedup_skipped == 1


def test_split_at_same_date_does_not_shadow_dividend() -> None:
    """같은 배당락일에 split(타 action_type)이 있어도 배당이 silent drop 되지 않음.

    oracle M2 — fetch_all_dividends_bulk 는 action_type 무관 raw 를 반환. split 의
    details 엔 "type" 키가 없어 _dividend_key 가 (effective_date, "") 가 되는데,
    dedup 이 cash_dividend 만 비교하므로 stckDvdnRcdNm="" 배당과 cross-action_type
    충돌하지 않는다.
    """
    ex = date(2024, 3, 28)
    # 기존 DB 에 같은 날짜 split 1건(details 에 type 없음) 미리 적재.
    split = CorporateActionRecord(
        id=uuid4(), code=_CODE, code_lineage_id=lineage_id_for_code(_CODE),
        action_type="split", announced_date=ex, effective_date=ex,
        payment_date=None, ratio=Decimal("2"), cash_amount=None,
        details={}, citation_id=uuid4(), superseded_by=None, created_at=_NOW,
    )
    # FakeDividendRepository 는 save_dividends 가 cash_dividend 만 허용하므로,
    # split 은 생성자 records 로 직접 주입(raw store).
    drepo = FakeDividendRepository(records=(split,))
    # 같은 날짜에 stckDvdnRcdNm="" 인 현금배당 — split 키 (ex, "") 와 충돌 위험.
    adapter = _FakeFscAdapter(factory=lambda c, b: _result([
        _pair(ex_date=ex, div_type="", per_share="361", batch_id=b),
    ]))
    batch, _, _ = _batch(
        adapter=adapter, crno_mapping=_mapping(), dividend_repo=drepo,
    )
    summary = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    # 배당이 split 에 가려지지 않고 정상 저장.
    assert summary.dividends_saved == 1
    assert summary.dedup_skipped == 0


def test_same_exdate_different_type_both_saved() -> None:
    """같은 배당락일이라도 배당종류가 다르면 별개 배당 → 둘 다 저장."""
    def factory(code, batch_id):
        return _result([
            _pair(ex_date=date(2024, 3, 28), div_type="결산배당", batch_id=batch_id),
            _pair(ex_date=date(2024, 3, 28), div_type="중간배당", batch_id=batch_id),
        ])

    adapter = _FakeFscAdapter(factory=factory)
    batch, drepo, _ = _batch(adapter=adapter, crno_mapping=_mapping())
    summary = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert summary.dividends_saved == 2


# =============================================================================
# crno 매핑 skip
# =============================================================================

def test_crno_mapping_missing_skipped() -> None:
    """crno 매핑 없는 종목 → skip(failure 아님) + alert."""
    alert = _RecordingAlert()
    adapter = _FakeFscAdapter(factory=lambda c, b: _result([]))
    # 매핑에 다른 종목만 → _CODE 미매핑.
    batch, _, _ = _batch(
        adapter=adapter,
        crno_mapping=CrnoMapping.from_dict({"000660": "1301110017990"}),
        alert=alert,
    )
    summary = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert summary.skipped_count == 1
    assert summary.skipped_codes == (_CODE,)
    assert summary.success_count == 0
    # adapter 미호출(매핑 단계에서 skip).
    assert adapter.call_log == []
    assert any("crno mapping missing" in r for _, r in alert.failures)


# =============================================================================
# 데이터 갭 가시화 (§2.1)
# =============================================================================

def test_data_gap_warning_counted_not_failure() -> None:
    """[DATA_GAP] warning → data_gap_count + WARNING 로그. 종목은 여전히 success.

    oracle M1 — data_gap 은 on_failure(종목 실패)로 보내지 않는다(부분 데이터로도
    success). 따라서 alert.failures 에 안 들어가고 data_gap_count + success 동시.
    """
    alert = _RecordingAlert()

    def factory(code, batch_id):
        return _result(
            [_pair(ex_date=date(2024, 3, 28), batch_id=batch_id)],
            warnings=(
                f"{DATA_GAP_PREFIX} 캘린더 범위 밖 배당락일 산출 불가",
                "FSC non-common stock skipped (우선주)",  # benign.
            ),
        )

    adapter = _FakeFscAdapter(factory=factory)
    batch, _, _ = _batch(adapter=adapter, crno_mapping=_mapping(), alert=alert)
    summary = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert summary.data_gap_count == 1
    # 부분 데이터로도 종목은 success — data_gap 은 failure 아님.
    assert summary.success_count == 1
    assert summary.failure_count == 0
    # data_gap·benign 둘 다 on_failure 로 가지 않음(요청 데이터는 정상 저장).
    assert alert.failures == []
    assert summary.dividends_saved == 1


# =============================================================================
# failure isolation
# =============================================================================

def test_adapter_error_isolated() -> None:
    """한 종목 AdapterError → failure 격리, 다른 종목 계속."""
    def factory(code, batch_id):
        return _result([_pair(code=code, ex_date=date(2024, 3, 28), batch_id=batch_id)])

    adapter = _FakeFscAdapter(
        factory=factory,
        raise_for={"005930": AdapterError("HTTP 500")},
    )
    mapping = CrnoMapping.from_dict(
        {"005930": _CRNO, "000660": "1301110017990"},
    )
    batch, drepo, _ = _batch(adapter=adapter, crno_mapping=mapping)
    summary = batch.run(
        stock_codes=["005930", "000660"], begin_bas_dt=_BEGIN, end_bas_dt=_END,
    )
    assert summary.failure_count == 1
    assert summary.success_count == 1
    assert summary.failures[0][0] == "005930"
    # 000660 은 저장됨.
    assert "000660" in drepo.fetch_all_dividends_bulk(("000660",))


# =============================================================================
# dry_run
# =============================================================================

def test_dry_run_no_save() -> None:
    """dry_run=True → dividends_saved 는 신규 수이나 DB write 없음."""
    def factory(code, batch_id):
        return _result([_pair(ex_date=date(2024, 3, 28), batch_id=batch_id)])

    adapter = _FakeFscAdapter(factory=factory)
    batch, drepo, crepo = _batch(adapter=adapter, crno_mapping=_mapping())
    summary = batch.run(
        stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END, dry_run=True,
    )
    assert summary.dividends_saved == 1
    assert summary.dry_run is True
    # DB 미저장.
    assert drepo.fetch_all_dividends_bulk((_CODE,)).get(_CODE, ()) == ()
    assert crepo.fetch_by_batch(summary.batch_id) == ()


# =============================================================================
# batch_runs 영속화 (source FSC)
# =============================================================================

def test_batch_runs_persistence_source_fsc() -> None:
    """batch_run_repo 주입 → start(source=FSC) + finalize(SUCCESS) 호출."""
    repo = _FakeBatchRunRepo()
    adapter = _FakeFscAdapter(factory=lambda c, b: _result([
        _pair(ex_date=date(2024, 3, 28), batch_id=b),
    ]))
    batch, _, _ = _batch(
        adapter=adapter, crno_mapping=_mapping(), batch_run_repo=repo,
    )
    summary = batch.run(stock_codes=[_CODE], begin_bas_dt=_BEGIN, end_bas_dt=_END)
    assert len(repo.started) == 1
    assert repo.started[0][2] == "FSC"  # source.
    assert repo.started[0][0] == summary.batch_id
    assert len(repo.finalized) == 1
    # status — failure 0 → SUCCESS.
    assert repo.finalized[0][3] == "success"


def test_batch_runs_partial_on_failure() -> None:
    """일부 실패 → finalize status=partial."""
    repo = _FakeBatchRunRepo()
    adapter = _FakeFscAdapter(
        factory=lambda c, b: _result([_pair(code=c, ex_date=date(2024, 3, 28), batch_id=b)]),
        raise_for={"005930": AdapterError("boom")},
    )
    mapping = CrnoMapping.from_dict(
        {"005930": _CRNO, "000660": "1301110017990"},
    )
    batch, _, _ = _batch(
        adapter=adapter, crno_mapping=mapping, batch_run_repo=repo,
    )
    batch.run(
        stock_codes=["005930", "000660"], begin_bas_dt=_BEGIN, end_bas_dt=_END,
    )
    assert repo.finalized[0][3] == "partial"
