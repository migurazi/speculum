"""Backtest freeze artifact — ScreenRunSnapshot 동형 (ADR-0027 D5 / ADR-0025 D5 확장).

백테스트 결과를 §2.10 Reproducibility 기준으로 freeze 한다. `ScreenRunSnapshot`
(screen_run.py) 의 직계 시계열 확장 — query 가 단일 as_of 가 아닌 기간/주기/거래
비용 가정으로 확장된 점만 다르다.

freeze 입력 (result_hash = SHA-256 JCS, ADR-0027 D5):
- pack content_hash + slug + version (어느 pack 정의로 평가했는가).
- krx / dart batch_id (어느 데이터 배치 시점인가 — `collect_run_data_versions`).
- rebalance 정책 (frequency) + 기간 (start/end).
- 거래비용 가정 (tax_bps / commission_bps) — 비용이 다르면 결과가 다르므로 freeze.
- backtest_engine_version — 산식 의미가 바뀌면 hash 변경 (재현 무결성).

재현 불변식 (ADR-0025 D5): frozen artifact 로만 재로드. 현재 active pack 사용
금지 — frozen data_versions 의 slug/version 으로 PackRegistry 가 정본 재로드.

관련 ADR / 문서:
- ADR-0027 D5 (백테스트 freeze), ADR-0025 D5 (재현 불변식), ADR-0008 D7
  (ScreenRunSnapshot 원형), §2.10 Reproducibility.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from app.services._jcs import compute_content_hash
from app.services.backtest_engine import (
    BACKTEST_ENGINE_VERSION,
    BacktestCondition,
    CostAssumptions,
    RebalanceFrequency,
)

__all__ = [
    "BACKTEST_SNAPSHOT_SCHEMA_VERSION",
    "BacktestSnapshot",
    "BacktestSnapshotBuilder",
]

# data_versions schema 와 별개의 backtest snapshot 자체 schema 버전 — freeze 입력
# 키 set 이 바뀌면 bump (ScreenRunSnapshot SNAPSHOT_SCHEMA_VERSION 패턴).
BACKTEST_SNAPSHOT_SCHEMA_VERSION: Final[str] = "1.0"


@dataclass(frozen=True, slots=True)
class BacktestSnapshot:
    """백테스트의 완전한 freeze 단위 — ScreenRunSnapshot 동형 (ADR-0027 D5).

    Attributes:
        id: 백테스트 run 고유 UUID.
        user_id: 실행 사용자 (M0 SYSTEM_USER_ID / 인증 시 실유저).
        pack_slug / pack_version / pack_content_hash: 평가에 쓴 pack 정본 식별.
        krx_batch_id / dart_batch_id: 데이터 배치 시점 (frozen 재현 기준). 없으면 "".
        conditions: universe 필터 조건 (canonical 정렬 — hash 결정성).
        start / end: 백테스트 기간.
        rebalance: rebalance 주기 정책 ("quarterly" | "monthly").
        cost_assumptions: 적용된 거래비용 가정 (tax_bps / commission_bps).
        engine_version: 백테스트 엔진 산식 버전 (산식 변경 감지).
        data_versions: 정책 + batch_id freeze copy (collect_run_data_versions).
        result_hash: `sha256:<hex>` — JCS(전 freeze 입력) SHA-256.
        computed_at: 계산 완료 시각 (UTC). hash 입력 제외 (같은 입력 = 같은 hash).
    """

    id: UUID
    user_id: UUID
    pack_slug: str
    pack_version: str
    pack_content_hash: str
    krx_batch_id: str
    dart_batch_id: str
    conditions: tuple[Mapping[str, str], ...]
    start: date
    end: date
    rebalance: RebalanceFrequency
    cost_assumptions: CostAssumptions
    engine_version: str
    data_versions: Mapping[str, str]
    result_hash: str
    computed_at: datetime


def _canonical_conditions(
    conditions: Sequence[BacktestCondition],
) -> tuple[Mapping[str, str], ...]:
    """conditions 를 canonical 정렬 + str 강제 (hash 결정성, screen_run 패턴).

    각 condition 을 `{factor, op, value}` str dict 로 정규화 후 (factor, op, value)
    lexicographic 정렬 + dedup. 사용자 입력 순서는 hash 입력에서 제외 (§2.10).
    Decimal threshold 는 str 로 (JCS Decimal 직렬화 차단 — _jcs 의 str 강제).
    """
    seen: set[tuple[str, str, str]] = set()
    items: list[tuple[str, str, str]] = []
    for c in conditions:
        key = (c.factor, c.op, str(c.threshold))
        if key in seen:
            continue
        seen.add(key)
        items.append(key)
    items.sort()
    return tuple(
        MappingProxyType({"factor": f, "op": o, "value": v}) for f, o, v in items
    )


class BacktestSnapshotBuilder:
    """BacktestSnapshot 생성 — canonical 정규화 + result_hash 계산 (ScreenRunBuilder 동형).

    state-less — static method 만.
    """

    @staticmethod
    def build(
        *,
        run_id: UUID,
        user_id: UUID,
        pack_slug: str,
        pack_version: str,
        pack_content_hash: str,
        conditions: Sequence[BacktestCondition],
        start: date,
        end: date,
        rebalance: RebalanceFrequency,
        cost_assumptions: CostAssumptions,
        data_versions: Mapping[str, str],
        computed_at: datetime | None = None,
    ) -> BacktestSnapshot:
        """입력을 정규화 + result_hash 계산 + 완전 freeze (ADR-0027 D5).

        result_hash = JCS-SHA256 of:
            pack(content_hash/slug/version) + batch_id(krx/dart) + conditions
            (canonical) + 기간(start/end) + rebalance + cost_assumptions +
            engine_version + data_versions.

        Args:
            run_id: 백테스트 run UUID.
            user_id: 실행 사용자.
            pack_slug / pack_version / pack_content_hash: pack 정본 식별.
            conditions: universe 필터 조건 (canonical 정렬됨).
            start / end / rebalance: 기간 + 주기.
            cost_assumptions: 거래비용 가정.
            data_versions: collect_run_data_versions 결과 (정책 + batch_id).
            computed_at: None 이면 현재 UTC.

        Returns:
            BacktestSnapshot — 완전 freeze.
        """
        canonical_conditions = _canonical_conditions(conditions)
        frozen_versions = MappingProxyType(dict(data_versions))
        krx_batch_id = frozen_versions.get("krx_batch_id", "")
        dart_batch_id = frozen_versions.get("dart_batch_id", "")

        # result_hash 입력 — 모든 freeze 키. Decimal 은 str 로 (JCS 결정성).
        hash_input: dict[str, Any] = {
            "pack_content_hash": pack_content_hash,
            "pack_slug": pack_slug,
            "pack_version": pack_version,
            "krx_batch_id": krx_batch_id,
            "dart_batch_id": dart_batch_id,
            "conditions": [dict(c) for c in canonical_conditions],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "rebalance": rebalance,
            "cost_tax_bps": str(cost_assumptions.tax_bps),
            "cost_commission_bps": str(cost_assumptions.commission_bps),
            "engine_version": BACKTEST_ENGINE_VERSION,
            "snapshot_schema_version": BACKTEST_SNAPSHOT_SCHEMA_VERSION,
            "data_versions": dict(frozen_versions),
        }
        result_hash = compute_content_hash(hash_input, exclude_key=None)

        ts = computed_at if computed_at is not None else datetime.now(UTC)

        return BacktestSnapshot(
            id=run_id,
            user_id=user_id,
            pack_slug=pack_slug,
            pack_version=pack_version,
            pack_content_hash=pack_content_hash,
            krx_batch_id=krx_batch_id,
            dart_batch_id=dart_batch_id,
            conditions=canonical_conditions,
            start=start,
            end=end,
            rebalance=rebalance,
            cost_assumptions=cost_assumptions,
            engine_version=BACKTEST_ENGINE_VERSION,
            data_versions=frozen_versions,
            result_hash=result_hash,
            computed_at=ts,
        )
