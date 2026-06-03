"""ORM 모델 — ADR-0002 / ADR-0008 / ADR-0009 / ADR-0011 entity.

본 패키지는 ORM 클래스를 모두 import 하여 Alembic env (`alembic/env.py`) 가
`Base.metadata` 만 import 하면 모든 테이블이 등록되도록 보장. Alembic
autogenerate / 단위 테스트의 `Base.metadata.create_all` 모두 본 import 가
side-effect 으로 모델 등록을 트리거.

5 core 테이블 (T13 Phase A, migration 0001):
    - source_citations — ADR-0002 D3 의 7-tuple
    - stocks_master — ADR-0009 D6 의 lineage entity (+ code_history JSON)
    - prices_daily — ADR-0001 의 raw + adjusted 가격
    - financials — ADR-0002 D5 + ADR-0009 D5 정정공시 chain (superseded_by)
    - corporate_actions — ADR-0009 D1 (이중 PIT — announced/effective)

사용자 데이터 (T13 Phase B, migration 0002):
    - watchlists — ADR-0011 D2 의 폴더 (parent_id self-FK + depth ≤ 2)
    - watchlist_items — folder 의 종목 entry (UNIQUE(folder, lineage))
    - screener_sets — 조건셋 entity (JSON conditions + selected_factors)

Run snapshot (T13 Phase C, migration 0003):
    - screen_runs — ADR-0008 D7 + 8 기둥 §2.10 Reproducibility 의 freeze

Reproducibility 기반 (M1 Phase M1-0, migration 0006):
    - batch_runs — M1 T48a 의 일배치 메타데이터 SoT (source_citations.batch_id
      의 FK 참조 대상). 재현성 (§2.10) 의 "as_of 시점 최신 성공 batch" 쿼리.

market_cap 영구화 (Phase B, migration 0008):
    - market_caps — ADR-0003 D2 + ADR-0004 의 일별 시가총액 + 발행주식수.
      prices_daily 패턴 미러 (정정 없음, PK (code, effective_date)).

자사주 영구화 (migration 0009):
    - treasury_shares — DART stockTotqySttus.json 의 보통주 자기주식수.
      financials 패턴 미러 (정정 chain superseded_by + ADR-0020 조건부 트리거).
      DbFieldProvider 의 `shares_treasury` 해소 → factor `market-cap:ex-treasury`
      실평가.

ECOS 거시지표 vintage 선설계 (M1 T62, migration 0011):
    - macro_indicators — 한국은행 ECOS 거시지표 vintage 이중 시간축
      (reference_date × vintage_date). superseded_by 없이 새 vintage row 의
      자연 누적으로 사후 개정 표현. PIT 조회 규약(vintage_date<=as_of max)은
      T64 MacroIndicatorRepository 에서 구현 예정.

멀티유저 전환 인프라 (M2 T69, migration 0012):
    - users — ADR-0021 D2/D3 의 user 격리 FK anchor. 최소 스키마(id +
      created_at) + system sentinel row(00000000-…-0001). OAuth 메타는 T68
      (NextAuth)에서 컬럼 추가. watchlists/screener_sets/screen_runs.user_id 가
      본 테이블을 FK 참조.

종목별 사용자 메모 (M2 T76, migration 0014):
    - stock_notes — 종목 lineage 단위 사용자 Markdown 메모(USER_PRIVATE).
      user_id FK → users.id(격리 anchor) + code_lineage_id(FK 없음, lineage)
      + body TEXT + scope. watchlist_items 동형이나 폴더 없이 종목별 직접 메모,
      append-only 아닌 mutable CRUD. 시스템 생성 0 — route + repository 만 생성.

Factor Lab 사용자 정의 pack 영속화 (ADR-0022 D9, migration 0017):
    - custom_packs — Factor Lab 사용자 정의 pack 의 user-scoped 저장. user_id FK
      → users.id(격리 anchor) + pack_slug + version + content_hash + body JSON +
      factor_count. **append-only immutable**(updated_at 없음 — screen_runs 정신).
      UNIQUE(user_id, pack_slug, version)로 같은 버전 다른 정의를 충돌(409)로
      차단. 시스템 생성 0 — 인증 route + repository 만 생성(migration insert 없음).

Portfolio 거래내역 (M3 #4, ADR-0029 D1, migration 0019):
    - portfolio_transactions — 종목 lineage 단위 사용자 수동 입력 거래내역
      (side/quantity/unit_price/trade_date/fee). user_id FK → users.id(격리
      anchor) + code_lineage_id(FK 없음, lineage). **append-only**(updated_at
      없음 — 역분개로 정정, 입력 실수 정정용 본인 거래 삭제만 허용). 회계≠평가
      분리(D3) — position 계산은 service(portfolio_position)가 stateless 수행.
      시스템 생성 0 — 인증 route + repository 만 생성(migration insert 없음).

Out-of-scope (별도 사이클):
    - users 의 OAuth 메타(email / sub / provider) — NextAuth 합류 (T68)
    - factor_definitions / factor_packs (현재 in-memory pack loader 충분)
    - stock_snapshots (factor 결과 precomputed — 일배치 합류 후 의미)
"""

from __future__ import annotations

from app.db.orm.batch_runs import BatchRunORM
from app.db.orm.corporate_actions import CorporateActionORM
from app.db.orm.custom_packs import CustomPackORM
from app.db.orm.financials import FinancialORM
from app.db.orm.macro_indicators import MacroIndicatorORM
from app.db.orm.market_caps import MarketCapDailyORM
from app.db.orm.notes import StockNoteORM
from app.db.orm.portfolio_transactions import PortfolioTransactionORM
from app.db.orm.prices_daily import PriceDailyORM
from app.db.orm.screen_runs import ScreenRunSnapshotORM
from app.db.orm.screener_sets import ScreenerSetORM
from app.db.orm.source_citations import SourceCitationORM
from app.db.orm.stocks_master import StocksMasterORM
from app.db.orm.treasury_shares import TreasurySharesORM
from app.db.orm.users import UserORM
from app.db.orm.watchlists import WatchlistFolderORM, WatchlistItemORM

__all__ = [
    "BatchRunORM",
    "CorporateActionORM",
    "CustomPackORM",
    "FinancialORM",
    "MacroIndicatorORM",
    "MarketCapDailyORM",
    "PortfolioTransactionORM",
    "PriceDailyORM",
    "ScreenRunSnapshotORM",
    "ScreenerSetORM",
    "SourceCitationORM",
    "StockNoteORM",
    "StocksMasterORM",
    "TreasurySharesORM",
    "UserORM",
    "WatchlistFolderORM",
    "WatchlistItemORM",
]
