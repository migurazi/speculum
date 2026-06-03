"""ADR-0028 D1 — custom_packs.visibility 컬럼 (공유 visibility 메타).

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-02

ADR-0028 Factor pack community 의 공유 surface. custom pack 의 **body/content_hash
는 ADR-0020 append-only 불변**을 유지하되, 사용자 의도 메타인 `visibility` 만
mutable UPDATE(공유 토글) 한다. content_hash 가 불변이라 재현성(reproduce/freeze)에
영향 0 — visibility 는 result_hash 입력이 아니다.

컬럼 설계 (ADR-0028 D1):
    - visibility: String(16). 값은 'private'(default) | 'public'.
      enum 이 아니라 String + server_default 로 두는 이유: SQLite/PG 양쪽 동형 +
      값 도메인 강제는 service/route layer(set_visibility 가 두 값만 허용)가 담당.
    - NOT NULL + server_default 'private' — ADD COLUMN 시점에 기존 row 가
      'private' 로 backfill 된다(공유 의도 없는 기존 pack 은 비공개가 안전 default).
    - **기존 row UPDATE 0건** — server_default 가 backfill 을 수행하므로 별도
      UPDATE 문 불필요. 신규 컬럼이 추가될 뿐 기존 데이터 무변경(ADR-0020 정신).

custom_packs 는 append-only trigger 대상이 아니다(0007 은 financials/
corporate_actions 만) — visibility UPDATE 가 DB trigger 에 막히지 않는다. body
immutable 강제는 repository.set_visibility(body/content_hash 미변경)가 책임.

PG/SQLite 양쪽 (ADD COLUMN + server_default):
    batch_alter_table 로 dialect 무관 처리 — PG 는 ALTER TABLE ADD COLUMN, SQLite
    는 batch mode(테이블 재생성). custom_packs 는 append-only trigger 대상이
    아니므로 SQLite 재생성 부작용 0.

관련 ADR / 문서:
- ADR-0028 D1 (2026-06-02 — visibility 메타 컬럼), D2(공유 게이트)
- ADR-0020 (body immutable — visibility 만 mutable), ADR-0021 D5 (재현성 무관)
- migration 0016 (batch_alter_table ADD COLUMN 선례), 0017 (custom_packs 생성)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # visibility 컬럼 추가 — NOT NULL + server_default 'private'. 기존 row 는
    # server_default 로 'private' backfill(별도 UPDATE 0건, ADR-0020). batch
    # mode 로 PG(ADD COLUMN) / SQLite(테이블 재생성) 양쪽 처리.
    with op.batch_alter_table("custom_packs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "visibility",
                sa.String(16),
                nullable=False,
                server_default="private",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("custom_packs", schema=None) as batch_op:
        batch_op.drop_column("visibility")
