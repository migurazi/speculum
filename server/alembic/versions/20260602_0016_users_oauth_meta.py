"""M2 T68 — users 테이블 OAuth 메타 (google_sub + email) (ADR-0021 D1.1).

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-02

NextAuth(Google) 합류 인프라 — migration 0012 의 최소 users 스키마(id + created_at)
에 OAuth 식별자 2 컬럼을 추가한다:

1. **google_sub** (String, nullable, unique) — Google OAuth 의 `sub` 클레임.
   안정적 사용자 식별자 (email 변경에도 불변). JWT 검증 후 JIT provision 의 키:
   `user_repo.get_by_google_sub(sub) or provision(sub, email)`. 같은 sub 는 항상
   같은 user_id 로 해소된다.
   - **nullable** — 0012 가 insert 한 system sentinel(00000000-…-0001) row 는
     어떤 OAuth 세션도 받지 않으므로 google_sub=NULL 유지 (기존 insert 무변경).
   - **unique** — 한 Google 계정 = 한 user. 단, **NULL 다중 허용** (PG/SQLite
     모두 unique 인덱스에서 NULL 은 중복 위반 아님) → sentinel 외 미래의 다른
     provider 사용자(google_sub=NULL)도 충돌하지 않음.

2. **email** (String, nullable) — 표시용 이메일. JWT email 클레임에서 채움.
   unique 아님 (email 은 변경 가능 + 식별자 아님 — 식별은 google_sub).

**sentinel row 무변경 (ADR-0021 D3 — UPDATE 0건 유지)**:
    0012 가 insert 한 sentinel row 의 google_sub / email 은 NULL 로 둔다. 본
    migration 은 컬럼 추가(ADD COLUMN nullable)만 — 기존 row UPDATE 0건. sentinel
    은 마이그레이션된 자산의 소유자로만 잔존하며 로그인 세션이 받지 않으므로
    OAuth 메타가 NULL 인 것이 정확하다.

**재현성 불변식 (ADR-0021 D5 — user 무관)**:
    google_sub / email 추가는 user 메타일 뿐 result_hash 입력이 아니다. 기존 run
    의 hash / 재현은 본 migration 에 무영향. `computed_by` 의 hash 입력화 금지.

PG/SQLite 양쪽 (ADD COLUMN + unique index):
    batch_alter_table 로 dialect 무관 처리 — PG 는 ALTER TABLE ADD COLUMN, SQLite
    는 batch mode(테이블 재생성). unique 인덱스(uq_users_google_sub)는 batch 안에서
    생성하여 양쪽 호환. users 테이블은 append-only trigger 대상 아님(0007 은
    financials/corporate_actions 만) → SQLite 재생성 부작용 0.

관련 ADR / 문서:
- ADR-0021 D1.1 (2026-06-02 개정 — NextAuth JWT 인증 골격), D2 (users 격리), D3
  (sentinel + UPDATE 0건), D5 (재현성 불변식 — user 무관)
- migration 0012 (users 최소 스키마 + sentinel insert)
- docs/work-orders/m2-milestone.md T68
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# google_sub unique 인덱스 명 — ORM(users.py) __table_args__ + base.py naming
# convention(uq_<table>_<col>)과 일치. NULL 다중 허용(PG/SQLite 공통).
_UQ_GOOGLE_SUB = "uq_users_google_sub"


def upgrade() -> None:
    # google_sub + email nullable 컬럼 추가 — sentinel row 는 NULL 유지(UPDATE 0).
    # batch_alter_table 로 PG(ADD COLUMN) / SQLite(테이블 재생성) 양쪽 처리.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("google_sub", sa.String(255), nullable=True),
        )
        batch_op.add_column(
            sa.Column("email", sa.String(320), nullable=True),
        )
        # unique 인덱스 — 한 Google 계정 = 한 user. NULL 다중 허용이라 sentinel
        # (google_sub=NULL) 및 미래 NULL row 가 충돌하지 않음.
        batch_op.create_index(
            _UQ_GOOGLE_SUB, ["google_sub"], unique=True,
        )


def downgrade() -> None:
    # 인덱스 → 컬럼 역순 제거.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(_UQ_GOOGLE_SUB)
        batch_op.drop_column("email")
        batch_op.drop_column("google_sub")
