"""유니버스 DART 재무 backfill — TTM factor(EPS/ROE)가 실값을 내도록 N분기 적재.

ROADMAP_v2 V1. TTM(4분기) factor 는 **5개 연속 분기**가 필요하다(B1 누적→standalone
변환에 직전 분기까지 필요 — `db_field_provider._resolve_financial_series`). 기본값은
trailing 5분기(2023Q1~2024Q1) — 데모 as_of=2024-06-28 기준.

DART 는 keyless 가 아니다(DART_API_KEY 필요, server/.env 에 보유). S1 sentinel 수정
(`dart_adapter` 의 `-표준계정코드 미사용-` 제외)으로 반기/분기보고서 적재가 가능해졌다.

운영 도구 — `batch.scheduler.run_dart_job`(분기당 전 종목 1회) 을 분기 list 로 반복.
정정공시 supersede chain·멱등(ON CONFLICT)은 run_dart_job 내부에서 처리(재실행 안전).

실행:
    python -m scripts.backfill_dart_financials                  # universe 전체, 5분기
    python -m scripts.backfill_dart_financials --codes 005930   # 특정 종목
    python -m scripts.backfill_dart_financials --periods 2024Q1 2023Q4
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from sqlalchemy import select

from app.adapters.dart_adapter import DartAdapter
from app.db.orm.stocks_master import StocksMasterORM
from batch.corp_code_bootstrap import CorpCodeBootstrap
from batch.scheduler import (
    _build_engine_and_maker,
    _session_scope,
    run_dart_job,
)

# TTM(4분기) 의 trailing 5분기 — B1 standalone 변환에 직전 분기(2023Q1)까지 필요.
_DEFAULT_PERIODS: tuple[tuple[int, int], ...] = (
    (2023, 1), (2023, 2), (2023, 3), (2023, 4), (2024, 1),
)


def _parse_period(s: str) -> tuple[int, int]:
    """`"2024Q1"` → `(2024, 1)`."""
    year_str, _, quarter_str = s.partition("Q")
    return int(year_str), int(quarter_str)


def _universe_codes(maker: object) -> list[str]:
    """stocks_master 의 전 종목코드 (current_code)."""
    with _session_scope(maker) as session:  # type: ignore[arg-type]
        rows = session.execute(
            select(StocksMasterORM.current_code).order_by(StocksMasterORM.current_code)
        ).scalars().all()
    return [c for c in rows if c]


def run(
    *,
    codes: Sequence[str],
    periods: Sequence[tuple[int, int]],
    throttle_seconds: float = 6.0,
) -> None:
    """`codes` × `periods` DART 재무 적재 — 분기당 전 종목 1회(run_dart_job).

    throttle_seconds: 종목 fetch 간 sleep. 전체 유니버스(수천 종목) 일회성 backfill
    은 6.0s 기본이면 22시간+ 이 걸려 비현실적 — DART 일일 한도(20,000/일)에 여유가
    있으므로 0.3~0.5s 로 낮춰 ~2시간으로 단축한다. status=020(rate limit) 시
    run_dart_job 내부에서 per-code 실패로 격리(전체 abort X)되니 안전.
    """
    eng, maker = _build_engine_and_maker()
    try:
        target_codes = list(codes) if codes else _universe_codes(maker)
        corp = CorpCodeBootstrap().load()
        adapter = DartAdapter()
        total_rows = 0
        for year, quarter in periods:
            # 분기당 1 session/트랜잭션 — run_dart_job 이 종목별 SAVEPOINT + 멱등.
            with _session_scope(maker) as session:
                summary = run_dart_job(
                    session=session,
                    adapter=adapter,
                    corp_mapping=corp,
                    stock_codes=target_codes,
                    fiscal_year=year,
                    fiscal_quarter=quarter,
                    throttle_seconds=throttle_seconds,
                )
            total_rows += summary.total_rows_saved
            print(
                f"  {year}Q{quarter}: success={summary.success_count} "
                f"fail={summary.failure_count} skipped={summary.skipped_count} "
                f"rows={summary.total_rows_saved}",
            )
        print(
            f"backfill 완료: {len(target_codes)} 종목 × {len(periods)} 분기, "
            f"총 {total_rows} rows.",
        )
    finally:
        eng.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="유니버스 DART 재무 N분기 backfill.")
    parser.add_argument(
        "--codes", nargs="*", default=None,
        help="대상 종목코드. 미지정 시 stocks_master 전체.",
    )
    parser.add_argument(
        "--periods", nargs="*", default=None,
        help="대상 분기 (예: 2024Q1 2023Q4). 미지정 시 trailing 5분기(2023Q1~2024Q1).",
    )
    parser.add_argument(
        "--throttle", type=float, default=6.0,
        help="종목 fetch 간 sleep(초). 전체 유니버스 backfill 은 0.3~0.5 권장(기본 6.0).",
    )
    args = parser.parse_args()
    periods = (
        tuple(_parse_period(p) for p in args.periods)
        if args.periods
        else _DEFAULT_PERIODS
    )
    run(
        codes=args.codes or [],
        periods=periods,
        throttle_seconds=args.throttle,
    )


if __name__ == "__main__":
    main()
