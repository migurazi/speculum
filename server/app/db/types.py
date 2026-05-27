"""Custom SQLAlchemy column types — cross-dialect 호환 보강.

본 모듈은 PostgreSQL 16 vs SQLite (test) 사이의 미세한 동작 차이를 흡수하는
TypeDecorator 모음.

**UTCDateTime** — SQLite 가 tz 정보를 보존하지 않는 문제 해결:
    - PostgreSQL `timestamptz` = tz-aware datetime native 저장·복원.
    - SQLite `DateTime(timezone=True)` = ISO 문자열로 저장 → 복원 시 tz-naive.
    - 본 type 이 복원 단에서 UTC tzinfo 부착 → 모든 layer 에서 tz-aware 보장.

본 type 은 ORM 컬럼 정의에서 sa.DateTime(timezone=True) 의 drop-in 대체. 모든
`retrieved_at` / `created_at` / 기타 timestamp 컬럼이 본 type 으로 통일.

Note (test 한정 이슈가 아닌 이유):
    SQLite 동작이 단순 test 영향이라면 ORM 모델 수정 없이 conftest 에서만 fix
    가능. 그러나 운영 import path 의 도메인 invariant (SourceCitation 의
    UTC 강제) 가 SQLite 데이터에 노출되면 application layer 가 깨짐. 본 type
    이 layer 경계의 일관성 단일 source.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

__all__ = ["UTCDateTime"]


class UTCDateTime(TypeDecorator[datetime]):
    """Cross-dialect tz-aware DateTime — 복원 시 UTC 강제.

    - **bind (insert)**: 호출자가 항상 tz-aware UTC 전달 (도메인 invariant).
      Naive datetime 이 전달되면 ValueError — silent KST/UTC drift 차단.
    - **result (fetch)**: SQLite 는 tz-naive 반환 → UTC tzinfo 부착. PostgreSQL
      은 이미 tz-aware → no-op (idempotent).

    `cache_ok = True` — SQLAlchemy statement cache 호환 안전 (immutable wrapper).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """INSERT/UPDATE 시 호출 — tz-aware UTC 강제 + 비 UTC 정규화.

        oracle 리뷰 C3 — non-UTC tz-aware (KST 등) 가 그대로 SQLite 에 저장되면
        process_result_value 의 단순 UTC 부착 (`replace(tzinfo=utc)`) 이 KST 시각
        라벨만 UTC 로 잘못 변환하여 9 시간 drift 발생. bind 시점에 UTC 로
        정규화하면 round-trip 시 일관.

        Raises:
            ValueError: tz-naive datetime 이 전달되면 거부. 도메인 invariant
                (SourceCitation `_validate_utc_datetime` 등) 의 DB layer 1 차
                방어선.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"UTCDateTime requires tz-aware datetime, got naive: {value!r}"
            )
        # UTC 정규화 — KST 등 non-UTC offset 도 받아 UTC 로 변환. SourceCitation
        # 의 `_validate_utc_datetime` 가 이미 UTC offset=0 강제이지만, fact
        # 테이블 (PriceRecord.created_at 등) 의 timestamp 도 layer 경계에서 동일
        # 보장.
        return value.astimezone(timezone.utc)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """SELECT 결과 변환 — tz-naive 면 UTC 부착 (SQLite 호환)."""
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite ISO 8601 round-trip 후 tz-naive 로 복원되는 path.
            return value.replace(tzinfo=timezone.utc)
        # PostgreSQL — 이미 tz-aware. UTC 가 아닐 경우 UTC 로 정규화 (도메인 일관성).
        return value.astimezone(timezone.utc)
