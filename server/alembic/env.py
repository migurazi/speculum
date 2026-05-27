"""Alembic environment — sync engine + Speculum metadata.

본 모듈은 `alembic upgrade head` / `alembic revision --autogenerate` 호출 시
실행. `SPECULUM_DATABASE_URL` 환경변수를 1순위로 읽어 PostgreSQL/SQLite 모두
지원.

설계 결정:
    - `target_metadata = Base.metadata` — `app.db.orm` 의 모든 ORM 클래스가 본
      import 의 side-effect 로 등록.
    - **sync engine** — sync repo 정책 (T13) 에 맞춤. SQLAlchemy 2 의
      `engine_from_config`.
    - `compare_type=True` — 컬럼 타입 변경 감지.
    - **offline mode 도 지원** — `alembic upgrade head --sql` 로 SQL 만 export
      가능 (DBA review).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ORM 모델 import — Base.metadata 에 모든 테이블 등록 side-effect.
from app.db.base import Base
from app.db.orm import (  # noqa: F401  ← import side-effect 가 핵심
    CorporateActionORM,
    FinancialORM,
    PriceDailyORM,
    SourceCitationORM,
    StocksMasterORM,
)

config = context.config

# 환경변수 1순위 → ini 파일 fallback.
_db_url = os.environ.get("SPECULUM_DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

# logging — alembic.ini 의 logger 설정 활성.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """SQL 만 export (DBA review 시 사용)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """`alembic upgrade head` 시 진입점."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
