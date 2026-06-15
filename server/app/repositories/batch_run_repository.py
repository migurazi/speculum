"""BatchRun Repository Protocol + Fake + SQL — M1 T48a 의 batch_runs 영속화.

`citation_repository.py` / `pit_protocols.py` 와 동일한 Protocol + Fake + Sql
3-구현 패턴. 일배치 (`batch/krx_daily.py`, `batch/dart_daily.py`) 가:

    1. 배치 시작 시 `start(...)` → status='running' row INSERT.
    2. 배치 종료 시 `finalize(...)` → ended_at / success_count / status UPDATE.

INSERT/UPDATE 분리의 근거 (orm/batch_runs.py 모듈 docstring):
    `source_citations.batch_id` FK 가 DEFERRABLE INITIALLY DEFERRED 여도 SQLite 는
    SAVEPOINT RELEASE 시점에 검사. 종목/회사별 SAVEPOINT 가 loop 중 RELEASE 되므로
    citation 이 참조할 batch_runs row 가 그 전(=배치 시작)에 존재해야 FK 만족.
    batch_runs 는 fact 테이블 아닌 run 메타라 finalize UPDATE 허용 (ADR-0020 의
    append-only 대상은 financials / corporate_actions 뿐).

T48b 의 `collect_batch_versions` 는 본 repository 를 거치지 않고 session 으로
직접 쿼리 (source 별 max started_at 집계 = SQL aggregate). 본 repository 는
배치의 INSERT/finalize 진입점만 담당.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.orm.batch_runs import (
    BATCH_STATUS_PARTIAL,
    BATCH_STATUS_RUNNING,
    BATCH_STATUS_SUCCESS,
    BatchRunORM,
)

__all__ = [
    "EXCLUDE_ALL_CUTOFF",
    "BatchCutoff",
    "BatchRunRecord",
    "BatchRunRepository",
    "CitationBatch",
    "FakeBatchRunRepository",
    "SqlBatchRunRepository",
]


@dataclass(frozen=True, slots=True)
class BatchCutoff:
    """재현 fetch 의 batch 경계 — `(started_at, id)` lexicographic 상한 (M1 T48c).

    Screen Run snapshot 의 frozen batch_id 를 `get_run` 으로 해소한 batch 의
    `(started_at, id)` 쌍. fact fetch 가 본 cutoff 이하 batch 가 생산한 row 만
    통과시키도록 제한 — 8 기둥 §2.10 Reproducibility (byte-동일 재현).

    **freeze 와 정확한 대칭 (Momus C#2)**:
        freeze (`snapshot_versions.py:160-172`) 는 `func.date(started_at) <= as_of`
        후보 중 `ORDER BY started_at DESC, id DESC LIMIT 1` — `id` 가 final
        tiebreaker. 따라서 재현 cutoff 의 통과 판정은 단순 `started_at <=` 가
        아니라 **`(X.started_at, X.id) <= (cutoff.started_at, cutoff.id)`**
        (lexicographic). 단순 started_at 비교는 started_at tie (동시/재시도 batch)
        에서 freeze 가 고르지 않은 batch 의 row 까지 통과시켜 재현을 깬다.

    **EXCLUDE_ALL sentinel (Momus H#5)**:
        snapshot 시점에 그 source 의 성공 batch 가 없었으면 (frozen batch_id="")
        cutoff 는 "무필터" 가 아니라 그 source 의 fact 를 **전부 제외**해야 한다
        — as_of 시점 데이터 없음 상태를 정확히 재현 (look-ahead 누출 차단).
        `EXCLUDE_ALL_CUTOFF` (= `started_at` / `id` 모두 None) 이 그 신호.

    Attributes:
        started_at: frozen batch 의 시작 시각 (full UTC tz-aware). EXCLUDE_ALL 이면
            None. `func.date` 절단 금지 — lexicographic 비교는 full timestamp 기준.
        id: frozen batch 의 UUID (started_at tie 의 final tiebreaker). EXCLUDE_ALL
            이면 None.

    Note:
        `None` (cutoff 미주입) = 무필터 = 기존 동작 완전 보존. `EXCLUDE_ALL` =
        전부 제외 (빈 결과). 양자는 의미가 정반대이므로 명시 구별.
    """

    started_at: datetime | None
    id: UUID | None

    @property
    def is_exclude_all(self) -> bool:
        """EXCLUDE_ALL sentinel 인지 — 그 source 의 fact 전부 제외 신호 (H#5)."""
        return self.started_at is None or self.id is None

    def allows(self, *, started_at: datetime, batch_id: UUID) -> bool:
        """batch `(started_at, batch_id)` 가 본 cutoff 이하인지 (lexicographic).

        EXCLUDE_ALL 이면 어떤 batch 도 통과 못 함 (전부 제외). Fake repository 의
        record 단위 cutoff 판정에 사용 (SQL 의 `(br.started_at < :cs) OR
        (br.started_at = :cs AND br.id <= :cid)` join 과 동일 의미).
        """
        if self.is_exclude_all:
            return False
        assert self.started_at is not None and self.id is not None
        if started_at < self.started_at:
            return True
        if started_at == self.started_at:
            return batch_id <= self.id
        return False


# EXCLUDE_ALL sentinel — 빈 frozen batch_id (H#5) 의 cutoff. 그 source fact 전부
# 제외 (무필터 아님). reproduce_run 이 빈 batch_id 해소 시 본 sentinel 사용.
EXCLUDE_ALL_CUTOFF: Final[BatchCutoff] = BatchCutoff(started_at=None, id=None)


@dataclass(frozen=True, slots=True)
class CitationBatch:
    """Fake repository 의 citation → batch 평탄화 entry (M1 T48c).

    SQL 의 `source_citations` JOIN `batch_runs` 2-hop 을 Fake 에서는 별도 테이블
    없이 `citation_id → (started_at, batch_id, source)` 매핑으로 평탄화. Fake
    생성자의 `citation_runs` map value. cutoff 판정 시 record.citation_id (H#4 —
    record 가 citation_id 보유) 로 본 entry 를 조회, source 일치 +
    `(started_at, batch_id) <= cutoff` (lexicographic) 아닌 record 제외 — SQL
    join 술어 (`_batch_cutoff_predicate`) 와 동일 의미.

    Attributes:
        started_at: 그 citation 을 생산한 batch 의 시작 시각 (full UTC tz-aware).
        batch_id: 그 batch 의 UUID (started_at tie 의 lexicographic tiebreaker).
        source: "KRX" | "DART" — H#3 source 정합 방어.
    """

    started_at: datetime
    batch_id: UUID
    source: str


@dataclass(frozen=True, slots=True)
class BatchRunRecord:
    """batch_runs row 의 frozen dataclass 표현 — batch summary 영속화 단위.

    Attributes:
        id: 본 실행의 UUID = BatchSummary.batch_id (기존 uuid4 재사용).
        market: "KOSPI"/"KOSDAQ" (KRX) 또는 None (DART — 시장 구분 없음).
        source: "KRX" | "DART".
        started_at: batch 시작 시각 (UTC tz-aware).
        ended_at: batch 종료 시각 (UTC tz-aware). finalize 전엔 started_at 동일값.
        success_count: 성공 처리 종목/회사 수. finalize 전엔 0.
        status: "running" | "success" | "partial" | "skipped" — BatchRunORM 의
            BATCH_STATUS_*. T48b collect_batch_versions 는 "success" + "partial"
            을 freeze 후보로 사용 (partial = 성공분 commit, 자격 동일 — byte-동일).
    """

    id: UUID
    market: str | None
    source: str
    started_at: datetime
    ended_at: datetime
    success_count: int
    status: str


@runtime_checkable
class BatchRunRepository(Protocol):
    """batch_runs 저장/조회 contract — start(INSERT) + finalize(UPDATE)."""

    def start(
        self,
        *,
        run_id: UUID,
        market: str | None,
        source: str,
        started_at: datetime,
    ) -> None:
        """배치 시작 시 status='running' row INSERT. id 중복 시 raise 권장."""
        ...

    def finalize(
        self,
        *,
        run_id: UUID,
        ended_at: datetime,
        success_count: int,
        status: str,
    ) -> None:
        """배치 종료 시 ended_at/success_count/status UPDATE.

        run_id 의 row 가 없으면 raise (start 누락). 동일 row 의 단순 메타 갱신 —
        append-only 무관 (run 메타).
        """
        ...

    def fetch_by_id(self, run_id: UUID) -> BatchRunRecord | None:
        """`run_id` 의 batch_run. 없으면 None."""
        ...

    def get_run(self, batch_id: UUID) -> BatchRunRecord | None:
        """frozen batch_id → `BatchRunRecord` (재현 cutoff 해소용, M1 T48c).

        reproduce_run 이 snapshot.data_versions 의 krx/dart batch_id 를 본
        메서드로 해소하여 `BatchCutoff(started_at, id)` 를 구성. `fetch_by_id`
        와 동일 의미 (별칭) — 재현 경로의 의도를 type-level 명시 + full
        `started_at` timestamp 보장 (L3: `func.date` 절단 금지, lexicographic
        비교는 full timestamp 기준). 없으면 None (run 의 frozen batch 가 삭제됐거나
        잘못된 id — 호출자가 처리).
        """
        ...

    def latest_successful(self, source: str) -> BatchRunRecord | None:
        """그 `source` 의 status ∈ {success, partial} batch 중 `started_at` 최신 1건.

        "successful" = committed data 를 생산한 배치 = success + partial. partial
        (부분 실패) 배치도 성공분의 실 데이터(citation/row)를 commit 했으므로
        데이터 기준일(freshness) source 자격이 success 와 동일하다. partial 도입
        전엔 이 배치들이 'success' 로 저장됐고 그때도 본 메서드가 집었으므로 본
        변경은 **byte-동일** (자격 불변, status 라벨만 운영 가시성 위해 분리).
        skipped/running 은 데이터 미생산·미확정이라 제외 (M5 #4a). 메서드명은
        파급 회피 위해 latest_successful 유지.

        `collect_batch_versions` (`snapshot_versions.py`) 의 source 별 max
        started_at 선택 패턴을 차용하되, **as_of 필터 없이** 그 source 전체에서
        가장 최근 성공/부분성공 batch 를 반환 — 데이터 신선도(stale) 진단의
        "데이터 기준일" source. tie (동일 started_at, 재시도 batch) 는 id
        내림차순으로 결정성 확보 (`collect_batch_versions` 의
        `ORDER BY started_at DESC, id DESC` 와 동일).

        후보가 없으면 None (그 source 의 성공 batch 가 한 번도 없음 = 데이터 없음).
        본 메서드는 사실(최신 성공 batch 의 시각)만 반환 — stale 판정·해석은 상위
        서비스 (`data_freshness.assess_data_freshness`) 책임 (§2.1 Fidelity).
        """
        ...


class FakeBatchRunRepository(BatchRunRepository):
    """In-memory batch_runs store — SQL 구현체의 contract reference."""

    def __init__(self) -> None:
        self._by_id: dict[UUID, BatchRunRecord] = {}

    def start(
        self,
        *,
        run_id: UUID,
        market: str | None,
        source: str,
        started_at: datetime,
    ) -> None:
        if run_id in self._by_id:
            raise ValueError(
                f"batch_run with id {run_id} already exists "
                f"(start 1 회 — run 메타)"
            )
        self._by_id[run_id] = BatchRunRecord(
            id=run_id,
            market=market,
            source=source,
            started_at=started_at,
            ended_at=started_at,
            success_count=0,
            status=BATCH_STATUS_RUNNING,
        )

    def finalize(
        self,
        *,
        run_id: UUID,
        ended_at: datetime,
        success_count: int,
        status: str,
    ) -> None:
        existing = self._by_id.get(run_id)
        if existing is None:
            raise ValueError(
                f"batch_run with id {run_id} not found (finalize before start)"
            )
        self._by_id[run_id] = BatchRunRecord(
            id=existing.id,
            market=existing.market,
            source=existing.source,
            started_at=existing.started_at,
            ended_at=ended_at,
            success_count=success_count,
            status=status,
        )

    def fetch_by_id(self, run_id: UUID) -> BatchRunRecord | None:
        return self._by_id.get(run_id)

    def get_run(self, batch_id: UUID) -> BatchRunRecord | None:
        # fetch_by_id 와 동일 — 재현 cutoff 해소용 별칭 (M1 T48c).
        return self._by_id.get(batch_id)

    def all(self) -> Sequence[BatchRunRecord]:
        """테스트 보조 — 결정적 정렬 (started_at, id) 전체 row."""
        return tuple(sorted(
            self._by_id.values(),
            key=lambda r: (r.started_at, str(r.id)),
        ))

    def latest_successful(self, source: str) -> BatchRunRecord | None:
        # source 일치 + status ∈ {success, partial} 후보 중 max(started_at),
        # tie 는 id (SQL 의 ORDER BY started_at DESC, id DESC LIMIT 1 동일 결정성).
        # partial 도 성공분 데이터를 commit 했으므로 자격 동일 — partial 도입 전
        # 'success' 저장과 byte-동일 (라벨만 분리).
        candidates = [
            r for r in self._by_id.values()
            if r.source == source
            and r.status in (BATCH_STATUS_SUCCESS, BATCH_STATUS_PARTIAL)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.started_at, r.id))


class SqlBatchRunRepository(BatchRunRepository):
    """SQLAlchemy 기반 batch_runs store — start(INSERT) + finalize(UPDATE)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start(
        self,
        *,
        run_id: UUID,
        market: str | None,
        source: str,
        started_at: datetime,
    ) -> None:
        existing = self._session.get(BatchRunORM, run_id)
        if existing is not None:
            raise ValueError(
                f"batch_run with id {run_id} already exists "
                f"(start 1 회 — run 메타)"
            )
        self._session.add(
            BatchRunORM(
                id=run_id,
                market=market,
                source=source,
                started_at=started_at,
                # finalize 전 placeholder — running row.
                ended_at=started_at,
                success_count=0,
                status=BATCH_STATUS_RUNNING,
            )
        )
        self._session.flush()

    def finalize(
        self,
        *,
        run_id: UUID,
        ended_at: datetime,
        success_count: int,
        status: str,
    ) -> None:
        orm = self._session.get(BatchRunORM, run_id)
        if orm is None:
            raise ValueError(
                f"batch_run with id {run_id} not found (finalize before start)"
            )
        # run 메타 갱신 — fact 테이블 아니므로 UPDATE 허용 (ADR-0020 무관).
        orm.ended_at = ended_at
        orm.success_count = success_count
        orm.status = status
        self._session.flush()

    def fetch_by_id(self, run_id: UUID) -> BatchRunRecord | None:
        orm = self._session.get(BatchRunORM, run_id)
        if orm is None:
            return None
        return BatchRunRecord(
            id=orm.id,
            market=orm.market,
            source=orm.source,
            # full started_at timestamp (L3) — UTCDateTime round-trip. cutoff
            # lexicographic 비교가 full timestamp 기준이므로 func.date 절단 금지.
            started_at=orm.started_at,
            ended_at=orm.ended_at,
            success_count=orm.success_count,
            status=orm.status,
        )

    def get_run(self, batch_id: UUID) -> BatchRunRecord | None:
        # fetch_by_id 와 동일 — 재현 cutoff 해소용 별칭 (M1 T48c). full
        # started_at timestamp 보장 (cutoff lexicographic 비교 기준).
        return self.fetch_by_id(batch_id)

    def latest_successful(self, source: str) -> BatchRunRecord | None:
        # collect_batch_versions (snapshot_versions.py) 의 정렬 패턴 차용 —
        # as_of 필터 없이 그 source 전체에서 최신 성공 batch 1건. started_at tie
        # 는 id 내림차순 (재시도 batch 결정성). success + partial 모두 수용 —
        # partial 도 성공분 데이터를 commit 했으므로 자격 동일 (partial 도입 전
        # 'success' 저장과 byte-동일, 라벨만 분리).
        stmt = (
            select(BatchRunORM)
            .where(
                BatchRunORM.source == source,
                BatchRunORM.status.in_(
                    (BATCH_STATUS_SUCCESS, BATCH_STATUS_PARTIAL)
                ),
            )
            .order_by(BatchRunORM.started_at.desc(), BatchRunORM.id.desc())
            .limit(1)
        )
        orm = self._session.execute(stmt).scalars().first()
        if orm is None:
            return None
        return BatchRunRecord(
            id=orm.id,
            market=orm.market,
            source=orm.source,
            started_at=orm.started_at,
            ended_at=orm.ended_at,
            success_count=orm.success_count,
            status=orm.status,
        )
