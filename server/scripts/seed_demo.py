"""dev 데모 seed 스크립트 — 빈 앱에 샘플 fact 를 심어 factor 카드 / Screener 실동작.

목적:
    `SPECULUM_DATABASE_URL` 로 SQL repository wiring 을 켠 앱은 데이터가 없으면
    모든 factor 가 N/A 로 표시된다. 본 스크립트는 ~6 종목의 현실적 종목/재무/가격/
    시총/자사주를 심어, `uvicorn` + `pnpm dev` 로 띄웠을 때 실제 PER/PBR/ROE/EPS/
    market-cap 카드 + 작동하는 Screener 를 볼 수 있게 한다.

실행:
    python -m scripts.seed_demo
    python scripts/seed_demo.py
    SPECULUM_DATABASE_URL=sqlite:///./speculum_dev.db python -m scripts.seed_demo

설계:
    - **스키마**: `Base.metadata.create_all`. SQLite 는 ADR-0020 의 PG-only
      append-only 트리거를 dialect 분기로 skip 하므로 create_all 로 충분.
      FK 순서는 본 스크립트가 INSERT 순서로 강제 (batch_runs → source_citations
      → fact). source_citations.batch_id FK 는 DEFERRABLE INITIALLY DEFERRED 라
      batch_runs 를 먼저 넣으면 안전.
    - **멱등성**: 같은 종목코드의 기존 fact / citation / batch / 마스터를 먼저
      삭제 후 재삽입. 재실행 시 항상 동일 결과 (PIT effective_date <= as_of 보장).
    - **PIT**: 모든 fact 의 effective_date <= as_of (2024-06-28). financials /
      treasury 의 effective_date 는 보고서 접수일 (as_of 이전), effective_date_
      precise=True. 모든 fact 가 citation_id FK 로 출처 연결 (Fidelity §2.1).
    - **KRX 캘린더**: verified 범위 = 2024 단년. as_of 직전 ~35 영업일 가격을
      심어 trading_value_20d_avg (20 영업일) 도 산출 가능.

본 스크립트는 dev 전용 — 운영 데이터는 batch/ 의 일배치가 생산. 종목명 / 주석에
투자권유성 어휘 (추천 등) 를 쓰지 않는다 (ADR-0007).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, delete, event
from sqlalchemy.orm import Session

# `python scripts/seed_demo.py` 직접 실행 시 server/ 를 sys.path 에 추가 (app
# 패키지 import). `python -m scripts.seed_demo` 는 cwd 가 server/ 라 불필요하나
# 양쪽 실행 방식 모두 지원하기 위해 방어적으로 추가.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from app.db.base import Base  # noqa: E402

# `app.db.orm` 패키지 import 는 __init__ 가 모든 ORM 모델을 import 하는
# side-effect 로 Base.metadata 에 전 테이블을 등록 → create_all 이 FK 대상
# 포함 전 테이블 생성. 아래는 본 스크립트 코드가 직접 참조하는 ORM 만 명시 import.
from app.db.orm import (  # noqa: E402
    BatchRunORM,
    FinancialORM,
    MacroIndicatorORM,
    MarketCapDailyORM,
    PriceDailyORM,
    SourceCitationORM,
    StocksMasterORM,
    TreasurySharesORM,
)
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS  # noqa: E402
from app.db.session import create_engine_from_url, create_sessionmaker  # noqa: E402
from app.models.source_citation import SourceCitation, SourceKind  # noqa: E402
from app.repositories.pit_protocols import (  # noqa: E402
    CodeHistoryEntry,
    FinancialRecord,
    MacroIndicatorRecord,
    MarketCapRecord,
    PriceRecord,
    StockMasterRecord,
    TreasurySharesRecord,
)
from app.services.krx_calendar import DEFAULT_CALENDAR  # noqa: E402

__all__ = [
    "DEFAULT_DEV_DATABASE_URL",
    "DemoStock",
    "SEED_AS_OF",
    "build_seed_dataset",
    "run_seed",
    "seed_session",
]


# =============================================================================
# 상수 — as_of / DB URL / 어댑터 버전
# =============================================================================

# KRX 캘린더 verified 범위 = 2024 단년 (ADR-0008 D8). as_of 는 2024-06-28 (금).
SEED_AS_OF: date = date(2024, 6, 28)

# dev 기본 DB — env 미설정 시 cwd 상대 SQLite 파일. SQLite 는 ADR-0020 트리거를
# dialect 분기로 skip 하므로 dev 실행 가능 (PG-only append-only 트리거 미적용).
DEFAULT_DEV_DATABASE_URL: str = "sqlite:///./speculum_dev.db"

# seed 가 만드는 citation 의 adapter_version (semver). dev 데이터 식별용.
_KRX_ADAPTER_VERSION = "0.1.0"
_DART_ADAPTER_VERSION = "0.1.0"
_ECOS_ADAPTER_VERSION = "0.1.0"

# seed 데이터의 결정성을 위한 namespace UUID — 재실행 시 같은 종목이 같은 lineage
# id 를 갖도록 (멱등성 강화). batch_id / citation_id 는 매 실행 새 uuid4 (멱등성은
# 종목코드 기준 삭제로 보장하므로 id 안정성 불요).
_LINEAGE_NS = UUID("5e9ed000-0000-4000-8000-000000000001")


# =============================================================================
# 종목 정의 — 현실적 수치 (KOSPI 대형주). 종목명 = 공식 상호 (투자권유 어휘 없음).
# =============================================================================

class DemoStock:
    """seed 대상 한 종목의 입력 — 현실적 수치 묶음.

    재무 수치는 현실적 규모이되 dev 데모용 임의값 (정확성 보증 아님). PER/PBR/ROE/
    EPS 가 0 분모 없이 산출되도록 일관성만 유지. 종목명은 공식 상호.

    Attributes:
        code: 6 자리 종목코드.
        name: 공식 상호 (KOSPI 상장명).
        listing_date: 상장일 (as_of 이전 과거).
        close_won: as_of 직전 영업일 종가 (원).
        shares_outstanding: 발행주식수 (자사주 포함).
        shares_treasury: 보통주 자기주식수 (0 이상 정수). 모든 종목이 treasury_
            shares row 를 가져 market-cap:ex-treasury 가 실평가됨 (0 이면
            ex-treasury = 전체 시총). 종목별 자사주 규모는 차등 (0 ~ 수천만주).
        net_income_quarters: 2023Q1~Q4 지배주주귀속 당기순이익 (원, 분기별).
        equity_periods: (fiscal_period, 지배주주귀속 자본총계) 5 쌍 — 2022Q4 +
            2023Q1~Q4. period_begin (직전 분기) 산출용으로 2022Q4 포함.
        basic_eps_quarters: 2023Q1~Q4 기본주당순이익 (원, 분기별).
    """

    __slots__ = (
        "basic_eps_quarters",
        "close_won",
        "code",
        "equity_periods",
        "listing_date",
        "name",
        "net_income_quarters",
        "shares_outstanding",
        "shares_treasury",
    )

    def __init__(
        self,
        *,
        code: str,
        name: str,
        listing_date: date,
        close_won: int,
        shares_outstanding: int,
        shares_treasury: int,
        net_income_quarters: tuple[int, int, int, int],
        equity_periods: tuple[tuple[str, int], ...],
        basic_eps_quarters: tuple[int, int, int, int],
    ) -> None:
        self.code = code
        self.name = name
        self.listing_date = listing_date
        self.close_won = close_won
        self.shares_outstanding = shares_outstanding
        self.shares_treasury = shares_treasury
        self.net_income_quarters = net_income_quarters
        self.equity_periods = equity_periods
        self.basic_eps_quarters = basic_eps_quarters


# 단위 축약 — 가독성 (조 / 억).
_JO = 1_000_000_000_000  # 1 조
_EOK = 100_000_000  # 1 억


def _equity(q4_2022: int, q1: int, q2: int, q3: int, q4: int) -> tuple[tuple[str, int], ...]:
    """자본총계 5 분기 (2022Q4 + 2023Q1~Q4) — period_begin 산출용 2022Q4 포함."""
    return (
        ("2022Q4", q4_2022),
        ("2023Q1", q1),
        ("2023Q2", q2),
        ("2023Q3", q3),
        ("2023Q4", q4),
    )


# 6 종목 — KOSPI 대형주. 수치는 현실적 규모의 dev 임의값 (정확성 보증 아님).
_DEMO_STOCKS: tuple[DemoStock, ...] = (
    DemoStock(
        code="005930", name="삼성전자",
        listing_date=date(1975, 6, 11), close_won=81_000,
        shares_outstanding=5_969_782_550, shares_treasury=20_000_000,
        net_income_quarters=(15 * _JO, 17 * _JO, 16 * _JO, 18 * _JO),
        equity_periods=_equity(360 * _JO, 365 * _JO, 372 * _JO, 378 * _JO, 386 * _JO),
        basic_eps_quarters=(2_200, 2_500, 2_350, 2_650),
    ),
    DemoStock(
        code="000660", name="SK하이닉스",
        listing_date=date(1996, 12, 26), close_won=215_000,
        shares_outstanding=728_002_365, shares_treasury=5_000_000,
        net_income_quarters=(3 * _JO, 4 * _JO, 5 * _JO, 6 * _JO),
        equity_periods=_equity(60 * _JO, 63 * _JO, 67 * _JO, 72 * _JO, 78 * _JO),
        basic_eps_quarters=(4_100, 5_400, 6_800, 8_200),
    ),
    DemoStock(
        code="035420", name="NAVER",
        listing_date=date(2008, 11, 28), close_won=172_000,
        shares_outstanding=163_809_805, shares_treasury=14_000_000,
        net_income_quarters=(3_500 * _EOK, 3_800 * _EOK, 4_000 * _EOK, 4_200 * _EOK),
        equity_periods=_equity(
            22 * _JO, 22 * _JO + 5_000 * _EOK, 23 * _JO,
            23 * _JO + 6_000 * _EOK, 24 * _JO,
        ),
        basic_eps_quarters=(2_100, 2_300, 2_450, 2_550),
    ),
    DemoStock(
        code="005380", name="현대차",
        listing_date=date(1974, 6, 28), close_won=248_000,
        shares_outstanding=211_531_506, shares_treasury=8_000_000,
        net_income_quarters=(3 * _JO, 3 * _JO + 2_000 * _EOK, 3 * _JO + 5_000 * _EOK, 4 * _JO),
        equity_periods=_equity(78 * _JO, 81 * _JO, 84 * _JO, 88 * _JO, 92 * _JO),
        basic_eps_quarters=(13_000, 13_800, 15_500, 17_200),
    ),
    DemoStock(
        code="051910", name="LG화학",
        listing_date=date(2001, 4, 25), close_won=395_000,
        shares_outstanding=70_592_343, shares_treasury=3_000_000,
        net_income_quarters=(4_000 * _EOK, 4_500 * _EOK, 3_800 * _EOK, 4_200 * _EOK),
        equity_periods=_equity(34 * _JO, 35 * _JO, 36 * _JO, 36 * _JO + 5_000 * _EOK, 37 * _JO),
        basic_eps_quarters=(5_600, 6_300, 5_300, 5_900),
    ),
    DemoStock(
        code="035720", name="카카오",
        listing_date=date(2017, 7, 10), close_won=42_000,
        shares_outstanding=444_278_822, shares_treasury=6_000_000,
        net_income_quarters=(900 * _EOK, 1_100 * _EOK, 1_200 * _EOK, 1_300 * _EOK),
        equity_periods=_equity(
            11 * _JO, 11 * _JO + 3_000 * _EOK, 11 * _JO + 6_000 * _EOK,
            12 * _JO, 12 * _JO + 4_000 * _EOK,
        ),
        basic_eps_quarters=(210, 250, 270, 300),
    ),
)


# financials 의 DART account canonical key — db_field_provider 의 _RESOLUTIONS
# (account 매핑) 와 일치해야 factor 가 해소됨.
_NET_INCOME_ACCOUNT = "net_income_attributable_to_owners"
_EQUITY_ACCOUNT = "equity_attributable_to_owners"
_BASIC_EPS_ACCOUNT = "basic_eps"
_CONSOLIDATED = "consolidated"

# 분기별 보고서 접수일 (effective_date) — 자본시장법 신고기한 내 현실적 공시일.
# 모두 as_of (2024-06-28) 이전 → PIT 만족. fiscal_period → 접수일 (보고서 발효).
_FISCAL_EFFECTIVE: dict[str, date] = {
    "2022Q4": date(2023, 3, 30),  # 2022 사업보고서
    "2023Q1": date(2023, 5, 15),  # 2023 1분기보고서
    "2023Q2": date(2023, 8, 14),  # 2023 반기보고서
    "2023Q3": date(2023, 11, 14),  # 2023 3분기보고서
    "2023Q4": date(2024, 3, 29),  # 2023 사업보고서
}

# 자사주 (treasury_shares) fiscal_period 와 접수일 — 2024Q1 (as_of 이전).
_TREASURY_FISCAL_PERIOD = "2024Q1"
_TREASURY_EFFECTIVE = date(2024, 5, 16)  # 2024 1분기보고서 접수일 (as_of 이전)


# =============================================================================
# 데이터셋 빌더 — Record dataclass 묶음 (테스트가 재사용)
# =============================================================================

class SeedDataset:
    """seed 가 삽입할 모든 Record 의 묶음 — INSERT 순서 보존.

    citation → fact FK 순서를 호출자가 알 수 있도록 batch / citation 을 fact 와
    분리해 노출. 테스트가 본 dataset 을 임시 SQLite 에 적용해 factor 산출을 검증.
    """

    __slots__ = (
        "batch_runs",
        "citations",
        "codes",
        "financials",
        "macro_indicators",
        "market_caps",
        "prices",
        "stocks",
        "treasury",
    )

    def __init__(
        self,
        *,
        batch_runs: list[BatchRunORM],
        citations: list[SourceCitation],
        stocks: list[StockMasterRecord],
        prices: list[PriceRecord],
        market_caps: list[MarketCapRecord],
        financials: list[FinancialRecord],
        treasury: list[TreasurySharesRecord],
        macro_indicators: list[MacroIndicatorRecord],
        codes: list[str],
    ) -> None:
        self.batch_runs = batch_runs
        self.citations = citations
        self.stocks = stocks
        self.prices = prices
        self.market_caps = market_caps
        self.financials = financials
        self.treasury = treasury
        self.macro_indicators = macro_indicators
        self.codes = codes


def _business_days_window(as_of: date, *, count: int) -> list[date]:
    """as_of 이하 최근 영업일부터 역방향 `count` 영업일 (오름차순).

    KRX 캘린더 (verified 2024) 기준. trading_value_20d_avg (20 영업일) 산출을
    위해 count >= 20 권장.
    """
    cursor = DEFAULT_CALENDAR.latest_business_day(as_of)
    days = [cursor]
    for _ in range(count - 1):
        cursor = DEFAULT_CALENDAR.previous_business_day(cursor)
        days.append(cursor)
    days.sort()
    return days


def build_seed_dataset(as_of: date = SEED_AS_OF) -> SeedDataset:
    """seed 대상 Record 전체 구성 — DB 무관 순수 함수 (테스트 재사용).

    KRX 성공 batch + DART 성공 batch 각 1 개를 만들고, 그 batch_id 를 참조하는
    citation (KRX / DART) 을 만든 뒤, 각 종목의 가격 / 시총 / 자사주 / 재무 Record
    를 구성한다. 모든 fact 의 effective_date <= as_of (PIT).

    Args:
        as_of: PIT 기준일 (default 2024-06-28). 모든 fact 가 이 시점 이하.

    Returns:
        SeedDataset — batch / citation / stocks / fact Record 묶음.
    """
    now = datetime(2024, 6, 28, 9, 0, tzinfo=UTC)
    # KRX / DART 일배치 (success) — started_at 은 as_of 근방. citation.batch_id
    # 가 본 batch.id 를 참조 (재현성 SoT). source 는 'KRX' / 'DART'.
    krx_batch_id = uuid4()
    dart_batch_id = uuid4()
    krx_started = datetime(2024, 6, 28, 18, 0, tzinfo=UTC)
    dart_started = datetime(2024, 6, 28, 19, 0, tzinfo=UTC)
    batch_runs = [
        BatchRunORM(
            id=krx_batch_id, market="KOSPI", source="KRX",
            started_at=krx_started, ended_at=krx_started,
            success_count=len(_DEMO_STOCKS), status=BATCH_STATUS_SUCCESS,
        ),
        BatchRunORM(
            id=dart_batch_id, market=None, source="DART",
            started_at=dart_started, ended_at=dart_started,
            success_count=len(_DEMO_STOCKS), status=BATCH_STATUS_SUCCESS,
        ),
    ]

    # citation — KRX (가격 / 시총) 1 개, DART (재무 / 자사주) 1 개. 모든 fact 가
    # 이 둘 중 하나를 citation_id FK 로 참조 (Fidelity §2.1, ADR-0002 D3).
    krx_citation = SourceCitation(
        id=uuid4(), source=SourceKind.KRX,
        identifier="KRX|20240628|KOSPI", retrieved_at=now,
        effective_date=as_of, adapter_version=_KRX_ADAPTER_VERSION,
        batch_id=krx_batch_id,
        url="https://data.krx.co.kr/", created_at=now,
    )
    dart_citation = SourceCitation(
        id=uuid4(), source=SourceKind.DART,
        identifier="DART|demo-seed|2023-2024", retrieved_at=now,
        effective_date=as_of, adapter_version=_DART_ADAPTER_VERSION,
        batch_id=dart_batch_id,
        url="https://opendart.fss.or.kr/", created_at=now,
    )
    # ECOS 거시지표 citation — batch_id 는 DART 배치 재사용 (dev seed 단순화).
    # 운영에서는 별도 ECOS batch_run 이 생성. seed 멱등 삭제는 identifier 로 한정.
    ecos_citation = SourceCitation(
        id=uuid4(), source=SourceKind.ECOS,
        identifier="ECOS|demo-seed|macro-2024", retrieved_at=now,
        effective_date=as_of, adapter_version=_ECOS_ADAPTER_VERSION,
        batch_id=dart_batch_id,
        url="https://ecos.bok.or.kr/", created_at=now,
    )
    citations = [krx_citation, dart_citation, ecos_citation]

    business_days = _business_days_window(as_of, count=35)

    stocks: list[StockMasterRecord] = []
    prices: list[PriceRecord] = []
    market_caps: list[MarketCapRecord] = []
    financials: list[FinancialRecord] = []
    treasury: list[TreasurySharesRecord] = []
    codes: list[str] = []

    # ECOS 매크로 지표 — 표시용만 (factor 입력 금지, ADR-0007 D5).
    # 잠정→확정 vintage 2 row 를 한 reference_date 에 넣어 PIT 시연.
    macro_indicators = _build_macro_indicators(ecos_citation.id, now)

    for stock in _DEMO_STOCKS:
        codes.append(stock.code)
        # lineage id 는 종목코드 기준 결정적 (재실행 시 안정).
        lineage_id = uuid5(_LINEAGE_NS, stock.code)

        # --- stocks_master — lineage entity + 단일 code_history entry ---
        stocks.append(StockMasterRecord(
            id=lineage_id,
            current_code=stock.code,
            current_name=stock.name,
            market="KOSPI",
            listing_date=stock.listing_date,
            delisting_date=None,
            fiscal_month=12,
            code_history=(
                CodeHistoryEntry(
                    code=stock.code, valid_from=stock.listing_date,
                    valid_to=None, reason="initial_listing",
                ),
            ),
            ifrs_preference_default="AUTO",
            # ADR-0023 D2 — 데모 6 종목은 전원 KOSPI 보통주.
            security_type="common",
        ))

        # --- prices_daily — 35 영업일 OHLCV + trading_value + close_adjusted ---
        prices.extend(_build_prices(
            stock, lineage_id, business_days, krx_citation.id, now,
        ))

        # --- market_caps — as_of 시총 + 발행주식수 ---
        # 시가총액 = 발행주식수 × 종가 (KRX 공식 정의와 일관). shares_treasury 는
        # market_caps 에는 항상 None (pykrx 미제공 가정) — 자사주는 treasury_shares
        # table 에서 DbFieldProvider 가 별도 해소.
        market_cap = Decimal(stock.shares_outstanding) * Decimal(stock.close_won)
        market_caps.append(MarketCapRecord(
            id=uuid4(),
            code=stock.code,
            code_lineage_id=lineage_id,
            effective_date=as_of,
            market_cap=market_cap,
            shares_outstanding=stock.shares_outstanding,
            shares_treasury=None,
            citation_id=krx_citation.id,
            created_at=now,
        ))

        # --- financials — net_income (4Q) / equity (5Q) / basic_eps (4Q) ---
        financials.extend(_build_financials(
            stock, lineage_id, dart_citation.id, now,
        ))

        # --- treasury_shares — 모든 종목 (effective_date_precise) ---
        # 모든 종목이 자사주 row 를 가져 market-cap:ex-treasury 가 실평가됨
        # (자사주 0 이면 ex-treasury = 전체 시총). 종목별 자사주 규모는 차등.
        if stock.shares_treasury >= 0:
            treasury.append(TreasurySharesRecord(
                id=uuid4(),
                code=stock.code,
                code_lineage_id=lineage_id,
                effective_date=_TREASURY_EFFECTIVE,
                fiscal_period=_TREASURY_FISCAL_PERIOD,
                shares_treasury=stock.shares_treasury,
                citation_id=dart_citation.id,
                superseded_by=None,
                created_at=now,
                effective_date_precise=True,
            ))

    return SeedDataset(
        batch_runs=batch_runs,
        citations=citations,
        stocks=stocks,
        prices=prices,
        market_caps=market_caps,
        financials=financials,
        treasury=treasury,
        macro_indicators=macro_indicators,
        codes=codes,
    )


def _build_prices(
    stock: DemoStock,
    lineage_id: UUID,
    business_days: list[date],
    citation_id: UUID,
    now: datetime,
) -> list[PriceRecord]:
    """종목의 영업일별 OHLCV — 종가에서 소폭 변동을 준 현실적 시계열.

    마지막 영업일 (= as_of) 종가가 stock.close_won 과 일치하도록 역방향으로 소폭
    선형 변동. corporate action 없음 → close_adjusted = close_raw. trading_value
    는 발행주식수 일부가 회전한 현실적 규모.
    """
    records: list[PriceRecord] = []
    n = len(business_days)
    for idx, d in enumerate(business_days):
        # 가장 오래된 날을 -3% 에서 시작해 마지막 날 종가에 수렴 (단조 아닌 소폭
        # 변동 — 짝수 인덱스에 +0.4% 가산). dev 데모용 임의 시계열.
        ramp = (idx - (n - 1)) * 3 / max(n - 1, 1)  # -3 ~ 0 (%)
        wobble = 0.4 if idx % 2 == 0 else -0.2
        factor = Decimal(str(1 + (ramp + wobble) / 100))
        close = (Decimal(stock.close_won) * factor).quantize(Decimal("1"))
        if d == business_days[-1]:
            # 마지막 영업일 = as_of — 정확히 목표 종가 (factor 카드 일관성).
            close = Decimal(stock.close_won)
        open_p = (close * Decimal("0.995")).quantize(Decimal("1"))
        high_p = (close * Decimal("1.012")).quantize(Decimal("1"))
        low_p = (close * Decimal("0.988")).quantize(Decimal("1"))
        # 거래대금 = 발행주식수의 ~0.3% 회전 × 종가 (현실적 일거래대금 규모).
        turnover_shares = stock.shares_outstanding * 3 // 1000
        trading_value = (Decimal(turnover_shares) * close).quantize(Decimal("1"))
        records.append(PriceRecord(
            id=uuid4(),
            code=stock.code,
            code_lineage_id=lineage_id,
            effective_date=d,
            open_raw=open_p,
            high_raw=high_p,
            low_raw=low_p,
            close_raw=close,
            volume=turnover_shares,
            trading_value=trading_value,
            close_adjusted=close,  # corporate action 없음 → raw = adjusted.
            citation_id=citation_id,
            created_at=now,
        ))
    return records


def _build_financials(
    stock: DemoStock,
    lineage_id: UUID,
    citation_id: UUID,
    now: datetime,
) -> list[FinancialRecord]:
    """종목의 재무 Record — net_income (4Q) / equity (5Q) / basic_eps (4Q).

    모두 consolidated (연결). effective_date 는 보고서 접수일 (as_of 이전),
    effective_date_precise=True. 정정공시 없음 → superseded_by=None.
    """
    records: list[FinancialRecord] = []

    def fin(*, account: str, fiscal_period: str, value: int, unit: str) -> FinancialRecord:
        return FinancialRecord(
            id=uuid4(),
            code=stock.code,
            code_lineage_id=lineage_id,
            effective_date=_FISCAL_EFFECTIVE[fiscal_period],
            fiscal_period=fiscal_period,
            account=account,
            value=Decimal(value),
            unit=unit,
            ifrs_type=_CONSOLIDATED,
            citation_id=citation_id,
            superseded_by=None,
            created_at=now,
            effective_date_precise=True,
        )

    quarters = ("2023Q1", "2023Q2", "2023Q3", "2023Q4")
    # net_income — 4 분기 (TTM 합 산출용).
    for fp, ni in zip(quarters, stock.net_income_quarters, strict=True):
        records.append(fin(account=_NET_INCOME_ACCOUNT, fiscal_period=fp,
                           value=ni, unit="krw"))
    # equity — 5 분기 (2022Q4 + 2023Q1~Q4). period_begin (직전 분기) 산출용.
    for fp, eq in stock.equity_periods:
        records.append(fin(account=_EQUITY_ACCOUNT, fiscal_period=fp,
                           value=eq, unit="krw"))
    # basic_eps — 4 분기 (TTM 합 = 연간 EPS).
    for fp, eps in zip(quarters, stock.basic_eps_quarters, strict=True):
        records.append(fin(account=_BASIC_EPS_ACCOUNT, fiscal_period=fp,
                           value=eps, unit="krw"))
    return records


def _build_macro_indicators(
    citation_id: UUID,
    now: datetime,
) -> list[MacroIndicatorRecord]:
    """ECOS 대표 매크로 지표 seed — 기준금리 + CPI (표시용만, factor 입력 금지).

    잠정→확정 vintage 2 row 를 같은 reference_date 에 삽입해 PIT 시연:
        - 기준금리 2024-01-01 기준: 잠정 vintage 2024-01-15 (3.50%),
          확정 vintage 2024-03-01 (3.50%). 같은 값이지만 vintage 2 row 로
          as_of ∈ [2024-01-15, 2024-03-01) → 잠정 반환,
          as_of >= 2024-03-01 → 확정 반환 재현.
        - 기준금리 최근: 2024-05-01 기준, vintage 2024-05-15 (3.50%).
        - CPI 2024-04-01 기준, vintage 2024-05-02 (113.56, index 단위).

    vintage_date 는 우리가 ECOS 에서 관측한 시점의 근사 (정확한 공표일 아님 — T63).
    모든 record 의 reference_date + vintage_date <= SEED_AS_OF (2024-06-28) — PIT.
    """
    records: list[MacroIndicatorRecord] = []

    def macro(
        indicator_id: str,
        reference_date: date,
        value: str,
        unit: str,
        vintage_date: date,
    ) -> MacroIndicatorRecord:
        return MacroIndicatorRecord(
            id=uuid4(),
            indicator_id=indicator_id,
            reference_date=reference_date,
            value=Decimal(value),
            unit=unit,
            vintage_date=vintage_date,
            citation_id=citation_id,
            created_at=now,
        )

    # 기준금리 (722Y001/0101000) — 월별 (reference_date = 해당 월 1 일).
    # 2024-01 기준: 잠정(vintage 2024-01-15) + 확정(vintage 2024-03-01) — PIT 시연.
    records.append(macro(
        "722Y001/0101000", date(2024, 1, 1), "3.50", "percent",
        date(2024, 1, 15),
    ))
    records.append(macro(
        "722Y001/0101000", date(2024, 1, 1), "3.50", "percent",
        date(2024, 3, 1),
    ))
    # 2024-02 ~ 2024-05 기준금리 (단일 vintage, as_of 이전).
    for ref_month, vintage_day in [
        (date(2024, 2, 1), date(2024, 2, 15)),
        (date(2024, 3, 1), date(2024, 3, 15)),
        (date(2024, 4, 1), date(2024, 4, 15)),
        (date(2024, 5, 1), date(2024, 5, 15)),
    ]:
        records.append(macro(
            "722Y001/0101000", ref_month, "3.50", "percent", vintage_day,
        ))

    # 소비자물가지수 (901Y009/0) — 월별 (reference_date = 해당 월 1 일).
    # 2024-04 기준 (as_of 이전 최신 공표).
    records.append(macro(
        "901Y009/0", date(2024, 4, 1), "113.56", "index",
        date(2024, 5, 2),
    ))

    return records


# =============================================================================
# DB 적용 — 멱등 삭제 후 INSERT
# =============================================================================

def _enable_sqlite_fk(engine: Engine) -> None:
    """SQLite 는 PRAGMA foreign_keys 가 connection 단위 default OFF — ON 강제.

    운영 PG 와 동일 FK 동작 보장 (citation → fact FK 무결성). PG / 기타 dialect
    는 무해 (event listener 가 SQLite 만 대상).
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_fk(dbapi_connection, _conn_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _purge_existing(session: Session, codes: list[str]) -> None:
    """멱등성 — 같은 종목코드의 기존 fact / citation / batch / 마스터 삭제.

    FK 역순 삭제 (fact → citation → batch_run, stocks_master). 본 스크립트가 만든
    데이터만 정리 — 운영 데이터와 충돌하지 않도록 종목코드 / seed citation
    identifier / seed batch (KOSPI KRX + DART success) 기준으로 한정.

    삭제 순서:
        1. fact (prices / market_caps / financials / treasury) — 종목코드 기준.
        2. source_citations — seed identifier prefix 기준.
        3. batch_runs — seed batch_id 기준 (citation 삭제 후, FK 안전).
        4. stocks_master — 종목코드 (current_code) 기준.
    """
    # 1. fact 삭제 (종목코드 기준 + 매크로 지표는 indicator_id 기준).
    for orm in (PriceDailyORM, MarketCapDailyORM, FinancialORM, TreasurySharesORM):
        session.execute(delete(orm).where(orm.code.in_(codes)))
    # 매크로 지표 — 종목코드 없음, seed indicator_id 기준으로 삭제.
    _SEED_MACRO_INDICATOR_IDS = ["722Y001/0101000", "901Y009/0"]
    session.execute(
        delete(MacroIndicatorORM).where(
            MacroIndicatorORM.indicator_id.in_(_SEED_MACRO_INDICATOR_IDS),
        )
    )

    # 2. seed citation 삭제 — identifier prefix 로 본 스크립트 데이터만 한정.
    #    (운영 citation 과 충돌 방지.) batch_id 를 먼저 수집해 batch_runs 정리.
    _SEED_CITATION_IDENTIFIERS = [
        "KRX|20240628|KOSPI",
        "DART|demo-seed|2023-2024",
        "ECOS|demo-seed|macro-2024",
    ]
    seed_citation_orms = session.execute(
        SourceCitationORM.__table__.select().where(
            SourceCitationORM.identifier.in_(_SEED_CITATION_IDENTIFIERS)
        )
    ).all()
    seed_batch_ids = {row.batch_id for row in seed_citation_orms}
    session.execute(
        delete(SourceCitationORM).where(
            SourceCitationORM.identifier.in_(_SEED_CITATION_IDENTIFIERS)
        )
    )

    # 3. batch_runs 삭제 (citation 삭제 후 — FK 안전).
    if seed_batch_ids:
        session.execute(
            delete(BatchRunORM).where(BatchRunORM.id.in_(seed_batch_ids))
        )

    # 4. stocks_master 삭제 (종목코드 기준).
    session.execute(
        delete(StocksMasterORM).where(StocksMasterORM.current_code.in_(codes))
    )
    session.flush()


def seed_session(session: Session, dataset: SeedDataset) -> None:
    """주어진 session 에 dataset 을 멱등 적용 — 기존 seed 삭제 후 INSERT.

    INSERT 순서 (FK 의무):
        1. batch_runs — source_citations.batch_id FK 의 참조 대상 (먼저).
        2. source_citations — fact.citation_id FK 의 참조 대상.
        3. stocks_master — lineage entity (fact 와 FK 무관하나 도메인 순서).
        4. fact (prices / market_caps / financials / treasury).

    본 함수는 commit 하지 않음 — 호출자 (run_seed 또는 테스트) 가 commit / rollback
    결정. 테스트는 in-memory SQLite 에서 commit 없이 평가 가능.
    """
    from app.db.converters import (
        citation_record_to_orm,
        financial_record_to_orm,
        macro_indicator_record_to_orm,
        market_cap_record_to_orm,
        price_record_to_orm,
        stocks_master_record_to_orm,
        treasury_shares_record_to_orm,
    )

    _purge_existing(session, dataset.codes)

    # 1. batch_runs (citation FK 참조 대상 — 먼저).
    for batch in dataset.batch_runs:
        session.add(batch)
    session.flush()

    # 2. source_citations (fact FK 참조 대상).
    for citation in dataset.citations:
        session.add(citation_record_to_orm(citation))
    session.flush()

    # 3. stocks_master.
    for stock in dataset.stocks:
        session.add(stocks_master_record_to_orm(stock))

    # 4. fact — citation 이후라 FK 만족.
    for price in dataset.prices:
        session.add(price_record_to_orm(price))
    for mc in dataset.market_caps:
        session.add(market_cap_record_to_orm(mc))
    for fin_rec in dataset.financials:
        session.add(financial_record_to_orm(fin_rec))
    for tr in dataset.treasury:
        session.add(treasury_shares_record_to_orm(tr))
    # 매크로 지표 — ECOS citation 이후라 FK 만족. 표시용만 (factor 입력 금지).
    for mi in dataset.macro_indicators:
        session.add(macro_indicator_record_to_orm(mi))
    session.flush()


def run_seed(database_url: str | None = None, *, as_of: date = SEED_AS_OF) -> str:
    """env / 인자에서 DB URL 해소 → 스키마 생성 → dataset seed → commit.

    Args:
        database_url: 명시 DB URL. None 이면 `SPECULUM_DATABASE_URL` env →
            그것도 없으면 `DEFAULT_DEV_DATABASE_URL` (sqlite:///./speculum_dev.db).
        as_of: seed PIT 기준일 (default 2024-06-28).

    Returns:
        실제 사용한 DB URL (안내 print 용).
    """
    url = (database_url or os.environ.get("SPECULUM_DATABASE_URL", "").strip()
           or DEFAULT_DEV_DATABASE_URL)

    engine = create_engine_from_url(url)
    _enable_sqlite_fk(engine)
    # 스키마 — SQLite 는 ADR-0020 PG-only 트리거를 dialect 분기로 skip 하므로
    # create_all 로 충분. 이미 있으면 no-op (멱등).
    Base.metadata.create_all(engine)

    sessionmaker_ = create_sessionmaker(engine)
    dataset = build_seed_dataset(as_of)
    with sessionmaker_() as session:
        seed_session(session, dataset)
        session.commit()
    engine.dispose()
    return url


# =============================================================================
# 실행 안내 print
# =============================================================================

def _print_instructions(url: str, dataset: SeedDataset, as_of: date) -> None:
    """seed 완료 후 실행 안내 — DB URL + uvicorn + pnpm dev + 예시 URL."""
    sample_code = dataset.codes[0] if dataset.codes else "005930"
    iso = as_of.isoformat()
    lines = [
        "",
        "=" * 70,
        " Speculum dev 데모 seed 완료",
        "=" * 70,
        f" DB URL          : {url}",
        f" 종목 수         : {len(dataset.stocks)} ({', '.join(dataset.codes)})",
        f" 가격 row        : {len(dataset.prices)} (종목별 ~35 영업일)",
        f" 재무 row        : {len(dataset.financials)}",
        f" 시총 row        : {len(dataset.market_caps)}",
        f" 자사주 row      : {len(dataset.treasury)}",
        f" 매크로 row      : {len(dataset.macro_indicators)} (ECOS 기준금리+CPI, 표시용)",
        f" as_of (PIT)     : {iso}",
        "",
        " 1) 백엔드 (server/ 에서):",
        f"      SPECULUM_DATABASE_URL='{url}' uvicorn app.main:app --reload --port 8000",
        "",
        " 2) 프론트 (client/ 에서):",
        "      NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm dev",
        "",
        " 3) 브라우저: http://localhost:3000  (as_of 를 " + iso + " 로 지정)",
        "",
        " API 예시:",
        f"      http://localhost:8000/api/stocks/{sample_code}?as_of={iso}",
        "      POST http://localhost:8000/api/screen",
        '        {"as_of":"' + iso + '",'
        ' "conditions":[{"factor":"per:ttm-consolidated-ifrs","op":"<","value":"100"}]}',
        "=" * 70,
        "",
    ]
    print("\n".join(lines))


def main() -> None:
    """엔트리포인트 — env / 기본 URL 로 seed 실행 후 안내 print."""
    url = run_seed()
    dataset = build_seed_dataset()
    _print_instructions(url, dataset, SEED_AS_OF)


if __name__ == "__main__":
    main()
