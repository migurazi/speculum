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
                        factor_evaluator, db_universe_distribution
    위 6 모듈은 본 aggregator 를 import 하지 말 것 (단방향 의존).
    M2 T72 추가: `db_universe_distribution` 은 `DISTRIBUTION_POLICY_VERSION` 상수
    만 제공 (db_field_provider/factor_evaluator 의존, snapshot_versions 미import)
    → cycle 없음.
    factor_evaluator 가 향후 factor_pack 또는 as_of_policy 를 import 하면 cycle
    이 발생하지 않으나, snapshot_versions 가 새 정책 모듈을 추가할 때마다 본
    invariant 검증. test_import_topology (별도 backlog) 가 강제 권장.

    M1 T48b 추가: `collect_batch_versions` 가 `app.db.orm.batch_runs` (ORM) 를
    import. ORM 은 snapshot_versions 를 import 하지 않으므로 cycle 없음. 정책-only
    `collect_active_policy_versions` 는 여전히 무인자 순수 함수 (DB 무의존).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS, BatchRunORM
from app.services.as_of_policy import PIT_POLICY_VERSION
from app.services.db_universe_distribution import DISTRIBUTION_POLICY_VERSION
from app.services.factor_evaluator import EVALUATOR_POLICY_VERSION
from app.services.factor_pack import DEFAULT_PACK, LoadedPack
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
    "collect_batch_versions",
    "collect_run_data_versions",
]

# Snapshot 의 data_versions JSONB schema 자체의 버전 — 키 set 이 변경되면 bump.
# M1 T48b — KRX/DART batch_id 키 2 개 (`krx_batch_id`, `dart_batch_id`) 가 run
# 생성 경로에서 data_versions 에 합류 → schema 키 set 확장 → "1.0" → "1.1".
# M2 T72 — 유니버스-상대 분포 정책 키 1 개 (`distribution_policy_version`) 가
# 정책-only set 에 합류 (모집단 정의 / N-A 제외 / tie-break 규칙 버전) → schema
# 키 set 확장 → "1.1" → "1.2".
#
# 하위 호환 (M1 지시서 제약 + H3 확인): 기존 "1.0" run 의 저장된 data_versions
# (11 키) 는 batch_id 키 부재. `ScreenRunSnapshot.diff_versions` (screen_run.py
# :243-244) 가 누락 키를 `.get(k, "")` 로 처리 → 기존 run 은 batch_id 가
# `("", new)` 로 표시되어 정상 동작. **기존 저장 run 의 result_hash 는 재계산
# 하지 않음** (freeze 약속 — 본 bump 는 신규 run 의 schema_version 만 변경).
#
# 마찬가지로 "1.1" run 은 `distribution_policy_version` 키 부재 → diff_versions
# 가 `("", "1.0")` 로 표시 (하위호환). 기존 run 의 result_hash 불변 — 분포 정책
# 버전은 신규 "1.2" run 부터 data_versions 에 포함 (M1 batch_id bump 와 동형).
SNAPSHOT_SCHEMA_VERSION: Final[str] = "1.2"


def collect_active_policy_versions(
    pack: LoadedPack = DEFAULT_PACK,
) -> Mapping[str, str]:
    """현재 활성 정책의 hash + version 모두를 freeze key 로 반환.

    Returns:
        `Mapping[str, str]` — 모든 value 가 str (Decimal/UUID/datetime 직렬화 모호성
        차단). MappingProxyType 으로 immutable view.

    오라클 자문 결정 3 — 12 키 set (M2 T72 에서 distribution_policy_version 1 키
    추가, 기존 11 키 → 12 키):

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
    - `distribution_policy_version`: 유니버스-상대 분포 정책 semver (모집단 정의
      / N-A 종목 제외 / tie-break 규칙 버전 — ADR-0022 D3, T72)
    - `snapshot_schema_version`: data_versions schema 자체의 버전

    **default 인자 byte 불변 — custom 경로만 pack 명시** (ADR-0025 D3). 무인자
    호출(`collect_active_policy_versions()`)은 `pack=DEFAULT_PACK` 로 해소돼 12 키
    값이 변경 전과 byte 동일(SNAPSHOT_SCHEMA_VERSION bump 없음, 키 set 불변). custom
    pack run 생성 경로(Phase 2c)만 `pack` 을 명시 주입하여 그 pack 의
    content_hash/slug/version 을 freeze. batch_id 는 run-scoped (as_of + session
    필요) 이라 본 정책-only 함수에 합류시키지 않음 —
    `collect_batch_versions(as_of, session)` 로 분리. 두 결과의 merge 는 run 생성
    경로 (routes/screen.py, routes/runs.py) 가 명시 수행.
    """
    return MappingProxyType({
        "factor_pack_content_hash": pack.computed_hash,
        "factor_pack_slug": pack.pack_slug,
        "factor_pack_version": pack.version,
        "calendar_content_hash": DEFAULT_CALENDAR.content_hash,
        "calendar_version": DEFAULT_CALENDAR.version,
        "pit_policy_version": PIT_POLICY_VERSION,
        "price_adjustment_policy_hash": PRICE_ADJUSTMENT_POLICY_HASH,
        "adjustment_policy_version": ADJUSTMENT_POLICY_VERSION,
        "ca_policy_version": CORPORATE_ACTION_POLICY_VERSION,
        "evaluator_version": EVALUATOR_POLICY_VERSION,
        "distribution_policy_version": DISTRIBUTION_POLICY_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    })


# data_versions 의 batch_id key — collect_batch_versions 가 반환하는 2 키.
# run 생성 경로가 collect_active_policy_versions() 결과와 merge.
_BATCH_VERSION_KEYS: Final[Mapping[str, str]] = MappingProxyType({
    # data_versions key ← batch_runs.source 값.
    "krx_batch_id": "KRX",
    "dart_batch_id": "DART",
})


def collect_batch_versions(
    as_of: date,
    session: Session,
) -> Mapping[str, str]:
    """`as_of` 시점에 활성인 source 별 최신 성공 batch_id 를 freeze key 로 반환.

    M1 T48b (Momus C2 해소) — `collect_active_policy_versions()` 의 무인자 순수
    성격을 보존하기 위해 batch_id 합류를 본 별도 함수로 분리. run 생성 경로가
    두 결과를 merge 하여 `ScreenRunBuilder.build(data_versions=...)` 에 명시 주입.

    선택 규칙 (M1 지시서 T48b):
        - source ∈ {"KRX", "DART"} 별로
          `status = 'success' AND started_at::date <= as_of` 인 batch_runs row 중
          `max(started_at)` 의 id 를 str(UUID) 로 반환.
        - 해당 source 의 후보가 없으면 빈 문자열 "" (정책: 미존재 = empty,
          None 아님 — JCS hash 입력의 str 결정성 + diff_versions 의 `("", new)`
          하위호환).

    `started_at::date <= as_of` 의미: as_of 는 KST date (PIT 기준). batch 의
    `started_at` (UTC tz-aware) 의 date 부분이 as_of 이하인 것만 — "그 날까지
    실행된 성공 배치" 를 freeze 후보로 한정 (미래 배치 누출 차단).

    Returns:
        `Mapping[str, str]` — {"krx_batch_id": <uuid-str|"">, "dart_batch_id":
        <uuid-str|"">}. 모든 value str (JCS 결정성).

    Note (cross-dialect — started_at::date):
        SQLite/PG 모두 `func.date(BatchRunORM.started_at) <= as_of` 로 일자 비교.
        UTCDateTime 의 round-trip 후 date 부분 비교 — KST/UTC 경계 일자 drift 는
        본 cycle scope 밖 (M0 의 일 단위 PIT 한계, ADR-0008 D8 와 일관).
    """
    result: dict[str, str] = {}
    for version_key, source in _BATCH_VERSION_KEYS.items():
        stmt = (
            select(BatchRunORM.id)
            .where(
                BatchRunORM.source == source,
                BatchRunORM.status == BATCH_STATUS_SUCCESS,
                func.date(BatchRunORM.started_at) <= as_of,
            )
            .order_by(BatchRunORM.started_at.desc(), BatchRunORM.id.desc())
            .limit(1)
        )
        row_id = session.execute(stmt).scalars().first()
        result[version_key] = str(row_id) if row_id is not None else ""
    return MappingProxyType(result)


def collect_run_data_versions(
    as_of: date,
    session: Session | None,
    pack: LoadedPack = DEFAULT_PACK,
) -> Mapping[str, str]:
    """run 생성 경로의 data_versions — 정책 12 키 + (session 있으면) batch_id 2 키.

    M1 T48b — run 생성 endpoint (routes/screen.py, routes/runs.py) 의 단일
    merge 진입점. `collect_active_policy_versions()` (정책-only 12 키 — M2 T72
    에서 distribution_policy_version 합류) 와 `collect_batch_versions(as_of,
    session)` (batch_id 2 키) 를 명시 merge 하여
    `ScreenRunBuilder.build(data_versions=...)` 에 주입.

    Args:
        as_of: PIT 기준 일자 (run 의 as_of). batch_id 선택의 `started_at::date
            <= as_of` 필터에 사용.
        session: SQL session. None (Fake-only mode) 이면 batch_id 합류 skip →
            정책-only 12 키 반환 (테스트 / 비-SQL 컨텍스트 하위호환). 이 경우
            `ScreenRunBuilder` 의 `data_versions is None` fallback (정책-only)
            과 동일 결과 — screen_run.py:305 의 약속 일관.
        pack: freeze 대상 factor pack. default=DEFAULT_PACK 라 무인자(2-arg) 호출은
            byte 불변(ADR-0025 D3). custom pack run 생성 경로(Phase 2c)만 명시 주입.

    Returns:
        `Mapping[str, str]` — session 있으면 14 키 (12 정책 + krx/dart batch_id),
        None 이면 12 키. 모든 value str (JCS 결정성).
    """
    merged: dict[str, str] = dict(collect_active_policy_versions(pack))
    if session is not None:
        merged.update(collect_batch_versions(as_of, session))
    return MappingProxyType(merged)
