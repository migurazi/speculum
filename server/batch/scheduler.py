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
   (캐시) 으로 먼저 로드. all job 은 corp_code → krx → ecos → kosis → dart →
   snapshot 순. krx (가격/시총, pykrx/FDR — API 키 불필요) 가 raw 배치 중 가장
   먼저, snapshot 은 raw 데이터 적재 후 derived precompute — data_versions
   freeze, ⓓ).
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

from app.adapters.dart_adapter import (
    DartAdapter,
    _disclosure_deadline,
)
from app.adapters.ecos_adapter import EcosAdapter
from app.adapters.fdr_adapter import FdrAdapter
from app.adapters.kosis_adapter import KosisAdapter
from app.adapters.pykrx_adapter import PykrxAdapter
from app.core.config import get_database_url
from app.db.session import create_engine_from_url, create_sessionmaker
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.sql_repositories import (
    SqlFinancialRepository,
    SqlMarketCapRepository,
    SqlPriceRepository,
    SqlTreasurySharesRepository,
)
from app.services.corp_code_mapping import CorpCodeMapping
from app.services.krx_calendar import DEFAULT_CALENDAR, TradingCalendar
from batch.alerts import BatchAlertHandler, LoggingAlertHandler
from batch.corp_code_bootstrap import CorpCodeBootstrap
from batch.dart_daily import DartBatchSummary, DartDailyBatch
from batch.ecos_daily import EcosBatchSummary, EcosDailyBatch
from batch.kosis_daily import KosisBatchSummary, KosisDailyBatch
from batch.krx_daily import BatchSummary as KrxBatchSummary
from batch.krx_daily import KrxDailyBatch
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
    "run_krx_job",
    "run_snapshot_job",
]

logger = logging.getLogger(__name__)

# CLI --job 선택지. "all" 은 corp_code → krx → ecos → kosis → dart → snapshot 순차.
# krx 는 raw 배치 중 가장 먼저 — 가격/시총은 KRX (pykrx/FDR) 출처라 API 키 불필요
# (재무·거시는 키 필요)이며 factor 평가의 기반 데이터다. snapshot 은 모든 raw
# 데이터 (krx/ecos/kosis/dart) 적재 **후** 실행돼야 data_versions 의 batch_id 가
# 당일 최신으로 freeze + factor 평가가 갓 적재된 데이터를 반영한다 (ⓓ).
VALID_JOBS: Final[tuple[str, ...]] = (
    "all", "corp-code", "krx", "ecos", "kosis", "dart", "snapshot",
)

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


# `_disclosure_deadline` 은 `app.adapters.dart_adapter` 의 단일 정의를 re-import
# (위 import). 과거엔 scheduler 에 중복 정의돼 있었고 test_scheduler 가 두 산식의
# 일치를 역설적으로 검증했으나, 이제 같은 객체라 DRY 위반·drift 위험 0
# (ADR-0012 D1 신고기한 매트릭스의 단일 출처 = dart_adapter).


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


# KRX 일배치가 처리하는 시장 — "전체 universe" = KOSPI + KOSDAQ. KrxDailyBatch.run
# 은 시장 1 개씩 처리 (universe fetch 가 시장 인자를 받음) 이므로 job 함수가 반복.
KRX_MARKETS: Final[tuple[str, ...]] = ("KOSPI", "KOSDAQ")


def run_krx_job(
    *,
    session: Session,
    primary_adapter: PykrxAdapter,
    verify_adapter: FdrAdapter | None,
    calendar: TradingCalendar,
    as_of: date,
    markets: Sequence[str] = KRX_MARKETS,
    codes: Sequence[str] | None = None,
    throttle_seconds: float = 1.5,
    alert_handler: BatchAlertHandler | None = None,
) -> list[KrxBatchSummary]:
    """KRX 가격·시총 일배치 실행 — 시장별 (KOSPI/KOSDAQ) 순차, session 단위 트랜잭션.

    KRX OHLCV·시가총액은 pykrx (primary) / FDR (cross-check) 출처라 **API 키가
    필요 없다** (DART 재무·ECOS/KOSIS 거시와 달리). 따라서 키 미발급 상태에서도
    가격·시총 정식 적재가 가능한 유일한 경로 — scheduler 통합의 핵심 동기.

    KrxDailyBatch 는 시장 1 개를 1 회 run 으로 처리 (universe fetch 가 market 인자
    의존) 하므로 본 함수가 markets 를 순회한다. 각 run 은 자체 batch_id 를 발급
    (batch_runs row 분리). 두 시장의 write 는 동일 session (호출자가 commit) 안의
    종목별 SAVEPOINT 로 격리된다.

    Args:
        session: SQLAlchemy session (호출자가 commit/rollback).
        primary_adapter: pykrx 1차 출처.
        verify_adapter: FDR cross-check. None 이면 cross-check skip (conflict
            detection 없음). 주입 시 KrxDailyBatch 가 default ConflictDetector 구성.
        calendar: KRX 영업일 캘린더 (휴장일 skip 판정). 운영은 DEFAULT_CALENDAR.
        as_of: 처리 대상 거래일 (KST). 휴장일이면 시장별 skip (summary 에 사유).
        markets: 처리할 시장 list. 기본 KOSPI + KOSDAQ (전체 universe).
        codes: 종목코드 subset. None (기본) 이면 시장 전체 universe. 지정 시 각
            시장 universe 와 교집합만 적재 — 빠른 스모크/검증용 (수 시간 걸리는
            전체 적재 없이 소수 종목만). 시장별로 교집합이라 KOSPI 코드는 KOSPI
            run 에서, KOSDAQ 코드는 KOSDAQ run 에서 자연히 매칭.
        throttle_seconds: 종목 fetch 간 sleep (ADR-0003 D6 rate limit, 기본 1.5).
        alert_handler: 충돌/실패 alert handler. None 이면 batch 가 NullAlertHandler.

    Returns:
        시장별 KrxBatchSummary list (markets 순서).

    Note:
        KrxDailyBatch.run 은 raise 하지 않음 (종목별 실패는 summary.failures 로
        반환, universe fetch 실패는 skipped_reason). 따라서 본 함수도 정상 종료 —
        부분 실패는 로깅으로 노출하고 호출자 (_run_guarded) 의 exit code 판정은
        예외 발생 시에만 동작. 운영 모니터링은 summary 의 failure_count 를 확인.
    """
    batch = KrxDailyBatch(
        primary_adapter=primary_adapter,
        verify_adapter=verify_adapter,
        # verify_adapter 주입 시 KrxDailyBatch 가 default ConflictDetector 생성.
        conflict_detector=None,
        calendar=calendar,
        citation_repo=SqlCitationRepository(session),
        price_repo=SqlPriceRepository(session),
        market_cap_repo=SqlMarketCapRepository(session),
        throttle_seconds=throttle_seconds,
        session=session,
        alert_handler=alert_handler,
    )
    summaries: list[KrxBatchSummary] = []
    for market in markets:
        summary = batch.run(as_of=as_of, market=market, codes=codes)
        logger.info(
            "KRX 배치 완료 — market=%s universe=%d success=%d failure=%d "
            "conflicts=%d skipped=%s",
            market, summary.universe_size, summary.success_count,
            summary.failure_count, len(summary.conflicts),
            summary.skipped_reason,
        )
        summaries.append(summary)
    return summaries


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
        help=(
            "실행할 배치 (default: all = "
            "corp-code→krx→ecos→kosis→dart→snapshot). "
            "krx (가격/시총) 는 API 키 불필요 — 키 미발급 시 단독 실행 가능."
        ),
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
        help=(
            "대상 종목코드 (공백 구분) — DART + KRX 공통. 미지정 시 DART 는 "
            "corp_code 전체, KRX 는 시장 전체 universe. 지정 시 양쪽 모두 해당 "
            "종목만 적재 (빠른 스모크/검증)."
        ),
    )
    parser.add_argument(
        "--market", choices=list(KRX_MARKETS), default=None,
        help=(
            "KRX 대상 시장 (KOSPI | KOSDAQ). 미지정 시 양 시장 모두. 단일 시장 "
            "지정 시 타 시장 universe fetch 를 생략 — --codes 와 함께 쓰면 빠른 "
            "타겟 스모크 (예: --market KOSPI --codes 005930)."
        ),
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
    needs_db = job in ("all", "krx", "ecos", "kosis", "dart", "snapshot")
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

        # krx 는 raw 배치 중 가장 먼저 — 가격/시총 (pykrx/FDR, API 키 불필요) 이
        # factor 평가의 기반. corp_code 매핑과 독립 (가격은 종목코드만 사용).
        if job in ("all", "krx"):
            assert maker is not None  # needs_db 보장.
            db_maker = maker
            # --codes 는 DART 와 공유 — 지정 시 krx 도 해당 종목만 (빠른 스모크).
            # None (미지정) 이면 전체 universe. 빈 list 도 None 으로 정규화.
            krx_codes = args.codes or None
            # --market 미지정 → 양 시장 (KRX_MARKETS). 지정 → 단일 시장만
            # (타 시장 universe fetch 생략).
            krx_markets = (args.market,) if args.market else KRX_MARKETS
            _run_guarded(
                "krx", failures,
                lambda: _do_krx(
                    db_maker, observed_date, krx_codes, krx_markets,
                ),
            )

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


def _do_krx(
    maker: sessionmaker[Session],
    observed_date: date,
    codes: Sequence[str] | None = None,
    markets: Sequence[str] = KRX_MARKETS,
) -> None:
    """KRX job — session scope 안에서 실행 (pykrx primary + FDR cross-check).

    observed_date 를 거래일 (as_of) 로 사용 — ECOS/KOSIS 의 수집 기준일과 동일
    CLI 플래그 (--observed-date) 공유. 휴장일이면 KrxDailyBatch 가 시장별 skip.

    codes 가 주어지면 (--codes) 해당 종목만 적재 — 전체 universe (~수 시간) 없이
    소수 종목 빠른 검증. DART 와 동일 --codes 플래그 공유 (all job 시 양쪽 동시
    제한 → 일관된 스모크). None 이면 전체 universe.

    markets (--market) 로 단일 시장만 적재 가능 — 기본 KOSPI+KOSDAQ. 단일 지정
    시 타 시장 universe fetch (네트워크) 를 생략 → --codes 와 결합한 빠른 스모크.

    pykrx/FDR adapter 는 라이브러리 wrapper 라 보유 HTTP client 가 없어 close()
    가 불필요하다 (ecos/kosis/dart adapter 와 달리 — 그쪽은 httpx client 정리
    위해 finally close). LoggingAlertHandler 로 충돌/실패를 로깅 채널에 노출.
    """
    with _session_scope(maker) as session:
        run_krx_job(
            session=session,
            primary_adapter=PykrxAdapter(),
            verify_adapter=FdrAdapter(),
            calendar=DEFAULT_CALENDAR,
            as_of=observed_date,
            markets=markets,
            codes=codes,
            alert_handler=LoggingAlertHandler(),
        )


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
