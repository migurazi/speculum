"""Batch alert hook — M0_PLAN T43 (일배치 + adapter 충돌 alert).

KrxDailyBatch / DartDailyBatch 가 conflict / failure / completion 시점에
호출하는 단일 인터페이스. 본 모듈은 alert 발송 자체의 인프라 (Sentry / Slack
/ logging) 와 분리 — Protocol 만 정의하고 호출자 (운영 script / CI) 가 구현체
주입.

설계 결정:

1. **Protocol + None 기본** — batch 가 alert 책임이 없음. alert_handler 주입
   없으면 NullAlertHandler 가 no-op. test / Fake 모드도 추가 wiring 불필요.

2. **Sentry 직접 의존 X** — sentry-sdk 는 운영 환경에서만 의존. CI / local
   dev 에서 의존성 누락이 batch 모듈의 import 실패를 만들면 안 됨. 호출자가
   `SentryAlertHandler` 같은 구현체를 wire — 본 모듈은 `LoggingAlertHandler`
   만 stdlib 의존으로 제공.

3. **3 개 이벤트 한정** — conflict / failure / complete. 더 fine-grained (예:
   per-citation save) 한 hook 은 noise + batch 성능 영향. 운영 의미상 의사결정
   필요한 시점만 3 개.

4. **on_complete 의 두 종류 summary** — KrxDailyBatch.BatchSummary 와
   DartDailyBatch.DartBatchSummary 가 다른 schema. Protocol 의 `on_complete`
   는 둘 다 받아야 하므로 `object` 로 타입 — 호출자가 isinstance 분기. 잘
   설계된 Protocol 의 변형이긴 하나 batch 두 종류 통합 추상 도입은
   overengineering (M0 scope 외).

관련 ADR / 문서:
- ADR-0003 D4 (multi-source 충돌) — ConflictDetector 가 발견, alert 가 신호
- M0_PLAN T43 (백엔드 통합 테스트 — adapter 충돌 시 alert)
- AC-O-02 (Sentry 통합 + 일배치 실패 alert) — 본 protocol 의 운영 합류점
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.services.conflict_detector import ConflictDetectionResult

__all__ = [
    "BatchAlertHandler",
    "LoggingAlertHandler",
    "NullAlertHandler",
]


@runtime_checkable
class BatchAlertHandler(Protocol):
    """일배치의 alert 호출점 — KrxDailyBatch / DartDailyBatch 가 의존.

    구현체 책임:
        on_conflict — ConflictDetector 가 threshold 초과 차이 발견. 운영
            의사결정 (출처 우선순위 ADR-0003 D4) 필요 시점.
        on_failure — 종목/회사별 fetch 또는 DB write 실패. failure isolation
            안에서 호출 → batch 진행 계속 + 알림 발송.
        on_complete — batch 종료. summary 가 dry_run / failures / conflicts
            의 집계. 정상 종료도 의무 호출 (운영 SLA 신호).
    """

    def on_conflict(self, conflict: ConflictDetectionResult) -> None:
        """ConflictDetector 의 결과가 threshold 초과 시 호출.

        Note:
            `conflict.conflicts` 가 빈 list 면 호출자가 본 메서드 skip
            (batch 책임). 본 hook 는 "실 alert" 만 받음.
        """
        ...

    def on_failure(self, code: str, reason: str) -> None:
        """종목/회사별 처리 실패 시 호출.

        Args:
            code: KRX 종목코드 (KRX batch) 또는 stock_code (DART batch).
            reason: AdapterError 메시지 또는 `unexpected: ...` 라벨.
        """
        ...

    def on_complete(self, summary: object) -> None:
        """batch 종료 시 호출 — 정상 / dry_run / 부분 실패 모두.

        Args:
            summary: BatchSummary (KRX) 또는 DartBatchSummary (DART). schema 가
                다르므로 호출자가 isinstance 분기. Protocol 통일 추상은 M0
                scope 외.
        """
        ...


class NullAlertHandler:
    """no-op handler — alert_handler 주입 없을 때 default.

    Protocol 의 모든 메서드를 silent pass. test / Fake 모드 / 운영의 alert
    비활성화 옵션에서 사용. `runtime_checkable` 의 isinstance 검사는 메서드
    존재만 보므로 본 class 도 통과.
    """

    def on_conflict(self, conflict: ConflictDetectionResult) -> None:
        # 운영 의미: alert 미발송. ConflictDetectionResult 는 BatchSummary 의
        # conflicts 필드로 호출자에게 여전히 노출됨.
        del conflict

    def on_failure(self, code: str, reason: str) -> None:
        del code, reason

    def on_complete(self, summary: object) -> None:
        del summary


class LoggingAlertHandler:
    """stdlib logging 기반 handler — Sentry 합류 전 기본 운영 구현.

    각 이벤트를 logging 으로 emit. logger 이름은 `speculum.batch.alerts` 로
    고정 — 운영 시 logging.yaml 의 handler 라우팅 단일 진입점.

    Sentry 통합 (AC-O-02) 은 별도 cycle — `SentryAlertHandler` 가 본 class 의
    composition 으로 추가 wiring. 본 cycle 은 logging 만으로 운영 신호 확보.
    """

    # Module-level logger 명 — handler 인스턴스가 여러 개여도 logger 단일.
    _LOGGER_NAME = "speculum.batch.alerts"

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger if logger is not None else logging.getLogger(
            self._LOGGER_NAME,
        )

    def on_conflict(self, conflict: ConflictDetectionResult) -> None:
        """conflict 발견 → WARNING 레벨 logging.

        운영 의미: ADR-0003 D4 의 threshold 초과. ConflictReport list 의 통계만
        log (full row 는 BatchSummary 로 dump). N+1 log message 회피.
        """
        report_count = len(conflict.conflicts)
        self._logger.warning(
            "adapter conflict detected: %d report(s), missing_in_primary=%d, "
            "missing_in_verify=%d, compared_fields=%d, skipped_fields=%s",
            report_count,
            len(conflict.missing_in_primary),
            len(conflict.missing_in_verify),
            conflict.compared_field_count,
            sorted(conflict.skipped_fields),
        )

    def on_failure(self, code: str, reason: str) -> None:
        """종목별 실패 → ERROR 레벨 logging.

        운영 의미: failure isolation 으로 batch 는 계속되나 운영자가 root cause
        확인 필요. reason 의 stacktrace 는 호출자가 별도 logging.exception
        으로 보강 (본 hook 는 reason string 만).
        """
        self._logger.error("batch failure: code=%s reason=%s", code, reason)

    def on_complete(self, summary: object) -> None:
        """batch 종료 → INFO 레벨 logging.

        summary 의 핵심 통계만 log (full dump 는 호출자 책임). dry_run 일 경우
        명시적 표기 — 운영자가 실 commit 과 dry 시뮬레이션 구분.
        """
        self._logger.info("batch complete: %r", summary)
