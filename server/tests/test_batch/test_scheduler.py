"""batch.scheduler 단위 테스트 — fiscal 추정 / CLI 파싱 / job dispatch 격리.

batch run 자체는 ecos_daily / kosis_daily / dart_daily 테스트가 커버. 본 모듈은
scheduler 고유 로직만 검증:
    fiscal 추정:
        1~3. recent_completed_quarter — 신고기한 지난 최근 분기.
        4. _disclosure_deadline 매트릭스.
    CLI 파싱 (build_arg_parser):
        5. 기본값 (job=all).
        6. job choice 검증.
        7. observed-date 파싱 + 형식 오류.
    job dispatch (main, monkeypatch stub):
        8. job=ecos → ecos 만 실행.
        9. job=all → corp-code→ecos→kosis→dart 모두 실행.
        10. corp-code 실패 → dart skip + exit 1.
        11. job 실패 → exit code 1.
    순수 job:
        12. run_corp_code_job — bootstrap.load 위임 + size 로깅.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.corp_code_mapping import CorpCodeMapping
from batch import scheduler
from batch.scheduler import (
    _disclosure_deadline,
    build_arg_parser,
    recent_completed_quarter,
    run_corp_code_job,
    run_krx_job,
)

# =============================================================================
# 1~3. recent_completed_quarter
# =============================================================================

@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2024, 6, 1), (2024, 1)),    # Q2 신고기한(08-14) 미도래 → Q1(05-15).
        (date(2024, 5, 1), (2023, 4)),    # Q1 신고기한(05-15) 미도래 → 2023 Q4.
        (date(2024, 12, 1), (2024, 3)),   # Q4 신고기한(익년) 미도래 → Q3(11-14).
    ],
)
def test_recent_completed_quarter(today: date, expected: tuple[int, int]) -> None:
    """today 기준 신고기한이 지난 가장 최근 분기 산정."""
    assert recent_completed_quarter(today) == expected


def test_recent_completed_quarter_just_after_deadline() -> None:
    """신고기한 당일 (Q1 05-15) → 해당 분기 포함 (deadline <= today)."""
    assert recent_completed_quarter(date(2024, 5, 15)) == (2024, 1)


# =============================================================================
# 4. _disclosure_deadline
# =============================================================================

def test_disclosure_deadline_matrix() -> None:
    """분기보고서 +45일, 사업보고서 +90일."""
    assert _disclosure_deadline(2024, 1) == date(2024, 5, 15)   # 03-31+45.
    assert _disclosure_deadline(2024, 2) == date(2024, 8, 14)   # 06-30+45.
    assert _disclosure_deadline(2024, 3) == date(2024, 11, 14)  # 09-30+45.
    # 2023 Q4: 12-31 + 90일 = 2024-03-30 (2024 윤년 → Feb 29 포함).
    assert _disclosure_deadline(2023, 4) == date(2024, 3, 30)


def test_disclosure_deadline_is_dart_adapter_reexport() -> None:
    """scheduler._disclosure_deadline 이 dart_adapter 의 것을 re-export (DRY).

    과거엔 scheduler 에 중복 정의가 있어 두 산식의 값 일치를 검증했으나, 이제
    중복을 제거하고 dart_adapter 의 단일 정의를 import 한다 → **같은 객체**
    (`is` 동일성)임을 확인. 산식 drift 자체가 구조적으로 불가능 (M-4 해소).
    """
    from app.adapters.dart_adapter import _disclosure_deadline as dart_dd

    assert _disclosure_deadline is dart_dd


# =============================================================================
# 5~7. build_arg_parser
# =============================================================================

def test_arg_parser_defaults() -> None:
    """인자 없으면 job=all, 나머지 None."""
    args = build_arg_parser().parse_args([])
    assert args.job == "all"
    assert args.observed_date is None
    assert args.fiscal_year is None
    assert args.fiscal_quarter is None
    assert args.codes is None
    assert args.force_refresh_corp_code is False


def test_arg_parser_invalid_job() -> None:
    """알 수 없는 job → SystemExit (argparse choices)."""
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--job", "unknown"])


def test_arg_parser_observed_date_parsed() -> None:
    """--observed-date YYYY-MM-DD 파싱."""
    args = build_arg_parser().parse_args(["--observed-date", "2024-06-15"])
    assert args.observed_date == date(2024, 6, 15)


def test_arg_parser_observed_date_invalid() -> None:
    """잘못된 날짜 형식 → SystemExit (ArgumentTypeError)."""
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--observed-date", "2024/06/15"])


def test_arg_parser_market_choice() -> None:
    """--market 는 KOSPI/KOSDAQ 만 허용, 미지정 시 None (양 시장)."""
    assert build_arg_parser().parse_args(["--market", "KOSDAQ"]).market == "KOSDAQ"
    assert build_arg_parser().parse_args([]).market is None
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--market", "NASDAQ"])


def test_arg_parser_codes_and_fiscal() -> None:
    """--codes 다중 + fiscal 옵션."""
    args = build_arg_parser().parse_args([
        "--job", "dart", "--codes", "005930", "000660",
        "--fiscal-year", "2023", "--fiscal-quarter", "4",
    ])
    assert args.job == "dart"
    assert args.codes == ["005930", "000660"]
    assert args.fiscal_year == 2023
    assert args.fiscal_quarter == 4


# =============================================================================
# 8~11. main dispatch (monkeypatch stub)
# =============================================================================

class _StubBootstrap:
    """CorpCodeBootstrap stub — load 가 고정 매핑 반환 (또는 raise)."""

    raise_on_load = False

    def __init__(self, *a: object, **kw: object) -> None:
        pass

    def load(self, *, force_refresh: bool = False) -> CorpCodeMapping:
        if _StubBootstrap.raise_on_load:
            raise RuntimeError("corpCode fetch 실패 — 테스트")
        return CorpCodeMapping.from_dict({"005930": "00126380"})

    def close(self) -> None:
        pass


class _StubEngine:
    """engine stub — main 의 finally dispose() 호출 기록 (C-3 검증)."""

    disposed = 0

    def dispose(self) -> None:
        _StubEngine.disposed += 1


@pytest.fixture
def _patched_jobs(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """_do_* / _build_engine_and_maker / CorpCodeBootstrap stub — 호출 기록 반환."""
    calls: dict[str, list] = {
        "krx": [], "ecos": [], "kosis": [], "dart": [], "snapshot": [],
    }

    _StubEngine.disposed = 0
    monkeypatch.setattr(
        scheduler, "_build_engine_and_maker",
        lambda: (_StubEngine(), object()),
    )
    monkeypatch.setattr(scheduler, "CorpCodeBootstrap", _StubBootstrap)
    _StubBootstrap.raise_on_load = False

    monkeypatch.setattr(
        scheduler, "_do_krx",
        # _do_krx(maker, observed_date, codes, markets) — codes/markets 전달
        # 검증 위해 함께 기록.
        lambda maker, observed_date, codes=None, markets=scheduler.KRX_MARKETS:
            calls["krx"].append((observed_date, codes, markets)),
    )
    monkeypatch.setattr(
        scheduler, "_do_ecos",
        lambda maker, observed_date: calls["ecos"].append(observed_date),
    )
    monkeypatch.setattr(
        scheduler, "_do_kosis",
        lambda maker, observed_date: calls["kosis"].append(observed_date),
    )
    monkeypatch.setattr(
        scheduler, "_do_dart",
        lambda maker, mapping, codes, fy, fq: calls["dart"].append((fy, fq)),
    )
    monkeypatch.setattr(
        scheduler, "_do_snapshot",
        lambda maker, observed_date: calls["snapshot"].append(observed_date),
    )
    return calls


def test_main_job_ecos_only(_patched_jobs: dict[str, list]) -> None:
    """job=ecos → ecos 만 실행, 나머지 0."""
    code = scheduler.main([
        "--job", "ecos", "--observed-date", "2024-06-15",
    ])
    assert code == 0
    assert _patched_jobs["ecos"] == [date(2024, 6, 15)]
    assert _patched_jobs["krx"] == []
    assert _patched_jobs["kosis"] == []
    assert _patched_jobs["dart"] == []
    assert _patched_jobs["snapshot"] == []


def test_main_job_krx_only(_patched_jobs: dict[str, list]) -> None:
    """job=krx → krx 만 실행, 나머지 0 (API 키 불필요 가격/시총 단독 적재)."""
    code = scheduler.main([
        "--job", "krx", "--observed-date", "2024-06-28",
    ])
    assert code == 0
    # codes 미지정 → None (전체 universe), market 미지정 → 양 시장.
    assert _patched_jobs["krx"] == [
        (date(2024, 6, 28), None, scheduler.KRX_MARKETS),
    ]
    assert _patched_jobs["ecos"] == []
    assert _patched_jobs["kosis"] == []
    assert _patched_jobs["dart"] == []
    assert _patched_jobs["snapshot"] == []


def test_main_krx_codes_passthrough(_patched_jobs: dict[str, list]) -> None:
    """--codes 지정 → krx job 에 종목 subset 전달 (DART 와 공유, 빠른 스모크)."""
    code = scheduler.main([
        "--job", "krx", "--observed-date", "2024-06-28",
        "--codes", "005930", "000660",
    ])
    assert code == 0
    # market 미지정 → 양 시장 기본.
    assert _patched_jobs["krx"] == [
        (date(2024, 6, 28), ["005930", "000660"], scheduler.KRX_MARKETS),
    ]


def test_main_krx_market_limits_to_single(
    _patched_jobs: dict[str, list],
) -> None:
    """--market 지정 → 해당 단일 시장만 (타 시장 universe fetch 생략)."""
    code = scheduler.main([
        "--job", "krx", "--observed-date", "2024-06-28",
        "--market", "KOSPI", "--codes", "005930",
    ])
    assert code == 0
    assert _patched_jobs["krx"] == [
        (date(2024, 6, 28), ["005930"], ("KOSPI",)),
    ]


def test_main_all_runs_every_job(_patched_jobs: dict[str, list]) -> None:
    """job=all → krx + ecos + kosis + dart + snapshot 모두 실행."""
    code = scheduler.main([
        "--job", "all", "--observed-date", "2024-06-15",
    ])
    assert code == 0
    # krx 는 raw 배치 중 가장 먼저 (가격/시총 = factor 기반). codes 미지정 → None,
    # market 미지정 → 양 시장.
    assert _patched_jobs["krx"] == [
        (date(2024, 6, 15), None, scheduler.KRX_MARKETS),
    ]
    assert _patched_jobs["ecos"] == [date(2024, 6, 15)]
    assert _patched_jobs["kosis"] == [date(2024, 6, 15)]
    # fiscal 자동 추정 — 2024-06-15 기준 (2024, 1).
    assert _patched_jobs["dart"] == [(2024, 1)]
    # snapshot 은 raw 배치 후 마지막 실행(ⓓ).
    assert _patched_jobs["snapshot"] == [date(2024, 6, 15)]


def test_main_job_snapshot_only(_patched_jobs: dict[str, list]) -> None:
    """job=snapshot → snapshot 만 실행, 나머지 0(ⓓ precompute 독립 호출)."""
    code = scheduler.main([
        "--job", "snapshot", "--observed-date", "2024-06-15",
    ])
    assert code == 0
    assert _patched_jobs["snapshot"] == [date(2024, 6, 15)]
    assert _patched_jobs["ecos"] == []
    assert _patched_jobs["kosis"] == []
    assert _patched_jobs["dart"] == []


def test_main_corp_code_failure_skips_dart(_patched_jobs: dict[str, list]) -> None:
    """corp-code 실패 → dart skip + exit 1 (ecos/kosis 는 진행)."""
    _StubBootstrap.raise_on_load = True
    code = scheduler.main(["--job", "all", "--observed-date", "2024-06-15"])
    assert code == 1
    # corp-code 실패해도 ecos/kosis 는 독립 실행.
    assert _patched_jobs["ecos"] == [date(2024, 6, 15)]
    assert _patched_jobs["kosis"] == [date(2024, 6, 15)]
    # dart 는 매핑 부재로 skip.
    assert _patched_jobs["dart"] == []


def test_main_disposes_engine_once(_patched_jobs: dict[str, list]) -> None:
    """all job → engine 1 회 생성·dispose (C-3, job 마다 누수 X)."""
    scheduler.main(["--job", "all", "--observed-date", "2024-06-15"])
    assert _StubEngine.disposed == 1


def test_main_corp_code_only_skips_engine(_patched_jobs: dict[str, list]) -> None:
    """job=corp-code → DB 미사용 → engine 미생성 (needs_db False)."""
    code = scheduler.main(["--job", "corp-code"])
    assert code == 0
    assert _StubEngine.disposed == 0
    assert _patched_jobs["ecos"] == []


def test_main_fiscal_year_alone_errors(_patched_jobs: dict[str, list]) -> None:
    """--fiscal-year 단독 지정 → parser.error (SystemExit), silent discard 방지."""
    with pytest.raises(SystemExit):
        scheduler.main([
            "--job", "dart", "--observed-date", "2024-06-15",
            "--fiscal-year", "2024",
        ])


def test_main_explicit_fiscal_override(_patched_jobs: dict[str, list]) -> None:
    """fiscal 명시 → 자동 추정 무시."""
    code = scheduler.main([
        "--job", "dart", "--observed-date", "2024-06-15",
        "--fiscal-year", "2022", "--fiscal-quarter", "2",
    ])
    assert code == 0
    assert _patched_jobs["dart"] == [(2022, 2)]


def test_main_job_failure_returns_exit_1(
    monkeypatch: pytest.MonkeyPatch, _patched_jobs: dict[str, list],
) -> None:
    """job 실행 중 예외 → failures 집계 + exit 1."""
    def _boom(maker: object, observed_date: date) -> None:
        raise RuntimeError("ecos 폭발 — 테스트")

    monkeypatch.setattr(scheduler, "_do_ecos", _boom)
    code = scheduler.main(["--job", "ecos", "--observed-date", "2024-06-15"])
    assert code == 1


# =============================================================================
# 12. run_corp_code_job
# =============================================================================

def test_run_corp_code_job_delegates_to_bootstrap() -> None:
    """run_corp_code_job → bootstrap.load 위임, 매핑 반환."""
    bootstrap = _StubBootstrap()
    _StubBootstrap.raise_on_load = False
    mapping = run_corp_code_job(bootstrap=bootstrap)  # type: ignore[arg-type]
    assert mapping.to_corp_code("005930") == "00126380"


# =============================================================================
# 13~14. run_krx_job (KrxDailyBatch 를 시장별로 반복)
# =============================================================================

class _StubKrxBatch:
    """KrxDailyBatch stub — run(as_of, market) 호출 기록 + 가짜 summary 반환.

    run_krx_job 의 시장 반복 로직만 검증 (실 pykrx/FDR fetch·DB write 없이).
    KrxDailyBatch 생성자 kwargs 는 검사용으로 보관.
    """

    instances: list[_StubKrxBatch] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.runs: list[tuple[date, str, object]] = []
        _StubKrxBatch.instances.append(self)

    def run(
        self,
        *,
        as_of: date,
        market: str,
        dry_run: bool = False,
        codes: object = None,
    ) -> SimpleNamespace:
        self.runs.append((as_of, market, codes))
        # run_krx_job 의 logging 이 접근하는 속성만 채운 가짜 summary.
        return SimpleNamespace(
            universe_size=2,
            success_count=2,
            failure_count=0,
            conflicts=(),
            skipped_reason=None,
        )


def test_run_krx_job_iterates_both_markets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_krx_job → 기본 markets (KOSPI + KOSDAQ) 를 동일 batch 인스턴스로 순차 run.

    전체 universe 적재 = 두 시장 모두. KrxDailyBatch.run 이 시장 1 개를 처리하므로
    job 함수가 markets 를 순회해야 한다 (단일 batch 인스턴스 재사용).
    """
    _StubKrxBatch.instances = []
    monkeypatch.setattr(scheduler, "KrxDailyBatch", _StubKrxBatch)

    summaries = run_krx_job(
        session=object(),  # type: ignore[arg-type]  # repo 생성자는 session 보관만.
        primary_adapter=object(),  # type: ignore[arg-type]
        verify_adapter=object(),  # type: ignore[arg-type]
        calendar=object(),  # type: ignore[arg-type]
        as_of=date(2024, 6, 28),
    )

    assert len(summaries) == 2
    # batch 인스턴스는 1 개 (시장마다 새로 만들지 않음). codes 미지정 → None.
    assert len(_StubKrxBatch.instances) == 1
    assert _StubKrxBatch.instances[0].runs == [
        (date(2024, 6, 28), "KOSPI", None),
        (date(2024, 6, 28), "KOSDAQ", None),
    ]


def test_run_krx_job_respects_custom_markets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """markets 인자 명시 → 해당 시장만 run (단일 시장 적재 지원)."""
    _StubKrxBatch.instances = []
    monkeypatch.setattr(scheduler, "KrxDailyBatch", _StubKrxBatch)

    summaries = run_krx_job(
        session=object(),  # type: ignore[arg-type]
        primary_adapter=object(),  # type: ignore[arg-type]
        verify_adapter=None,
        calendar=object(),  # type: ignore[arg-type]
        as_of=date(2024, 6, 28),
        markets=("KOSPI",),
    )

    assert len(summaries) == 1
    assert _StubKrxBatch.instances[0].runs == [
        (date(2024, 6, 28), "KOSPI", None),
    ]


def test_run_krx_job_forwards_codes_to_each_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codes 지정 → 각 시장 run 에 동일 codes subset 전달 (빠른 스모크 적재)."""
    _StubKrxBatch.instances = []
    monkeypatch.setattr(scheduler, "KrxDailyBatch", _StubKrxBatch)

    run_krx_job(
        session=object(),  # type: ignore[arg-type]
        primary_adapter=object(),  # type: ignore[arg-type]
        verify_adapter=None,
        calendar=object(),  # type: ignore[arg-type]
        as_of=date(2024, 6, 28),
        codes=["005930", "000660"],
    )

    assert _StubKrxBatch.instances[0].runs == [
        (date(2024, 6, 28), "KOSPI", ["005930", "000660"]),
        (date(2024, 6, 28), "KOSDAQ", ["005930", "000660"]),
    ]
