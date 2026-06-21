"""가격 history 백필 — 차트용 1년치 실 OHLCV 적재 (pykrx, 키 불필요).

KRX **일배치**(`--job krx`)는 as_of **하루치** OHLCV 만 적재한다(매일 누적 설계).
따라서 한 번 실행하면 차트에 1 bar 만 뜨고, 1년 history 가 필요한 factor
(`price-return:total-annual`)도 N/A 다. 본 스크립트는 **과거 구간(기본 1년)**의
OHLCV 를 한 번에 적재해 실 가격 차트(시계열)를 채운다.

일배치와의 차이:
- 일배치: as_of 1일 + market_cap + FDR 교차검증 + batch_runs/freshness. 매일 cron.
- 본 백필: 과거 N일 OHLCV 만(가격 history). market_cap/배치메타 없음 — 차트/
  price-return 용 history 보충 전용. pykrx range fetch(fromdate~todate) 사용.

`close_adjusted` 는 일배치와 동일하게 raw 동일값(T20 보정 미적용). 적재는
SqlPriceRepository.save_prices 의 ON CONFLICT DO NOTHING first-wins 라 재실행·일배치
중복과 멱등(같은 (code, effective_date) 보존).

실행:
    cd server
    set SPECULUM_DATABASE_URL=sqlite:///./speculum_real.db
    python -m scripts.backfill_prices 005930 000660 --as-of 2024-06-28 --days 365
    python -m scripts.backfill_prices                 # prices_daily 적재 종목, 기본 365일
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.adapters.base import AdapterError
from app.adapters.pykrx_adapter import PykrxAdapter
from app.core.config import get_database_url
from app.db.orm import PriceDailyORM  # noqa: F401  (metadata 등록)
from app.db.session import create_engine_from_url, create_sessionmaker
from app.repositories.citation_repository import SqlCitationRepository
from app.repositories.pit_protocols import PriceRecord
from app.repositories.sql_repositories import SqlPriceRepository
from app.services.lineage import lineage_id_for_code

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_prices")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts.backfill_prices",
        description="차트용 1년치 실 OHLCV 백필 (pykrx, 키 불필요).",
    )
    p.add_argument("codes", nargs="*", help="대상 종목코드. 미지정 시 prices_daily 적재 종목.")
    p.add_argument(
        "--as-of", type=date.fromisoformat, default=None,
        help="종료일(거래일, YYYY-MM-DD). 미지정 시 오늘.",
    )
    p.add_argument("--days", type=int, default=365, help="백필 기간(일). 기본 365.")
    return p


def _distinct_price_codes(session) -> list[str]:
    rows = session.execute(select(PriceDailyORM.code).distinct()).all()
    return sorted({r[0] for r in rows})


def _to_price_records(rows, citation_id) -> tuple[PriceRecord, ...]:
    """OHLCVRow → PriceRecord (KrxDailyBatch._build_price_records 동형, close_adjusted=raw)."""
    now = datetime.now(UTC)
    return tuple(
        PriceRecord(
            id=uuid4(),
            code=r.code,
            code_lineage_id=lineage_id_for_code(r.code),
            effective_date=r.trade_date,
            open_raw=r.open,
            high_raw=r.high,
            low_raw=r.low,
            close_raw=r.close,
            volume=r.volume,
            trading_value=r.value,
            close_adjusted=r.close,  # T20 미적용 — raw 동일.
            citation_id=citation_id,
            created_at=now,
        )
        for r in rows
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    url = get_database_url()
    if not url:
        logger.error("SPECULUM_DATABASE_URL 미설정 — 실 DB 가 필요합니다.")
        return 1

    as_of = args.as_of or date.today()
    start = as_of - timedelta(days=args.days)
    batch_id = uuid4()

    engine = create_engine_from_url(url)
    maker = create_sessionmaker(engine)
    adapter = PykrxAdapter()

    total_rows = 0
    saved_codes = 0
    skipped: list[str] = []
    try:
        with maker() as session:
            codes = args.codes or _distinct_price_codes(session)
            if not codes:
                logger.warning("대상 종목 없음 — 종목코드를 인자로 주세요.")
                return 1
            price_repo = SqlPriceRepository(session)
            citation_repo = SqlCitationRepository(session)
            logger.info(
                "가격 history 백필 — %d 종목, %s ~ %s (%d일)",
                len(codes), start.isoformat(), as_of.isoformat(), args.days,
            )

            for code in codes:
                try:
                    result = adapter.fetch_ohlcv_by_date_range(
                        code, fromdate=start, todate=as_of, batch_id=batch_id,
                    )
                except AdapterError as exc:
                    skipped.append(code)
                    logger.warning("OHLCV 백필 실패 skip code=%s: %s", code, exc)
                    continue
                if not result.data:
                    skipped.append(code)
                    continue
                # citation → prices 순서(FK). save_prices 는 first-wins 멱등.
                for c in result.citations:
                    citation_repo.save(c)
                cid = result.citations[0].id if result.citations else None
                if cid is None:
                    skipped.append(code)
                    continue
                records = _to_price_records(result.data, cid)
                price_repo.save_prices(records)
                total_rows += len(records)
                saved_codes += 1
                logger.info("  %s: %d일 적재", code, len(records))

            session.commit()
    finally:
        engine.dispose()

    logger.info(
        "가격 백필 완료 — 종목=%d rows=%d skipped=%d%s",
        saved_codes, total_rows, len(skipped),
        f" ({', '.join(skipped)})" if skipped else "",
    )
    return 0 if saved_codes > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
