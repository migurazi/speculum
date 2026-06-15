"""BatchRunORM — M1 T48a 의 `batch_runs` 테이블 (재현성 SoT).

`batch/krx_daily.py` 의 `BatchSummary` / `batch/dart_daily.py` 의
`DartBatchSummary` 의 영속화 표현. 한 일배치 실행 = 한 row.

핵심 결정 (M1 지시서 T48a — Momus H1 "SoT 명시"):
    - **batch_runs = run 메타데이터의 권위 테이블.** `source_citations.batch_id`
      (`orm/source_citations.py:54`) 가 본 테이블의 `id` 를 FK 로 참조 → 불변식:
      모든 `source_citations.batch_id` ∈ `batch_runs.id`. 기존 2-hop 추적
      (price → citation → batch_id) 은 "어느 batch 가 이 row 를 생산" 용도로
      유지하고, batch_runs 는 "as_of 시점 최신 성공 batch" 쿼리 + run 메타를
      추가 제공.
    - **`market` nullable** — KRX 배치는 "KOSPI"/"KOSDAQ", DART 배치는 시장
      구분 없음 (회사 단위). DART row 는 market=NULL.
    - **`source`** — "KRX" | "DART" | "FSC". `collect_batch_versions` (T48b,
      M7 #5 에서 FSC=금융위 배당 adapter 조회 추가) 가 source 별 max(started_at)
      성공 batch 를 조회하는 인덱스 key. DB 레벨 CHECK 제약 없음 (String(16) 만)
      — 허용 값은 application(collect_batch_versions._BATCH_VERSION_KEYS) 이 강제.
    - **`status`** — "running" | "success" | "partial" | "skipped".
      `collect_batch_versions` (T48b) 는 `status ∈ {'success', 'partial'}` 을
      freeze 후보로 사용 (휴장일 skip / universe fetch 실패 / 미완료(running)
      batch → 데이터 미생산·미확정 → 제외). "partial" = 일부 종목/지표 실패했으나
      성공분의 실 데이터(citation/row)는 commit 한 배치 — 운영 가시성 위해
      "success" 와 라벨만 분리하되 freeze 자격은 동일(byte-동일, 1c 참조).
    - **`started_at` / `ended_at`** — UTC tz-aware. `started_at` 은 batch 의
      uuid4 생성 직후 시각 (BatchSummary.started_at 와 동일). T48b 의
      `started_at::date <= as_of` PIT 필터 + source 별 max(started_at) 정렬 key.
    - **INSERT = 배치 시작 (status='running'), finalize = 배치 종료 (UPDATE)** —
      `source_citations.batch_id` FK 가 DEFERRABLE INITIALLY DEFERRED 라도
      SQLite 는 SAVEPOINT RELEASE 시점에 deferred FK 를 검사 (PG 는 COMMIT
      시점만). 종목/회사별 SAVEPOINT 가 loop 중 RELEASE 되므로 citation 이
      참조할 batch_runs row 가 그 전(=배치 시작)에 존재해야 함. batch_runs 는
      fact 테이블 아닌 run 메타라 UPDATE 허용 (ADR-0020 의 append-only 대상은
      financials / corporate_actions 뿐). 미완료 batch (process crash) 는
      'running' 으로 남아 freeze 후보에서 자연 제외 — 부수적 견고성.

컬럼 ↔ summary 매핑:
    - `id` ↔ `BatchSummary.batch_id` / `DartBatchSummary.batch_id` (기존 uuid4 재사용)
    - `market` ↔ `BatchSummary.market` (KRX) / None (DART)
    - `source` ↔ "KRX" / "DART" / "FSC" (배치 종류 — FSC=금융위 배당, M7 #5)
    - `started_at` ↔ `started_at`
    - `ended_at` ↔ `ended_at` (finalize 시 갱신; 시작 시점엔 started_at 동일값)
    - `success_count` ↔ `success_count` (finalize 시 갱신; 시작 시점엔 0)
    - `status` ↔ "running"(시작) → "skipped"(skipped_reason 있음) /
      "partial"(failure_count>0, 성공분은 commit) / "success"(전부 성공)

관련:
- M1 지시서 Phase M1-0 T48a
- ADR-0002 D5 (source_citations.batch_id 의미), ADR-0008 D7 (data_versions)
- 10 기둥 §2.10 Reproducibility — frozen batch_id 재쿼리
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# status 상수 — collect_batch_versions (T48b) 의 freeze 후보 필터 + 배치
# 영속화 코드가 공유. typo drift 차단.
BATCH_STATUS_RUNNING = "running"
BATCH_STATUS_SUCCESS = "success"
# 부분 실패 — 일부 종목/지표는 실패했으나 성공분의 실 데이터(citation/row)는
# commit 한 배치. 운영 모니터링이 "전부 성공" 과 구분할 수 있도록 별도 라벨.
# freeze 후보·freshness 자격은 success 와 동일 (collect_batch_versions /
# latest_successful 가 둘 다 수용 — 성공분 데이터가 실재하므로). String(16)
# 컬럼의 새 값일 뿐이라 migration 불필요.
BATCH_STATUS_PARTIAL = "partial"
BATCH_STATUS_SKIPPED = "skipped"


class BatchRunORM(Base):
    """batch_runs row — 일배치 1 회 실행의 메타데이터 (재현성 SoT)."""

    __tablename__ = "batch_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    # KRX = "KOSPI"/"KOSDAQ", DART = NULL (회사 단위, 시장 구분 없음).
    market: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # "KRX" | "DART" | "FSC" — collect_batch_versions 의 source 별 조회 key
    # (FSC=금융위 배당 adapter, M7 #5). String(16), DB CHECK 없음 — 허용 값은
    # application(_BATCH_VERSION_KEYS) 강제.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # "running" | "success" | "partial" | "skipped" — BATCH_STATUS_* 상수.
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        # T48b — source 별 (status IN ('success','partial') AND
        # started_at::date <= as_of) 의 max(started_at) row 조회 hot path.
        # (source, status, started_at) 복합. partial 도 committed 데이터를 생산해
        # freeze 후보·freshness 소스 자격을 가지므로 status range 로 조회
        # (collect_batch_versions / latest_successful 와 일관).
        # row 수가 작아(일 ~6 row) status range 의 정렬 merge 비용은 무시 가능.
        Index(
            "ix_batch_runs_source_status_started",
            "source",
            "status",
            "started_at",
        ),
    )
