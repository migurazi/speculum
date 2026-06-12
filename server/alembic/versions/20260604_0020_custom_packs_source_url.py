"""ADR-0032 D3 — custom_packs.source_url 컬럼 (import provenance).

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-04

ADR-0032 Open Format pack 생태계의 provenance. 외부 URL 에서 import 한 pack 의 출처
URL 을 기록한다. **body 가 아닌 row 컬럼**에 두는 이유(D3 핵심):
    - content_hash 는 body 기준 봉인. provenance 를 body 에 넣으면 봉인이 바뀌어
      ADR-0020 append-only immutability + 재현 freeze 가 깨진다.
    - row 메타(visibility 옆)는 content_hash 와 무관 → 봉인 불변. `compute_pack_hash`
      출력이 source_url 유무와 동일함이 불변식.

컬럼 설계:
    - source_url: String(2048). nullable — editor 생성 pack 은 출처 없음(NULL).
      URL 최대 길이 관용(2048). 값 도메인(https 등)은 client/route 가 안내(서버는
      문자열 저장만 — 표시용 provenance).
    - **nullable 이라 server_default/backfill 불필요** — 기존 row 는 NULL(출처 미상).
      신규 컬럼만 추가, 기존 데이터 무변경(ADR-0020 정신).

PG/SQLite 양쪽 (batch_alter_table ADD COLUMN):
    custom_packs 는 append-only trigger 대상이 아니므로(0007 은 financials/
    corporate_actions 만) SQLite batch 재생성 부작용 0(0018 visibility 선례).

관련 ADR: ADR-0032 D3(provenance row metadata), ADR-0020(body immutable),
ADR-0028 D1/migration 0018(visibility — 동형 ADD COLUMN 선례).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # source_url 컬럼 추가 — nullable(기존 row NULL = 출처 미상). batch mode 로
    # PG(ADD COLUMN) / SQLite(테이블 재생성) 양쪽 처리(0018 선례).
    with op.batch_alter_table("custom_packs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source_url", sa.String(2048), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("custom_packs", schema=None) as batch_op:
        batch_op.drop_column("source_url")
