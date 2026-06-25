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
                record = StockMasterRecord(
                    id=lineage_id_for_code(code),
                    current_code=code,
                    current_name=master.name,
                    market=master.market,
                    listing_date=listing,
                    delisting_date=None,
                    fiscal_month=12,
                    code_history=(
                        CodeHistoryEntry(
                            code=code,
                            valid_from=listing,
                            valid_to=None,
                            reason="initial_listing",
                        ),
                    ),
                    ifrs_preference_default="AUTO",
                    security_type="common",
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
