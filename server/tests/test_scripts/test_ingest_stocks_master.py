"""scripts.ingest_stocks_master 단위 테스트 — FDR 종목 마스터 → stocks_master.

fake FdrAdapter(실 종목명 모사) → main() 이 tmp 파일 SQLite 에 StockMasterRecord
UPSERT. 네트워크 무관(FdrAdapter monkeypatch). 실 종목 상세 404 방지 경로의 회귀 가드.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.adapters.base import FetchResult, StockMaster
from app.db.base import Base
from app.db.orm import StocksMasterORM  # noqa: F401  (metadata 등록)
from app.services.lineage import lineage_id_for_code
from scripts import ingest_stocks_master as mod


class _FakeFdr:
    """fetch_stock_master 만 구현 — by_code 의 StockMaster 반환."""

    def __init__(self, by_code: dict[str, StockMaster]) -> None:
        self._by_code = by_code

    def fetch_stock_master(self, code, *, as_of, batch_id):
        from app.adapters.base import AdapterError
        master = self._by_code.get(code)
        if master is None:
            raise AdapterError(f"code={code} not found in FDR listing")
        return FetchResult(
            data=master, citations=(), warnings=(), estimated_fields=frozenset(),
        )


def _master(code: str, name: str, listing: date | None = date(2010, 1, 1)) -> StockMaster:
    return StockMaster(
        code=code, name=name, market="KOSPI",
        listing_date=listing, delisting_date=None, sector_krx="전기전자",
    )


@pytest.fixture
def _db_url(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """tmp 파일 SQLite(schema) + SPECULUM_DATABASE_URL 주입 (file DB — dispose 후 보존)."""
    db_path = tmp_path / "test_ingest.db"
    url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setenv("SPECULUM_DATABASE_URL", url)
    return url


def _stocks(url: str):
    engine = create_engine(url, future=True)
    try:
        with sessionmaker(bind=engine, future=True)() as s:
            rows = s.execute(
                select(
                    StocksMasterORM.current_code,
                    StocksMasterORM.current_name,
                    StocksMasterORM.market,
                ).order_by(StocksMasterORM.current_code)
            ).all()
            return [(r[0], r[1], r[2]) for r in rows]
    finally:
        engine.dispose()


def test_main_ingests_real_names(
    _db_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fake FDR 실 종목명 → stocks_master 적재. lineage id = 종목코드 결정적."""
    fake = _FakeFdr({
        "005930": _master("005930", "삼성전자"),
        "035420": _master("035420", "NAVER"),
    })
    monkeypatch.setattr(mod, "FdrAdapter", lambda: fake)

    rc = mod.main(["005930", "035420"])
    assert rc == 0
    assert _stocks(_db_url) == [
        ("005930", "삼성전자", "KOSPI"),
        ("035420", "NAVER", "KOSPI"),
    ]
    # lineage id 가 prices/financials 와 동일 공식.
    engine = create_engine(_db_url, future=True)
    try:
        with sessionmaker(bind=engine, future=True)() as s:
            row_id = s.scalar(
                select(StocksMasterORM.id).where(
                    StocksMasterORM.current_code == "005930",
                )
            )
            assert row_id == lineage_id_for_code("005930")
    finally:
        engine.dispose()


def test_main_skips_unknown_code(
    _db_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FDR 미발견 종목 skip + 적재 종목은 정상(전체 abort X)."""
    fake = _FakeFdr({"005930": _master("005930", "삼성전자")})
    monkeypatch.setattr(mod, "FdrAdapter", lambda: fake)

    rc = mod.main(["005930", "999999"])  # 999999 미발견.
    assert rc == 0  # 1종목 성공 → 0.
    assert _stocks(_db_url) == [("005930", "삼성전자", "KOSPI")]


def test_main_idempotent_upsert(
    _db_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재실행 — lineage id PK merge 멱등(중복 row 0, 이름 갱신)."""
    monkeypatch.setattr(
        mod, "FdrAdapter", lambda: _FakeFdr({"005930": _master("005930", "삼성전자")}),
    )
    mod.main(["005930"])
    # 종목명 변경 후 재실행 — merge 로 갱신(중복 아님).
    monkeypatch.setattr(
        mod, "FdrAdapter", lambda: _FakeFdr({"005930": _master("005930", "삼성전자우")}),
    )
    mod.main(["005930"])

    engine = create_engine(_db_url, future=True)
    try:
        with sessionmaker(bind=engine, future=True)() as s:
            n = s.scalar(select(func.count()).select_from(StocksMasterORM))
            assert n == 1  # 2건 아님.
            name = s.scalar(
                select(StocksMasterORM.current_name).where(
                    StocksMasterORM.current_code == "005930",
                )
            )
            assert name == "삼성전자우"  # merge 로 갱신.
    finally:
        engine.dispose()


def test_main_listing_date_fallback(
    _db_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FDR listing_date None → fallback 으로 채워 적재(date 필수 필드 안정)."""
    fake = _FakeFdr({"005930": _master("005930", "삼성전자", listing=None)})
    monkeypatch.setattr(mod, "FdrAdapter", lambda: fake)
    assert mod.main(["005930"]) == 0

    engine = create_engine(_db_url, future=True)
    try:
        with sessionmaker(bind=engine, future=True)() as s:
            ld = s.scalar(
                select(StocksMasterORM.listing_date).where(
                    StocksMasterORM.current_code == "005930",
                )
            )
            assert ld == mod._LISTING_DATE_FALLBACK
    finally:
        engine.dispose()
