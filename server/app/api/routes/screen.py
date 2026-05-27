"""Screen 실행 endpoint — POST /api/screen.

ADR-0008 D6 의 API contract — Screener 의 조건 빌더 입력 + as_of + 결과.
실행만 — Save Run 은 POST /api/runs 별도 (oracle T26 결정 1).

M0 stub 정책:
- 실제 factor 평가 + condition match 는 T18 합류 후. 본 사이클은 fixture 의
  StocksMasterRepository 의 모든 active 종목 코드 반환 — schema / hash 의 wire
  invariant 검증.
- ScreenRunBuilder 의 result_hash 결정성을 endpoint 단에서 확인 — 같은 query +
  as_of + (active 정책) 면 동일 hash.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import NormalizedAsOfDep
from app.api.dependencies.repositories import StocksRepoDep
from app.schemas.screen import ScreenResultOut, ScreenRunQueryIn
from app.services.snapshot_versions import collect_active_policy_versions

router = APIRouter(prefix="/api", tags=["screen"])


@router.post("/screen", response_model=ScreenResultOut)
async def execute_screen(
    as_of: NormalizedAsOfDep,
    stocks_repo: StocksRepoDep,
    body: ScreenRunQueryIn,
) -> ScreenResultOut:
    """Screener 실행 — Save 안 함, 결과만 반환.

    M0 stub: condition match 는 미구현. fixture 의 active 종목 코드 전체 반환.
    `data_versions` 는 호출 시점 freeze — 클라이언트가 그대로 /api/runs 저장
    POST 에 전달하여 두 호출 간 정책 변경 race 차단 (oracle Risk-X2).

    AC-F-03 (Screener 결과 — 1만 종목 가상화) 의 backend half — frontend 가
    response 의 result_codes 로 가상화 list 렌더.
    """
    # M0 stub — fixture 의 active 종목만. T18 합류 후 condition match 로 교체.
    # search(empty query) 가 ValueError 이므로 직접 fetch_by_code 들로 우회 X.
    # 간단히 모든 active 종목을 fixture iteration (FakeStocksMasterRepository 의
    # _records 직접 접근). 추상화는 별도 method (`list_active`) — backlog.
    all_codes = _fetch_all_active_codes(stocks_repo, as_of_value=as_of.value)

    # data_versions freeze — 호출 시점.
    data_versions = dict(collect_active_policy_versions())

    return ScreenResultOut(
        result_codes=tuple(all_codes),
        total=len(all_codes),
        data_versions=data_versions,
    )


def _fetch_all_active_codes(stocks_repo, *, as_of_value) -> list[str]:
    """active universe — Repository Protocol 의 `list_active` 위임 (oracle 2 차 C1).

    M0 stub: condition match 미구현 → 모든 active 종목 반환. T18 합류 후 본
    함수가 evaluator 호출 + `ConditionIn` 평가로 교체.

    Protocol 호환 — `_records` 직접 접근 (Fake 한정) 회피하여 SQLAlchemy 구현체
    도 같은 contract.
    """
    active = stocks_repo.list_active(as_of=as_of_value)
    return [r.current_code for r in active if r.current_code]
