"""stocks_master 실데이터 적재 — FDR StockListing(실 종목명/시장/상장일) 기반.

KRX/DART 일배치는 prices/financials 등 **fact** 만 적재하고 stocks_master(종목
마스터 = lineage entity)는 채우지 않는다. 그 결과 실데이터 적재 후
`/api/stocks/{code}`(종목 상세·검색·factor)가 lineage 부재로 404 가 된다.
본 스크립트가 그 갭을 메운다:

    FdrAdapter.fetch_stock_master(code) → 실 Name/Market/ListingDate →
    StockMasterRecord(lineage_id=종목코드 결정적) → stocks_master UPSERT.

lineage_id 는 `lineage_id_for_code`(배치/seed 와 동일 공식)라 prices/financials 의
code_lineage_id 와 자동 정합. FDR 은 키 불필요(StockListing). 종목코드 변경 history
는 단일 entry(initial_listing) — 실 code_history 추적은 별도(데모 동등).

실행:
    cd server
    set SPECULUM_DATABASE_URL=sqlite:///./speculum_real.db
    python -m scripts.ingest_stocks_master                 # prices_daily 적재 종목 전체
    python -m scripts.ingest_stocks_master 005930 000660   # 지정 종목만
    python -m scripts.ingest_stocks_master --full          # 전 상장종목(KOSPI+KOSDAQ)

설계:
- **UPSERT(session.merge)** — 재실행 멱등(같은 lineage id overwrite).
- **per-code 격리** — FDR 미발견/실패는 skip + 카운트(전체 abort X).
- **fiscal_month=12 / security_type="common" 기본** — FDR 은 결산월/증권유형 미제공.
  대부분 12월결산 보통주(financial holding/우선주는 후속 정밀화). listing_date
  부재 시 보수 fallback(상장일 미상 — code_history valid_from 안정성).
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from uuid import uuid4

from sqlalchemy import select

from app.adapters.base import AdapterError
from app.adapters.fdr_adapter import FdrAdapter
from app.core.config import get_database_url
from app.db.converters import stocks_master_record_to_orm
from app.db.orm import PriceDailyORM, StocksMasterORM  # noqa: F401  (metadata 등록)
from app.db.session import create_engine_from_url, create_sessionmaker
from app.repositories.pit_protocols import CodeHistoryEntry, StockMasterRecord
from app.services.lineage import lineage_id_for_code

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ingest_stocks_master")

# listing_date 미상 시 fallback — code_history valid_from 은 date 필수이므로,
# FDR 이 상장일을 안 주면 보수적 과거값(실제 상장일은 더 이후)으로 채운다.
_LISTING_DATE_FALLBACK = date(1980, 1, 1)


def _distinct_price_codes(session) -> list[str]:
    """prices_daily 에 적재된 distinct 종목코드 — 기본 대상."""
    rows = session.execute(select(PriceDailyORM.code).distinct()).all()
    return sorted({r[0] for r in rows})


def _full_universe_rows() -> list[tuple[str, str, str]]:
    """FDR StockListing(KOSPI+KOSDAQ) — 전체 KRX 유니버스 (code, name, market).

    keyless(FDR). 한 시장당 1 호출로 전 종목(code/name)을 받아 per-code FDR fetch
    (수천 회) 를 회피한다. listing_date 는 미제공이라 `--full` 경로는 보수 fallback.
    비숫자/6자리 아님/빈 이름 row 는 skip(ETF/지수/SPAC 일부 비정형 방어).
    """
    import FinanceDataReader as fdr

    rows: list[tuple[str, str, str]] = []
    for market in ("KOSPI", "KOSDAQ"):
        listing = fdr.StockListing(market)
        for _, r in listing.iterrows():
            code = str(r.get("Code") or "").strip()
            name = str(r.get("Name") or "").strip()
            if not name or not code.isdigit() or len(code) != 6:
                continue
            rows.append((code, name, market))
    return rows


def _build_master_record(
    *, code: str, name: str, market: str, listing_date: date,
) -> StockMasterRecord:
    """공통 마스터 레코드 빌드 — per-code 경로와 `--full` 경로 단일 출처."""
    return StockMasterRecord(
        id=lineage_id_for_code(code),
        current_code=code,
        current_name=name,
        market=market,
        listing_date=listing_date,
        delisting_date=None,
        fiscal_month=12,
        code_history=(
            CodeHistoryEntry(
                code=code,
                valid_from=listing_date,
                valid_to=None,
                reason="initial_listing",
            ),
        ),
        ifrs_preference_default="AUTO",
        security_type="common",
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    url = get_database_url()
    if not url:
        logger.error("SPECULUM_DATABASE_URL 미설정 — 실 DB 가 필요합니다.")
        return 1

    engine = create_engine_from_url(url)
    maker = create_sessionmaker(engine)
    adapter = FdrAdapter()
    as_of = date.today()
    batch_id = uuid4()

    saved = 0
    skipped: list[str] = []
    try:
        with maker() as session:
            # --full: FDR StockListing(KOSPI+KOSDAQ) 전체 유니버스 직적재(keyless,
            # per-code fetch 없음). 스크리너 모집단을 10 → 전 상장종목으로 확장.
            if "--full" in args:
                rows = _full_universe_rows()
                logger.info(
                    "전체 유니버스 적재 — %d 종목 (FDR StockListing, keyless)",
                    len(rows),
                )
                for code, name, market in rows:
                    record = _build_master_record(
                        code=code,
                        name=name,
                        market=market,
                        listing_date=_LISTING_DATE_FALLBACK,
                    )
                    session.merge(stocks_master_record_to_orm(record))
                    saved += 1
                session.commit()
                logger.info("전체 유니버스 적재 완료 — saved=%d", saved)
                return 0 if saved > 0 else 1

            codes = args or _distinct_price_codes(session)
            if not codes:
                logger.warning(
                    "대상 종목 없음 — 인자로 종목코드를 주거나 먼저 가격을 적재하세요.",
                )
                return 1
            logger.info("stocks_master 적재 대상 %d 종목", len(codes))

            for code in codes:
                try:
                    result = adapter.fetch_stock_master(
                        code, as_of=as_of, batch_id=batch_id,
                    )
                except AdapterError as exc:
                    skipped.append(code)
                    logger.warning("FDR 마스터 미발견 skip code=%s: %s", code, exc)
                    continue

                master = result.data
                listing = master.listing_date or _LISTING_DATE_FALLBACK
                record = _build_master_record(
                    code=code,
                    name=master.name,
                    market=master.market,
                    listing_date=listing,
                )
                # UPSERT — lineage id(PK) 기준 merge. 재실행 멱등.
                session.merge(stocks_master_record_to_orm(record))
                saved += 1
                logger.info(
                    "  %s %s (%s, 상장 %s)",
                    code, master.name, master.market, listing.isoformat(),
                )

            session.commit()
    finally:
        # FdrAdapter 는 라이브러리 wrapper 라 HTTP client 가 없어 close() 불필요
        # (pykrx/FDR adapter 공통 — scheduler _do_krx 주석 일관).
        engine.dispose()

    logger.info(
        "stocks_master 적재 완료 — saved=%d skipped=%d%s",
        saved, len(skipped),
        f" ({', '.join(skipped)})" if skipped else "",
    )
    return 0 if saved > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
