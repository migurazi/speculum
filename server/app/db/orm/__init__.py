"""ORM 모델 — ADR-0002 D5 / ADR-0009 D6 의 5 core 테이블.

본 패키지는 ORM 클래스를 모두 import 하여 Alembic env (`alembic/env.py`) 가
`Base.metadata` 만 import 하면 모든 테이블이 등록되도록 보장. Alembic
autogenerate / 단위 테스트의 `Base.metadata.create_all` 모두 본 import 가
side-effect 으로 모델 등록을 트리거.

5 core 테이블 (T13 scope):
    - source_citations — ADR-0002 D3 의 7-tuple
    - stocks_master — ADR-0009 D6 의 lineage entity (+ code_history JSON)
    - prices_daily — ADR-0001 의 raw + adjusted 가격
    - financials — ADR-0002 D5 + ADR-0009 D5 정정공시 chain (superseded_by)
    - corporate_actions — ADR-0009 D1 (이중 PIT — announced/effective)

Out-of-scope (T13 Phase B 또는 별도 사이클):
    - users / watchlists / watchlist_items / screener_sets (ADR-0011)
    - screen_runs (ADR-0014)
    - factor_definitions / factor_packs (현재 in-memory pack loader 충분)
    - stock_snapshots (factor 결과 precomputed — 일배치 합류 후 의미)
"""

from __future__ import annotations

from app.db.orm.corporate_actions import CorporateActionORM
from app.db.orm.financials import FinancialORM
from app.db.orm.prices_daily import PriceDailyORM
from app.db.orm.source_citations import SourceCitationORM
from app.db.orm.stocks_master import StocksMasterORM

__all__ = [
    "CorporateActionORM",
    "FinancialORM",
    "PriceDailyORM",
    "SourceCitationORM",
    "StocksMasterORM",
]
