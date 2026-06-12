"""배치 통합 CLI — corp_code bootstrap / ECOS / KOSIS / DART / snapshot 단일 진입점.

각 배치 (dart_daily / ecos_daily / kosis_daily) 는 orchestrator 클래스만 있고
진입점·운영 wiring 이 없었다 (docstring: "manual entry — APScheduler 통합은
별도 cycle"). 본 모듈이 그 1 마일을 채운다 — **최소 scheduler**:

    OS cron 이 `python -m batch.scheduler --job all` 을 일정에 호출 → 본 모듈이
    env (SPECULUM_DATABASE_URL / DART_API_KEY / ECOS_API_KEY / KOSIS_API_KEY) 로
    engine·session·adapter·repository 를 wiring 하고 배치들을 순차 실행.

APScheduler / cron 데몬 자체는 본 모듈 범위 밖 (OS cron 또는 k8s CronJob 에
위임) — "스케줄링" 이 아니라 "한 번 실행 가능한 운영 진입점" 이 목표. 일정
반복은 인프라가, 실행 wiring 은 본 모듈이 담당한다.

설계 결정:
1. **순수 job 함수 + env wiring 분리** — `run_*_job` 은 의존성 (session/adapter)
   을 인자로 받는 순수 함수 (테스트 가능). `main()`/`_build_*_from_env` 가 env
   에서 실제 wiring 을 구성. CLI 파싱·dispatch·fiscal 추정 helper 단위 테스트.
2. **session 단위 트랜잭션** — job 당 sessionmaker() context 1 개. 정상 종료
   시 commit, 예외 시 rollback (get_session 패턴). 배치 내부는 SAVEPOINT 로
   지표/회사 단위 격리 (각 orchestrator 책임).
3. **corp_code 선행** — dart job 은 corp_code 매핑 의존 → CorpCodeBootstrap
   (캐시) 으로 먼저 로드. all job 은 corp_code → ecos → kosis → dart → snapshot 순
   (snapshot 은 raw 데이터 적재 후 derived precompute — data_versions freeze, ⓓ).
4. **부분 실패 비치명** — 한 job 실패가 다른 job 을 막지 않음 (job 별 try).
   exit code 는 실패 job 있으면 1 (cron 알림 신호).

관련 ADR:
- ADR-0003 D8 (배치), ADR-0036 D8 (KOSIS 배치)
- ADR-0002 D3 (citation persistence)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from typing import Final

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.dart_adapter import DartAdapter
from app.adapters.ecos_adapter import EcosAdapter
from app.adapters.kosis_adapter import KosisAdapter
from app.core.config import get_database_url
from app.db.session import create_engine_from_url, create_sessionmaker
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.sql_repositories import (
    SqlFinancialRepository,
    SqlTreasurySharesRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping
from batch.corp_code_bootstrap import CorpCodeBootstrap
from batch.dart_daily import DartBatchSummary, DartDailyBatch
from batch.ecos_daily import EcosBatchSummary, EcosDailyBatch
from batch.kosis_daily import KosisBatchSummary, KosisDailyBatch
from batch.snapshot_daily import run_snapshot_job

__all__ = [
    "VALID_JOBS",
    "build_arg_parser",
    "main",
    "recent_completed_quarter",
    "run_corp_code_job",
    "run_dart_job",
    "run_ecos_job",
    "run_kosis_job",
    "run_snapshot_job",
]

logger = logging.getLogger(__name__)

# CLI --job 선택지. "all" 은 corp_code → ecos → kosis → dart → snapshot 순차
# (snapshot 은 모든 raw 데이터 적재 **후** 실행돼야 data_versions 의 batch_id 가
# 당일 최신으로 freeze + factor 평가가 갓 적재된 데이터를 반영, ⓓ).
VALID_JOBS: Final[tuple[str, ...]] = (
    "all", "corp-code", "ecos", "kosis", "dart", "snapshot",
)

# 공시 신고기한 기준 분기말 + lag (일). dart_adapter._disclosure_deadline 매트릭스
# 와 동일 — 본 모듈은 "현재 공시 완료가 보장된 가장 최근 분기" 산정용.
_QUARTER_END: Final[dict[int, tuple[int, int]]] = {
    1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31),
}


# =============================================================================
# fiscal 추정 helper
# =============================================================================

def recent_completed_quarter(today: date) -> tuple[int, int]:
    """today 기준 공시 신고기한이 지난 가장 최근 (fiscal_year, fiscal_quarter).

    자본시장법 제160조 신고기한 (분기 +45일, 사업보고서 +90일, ADR-0012 D1) 이
    today 이전인 분기 중 가장 최근. dart job 의 fiscal 기본값 — 이 시점이면
    100% 회사 공시 완료가 보장되어 look-ahead 0 (이미 공시 안 된 분기를 fetch
    하면 DART 가 빈 결과/013 반환).

    Args:
        today: 기준 날짜.

    Returns:
        (fiscal_year, fiscal_quarter) — 신고기한 지난 최근 분기. 직전 8 분기를
        역순으로 훑어 첫 매치 반환 (반드시 존재).
    """
    year = today.year
    # 직전 8 분기 후보를 최근 → 과거 순으로 생성, 신고기한 <= today 인 첫 것.
    for offset in range(0, 8):
        q_index = (year * 4 + (today.month - 1) // 3) - offset
        cand_year = q_index // 4
        cand_quarter = q_index % 4 + 1
        deadline = _disclosure_deadline(cand_year, cand_quarter)
        if deadline <= today:
            return cand_year, cand_quarter
    # 도달 불가 (8 분기 = 2 년이면 반드시 매치) — 방어 fallback.
    return today.year - 1, 4  # pragma: no cover


def _disclosure_deadline(year: int, quarter: int) -> date:
    """분기 신고기한 — dart_adapter._disclosure_deadline 매트릭스 동일 (보수값).

    Q1~Q3 분기보고서 = 분기말 + 45일, Q4 사업보고서 = 사업연도말 + 90일.
    timedelta 캘린더 산술 (윤년 자동 처리).
    """
    from datetime import timedelta

    month, day = _QUARTER_END[quarter]
    quarter_end = date(year, month, day)
    lag_days = 90 if quarter == 4 else 45
    return quarter_end + timedelta(days=lag_days)


# =============================================================================
# 순수 job 함수 (의존성 주입 — 테스트 가능)
# =============================================================================

def run_corp_code_job(
    *,
    bootstrap: CorpCodeBootstrap,
    force_refresh: bool = False,
) -> CorpCodeMapping:
    """corp_code 매핑 부트스트랩 — corpCode.xml fetch/캐시 → CorpCodeMapping.

    DB write 없음 (in-memory 매핑만). dart job 의 선행 의존.

    Args:
        bootstrap: CorpCodeBootstrap (env wiring 또는 테스트 주입).
        force_refresh: True 면 캐시 무시 재fetch.

    Returns:
        CorpCodeMapping — 전체 상장사 매핑.
    """
    mapping = bootstrap.load(force_refresh=force_refresh)
    logger.info("corp_code 매핑 로드 완료 — %d 종목", mapping.size)
    return mapping


def run_ecos_job(
    *,
    session: Session,
    adapter: EcosAdapter,
    observed_date: date,
    throttle_seconds: float = 0.5,
) -> EcosBatchSummary:
    """ECOS 거시지표 일배치 실행 — session 단위 트랜잭션.

    Args:
        session: SQLAlchemy session (호출자가 commit/rollback).
        adapter: EcosAdapter.
        observed_date: 수집 기준일 (vintage_date 주입값).
        throttle_seconds: 지표 fetch 간 sleep.

    Returns:
        EcosBatchSummary.
    """
    batch = EcosDailyBatch(
        adapter=adapter,
        citation_repo=SqlCitationRepository(session),
        session=session,
        throttle_seconds=throttle_seconds,
    )
    summary = batch.run(observed_date=observed_date)
    logger.info(
        "ECOS 배치 완료 — success=%d failure=%d skipped=%d rows=%d",
        summary.success_count, summary.failure_count,
        summary.skipped_count, summary.total_rows_saved,
    )
    return summary


def run_kosis_job(
    *,
    session: Session,
    adapter: KosisAdapter,
    observed_date: date,
    throttle_seconds: float = 0.1,
) -> KosisBatchSummary:
    """KOSIS 거시지표 일배치 실행 — session 단위 트랜잭션.

    Args:
        session: SQLAlchemy session.
        adapter: KosisAdapter.
        observed_date: 수집 기준일.
        throttle_seconds: 지표 fetch 간 sleep.

    Returns:
        KosisBatchSummary.
    """
    batch = KosisDailyBatch(
        adapter=adapter,
        citation_repo=SqlCitationRepository(session),
        session=session,
        throttle_seconds=throttle_seconds,
    )
    summary = batch.run(observed_date=observed_date)
    logger.info(
        "KOSIS 배치 완료 — success=%d failure=%d skipped=%d rows=%d",
        summary.success_count, summary.failure_count,
        summary.skipped_count, summary.total_rows_saved,
    )
    return summary


def run_dart_job(
    *,
    session: Session,
    adapter: DartAdapter,
    corp_mapping: CorpCodeMapping,
    stock_codes: Sequence[str],
    fiscal_year: int,
    fiscal_quarter: int,
    throttle_seconds: float = 6.0,
) -> DartBatchSummary:
    """DART 재무·자사주 일배치 실행 — session 단위 트랜잭션.

    Args:
        session: SQLAlchemy session.
        adapter: DartAdapter.
        corp_mapping: stock_code → corp_code 매핑 (run_corp_code_job 결과).
        stock_codes: 처리 대상 종목코드. 빈 list 면 corp_mapping 전체 종목.
        fiscal_year / fiscal_quarter: 대상 회계기간.
        throttle_seconds: 회사 fetch 간 sleep (DART rate limit, default 6.0).

    Returns:
        DartBatchSummary.
    """
    codes = (
        list(stock_codes)
        if stock_codes
        else sorted(corp_mapping.stock_to_corp.keys())
    )
    batch = DartDailyBatch(
        adapter=adapter,
        corp_mapping=corp_mapping,
        citation_repo=SqlCitationRepository(session),
        financial_repo=SqlFinancialRepository(session),
        treasury_repo=SqlTreasurySharesRepository(session),
        session=session,
        throttle_seconds=throttle_seconds,
    )
    summary = batch.run(
        stock_codes=codes,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
    )
    logger.info(
        "DART 배치 완료 — target=%d success=%d failure=%d skipped=%d rows=%d "
        "(%dQ%d)",
        summary.target_count, summary.success_count, summary.failure_count,
        summary.skipped_count, summary.total_rows_saved,
        fiscal_year, fiscal_quarter,
    )
    return summary


# =============================================================================
# env wiring
# =============================================================================

@contextmanager
def _session_scope(maker: sessionmaker[Session]) -> Iterator[Session]:
    """job 단위 트랜잭션 scope — 정상 종료 commit, 예외 rollback (get_session 패턴)."""
    session = maker()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        session.commit()
    finally:
        session.close()


def _build_engine_and_maker() -> tuple[Engine, sessionmaker[Session]]:
    """SPECULUM_DATABASE_URL 로 engine·sessionmaker 구성. 미설정 시 SystemExit.

    배치는 Fake 모드 (DB 없음) 로 운영 불가 — DB write 가 목적이므로 명시 실패.

    Note:
        engine 도 함께 반환 — 호출자 (main) 가 모든 job 종료 후 `engine.dispose()`
        로 connection pool 을 명시 정리해야 한다 (oracle C-3). job 마다 engine 을
        새로 만들면 PostgreSQL connection 이 pool 에 누수된다 → 전 job 이 단일
        engine·sessionmaker 를 공유.
    """
    url = get_database_url()
    if not url:
        raise SystemExit(
            "SPECULUM_DATABASE_URL 미설정 — 배치는 실 DB 가 필요합니다 "
            "(postgresql+psycopg://... 또는 sqlite:///path)."
        )
    engine = create_engine_from_url(url)
    return engine, create_sessionmaker(engine)


# =============================================================================
# CLI
# =============================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서 — job 선택 + 옵션."""
    parser = argparse.ArgumentParser(
        prog="batch.scheduler",
        description="Speculum 데이터 배치 통합 진입점 (cron 호출용).",
    )
    parser.add_argument(
        "--job",
        choices=VALID_JOBS,
        default="all",
        help="실행할 배치 (default: all = corp-code→ecos→kosis→dart→snapshot).",
    )
    parser.add_argument(
        "--observed-date",
        type=_parse_iso_date,
        default=None,
        help="ECOS/KOSIS 수집 기준일 (YYYY-MM-DD). 미지정 시 오늘.",
    )
    parser.add_argument(
        "--fiscal-year", type=int, default=None,
        help="DART 대상 사업연도. 미지정 시 신고기한 지난 최근 분기 자동 추정.",
    )
    parser.add_argument(
        "--fiscal-quarter", type=int, default=None, choices=[1, 2, 3, 4],
        help="DART 대상 분기 (1~4). 미지정 시 자동 추정.",
    )
    parser.add_argument(
        "--codes", nargs="*", default=None,
        help="DART 대상 종목코드 (공백 구분). 미지정 시 corp_code 전체 상장사.",
    )
    parser.add_argument(
        "--force-refresh-corp-code", action="store_true",
        help="corpCode.xml 캐시 무시 재fetch.",
    )
    return parser


def _parse_iso_date(value: str) -> date:
    """YYYY-MM-DD 파싱 — argparse type. 실패 시 ArgumentTypeError."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"날짜 형식 오류 (YYYY-MM-DD 기대): {value!r}"
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입점 — env wiring + job dispatch.

    Args:
        argv: 인자 list (None 이면 sys.argv[1:]).

    Returns:
        exit code — 모든 job 성공 0, 하나라도 실패 1 (cron 알림 신호).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    observed_date = args.observed_date or date.today()
    # M-7: fiscal-year/quarter 는 함께 지정해야 함 — 한쪽만 주면 silent discard 대신
    # 명시 에러 (오퍼레이터 실수 방지).
    if (args.fiscal_year is None) != (args.fiscal_quarter is None):
        parser.error(
            "--fiscal-year 와 --fiscal-quarter 는 함께 지정해야 합니다 "
            "(둘 다 생략 시 신고기한 지난 최근 분기 자동 추정)."
        )
    if args.fiscal_year is not None:
        fiscal_year, fiscal_quarter = args.fiscal_year, args.fiscal_quarter
    else:
        fiscal_year, fiscal_quarter = recent_completed_quarter(observed_date)

    job = args.job
    failures: list[str] = []

    # DB write job (ecos/kosis/dart) 이 하나라도 있으면 engine·sessionmaker 를
    # 1 회만 구성해 전 job 이 공유 (oracle C-3 — job 마다 engine 생성 시 pool 누수).
    # corp-code only job 은 DB 불필요 → engine 미생성.
    needs_db = job in ("all", "ecos", "kosis", "dart", "snapshot")
    engine: Engine | None = None
    maker: sessionmaker[Session] | None = None
    if needs_db:
        engine, maker = _build_engine_and_maker()

    try:
        # corp_code 매핑 — dart/all 에 선행 필요. 한 번 로드해 재사용.
        corp_mapping: CorpCodeMapping | None = None
        if job in ("all", "corp-code", "dart"):
            bootstrap = CorpCodeBootstrap()
            try:
                corp_mapping = run_corp_code_job(
                    bootstrap=bootstrap,
                    force_refresh=args.force_refresh_corp_code,
                )
            except Exception as exc:  # noqa: BLE001
                # oracle C-2 — load 실패해도 bootstrap.close() 보장 (httpx 누수 방지).
                logger.error("corp-code job 실패: %s", exc)
                failures.append("corp-code")
                # dart 는 매핑 없으면 진행 불가 — corp-code 실패 시 dart skip.
            finally:
                bootstrap.close()

        if job in ("all", "ecos"):
            assert maker is not None  # needs_db 보장.
            db_maker = maker
            _run_guarded(
                "ecos", failures, lambda: _do_ecos(db_maker, observed_date),
            )

        if job in ("all", "kosis"):
            assert maker is not None
            db_maker = maker
            _run_guarded(
                "kosis", failures, lambda: _do_kosis(db_maker, observed_date),
            )

        if job in ("all", "dart"):
            if corp_mapping is None:
                logger.error("dart job skip — corp_code 매핑 부재 (corp-code 실패).")
                failures.append("dart")
            else:
                assert maker is not None
                db_maker = maker
                mapping = corp_mapping
                _run_guarded(
                    "dart", failures,
                    lambda: _do_dart(
                        db_maker, mapping, args.codes or [],
                        fiscal_year, fiscal_quarter,
                    ),
                )

        # snapshot 은 마지막 — 위 raw 배치(ecos/kosis/dart)가 당일 데이터를 적재한
        # 뒤 실행돼야 data_versions batch_id freeze + factor 평가가 최신 반영(ⓓ).
        if job in ("all", "snapshot"):
            assert maker is not None
            db_maker = maker
            _run_guarded(
                "snapshot", failures,
                lambda: _do_snapshot(db_maker, observed_date),
            )
    finally:
        # 모든 job 종료 후 engine 명시 dispose — connection pool 정리 (C-3).
        if engine is not None:
            engine.dispose()

    if failures:
        logger.error("배치 실패 job: %s", ", ".join(sorted(set(failures))))
        return 1
    logger.info("모든 배치 job 성공 (job=%s).", job)
    return 0


def _run_guarded(
    name: str,
    failures: list[str],
    fn: Callable[[], None],
) -> None:
    """job 실행 가드 — 예외 격리 (한 job 실패가 전체 abort X).

    fn 은 0-arg callable (호출자가 sessionmaker 를 bind). 예외 시 failures 에
    name 추가. SystemExit 은 전파 (CLI 종료 신호).
    """
    try:
        fn()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("%s job 실패: %s", name, exc)
        failures.append(name)


def _do_ecos(maker: sessionmaker[Session], observed_date: date) -> None:
    """ECOS job — session scope 안에서 실행."""
    adapter = EcosAdapter()
    try:
        with _session_scope(maker) as session:
            run_ecos_job(
                session=session, adapter=adapter, observed_date=observed_date,
            )
    finally:
        adapter.close()


def _do_kosis(maker: sessionmaker[Session], observed_date: date) -> None:
    """KOSIS job — session scope 안에서 실행."""
    adapter = KosisAdapter()
    try:
        with _session_scope(maker) as session:
            run_kosis_job(
                session=session, adapter=adapter, observed_date=observed_date,
            )
    finally:
        adapter.close()


def _do_dart(
    maker: sessionmaker[Session],
    corp_mapping: CorpCodeMapping,
    codes: Sequence[str],
    fiscal_year: int,
    fiscal_quarter: int,
) -> None:
    """DART job — session scope 안에서 실행."""
    adapter = DartAdapter()
    try:
        with _session_scope(maker) as session:
            run_dart_job(
                session=session,
                adapter=adapter,
                corp_mapping=corp_mapping,
                stock_codes=codes,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
            )
    finally:
        adapter.close()


def _do_snapshot(maker: sessionmaker[Session], observed_date: date) -> None:
    """snapshot precompute job — session scope 안에서 실행(외부 adapter 없음, ⓓ)."""
    with _session_scope(maker) as session:
        run_snapshot_job(session=session, observed_date=observed_date)


if __name__ == "__main__":
    sys.exit(main())
