"""Snapshot 의 정책 hash + version aggregator — Screen Run snapshot freeze 의 single source.

5 사이클 (T22 / T17 / T21 / T20 / T22.1) 의 정책 hash 와 version 을 한 곳에 모아
`data_versions` 로 freeze. ADR-0008 D7 의 `screen_runs.data_versions` JSONB 입력.

설계 원칙 (oracle 자문 결정 3 + Risk-C1 + Risk-C2):

1. **Single source of truth** — 모든 `*_POLICY_VERSION` / `content_hash` 상수가 본
   모듈로 모임. 새 정책 도입 시 본 모듈에 키 추가가 의무 (test 가 ast walk 로
   CI 검증).
2. **str-typed value** — Decimal / datetime / UUID 직렬화 모호성 차단. JCS hash
   입력의 결정성 보장 (Risk-C2).
3. **pit / asof_policy 통합** — 두 모듈이 같은 `PIT_POLICY_VERSION` 상수 share —
   단일 키만 노출 (`pit_policy_version`).
4. **사람 가독성 + hash 동시 보존** — `factor_pack_content_hash` 만으론 6 개월 후
   디버깅 시 어느 pack 인지 식별 어려움. `pack_slug` + `version` 보조.

관련 ADR / 문서:
- ADR-0008 D7 (`screen_runs.data_versions`)
- 8 기둥 §2.10 Reproducibility — "어제의 자신과 같다"
- M0_PLAN T30 / AC-P-10 (Screen Run snapshot)

Import topology (oracle 2 차 M4 — circular import 위험 monitor):
    snapshot_versions → factor_pack, krx_calendar, as_of_policy, price_adjuster,
                        factor_evaluator
    위 5 모듈은 본 aggregator 를 import 하지 말 것 (단방향 의존).
    factor_evaluator 가 향후 factor_pack 또는 as_of_policy 를 import 하면 cycle
    이 발생하지 않으나, snapshot_versions 가 새 정책 모듈을 추가할 때마다 본
    invariant 검증. test_import_topology (별도 backlog) 가 강제 권장.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from app.services.as_of_policy import PIT_POLICY_VERSION
from app.services.factor_evaluator import EVALUATOR_POLICY_VERSION
from app.services.factor_pack import DEFAULT_PACK
from app.services.krx_calendar import DEFAULT_CALENDAR
from app.services.price_adjuster import (
    ADJUSTMENT_POLICY_VERSION,
    CORPORATE_ACTION_POLICY_VERSION,
)
from app.services.price_adjuster import (
    POLICY_CONTENT_HASH as PRICE_ADJUSTMENT_POLICY_HASH,
)

__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "collect_active_policy_versions",
]

# Snapshot 의 data_versions JSONB schema 자체의 버전 — 키 set 이 변경되면 bump.
# M1 에 KRX/DART batch_id 키 2 개 추가 예정.
SNAPSHOT_SCHEMA_VERSION: Final[str] = "1.0"


def collect_active_policy_versions() -> Mapping[str, str]:
    """현재 활성 정책의 hash + version 모두를 freeze key 로 반환.

    Returns:
        `Mapping[str, str]` — 모든 value 가 str (Decimal/UUID/datetime 직렬화 모호성
        차단). MappingProxyType 으로 immutable view.

    오라클 자문 결정 3 — 10 키 set:

    - `factor_pack_content_hash`: 활성 pack 의 RFC 8785 JCS + SHA-256 hash
    - `factor_pack_slug`: 사람 가독성 식별자 (예: "speculum-builtin")
    - `factor_pack_version`: pack 의 semver (예: "1.0.0")
    - `calendar_content_hash`: KRX 캘린더 데이터 hash
    - `calendar_version`: 캘린더 semver
    - `pit_policy_version`: PIT + AsOfPolicy 공유 (oracle 결정 3 통합)
    - `price_adjustment_policy_hash`: T20 의 결합 정책 hash (adj+ca+matrix+rounding)
    - `adjustment_policy_version`: 가격 보정 정책 semver (가독성)
    - `ca_policy_version`: corporate action 정책 semver (가독성)
    - `evaluator_version`: factor formula evaluator 정책 semver
    - `snapshot_schema_version`: data_versions schema 자체의 버전

    M1 추가 예정: `krx_batch_id`, `dart_batch_id`.
    """
    return MappingProxyType({
        "factor_pack_content_hash": DEFAULT_PACK.computed_hash,
        "factor_pack_slug": DEFAULT_PACK.pack_slug,
        "factor_pack_version": DEFAULT_PACK.version,
        "calendar_content_hash": DEFAULT_CALENDAR.content_hash,
        "calendar_version": DEFAULT_CALENDAR.version,
        "pit_policy_version": PIT_POLICY_VERSION,
        "price_adjustment_policy_hash": PRICE_ADJUSTMENT_POLICY_HASH,
        "adjustment_policy_version": ADJUSTMENT_POLICY_VERSION,
        "ca_policy_version": CORPORATE_ACTION_POLICY_VERSION,
        "evaluator_version": EVALUATOR_POLICY_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    })
