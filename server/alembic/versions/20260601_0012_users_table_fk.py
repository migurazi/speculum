"""M2 T69 — users 테이블 + system sentinel + user_id FK (ADR-0021 D2/D3).

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-01

멀티유저 전환 인프라. M0~M1 은 users 테이블 부재(migration 0002 의 deferred)로
`watchlists`/`screener_sets`/`screen_runs`.user_id 가 FK 없는 bare Uuid 였다.
본 migration 이 ADR-0021 D2/D3 를 코드로 구현:

1. **users 테이블** — 최소 스키마(id UUID PK + created_at). OAuth 메타(email /
   sub / provider)는 T68(NextAuth)에서 추가 — 본 작업은 격리의 FK anchor 만.

2. **system sentinel row** — `00000000-…-0001`(SYSTEM_USER_ID, `screen_run.py`)
   를 insert. M1 의 기존 SYSTEM-owned run/watchlist/screener_set 의 신규 FK 를
   충족(ADR-0021 D3). 어떤 로그인 세션도 이 row 를 받지 않음 — sentinel 은
   마이그레이션된 자산의 소유자로만 잔존(auth 교체는 T68).

3. **user_id FK** — watchlists/screener_sets/screen_runs.user_id 에
   `REFERENCES users(id)` FK 추가.

**FK 순서 (ADR-0021 D3 — 어기면 FK 위반)**:
    users 생성 → sentinel insert → FK 추가.
    기존 SYSTEM-owned row 의 user_id 는 모두 sentinel(`…-0001`)이므로, FK 추가
    시점에 그 참조 대상 row 가 이미 존재해야 한다. sentinel insert 를 FK 추가
    앞에 두지 않으면 기존 row 가 FK 를 즉시 위반한다.

**M1 마이그레이션 정책 (ADR-0021 D3 — UPDATE 0건)**:
    기존 SYSTEM-owned run/watchlist/screener_set 의 user_id 를 **UPDATE 하지
    않는다**(system-owned 유지). 재귀속(첫 실유저)=의미 오염·비결정, 폐기=§2.10
    freeze 위반. sentinel row 1 건의 INSERT 만으로 기존 row 의 FK 를 충족 —
    이것이 UPDATE 0건의 가장 보수적 해석(ADR-0021 Rationale). 본 정책은
    **운영 DB 한정**: dev/CI 는 fresh schema 이라 마이그레이션 대상 자체가 없다.

**재현성 불변식 (ADR-0021 D3 함정 C / D5)**:
    재현(reproduce) 불변식 = `result_hash`/`data_versions`/`as_of`/`result_codes`
    byte 불변이지 "user_id 보존"이 아니다. **user_id 는 result_hash 입력이 아니다**
    (`screen_run.py:318-327` — hash_input = query/as_of/result_codes/data_versions
    뿐). 따라서 본 migration 의 users/FK 신설은 기존 run 의 hash 를 건드리지 않고
    재현을 깨지 않는다. **`computed_by` 를 result_hash 입력에 추가 금지**
    (그 순간 기존 run hash 붕괴 + 재현의 user 종속화 — D5 불변식 붕괴).

**PG / SQLite 양쪽 (FK 추가)**:
    FK 추가는 `op.batch_alter_table` 로 dialect 무관 처리 — PG 는 ALTER TABLE
    ADD CONSTRAINT, SQLite 는 batch mode(테이블 재생성 + FK 포함)로 ALTER TABLE
    ADD CONSTRAINT 미지원을 우회. SQLite 의 FK 강제는 connection 단위 PRAGMA
    foreign_keys=ON 필요(테스트 conftest 가 활성, 운영 PG 는 default ON).

관련 ADR / 문서:
- ADR-0021 D2 (users 테이블 + FK), D3 (sentinel + UPDATE 0건 + 함정 C), D5
  (재현성 불변식 — user 무관)
- migration 0002 (deferred users 주석), screen_run.py:63 (SYSTEM_USER_ID)
- docs/work-orders/m2-milestone.md T69
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# system sentinel user — `screen_run.py:63` 의 SYSTEM_USER_ID 와 동일 fixed UUID.
# M1 의 SYSTEM-owned 자산의 FK anchor. 어떤 로그인 세션도 이 id 를 받지 않음.
_SYSTEM_USER_ID: UUID = UUID("00000000-0000-0000-0000-000000000001")

# user_id FK 를 추가할 테이블 — (table, FK constraint 이름).
# 이름은 base.py 의 naming_convention(fk_<table>_<col>_<reftable>)과 일치.
_FK_TABLES: tuple[tuple[str, str], ...] = (
    ("watchlists", "fk_watchlists_user_id_users"),
    ("screener_sets", "fk_screener_sets_user_id_users"),
    ("screen_runs", "fk_screen_runs_user_id_users"),
)


def upgrade() -> None:
    # ------------------------------------------------------------------------
    # 1. users 테이블 생성 (최소 스키마 — OAuth 메타는 T68).
    # ------------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )

    # ------------------------------------------------------------------------
    # 2. system sentinel row insert — FK 추가 이전 (ADR-0021 D3 순서).
    #    기존 SYSTEM-owned row 의 user_id 가 모두 이 id 이므로, FK 추가 시점에
    #    참조 대상이 존재해야 한다. 순서를 어기면 기존 row 가 FK 위반.
    # ------------------------------------------------------------------------
    users_table = sa.table(
        "users",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        users_table,
        [{"id": _SYSTEM_USER_ID, "created_at": datetime.now(UTC)}],
    )

    # ------------------------------------------------------------------------
    # 3. user_id FK 추가 — sentinel insert 이후 (ADR-0021 D3 순서).
    #    batch_alter_table 로 PG(ALTER ADD CONSTRAINT) / SQLite(테이블 재생성)
    #    양쪽 처리. 기존 SYSTEM-owned row(user_id=sentinel)는 2 단계 insert 로
    #    FK 충족, UPDATE 0건(system-owned 유지).
    #
    #    M1 마이그레이션 정책: 기존 run/watchlist/screener_set 의 user_id 는
    #    절대 UPDATE 하지 않는다(ADR-0021 D3). sentinel row 1 건만으로 FK 충족.
    # ------------------------------------------------------------------------
    for table, fk_name in _FK_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_foreign_key(
                fk_name,
                "users",
                ["user_id"],
                ["id"],
            )


def downgrade() -> None:
    # FK 제거 → sentinel row(테이블 drop 으로 소멸) → users 테이블 제거.
    # 역순: FK 가 남은 채 users 를 drop 하면 dangling FK.
    for table, fk_name in _FK_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
    op.drop_table("users")
