"""Portfolio 거래내역 + position endpoint — M3 #4 (ADR-0029).

거래내역 기반 사실 회계 — 사용자 **수동 입력** 거래내역의 CRUD + 종목별 position
(보유수량·평단·원가·실현손익 + 현재가 조회 가능 시 평가) 계산. 회계 ≠ 평가 분리
(D3) — 응답은 숫자 사실만(손익률·등급·순위·등락색 0). 세전만(D6). 계좌 연동 0(D4).

Endpoints:
    POST   /api/portfolio/transactions          — 거래 입력(201)
    GET    /api/portfolio/transactions           — 본인 거래내역
    DELETE /api/portfolio/transactions/{tx_id}   — 본인 거래 삭제(204)
    GET    /api/portfolio/positions?as_of=        — position 계산(CA 보정 적용)

설계 (watchlist/notes 선례 일관):
- 모든 endpoint 가 CurrentUserDep — body 에 user_id X(IDOR 차단). user_id 는
  CurrentUserDep 로만 주입.
- Pydantic v2 strict input + from_domain output.
- 404 — ownership mismatch / 미존재(구별 X — IDOR 정보 누출 차단).
- PortfolioDataError → 400(side 값, 음수 수량/단가). Pydantic Field 위반은 422.

가드레일(ADR-0029):
- user 격리 절대 — 타 user 거래 list 빈 결과 + delete 404.
- 평가 라벨 0(D3) — positions 응답에 손익률/등급/순위/색분기 필드 없음.
- 세전만(D6) — 세금 필드/계산 없음(fee 는 사용자 입력 거래 비용일 뿐).
- 수동 입력만(D4) — 증권사 계좌 연동 없음.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.as_of import BrowseAsOfDep
from app.api.dependencies.auth import CurrentUserDep
from app.api.dependencies.repositories import (
    CorporateActionRepoDep,
    PortfolioRepoDep,
    PriceRepoDep,
    StocksRepoDep,
)
from app.repositories.portfolio_repository import PortfolioDataError
from app.schemas.portfolio import (
    PortfolioPositionOut,
    PortfolioPositionsOut,
    PortfolioTransactionCreateIn,
    PortfolioTransactionListOut,
    PortfolioTransactionOut,
)
from app.services.portfolio_position import compute_position

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# 현재가 조회 lookback — as_of 가 휴장일이거나 직전 거래가 며칠 전이어도 최신
# close 를 찾도록 충분한 캘린더 윈도(연휴 + 주말 여유). PriceRepository 가
# effective_date<=as_of 만 반환하므로 look-ahead 0.
_PRICE_LOOKBACK_DAYS = 14


# =============================================================================
# 거래내역 CRUD
# =============================================================================

@router.post(
    "/transactions",
    response_model=PortfolioTransactionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_transaction(
    user: CurrentUserDep,
    repo: PortfolioRepoDep,
    body: PortfolioTransactionCreateIn,
) -> PortfolioTransactionOut:
    # user_id 는 CurrentUserDep 가 결정(body 에서 안 받음 — IDOR 차단). 수동 입력.
    try:
        tx = repo.add_transaction(
            user_id=user.user_id,
            code_lineage_id=body.code_lineage_id,
            side=body.side,
            quantity=body.quantity,
            unit_price=body.unit_price,
            trade_date=body.trade_date,
            fee=body.fee,
        )
    except PortfolioDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PortfolioTransactionOut.from_domain(tx)


@router.get("/transactions", response_model=PortfolioTransactionListOut)
async def list_transactions(
    user: CurrentUserDep,
    repo: PortfolioRepoDep,
    code_lineage_id: UUID | None = None,
) -> PortfolioTransactionListOut:
    # user_id 필터 — repository 가 강제(전 사용자 누출 차단). code_lineage_id
    # 주어지면 해당 종목만, None 이면 전 종목.
    txns = repo.list_transactions(
        user_id=user.user_id, code_lineage_id=code_lineage_id,
    )
    items = tuple(PortfolioTransactionOut.from_domain(t) for t in txns)
    return PortfolioTransactionListOut(items=items, total=len(items))


# response_model=None — 204 No Content body 불가(watchlists/notes delete 선례).
@router.delete(
    "/transactions/{tx_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_transaction(
    tx_id: UUID,
    user: CurrentUserDep,
    repo: PortfolioRepoDep,
) -> None:
    # 입력 실수 정정용 본인 거래 삭제(append-only 정신상 역분개 권장이나 허용).
    # 타 user 거래 → 404(owner mismatch — IDOR 차단).
    ok = repo.delete_transaction(tx_id, user_id=user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="거래를 찾을 수 없습니다.")


# =============================================================================
# Position 계산 — 거래내역 → 사실 회계(CA 보정 적용)
# =============================================================================

@router.get("/positions", response_model=PortfolioPositionsOut)
async def get_positions(
    as_of: BrowseAsOfDep,
    user: CurrentUserDep,
    repo: PortfolioRepoDep,
    stocks_repo: StocksRepoDep,
    price_repo: PriceRepoDep,
    corporate_action_repo: CorporateActionRepoDep,
) -> PortfolioPositionsOut:
    """종목별 position — 보유수량/평단/원가/실현손익 + (현재가 조회 시)평가.

    corporate action 보정(D5) 적용 — 거래일 이후 발효된 액면분할/무상증자 비율을
    보유수량·평단에 반영. 현재가는 PriceRepository 가 `effective_date <= as_of`
    최신 close 로 조회(조회 가능 시 평가금액·평가손익 산술, 불가 시 None).

    **숫자 사실만**(ADR-0029 D3) — 손익률/등급/순위/등락색 0. 세전만(D6).
    user 격리 절대 — repository 가 user_id 필터(타 user 거래 미반영).
    """
    txns = repo.list_transactions(user_id=user.user_id)

    # 종목별 거래 그룹화 — 같은 lineage 의 거래를 한 position 으로 집계.
    by_lineage: dict[UUID, list] = defaultdict(list)
    for tx in txns:
        by_lineage[tx.code_lineage_id].append(tx)

    # lineage_id → current_code 매핑 — as_of 시점 active lineage 에서 도출(현재가·
    # CA 조회용 KRX 코드). price/CA repo 가 code(str) 기반이라 lineage 를 코드로
    # 해소. 폐지/미상장/미bootstrap 등으로 코드 미해소 시 시장 데이터 없이 회계
    # 사실만 산출(수동 입력 장부 — 시장 데이터 부재 graceful).
    code_by_lineage = {
        rec.id: rec.current_code
        for rec in stocks_repo.list_active(as_of=as_of.value)
        if rec.current_code is not None
    }

    positions: list[PortfolioPositionOut] = []
    # code_lineage_id asc 결정적 정렬(손익순/랭킹 정렬 0 — D3 행동 유도 차단).
    for lineage_id in sorted(by_lineage.keys()):
        lineage_txns = by_lineage[lineage_id]
        code = code_by_lineage.get(lineage_id)

        corporate_actions = ()
        current_price = None
        if code is not None:
            # CA(D5 보정용) — announced_date<=as_of active chain. position service
            # 가 거래일 이후 발효 분만 보정 대상으로 필터.
            corporate_actions = corporate_action_repo.fetch_actions(
                code, as_of=as_of.value,
            )
            # 현재가 — effective_date<=as_of 최신 close(raw). 평가금액 산출.
            # lookback 윈도로 휴장일/연휴에도 직전 거래일 close 확보(look-ahead 0).
            price_records = price_repo.fetch_prices(
                code,
                as_of=as_of.value,
                start=as_of.value - timedelta(days=_PRICE_LOOKBACK_DAYS),
            )
            if price_records:
                # fetch_prices 는 오름차순 — 마지막이 as_of 최신.
                current_price = price_records[-1].close_raw

        position = compute_position(
            lineage_txns,
            code_lineage_id=lineage_id,
            corporate_actions=corporate_actions,
            current_price=current_price,
        )
        positions.append(PortfolioPositionOut.from_domain(position))

    return PortfolioPositionsOut(positions=tuple(positions))
