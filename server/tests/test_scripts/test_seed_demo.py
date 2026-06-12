"""seed_demo 검증 테스트 — seed 데이터가 실제 factor 를 N/A 아닌 실값으로 산출.

테스트 전략:
    1. in-memory SQLite 에 `Base.metadata.create_all` + seed dataset 적용
       (`seed_session`). 운영 wiring (SQL repository + DbFieldProvider +
       FactorEvaluator) 그대로 사용 — seed 가 실제 앱 경로를 통해 작동함을 보증.
    2. 한 종목의 PER / PBR / ROE / EPS / market-cap-ex-treasury 가 N/A 가 아닌
       실값임을 assert (자사주 보유 종목 = NAVER 로 market-cap:ex-treasury 실평가).
    3. Screener (per:ttm-consolidated-ifrs < 큰값) 가 종목을 반환함을 assert.
    4. 멱등성 — seed 2 회 적용 후에도 row 수 / factor 값 동일.
    5. PIT — 모든 fact 의 effective_date <= as_of.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.orm import (  # noqa: F401  ← Base.metadata 등록 side-effect
    BatchRunORM,
    CorporateActionORM,
    FinancialORM,
    MarketCapDailyORM,
    PriceDailyORM,
    ScreenerSetORM,
    ScreenRunSnapshotORM,
    SourceCitationORM,
    StocksMasterORM,
    TreasurySharesORM,
    WatchlistFolderORM,
    WatchlistItemORM,
)
from app.repositories.sql_repositories import (
    SqlCorporateActionRepository,
    SqlFinancialRepository,
    SqlMarketCapRepository,
    SqlPriceRepository,
    SqlStocksMasterRepository,
    SqlTreasurySharesRepository,
)
from app.schemas.screen import ConditionIn, OpEnum
from app.services.db_field_provider import DbFieldProvider
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import DEFAULT_PACK
from scripts.seed_demo import (
    SEED_AS_OF,
    build_seed_dataset,
    seed_session,
)

_NAVER = "035420"  # 자사주 보유 종목 — market-cap:ex-treasury 실평가 검증.


@pytest.fixture
def seeded_engine() -> Iterator[Engine]:
    """in-memory SQLite + seed dataset 적용된 engine (StaticPool 공유)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _conn_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Sessionmaker = sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)
    with Sessionmaker() as session:
        seed_session(session, build_seed_dataset())
        session.commit()
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _provider(session: Session, code: str) -> DbFieldProvider:
    """운영 wiring 그대로 — SQL repository + pack + evaluator 주입."""
    return DbFieldProvider(
        code=code,
        as_of=SEED_AS_OF,
        price_repo=SqlPriceRepository(session),
        financial_repo=SqlFinancialRepository(session),
        corporate_action_repo=SqlCorporateActionRepository(session),
        market_cap_repo=SqlMarketCapRepository(session),
        treasury_repo=SqlTreasurySharesRepository(session),
        factor_pack=DEFAULT_PACK,
        evaluator=FactorEvaluator(),
    )


def _factor(canonical_id: str) -> dict:
    return next(
        f for f in DEFAULT_PACK.body["factors"]
        if f["canonical_id"] == canonical_id
    )


def test_seed_produces_real_factor_values(seeded_engine: Engine) -> None:
    """seed 후 한 종목의 핵심 factor 가 N/A 아닌 실값 — 운영 경로 검증."""
    Sessionmaker = sessionmaker(
        bind=seeded_engine, expire_on_commit=False, autoflush=True,
    )
    with Sessionmaker() as session:
        evaluator = FactorEvaluator()
        provider = _provider(session, _NAVER)

        # market-cap:ex-treasury — 자사주 보유 종목이라 실평가 (NAVER).
        mcap = evaluator.evaluate(
            _factor("market-cap:ex-treasury"), provider, as_of=SEED_AS_OF,
        )
        assert mcap.is_na is False
        assert mcap.value is not None and mcap.value > 0

        per = evaluator.evaluate(
            _factor("per:ttm-consolidated-ifrs"), provider, as_of=SEED_AS_OF,
        )
        assert per.is_na is False
        assert per.value is not None and per.value > 0

        pbr = evaluator.evaluate(
            _factor("pbr:consolidated-ifrs"), provider, as_of=SEED_AS_OF,
        )
        assert pbr.is_na is False
        assert pbr.value is not None and pbr.value > 0

        roe = evaluator.evaluate(
            _factor("roe:ttm-avg-equity-consolidated-ifrs"),
            provider, as_of=SEED_AS_OF,
        )
        assert roe.is_na is False
        assert roe.value is not None and roe.value > 0

        eps = evaluator.evaluate(
            _factor("eps:basic-ttm-consolidated-ifrs"), provider, as_of=SEED_AS_OF,
        )
        assert eps.is_na is False
        assert eps.value is not None and eps.value > 0

        mcap_official = evaluator.evaluate(
            _factor("market-cap:krx-official"), provider, as_of=SEED_AS_OF,
        )
        assert mcap_official.is_na is False
        assert mcap_official.value is not None and mcap_official.value > 0


def test_seed_factor_values_match_expected_arithmetic(seeded_engine: Engine) -> None:
    """factor 값이 입력 수치와 산술적으로 일치 — 표시값 신뢰성 (Fidelity)."""
    from decimal import Decimal

    from scripts.seed_demo import _DEMO_STOCKS

    naver = next(s for s in _DEMO_STOCKS if s.code == _NAVER)
    Sessionmaker = sessionmaker(
        bind=seeded_engine, expire_on_commit=False, autoflush=True,
    )
    with Sessionmaker() as session:
        evaluator = FactorEvaluator()
        provider = _provider(session, _NAVER)

        expected_mcap = (
            (Decimal(naver.shares_outstanding) - Decimal(naver.shares_treasury))
            * Decimal(naver.close_won)
        )
        mcap = evaluator.evaluate(
            _factor("market-cap:ex-treasury"), provider, as_of=SEED_AS_OF,
        )
        assert mcap.value == expected_mcap

        # EPS TTM = 4 분기 basic_eps 합.
        eps = evaluator.evaluate(
            _factor("eps:basic-ttm-consolidated-ifrs"), provider, as_of=SEED_AS_OF,
        )
        assert eps.value == Decimal(sum(naver.basic_eps_quarters))

        # PER = market_cap_ex_treasury / sum(net_income 4Q).
        per = evaluator.evaluate(
            _factor("per:ttm-consolidated-ifrs"), provider, as_of=SEED_AS_OF,
        )
        ni_total = Decimal(sum(naver.net_income_quarters))
        assert per.value == expected_mcap / ni_total


def test_seed_screener_returns_stocks(seeded_engine: Engine) -> None:
    """Screener (per < 큰값) 가 종목을 반환 — 작동하는 Screener 검증."""
    from app.api.routes.screen import screen_active_codes

    Sessionmaker = sessionmaker(
        bind=seeded_engine, expire_on_commit=False, autoflush=True,
    )
    with Sessionmaker() as session:
        codes = screen_active_codes(
            as_of=SEED_AS_OF,
            conditions=[
                ConditionIn(
                    factor="per:ttm-consolidated-ifrs",
                    op=OpEnum.LT, value="100000",
                ),
            ],
            stocks_repo=SqlStocksMasterRepository(session),
            pack=DEFAULT_PACK,
            evaluator=FactorEvaluator(),
            price_repo=SqlPriceRepository(session),
            financial_repo=SqlFinancialRepository(session),
            corporate_action_repo=SqlCorporateActionRepository(session),
            market_cap_repo=SqlMarketCapRepository(session),
            treasury_repo=SqlTreasurySharesRepository(session),
        )
        # 모든 종목이 PER < 100000 충족 (현실적 PER 은 수~수십). 6 종목 모두 반환.
        assert len(codes) == 6
        assert _NAVER in codes


def test_seed_screener_eps_threshold_filters(seeded_engine: Engine) -> None:
    """Screener (eps > 큰 임계) 가 일부만 반환 — 실제 비교 동작 검증."""
    from app.api.routes.screen import screen_active_codes

    Sessionmaker = sessionmaker(
        bind=seeded_engine, expire_on_commit=False, autoflush=True,
    )
    with Sessionmaker() as session:
        # EPS TTM > 10000 — 현대차 (TTM ~59500) 만 충족, 카카오 (TTM ~1030) 제외.
        codes = screen_active_codes(
            as_of=SEED_AS_OF,
            conditions=[
                ConditionIn(
                    factor="eps:basic-ttm-consolidated-ifrs",
                    op=OpEnum.GT, value="10000",
                ),
            ],
            stocks_repo=SqlStocksMasterRepository(session),
            pack=DEFAULT_PACK,
            evaluator=FactorEvaluator(),
            price_repo=SqlPriceRepository(session),
            financial_repo=SqlFinancialRepository(session),
            corporate_action_repo=SqlCorporateActionRepository(session),
            market_cap_repo=SqlMarketCapRepository(session),
            treasury_repo=SqlTreasurySharesRepository(session),
        )
        assert "005380" in codes  # 현대차 — EPS TTM 높음.
        assert "035720" not in codes  # 카카오 — EPS TTM 낮음.


def test_seed_idempotent(seeded_engine: Engine) -> None:
    """seed 재적용 시 row 수 동일 — 멱등성 (기존 삭제 후 재삽입)."""
    Sessionmaker = sessionmaker(
        bind=seeded_engine, expire_on_commit=False, autoflush=True,
    )
    with Sessionmaker() as session:
        before = session.execute(
            select(func.count()).select_from(PriceDailyORM)
        ).scalar_one()
        # 두 번째 seed 적용.
        seed_session(session, build_seed_dataset())
        session.commit()
        after = session.execute(
            select(func.count()).select_from(PriceDailyORM)
        ).scalar_one()
        assert after == before
        # 종목 마스터도 6 종목 그대로.
        stock_count = session.execute(
            select(func.count()).select_from(StocksMasterORM)
        ).scalar_one()
        assert stock_count == 6


def test_seed_pit_all_facts_before_as_of(seeded_engine: Engine) -> None:
    """PIT — 모든 fact 의 effective_date <= as_of (look-ahead 0)."""
    Sessionmaker = sessionmaker(
        bind=seeded_engine, expire_on_commit=False, autoflush=True,
    )
    with Sessionmaker() as session:
        for orm in (PriceDailyORM, MarketCapDailyORM, FinancialORM, TreasurySharesORM):
            max_eff = session.execute(
                select(func.max(orm.effective_date))
            ).scalar_one()
            assert max_eff is not None
            assert date.fromisoformat(str(max_eff)) <= SEED_AS_OF, orm.__name__


def test_build_seed_dataset_shape() -> None:
    """dataset 구조 — 6 종목 / 2 batch / 3 citation (KRX+DART+ECOS) / 자사주 일부만."""
    ds = build_seed_dataset()
    assert len(ds.stocks) == 6
    assert len(ds.batch_runs) == 2
    # T64 — ECOS citation 추가로 3 개 (KRX / DART / ECOS).
    assert len(ds.citations) == 3
    # 모든 종목이 treasury row 보유 (market-cap:ex-treasury 실평가).
    assert len(ds.treasury) == 6
    # 매크로 지표 seed 확인 (기준금리 + CPI 일부).
    assert len(ds.macro_indicators) > 0
    # 모든 fact 가 citation_id FK 보유 (citation id 중 하나).
    citation_ids = {c.id for c in ds.citations}
    for rec in (*ds.prices, *ds.market_caps, *ds.financials, *ds.treasury,
                *ds.macro_indicators):
        assert rec.citation_id in citation_ids
