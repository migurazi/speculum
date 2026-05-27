"""Speculum batch jobs — KRX 16:30 KST + DART 03:00 KST 일배치.

본 패키지는 M0_PLAN T18 (KRX) + T19 (DART) 의 batch orchestrator. 외부 출처
(pykrx / FDR / DART) 와 SQL repository 의 합류점.

설계 결정 (T18 cycle):
    - APScheduler 미통합 — manual entry (CLI / 운영 script) 호출. M0 release
      직전 (T44+) APScheduler / cron 등으로 트리거.
    - sync orchestration — codebase 정책 일관. async 전환은 M1+ backlog.
    - 종목별 failure isolation — 한 종목 fetch 실패가 batch 전체 멈춤 X.
      summary 의 failures list 로 alerting.
    - rate limit / circuit breaker (ADR-0003 D6) — 종목 호출 사이 sleep
      configurable. test 시 0.
"""

from __future__ import annotations

from batch.dart_daily import DartBatchSummary, DartDailyBatch
from batch.krx_daily import BatchSummary, KrxDailyBatch

__all__ = [
    "BatchSummary",
    "DartBatchSummary",
    "DartDailyBatch",
    "KrxDailyBatch",
]
