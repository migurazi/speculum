"""scripts.backfill_dart_financials 순수 로직 단위 테스트.

외부 DART 호출·DB 적재는 운영 경로(실행 검증)이므로, 본 테스트는 분기 파싱·기본값
같은 순수 로직만 격리 검증한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.backfill_dart_financials import _DEFAULT_PERIODS, _parse_period, run


def test_parse_period() -> None:
    """`"YYYYQq"` → (year, quarter)."""
    assert _parse_period("2024Q1") == (2024, 1)
    assert _parse_period("2023Q4") == (2023, 4)


def test_default_periods_are_trailing_five_consecutive() -> None:
    """기본 적재 분기 = trailing 5분기(2023Q1~2024Q1) — TTM standalone 변환에 직전
    분기까지 필요(B1, 5개 연속). 회계연도 1분기부터 시작해야 standalone 산출 가능."""
    assert _DEFAULT_PERIODS == ((2023, 1), (2023, 2), (2023, 3), (2023, 4), (2024, 1))
    # 연속성 + Q1 시작(B1 전제) 검증.
    assert _DEFAULT_PERIODS[0][1] == 1  # 첫 분기 = 회계연도 1분기
    assert len(_DEFAULT_PERIODS) == 5


def test_run_forwards_throttle_to_dart_job() -> None:
    """--throttle 이 run_dart_job 으로 전달 — 전체 유니버스 backfill 속도 제어.

    전 종목 backfill 은 기본 6.0s 면 22시간+ 라 비현실적. throttle 인자가
    분기별 run_dart_job 호출마다 그대로 forwarding 되는지 검증(외부 호출 mock)."""
    summary = MagicMock(
        total_rows_saved=10, success_count=1, failure_count=0, skipped_count=0,
    )
    with (
        patch("scripts.backfill_dart_financials._build_engine_and_maker",
              return_value=(MagicMock(), MagicMock())),
        patch("scripts.backfill_dart_financials._session_scope"),
        patch("scripts.backfill_dart_financials.CorpCodeBootstrap"),
        patch("scripts.backfill_dart_financials.DartAdapter"),
        patch("scripts.backfill_dart_financials.run_dart_job",
              return_value=summary) as mock_job,
    ):
        run(codes=["005930"], periods=[(2024, 1)], throttle_seconds=0.3)

    assert mock_job.call_count == 1
    assert mock_job.call_args.kwargs["throttle_seconds"] == 0.3
