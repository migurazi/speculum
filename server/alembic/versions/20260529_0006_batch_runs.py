"""M1 T48a — batch_runs 테이블 + source_citations.batch_id FK (재현성 SoT).

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-29

M1 지시서 Phase M1-0 T48a (Momus H1 "SoT 명시"). 일배치 메타데이터의 권위
테이블 신설 + source_citations.batch_id 의 FK 참조 추가.

산출:
    1. batch_runs 테이블 — `BatchSummary` / `DartBatchSummary` 영속화
       (id UUID PK, market TEXT NULL, source TEXT, started_at/ended_at
       TIMESTAMPTZ, success_count INT, status TEXT).
    2. (source, status, started_at) 복합 인덱스 — T48b collect_batch_versions
       의 source 별 max(started_at) 성공 batch 조회 hot path.
    3. source_citations.batch_id → batch_runs.id **FK** (DEFERRABLE INITIALLY
       DEFERRED). 불변식: 모든 source_citations.batch_id ∈ batch_runs.id.

DEFERRABLE INITIALLY DEFERRED 의 근거 (orm/source_citations.py:batch_id 주석):
    일배치는 citation 을 실행 중 INSERT, batch_runs row 는 배치 종료 시 INSERT.
    즉시 검사 FK 라면 citation flush 시점에 batch_runs row 부재로 위반 →
    deferred 로 COMMIT 시점 검사하여 "citation INSERT → batch_runs INSERT →
    COMMIT" 순서를 허용.

PostgreSQL-specific (운영 주의 — alembic 0001 line 65-75 와 동일):
    - FK 의 DEFERRABLE 절은 PG 전용으로 추가 (SQLite 는 ALTER TABLE ADD
      CONSTRAINT 미지원이며, 테스트는 migration 이 아닌 `Base.metadata.create_all`
      로 ORM 정의를 직접 반영 — ORM 의 `ForeignKey(deferrable=True,
      initially="DEFERRED")` 가 SQLite create_all 시 DEFERRABLE 절 emit).
    - batch_runs 테이블 자체는 dialect 무관 생성.
    - 본 migration 적용 전 source_citations 에 batch_id orphan (대응 batch_runs
      row 없음) 이 있으면 FK 생성 실패 — M0 dev 환경은 빈 DB 라 무관. 운영
      backfill (기존 citation 의 batch_id 를 batch_runs 로 역구성) 은 별도 운영
      script (M1 데이터 backfill cycle). 본 migration 은 schema 준비만.

관련:
- M1 지시서 Phase M1-0 T48a
- ADR-0002 D5 (source_citations.batch_id), ADR-0008 D7 (data_versions)
- 10 기둥 §2.10 Reproducibility
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_source_citations_batch_id_batch_runs"


def upgrade() -> None:
    # ------------------------------------------------------------------------
    # 1. batch_runs — 일배치 메타데이터 SoT.
    # ------------------------------------------------------------------------
    op.create_table(
        "batch_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        # KRX = "KOSPI"/"KOSDAQ", DART = NULL (회사 단위, 시장 구분 없음).
        sa.Column("market", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_batch_runs"),
    )
    op.create_index(
        "ix_batch_runs_source_status_started",
        "batch_runs",
        ["source", "status", "started_at"],
    )

    # ------------------------------------------------------------------------
    # 2. source_citations.batch_id → batch_runs.id FK (DEFERRABLE — PG 전용).
    #    SQLite 는 ALTER TABLE ADD CONSTRAINT 미지원 → migration 단에서 skip.
    #    테스트는 create_all 로 ORM 의 deferrable FK 를 직접 반영.
    # ------------------------------------------------------------------------
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            _FK_NAME,
            source_table="source_citations",
            referent_table="batch_runs",
            local_cols=["batch_id"],
            remote_cols=["id"],
            deferrable=True,
            initially="DEFERRED",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            _FK_NAME, "source_citations", type_="foreignkey",
        )

    op.drop_index(
        "ix_batch_runs_source_status_started", table_name="batch_runs",
    )
    op.drop_table("batch_runs")
