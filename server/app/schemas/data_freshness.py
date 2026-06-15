"""데이터 신선도 진단 wire schema — GET /api/data-freshness (M5 #4a).

`app/services/data_freshness.DataFreshness` 도메인의 wire 표현. snake_case strict
스키마 (extra="forbid", frozen) — 사실(시각·stale bool·경과 일수)만 노출하고
판정·해석 문구는 일절 포함하지 않는다 (§2.1 Fidelity / §2.2 No Advice).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.services.data_freshness import DataFreshness, SourceFreshness

__all__ = [
    "DataFreshnessOut",
    "SourceFreshnessOut",
]

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class SourceFreshnessOut(BaseModel):
    """한 source(KRX/DART/KOSIS)의 신선도 사실 wire 표현."""

    model_config = _STRICT_MODEL_CONFIG

    source: str
    # 최신 성공 batch 의 종료 시각 (ISO 8601). 성공 batch 부재면 None.
    latest_batch_at: datetime | None
    # stale 여부 (사실 — 임계 초과 경과 또는 데이터 없음).
    is_stale: bool
    # batch 종료일~now 경과 calendar days (사실). batch 부재면 None.
    elapsed_days: int | None
    # 신선도 기준 최신 batch 가 부분 실패(status='partial')였는지의 사실.
    # batch 부재/완전 성공이면 False. 해석·평가 어휘 없음 (§2.1/§2.2).
    latest_batch_partial: bool

    @classmethod
    def from_domain(cls, src: SourceFreshness) -> SourceFreshnessOut:
        return cls(
            source=src.source,
            latest_batch_at=src.latest_batch_at,
            is_stale=src.is_stale,
            elapsed_days=src.elapsed_days,
            latest_batch_partial=src.latest_batch_partial,
        )


class DataFreshnessOut(BaseModel):
    """KRX·DART·KOSIS 신선도 진단 결과 wire 표현 — GET /api/data-freshness 응답."""

    model_config = _STRICT_MODEL_CONFIG

    krx: SourceFreshnessOut
    dart: SourceFreshnessOut
    kosis: SourceFreshnessOut
    # 진단 기준 시각 (now, ISO 8601) — 사실 재현/감사용.
    as_of: datetime

    @classmethod
    def from_domain(cls, freshness: DataFreshness) -> DataFreshnessOut:
        return cls(
            krx=SourceFreshnessOut.from_domain(freshness.krx),
            dart=SourceFreshnessOut.from_domain(freshness.dart),
            kosis=SourceFreshnessOut.from_domain(freshness.kosis),
            as_of=freshness.as_of,
        )
