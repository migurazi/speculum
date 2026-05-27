"""DB layer — SQLAlchemy 2 sync ORM + Alembic migration.

T13 산출물. ADR-0002 D5 / ADR-0009 D6 의 5 core 테이블 + 5 SQL repository
구현체 + Alembic initial migration.

운영 DB = PostgreSQL 16 (psycopg3), 단위 테스트 = SQLite in-memory (built-in
sqlite3). Cross-dialect 호환을 위해 JSONB → JSON (SQLAlchemy `JSON` 타입, SQLite
3.38+ 지원), UUID → SQLAlchemy `Uuid(as_uuid=True)` (PG native, SQLite CHAR(32)),
Decimal → Numeric(precision, scale) 사용.

**sync 결정 (T13)**: 기존 Repository Protocol 이 모두 sync `def` 시그니처 — 본
모듈도 sync. M1+ 멀티-사용자 진입 시 async 전환을 단일 cycle 로 분리.

본 layer 의 외부 진입점:

- `app.db.base.Base` — Declarative base. ORM 모델 정의는 `app/db/orm/*` 에 위치하지만
  Base 자체는 본 모듈 import.
- `app.db.session.create_engine_from_url` / `create_sessionmaker` — engine/session
  factory. M0 운영 부트스트랩 (`main.create_app()`) 에서 호출.
- `app.db.session.get_session` — FastAPI Depends 용 async generator. 한 request =
  한 session, commit/rollback 자동.

관련 ADR:
- ADR-0002 D5 (factor_definitions / stock_snapshots / source_citations schema)
- ADR-0009 D6 (stocks_master code_history JSONB)
- M0_PLAN T13 (Postgres 16 schema + Alembic migration)
"""

from __future__ import annotations

from app.db.base import Base
from app.db.session import (
    create_engine_from_url,
    create_sessionmaker,
    get_session,
)

__all__ = [
    "Base",
    "create_engine_from_url",
    "create_sessionmaker",
    "get_session",
]
