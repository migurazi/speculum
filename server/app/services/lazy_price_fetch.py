"""종목 상세 차트 전용 — 주가 on-demand lazy fetch + 캐시.

`GET /api/stocks/{code}/prices` 가 요청 구간의 주가를 DB(prices_daily)에서 찾지
못하면, KRX(pykrx, keyless)에서 즉석으로 당겨와 citation 과 함께 영속화한 뒤
반환하기 위한 helper. DART 공시 on-demand fetch(`get_dart_adapter`)와 같은
"serving 경로 외부 fetch" 선례를 따른다.

**왜 차트 전용인가 (절대조건)**:
    본 helper 는 오직 종목 상세 가격 차트 표시를 위한 read-time 캐시 충전이다.
    스크리너(screen)·백테스트 재현(reproduce)·factor 산출 경로는 frozen
    `batch_cutoff`/`data_versions` 위에서 동작하므로 serving 시점에 새 데이터를
    당겨오면 PIT 재현성(§2.4/§2.10)이 깨진다. 따라서 본 함수는 그 경로들과
    절대 엮이지 않으며(호출처는 차트 endpoint 단 한 곳), 적재되는 데이터는
    이후 어떤 frozen run 의 cutoff 에도 포함되지 않는다(자연 누적일 뿐).

**close_adjusted = raw 인 이유**:
    KRX OHLCV 의 close 는 corporate action 보정 전 raw 값이다. T20 보정 일배치는
    별도 산출물이므로, 본 lazy 적재는 `close_adjusted` 에도 raw close 를 그대로
    넣는다(기존 KRX 일배치 save 와 동형). 차트의 보정 종가는 read-time
    PriceAdjuster 가 corporate action chain 으로 산출하므로 저장값 보정은 불필요.

**멱등**:
    `save_prices` 는 PK (code, effective_date) ON CONFLICT DO NOTHING(first-wins)
    이라 같은 거래일을 다시 적재해도 안전하다. 또한 DB 에 이미 있는 최신
    거래일 이후 구간만 갭으로 fetch 하여 불필요한 외부 호출을 줄인다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.adapters.base import AdapterError
from app.adapters.pykrx_adapter import PykrxAdapter
from app.db.orm.batch_runs import BATCH_STATUS_SUCCESS
from app.repositories.batch_run_repository import BatchRunRepository
from app.repositories.citation_repository import CitationRepository
from app.repositories.pit_protocols import PriceRecord, PriceRepository
from app.services.lineage import lineage_id_for_code

# on-demand lazy fetch 의 batch_run source 라벨. 스케줄 KRX 배치("KRX")와 구분해
# data_freshness 의 latest_successful("KRX") (스케줄 파이프라인 신선도) 를 오염시키지
# 않는다 — 차트 조회로 인한 적재가 "스케줄 배치가 돌았다"로 오인되면 안 됨.
_ONDEMAND_SOURCE: str = "KRX_ONDEMAND"


def ensure_prices_cached(
    code: str,
    *,
    start: date,
    end: date,
    price_repo: PriceRepository,
    citation_repo: CitationRepository,
    krx_adapter: PykrxAdapter,
    batch_run_repo: BatchRunRepository,
) -> None:
    """[start, end] 주가가 DB에 부족하면 KRX 에서 갭만 lazy fetch→citation→save.

    종목 상세 차트 전용 — 스크리너/재현성과 무관(모듈 docstring 참조).
    close_adjusted=raw(read-time PriceAdjuster 가 보정). 멱등(save_prices ON
    CONFLICT DO NOTHING). 갭에 거래일이 없거나(주말 등) 일시 오류면 조용히
    degrade — DB 에 이미 있는 것만으로 차트가 그려진다.

    Args:
        code: KRX 종목코드 (6자리 zero-padded, 호출자가 정규화).
        start: 요청 구간 시작일 (inclusive).
        end: 요청 구간 종료일 (inclusive, = as_of).
        price_repo: 가격 repository. SqlPriceRepository 일 때만 실제 영속화되며,
            Fake/세션 미설정이면 무해(in-memory).
        citation_repo: citation repository (1차 자료 출처 기록).
        krx_adapter: pykrx adapter (keyless OHLCV fetch).
        batch_run_repo: batch_run repository. **citation.batch_id FK 불변식**
            ("모든 source_citations.batch_id ∈ batch_runs.id", ADR M1 T48a) 유지용 —
            on-demand 적재도 batch_run 으로 기록해야 FK 가 성립한다. (운영 SQLite 는
            FK OFF 라 orphan citation 이 silent 통과하나, 무결성상 위반 — V0 e2e
            (FK ON)가 이를 포착해 본 기록을 도입.)
    """
    # 1. DB 에 이미 캐시된 구간 확인 — 최신 거래일 이후만 갭으로 채운다.
    existing = price_repo.fetch_prices(code, as_of=end, start=start)
    db_max = max((r.effective_date for r in existing), default=None)
    fetch_from = start if db_max is None else db_max + timedelta(days=1)
    if fetch_from > end:
        # 이미 end 까지 캐시됨 — 외부 호출 불필요.
        return

    # 2. 갭 구간만 KRX 에서 fetch. 거래일이 없거나(주말/휴장) 일시 오류는
    #    AdapterError → degrade(DB 있는 것만으로 차트 표시).
    try:
        result = krx_adapter.fetch_ohlcv_by_date_range(
            code, fromdate=fetch_from, todate=end, batch_id=uuid4(),
        )
    except AdapterError:
        return
    if not result.data:
        return

    # 3. citation FK 불변식 유지 — on-demand 적재를 batch_run 으로 기록한 뒤
    #    citation/price 를 save. citation.batch_id (어댑터가 정한 authoritative
    #    값) 로 batch_run 을 만들어 FK(citation.batch_id ∈ batch_runs.id)를 만족.
    #    DEFERRABLE INITIALLY DEFERRED 라 같은 트랜잭션 내 순서는 무관하나,
    #    명확성 위해 citation 저장 전에 batch_run 을 기록한다.
    citation = result.citations[0]
    now = datetime.now(UTC)
    batch_run_repo.start(
        run_id=citation.batch_id,
        market=None,
        source=_ONDEMAND_SOURCE,
        started_at=now,
    )
    lineage = lineage_id_for_code(code)
    records = tuple(
        PriceRecord(
            id=uuid4(),
            code=row.code,
            code_lineage_id=lineage,
            effective_date=row.trade_date,
            open_raw=row.open,
            high_raw=row.high,
            low_raw=row.low,
            close_raw=row.close,
            volume=row.volume,
            trading_value=row.value,
            # close_adjusted=raw — T20 보정 미적용, read-time PriceAdjuster 가 보정.
            close_adjusted=row.close,
            citation_id=citation.id,
            created_at=now,
        )
        for row in result.data
    )
    batch_run_repo.finalize(
        run_id=citation.batch_id,
        ended_at=now,
        success_count=len(records),
        status=BATCH_STATUS_SUCCESS,
    )
    citation_repo.save(citation)
    price_repo.save_prices(records)
