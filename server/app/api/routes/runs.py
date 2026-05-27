"""Screen Run snapshot endpoint — POST /api/runs (Save Run) + GET.

ADR-0008 D7 / D7-bis 의 Screen Run snapshot freeze implementation. T40 (Save Run
버튼) 의 backend. ADR-0002 D4 의 immutable + append-only 일관.

설계 (oracle T26 자문 P0):
- POST /api/runs — 재실행 + 저장 (M0 단순화)
- GET /api/runs — CurrentUserDep 의 runs only (IDOR 차단)
- GET /api/runs/{id} — fetch by id (user_id 검증)
- GET /api/runs/{id}/diff — VersionsDiffOut (현재 active 와 비교)
"""

from __future__ import annotations

from typing import Final
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import RunsRepoDep, StocksRepoDep
from app.api.routes.screen import _fetch_all_active_codes
from app.schemas.screen import (
    ScreenRunListOut,
    ScreenRunQueryIn,
    ScreenRunSnapshotOut,
    VersionsDiffOut,
)
from app.services.screen_run import ScreenRunBuilder
from app.services.snapshot_versions import collect_active_policy_versions

router = APIRouter(prefix="/api/runs", tags=["runs"])

_DEFAULT_LIST_LIMIT: Final[int] = 20
_MAX_LIST_LIMIT: Final[int] = 100


@router.post("", response_model=ScreenRunSnapshotOut, status_code=status.HTTP_201_CREATED)
async def save_run(
    as_of: NormalizedAsOfDep,
    user: CurrentUserDep,
    stocks_repo: StocksRepoDep,
    runs_repo: RunsRepoDep,
    body: ScreenRunQueryIn,
) -> ScreenRunSnapshotOut:
    """Save Run — 재실행 + 저장. T40 (Save Run 버튼) 의 backend.

    M0 단순화 (oracle 자문 결정 1 대안 B):
        실행과 저장이 같은 호출. 두 호출 분리 + token 패턴은 over-engineering.
        실행 비용이 M0 stub 이라 무시 가능. T18 합류 후 실제 평가 비용이 커지면
        re-evaluate.

    결과 = ScreenRunBuilder.build(conditions, selected_factors, as_of, result_codes).
    result_codes 는 fixture 의 active 종목 (T18 후 condition match 로 교체).
    """
    # 1. M0 stub — fixture 의 active 종목 코드.
    result_codes = _fetch_all_active_codes(stocks_repo, as_of_value=as_of.value)

    # 2. ScreenRunBuilder — canonical 정규화 + result_hash + data_versions freeze.
    snapshot = ScreenRunBuilder.build(
        run_id=uuid4(),
        user_id=user.user_id,
        conditions=[
            {"factor": c.factor, "op": c.op.value, "value": c.value}
            for c in body.conditions
        ],
        selected_factors=body.selected_factors,
        as_of=as_of.value,
        result_codes=result_codes,
    )

    # 3. 저장 — Fake/Sql 무관 (append-only).
    runs_repo.save(snapshot)

    return ScreenRunSnapshotOut.from_domain(snapshot)


@router.get("", response_model=ScreenRunListOut)
async def list_runs(
    user: CurrentUserDep,
    runs_repo: RunsRepoDep,
    limit: int = Query(_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
) -> ScreenRunListOut:
    """Current user 의 recent runs — IDOR 차단 (oracle 결정 5).

    User_id query param 없음 — CurrentUserDep 가 SYSTEM_USER_ID (M0) 또는
    NextAuth jwt sub (T31 후).
    """
    snapshots = runs_repo.fetch_recent(user_id=user.user_id, limit=limit)
    items = tuple(ScreenRunSnapshotOut.from_domain(s) for s in snapshots)
    return ScreenRunListOut(items=items, total=len(items))


@router.get("/{run_id}", response_model=ScreenRunSnapshotOut)
async def get_run(
    run_id: UUID,
    user: CurrentUserDep,
    runs_repo: RunsRepoDep,
) -> ScreenRunSnapshotOut:
    """단일 Run snapshot — user_id 검증 (IDOR 차단)."""
    snapshot = runs_repo.fetch_by_id(run_id, user_id=user.user_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 을 찾을 수 없습니다.",
        )
    return ScreenRunSnapshotOut.from_domain(snapshot)


@router.get("/{run_id}/diff", response_model=VersionsDiffOut)
async def get_run_diff(
    run_id: UUID,
    user: CurrentUserDep,
    runs_repo: RunsRepoDep,
) -> VersionsDiffOut:
    """Snapshot 의 data_versions 와 현재 active 비교 — UI freshness banner.

    재현성 (§2.10) 의 운영 의미 — 6 개월 후 같은 Run 재현 시 변경된 정책 키 노출.
    빈 diff = 일치 (재현 OK).

    oracle 자문 결정 4 — list endpoint 의 N+1 회피 위해 별도 endpoint.
    """
    snapshot = runs_repo.fetch_by_id(run_id, user_id=user.user_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run 을 찾을 수 없습니다.",
        )
    current = collect_active_policy_versions()
    diff = snapshot.diff_versions(current)
    return VersionsDiffOut(
        snapshot_id=snapshot.id,
        diff={k: (v[0], v[1]) for k, v in diff.items()},
    )
