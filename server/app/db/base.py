"""SQLAlchemy 2 DeclarativeBase + 명명 규약.

본 모듈은 ORM 의 root entity 만 정의. 모든 ORM 모델은 `app/db/orm/*` 에서
`Base` 를 상속하고, Alembic env (`alembic/env.py`) 가 본 `Base.metadata` 를
target metadata 로 사용.

명명 규약 (naming_convention):
    Alembic autogenerate 시 인덱스·제약조건 이름이 deterministic 이어야 migration
    diff 의 noise 가 줄어듦. PostgreSQL/SQLite 동시 호환을 위해 `ix_`, `uq_`,
    `ck_`, `fk_`, `pk_` 접두사 + table/column 이름 조합 채택 (Alembic 권장).

핵심 결정:
    - `Base` = `DeclarativeBase` 직접 상속 (SQLAlchemy 2 표준 패턴).
    - `metadata` 의 `naming_convention` 은 Alembic 의 autogenerate 안정성을 위해
      문서화된 5-key 패턴 사용.
    - mapped_column 으로 컬럼 정의 (PEP 484 type hint 동기화 — mypy strict 호환).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Alembic autogenerate 의 deterministic naming — Alembic 공식 문서 권장 패턴.
# 키 이름은 SQLAlchemy 가 hardcoded (변경 불가).
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Speculum 의 모든 ORM 모델의 root.

    `app/db/orm/*.py` 의 모든 `Mapped*` 클래스가 본 Base 상속. Alembic 의
    `target_metadata = Base.metadata` 가 본 metadata 를 사용.
    """

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


__all__ = ["Base"]
