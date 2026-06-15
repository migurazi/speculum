"""Request-scoped 캐싱 repository 데코레이터 — screen N+1 완화.

스크리너(screen_active_codes)는 active universe(운영 ~2,500 종목)를 순회하며
종목마다 DbFieldProvider 를 만들어 factor 를 평가한다. 대부분의 fact(가격·재무·
시총·자사주·배당)는 종목별로 다르므로 종목당 1회 fetch 가 불가피하지만,
**거시지표(macro_indicator)는 종목과 무관**하다 — indicator_id(예: "722Y001/
0101000" 기준금리) 단위로 조회되며 모든 종목이 같은 값을 본다. 그런데 현재는
종목마다 DbFieldProvider 가 `macro_repo.fetch_latest(indicator_id, as_of)` 를
반복 호출하여 **N×M 개의 동일 쿼리**(N=종목 수, M=indicator 수)가 발생한다.

본 모듈의 `CachingMacroIndicatorRepository` 를 screen 1회 실행당 1 인스턴스로
모든 종목의 provider 에 공유 주입하면 `(indicator_id, as_of)` 당 1회로 축약되어
**N×M → M** 으로 줄어든다.

per-code fact(price·market_cap)는 종목마다 값이 달라 memoize 로는 중복 제거가
안 되므로(매 종목 cache miss) 양상이 다르다 — `CachingPriceRepository` /
`CachingMarketCapRepository` 는 루프 **이전에** `prime(universe_codes, as_of)` 로
1회 bulk fetch(`WHERE code IN (...)`) 하여 종목별 개별 쿼리 N 회 자체를 청크 쿼리
몇 회로 축약한다(N+1 의 본질인 round-trip 수 제거). 상세는 각 클래스 docstring.
financial·treasury(`CachingFinancialRepository`/`CachingTreasurySharesRepository`)도
동일 bulk prime 으로 종목 간 N+1 을 제거한다 — 단 supersede chain 해소를 serve-time
에 수행하므로 raw vintage 를 적재(account 가 serve-time 가변)하고, 해소 절차는
`pit_resolution` 의 공유 helper(Sql·Fake 와 단일 출처)에 위임해 중복을 없앤다.

PIT 안전성 (캐시가 결과를 바꾸지 않음):
    MacroIndicatorRepository.fetch_latest 는 `(indicator_id, as_of)` 에 대해
    vintage 이중 시간축 PIT(reference_date<=as_of AND vintage_date<=as_of 중 최신)
    로 **불변값**을 반환한다(look-ahead 0, pit_protocols.MacroIndicatorRepository).
    같은 키에 같은 값이므로 memoize 가 의미를 바꾸지 않는다. macro 는 KRX/DART 와
    달리 batch_cutoff 축이 없어(vintage_date 가 곧 재현 축) 캐시 키가 단순하다.

수명 (request-scoped):
    screen 1회 실행의 lifetime 동안만 유효. cross-request 공유 금지 — 매 호출이 새
    인스턴스를 만든다(stale 데이터·메모리 누적 방지). 캐시 키에 as_of 를 포함하므로
    **단일 as_of(screen)뿐 아니라 복수 as_of(backtest 의 rebalance 시점 순회)에도
    안전**하다 — 시점별 다른 PIT 값이 키로 격리되어 시점 간 혼선이 없고, 같은 시점
    내 N 종목 중복만 제거된다. backtest 는 run_backtest 진입부에서 전체 rebalance
    1개 인스턴스로 감싸 N×R×M → R×M 로 축약(R×M 엔트리, 수십 KB 수준 무해).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from uuid import UUID

from app.repositories.batch_run_repository import BatchCutoff
from app.repositories.pit_protocols import (
    CorporateActionRecord,
    CorporateActionRepository,
    DividendRepository,
    FinancialRecord,
    FinancialRepository,
    MacroIndicatorRecord,
    MacroIndicatorRepository,
    MarketCapRecord,
    MarketCapRepository,
    PriceRecord,
    PriceRepository,
    TreasurySharesRecord,
    TreasurySharesRepository,
)
from app.repositories.pit_resolution import (
    resolve_active_actions,
    resolve_dividends,
    resolve_financial_periods,
    resolve_latest_treasury,
)
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "CachingCorporateActionRepository",
    "CachingDividendRepository",
    "CachingFinancialRepository",
    "CachingMacroIndicatorRepository",
    "CachingMarketCapRepository",
    "CachingPriceRepository",
    "CachingTreasurySharesRepository",
    "PRICE_PREFETCH_WINDOW_DAYS",
]

# price prefetch 의 기본 lookback window (일). DbFieldProvider 의 최장 lookback
# (total_return_trailing_1y = as_of - 365일) + 여유. prime 은 [as_of-window, as_of]
# 만 적재하므로 full-history(date.min) 대비 메모리를 수백 MB → 수십 MB 로 억제한다
# (oracle 설계검토 A2). window 보다 더 먼 과거를 요청하는 fetch(예: _select_portfolio
# 의 date.min 존재판정)는 캐시 miss → inner 위임으로 정확성 보존(절대 silent
# truncate 안 함). window 가 작아도 correctness 는 불변 — perf(캐시 hit율)만 영향.

# sentinel — None 값 자체를 캐시하므로 "미조회"와 "조회결과 None"을 dict.get 의
# default 로 구분할 수 없다. 별도 sentinel 로 negative caching 정확성을 보장한다
# (클래스보다 먼저 정의 — fetch_latest 가 모듈 레벨에서 참조).
_MISS: object = object()


class CachingMacroIndicatorRepository:
    """MacroIndicatorRepository 위임 + (indicator_id, as_of) memoize (request-scoped).

    `MacroIndicatorRepository` Protocol 을 그대로 구현(duck typing)하므로
    DbFieldProvider 코드 무변경으로 드롭인 가능. screen_active_codes 가 inner
    repo 를 1회 감싸 모든 provider 에 주입.

    Args:
        inner: 실제 PIT 조회를 수행하는 MacroIndicatorRepository (SQL 또는 Fake).
    """

    def __init__(self, inner: MacroIndicatorRepository) -> None:
        self._inner = inner
        # (indicator_id, as_of) → 조회 결과(None 포함). None 도 캐시하여 데이터
        # 없는 indicator 의 반복 fetch 까지 제거(negative caching).
        self._cache: dict[tuple[str, date], MacroIndicatorRecord | None] = {}

    def fetch_latest(
        self,
        indicator_id: str,
        *,
        as_of: date,
    ) -> MacroIndicatorRecord | None:
        """캐시 hit 시 즉시 반환, miss 시 inner 위임 후 결과(None 포함) 저장.

        Protocol 시그니처 동일 — keyword-only as_of(PIT 의도 type-level 명시).
        """
        key = (indicator_id, as_of)
        cached = self._cache.get(key, _MISS)
        if cached is not _MISS:
            return cached  # type: ignore[return-value]
        record = self._inner.fetch_latest(indicator_id, as_of=as_of)
        self._cache[key] = record
        return record


PRICE_PREFETCH_WINDOW_DAYS: int = 400


class CachingPriceRepository:
    """PriceRepository 위임 + universe prefetch(bulk) 캐싱 (request-scoped, 단일 as_of).

    macro 와 N+1 양상이 다르다 — price 는 종목마다 code 가 달라 lazy memoize 로는
    중복 제거가 안 된다(매 종목 cache miss). 대신 universe 루프 **이전에**
    `prime(codes, as_of)` 로 1회 bulk fetch(`WHERE code IN (...)`) 하여 종목별 개별
    쿼리 N 회를 청크 쿼리 몇 회로 축약한다 — N+1 의 본질(쿼리 round-trip 수)을 제거.

    캐시 수명·범위 (단일 (as_of, start, batch_cutoff)):
        한 rebalance 시점(단일 as_of)의 universe 루프 1회 lifetime 만. 매 prime 이
        이전 캐시를 교체(reset)하므로 backtest 의 시점 순회에서도 현재 시점만
        메모리에 남는다(bounded). macro 처럼 run_backtest 전체 1 인스턴스를 써도
        무방하나, price 는 적재량이 커 시점별 재prime(단일 시점 수명)이 안전하다
        (oracle 설계검토 D).

    정확성 (캐시 = N 개 개별 fetch 와 byte-동일):
        - prime 은 `[as_of-window, as_of]` 만 적재(window=PRICE_PREFETCH_WINDOW_DAYS).
          provider 의 모든 lookback(<=365일)을 커버. fetch_prices(start=X) 는 캐시
          시계열을 `effective_date >= X` 로 필터 → SqlPriceRepository 가 start=X 로
          직접 쿼리한 것과 동일 범위·동일 정렬(effective_date asc).
        - **window 보다 더 먼 과거(start < prime 하한) 또는 다른 (as_of, batch_cutoff)
          요청은 inner 위임** — 캐시가 모르는 범위를 silent truncate 하면 빈/부족
          시계열로 equity_curve 가 붕괴하므로 절대 금지(oracle B/D).
        - batch_cutoff 를 키에 포함 — reproduce 모드(frozen cutoff)와 라이브(None)가
          섞여 오서빙되면 §2.10 재현성 위반(가장 심각). BatchCutoff 는 frozen
          dataclass(hashable·eq) 라 == 비교로 격리.
        - PIT 사후검사(assert_no_lookahead)는 bulk fetch 측(SqlPriceRepository.
          fetch_prices_bulk)이 전체 1회 수행 → 캐시 반환은 검증된 시계열의 subset.

    `PriceRepository` Protocol 을 그대로 구현(duck typing) → DbFieldProvider 무변경
    드롭인. inner 가 `fetch_prices_bulk` 를 제공하면 1회 bulk, 없으면 per-code
    위임으로 graceful degrade(정확성 동일, perf 만 차이).

    Args:
        inner: 실제 PIT 조회를 수행하는 PriceRepository (SQL 또는 Fake).
    """

    def __init__(self, inner: PriceRepository) -> None:
        self._inner = inner
        self._by_code: dict[str, tuple[PriceRecord, ...]] = {}
        self._as_of: date | None = None
        self._start: date | None = None
        self._batch_cutoff: BatchCutoff | None = None
        self._primed = False

    def prime(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
        window_days: int = PRICE_PREFETCH_WINDOW_DAYS,
    ) -> None:
        """universe codes 의 `[as_of-window, as_of]` 가격 시계열을 1회 bulk 적재.

        이전 prime 의 캐시를 교체(reset) — 시점별 재prime 시 메모리 bounded. 빈
        codes 면 빈 캐시(정상 — 루프가 돌지 않음).
        """
        start = as_of - timedelta(days=window_days)
        # 순서보존 dedup — 중복 code 의 bulk 인자 중복·캐시 키 충돌 방지.
        unique = list(dict.fromkeys(c for c in codes if c))
        bulk = getattr(self._inner, "fetch_prices_bulk", None)
        if bulk is not None:
            fetched = bulk(unique, as_of=as_of, start=start, batch_cutoff=batch_cutoff)
            self._by_code = {c: tuple(fetched.get(c, ())) for c in unique}
        else:
            # inner 가 bulk 미지원 — per-code 위임(정확성 동일, round-trip 절감 없음).
            self._by_code = {
                c: tuple(
                    self._inner.fetch_prices(
                        c, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
                    )
                )
                for c in unique
            }
        self._as_of = as_of
        self._start = start
        self._batch_cutoff = batch_cutoff
        self._primed = True

    def fetch_prices(
        self,
        code: str,
        *,
        as_of: date,
        start: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> Sequence[PriceRecord]:
        """캐시 범위 내면 필터 반환, 밖이면 inner 위임(절대 silent truncate 금지)."""
        if (
            not self._primed
            or as_of != self._as_of
            or batch_cutoff != self._batch_cutoff
            or self._start is None
            or start < self._start
        ):
            # 다른 (as_of, batch_cutoff) 또는 prime window 보다 더 먼 과거 요청 →
            # 캐시가 cover 못 함 → inner 위임(정확성 우선, oracle B/D).
            return self._inner.fetch_prices(
                code, as_of=as_of, start=start, batch_cutoff=batch_cutoff,
            )
        if start > as_of:
            return ()
        series = self._by_code.get(code, ())
        # 캐시는 [self._start, as_of] 전체 → 요청 [start, as_of] 로 필터(start >=
        # self._start 보장). start == self._start 이면 모든 row 통과(= 전체 반환).
        return tuple(r for r in series if r.effective_date >= start)

    def fetch_codes_with_prices(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> set[str]:
        """`as_of` 이하 가격이 1건이라도 있는 code 집합(존재 판정) — inner 위임.

        full-range 존재 판정이라 bounded prime 캐시로는 답할 수 없다(window 밖
        과거에만 데이터가 있는 종목 누락). inner 의 bulk 존재 쿼리에 위임 —
        _select_portfolio 의 survivorship(line 338) missing_price 판정용.
        """
        fn = getattr(self._inner, "fetch_codes_with_prices", None)
        if fn is not None:
            return fn(codes, as_of=as_of, batch_cutoff=batch_cutoff)
        # inner 가 bulk 존재 쿼리 미지원 — per-code fetch(start=date.min)로 fallback.
        return {
            c
            for c in codes
            if c
            and self._inner.fetch_prices(
                c, as_of=as_of, start=date.min, batch_cutoff=batch_cutoff,
            )
        }

    def save_prices(self, records: Sequence[PriceRecord]) -> None:
        """write 경로는 캐싱 무관 — inner 위임(Protocol 완전성)."""
        self._inner.save_prices(records)


class CachingMarketCapRepository:
    """MarketCapRepository 위임 + universe prefetch(bulk) 캐싱 (request-scoped).

    CachingPriceRepository 와 동형 — market_cap 도 supersede 무관(KRX 정정 없음)
    이라 `effective_date <= as_of` 중 최신 1건만 본다. prime 으로 universe 의 latest
    를 1회 bulk 적재 후 종목별 fetch_latest 를 캐시 서빙. 캐시 키 (as_of,
    batch_cutoff) — 다른 키 요청은 inner 위임.

    Args:
        inner: 실제 PIT 조회를 수행하는 MarketCapRepository (SQL 또는 Fake).
    """

    def __init__(self, inner: MarketCapRepository) -> None:
        self._inner = inner
        # code → latest record(없으면 키 부재 → .get 이 None). negative 도 의미상
        # None 반환으로 동일(inner.fetch_latest 가 없으면 None).
        self._by_code: dict[str, MarketCapRecord | None] = {}
        self._as_of: date | None = None
        self._batch_cutoff: BatchCutoff | None = None
        self._primed = False

    def prime(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> None:
        """universe codes 의 latest market_cap(<=as_of)을 1회 bulk 적재(이전 캐시 reset)."""
        unique = list(dict.fromkeys(c for c in codes if c))
        bulk = getattr(self._inner, "fetch_latest_bulk", None)
        if bulk is not None:
            self._by_code = dict(bulk(unique, as_of=as_of, batch_cutoff=batch_cutoff))
        else:
            self._by_code = {
                c: self._inner.fetch_latest(
                    c, as_of=as_of, batch_cutoff=batch_cutoff,
                )
                for c in unique
            }
        self._as_of = as_of
        self._batch_cutoff = batch_cutoff
        self._primed = True

    def fetch_latest(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> MarketCapRecord | None:
        """캐시 키 일치 시 캐시 반환(없으면 None), 불일치 시 inner 위임."""
        if (
            not self._primed
            or as_of != self._as_of
            or batch_cutoff != self._batch_cutoff
        ):
            return self._inner.fetch_latest(
                code, as_of=as_of, batch_cutoff=batch_cutoff,
            )
        return self._by_code.get(code)

    def save_market_caps(self, records: Sequence[MarketCapRecord]) -> None:
        """write 경로는 캐싱 무관 — inner 위임(Protocol 완전성)."""
        self._inner.save_market_caps(records)


class CachingFinancialRepository:
    """FinancialRepository 위임 + universe prefetch(bulk) 캐싱 (request-scoped).

    Caching{Price,MarketCap}Repository 와 동형 — 종목마다 값이 다른 per-code fact
    라 memoize 가 아닌 **루프 전 1회 bulk prime** 으로 종목별 개별 쿼리 N 회를 청크
    쿼리 몇 회로 축약(round-trip 제거). 단 price 와 두 가지가 다르다:

    1. **raw vintage 적재** — price 는 종목당 시계열 1개라 prefetch 결과를 그대로
       서빙하면 되지만, financial 은 한 종목에 (account, ifrs_type, fiscal_period)
       여러 fact 가 있고 **account/ifrs_type 이 serve-time 가변**(DbFieldProvider 가
       factor 별로 다른 account 요청)이다. 따라서 resolved 결과가 아닌 **raw row
       (전체 vintage)** 를 code 별로 적재하고, serve-time 에 `resolve_financial_
       periods`(pit_resolution 공유 helper)로 단건과 byte-동일하게 해소한다.
    2. **date 사전 필터 없음** (oracle 설계검토 C1 — load-bearing) — bulk 가 전체
       vintage 를 적재한다. chain integrity 검증(`_validate_chain_integrity`)이
       candidate 전체에 의존하므로 `effective_date <= as_of` 사전 필터를 넣으면
       depth/cycle 검사 결과가 단건과 달라져 §2.10 재현성이 깨진다. financial 은
       시계열이 아니라 row 수(종목당 수백)가 작아 메모리 부담이 price 보다 가볍다.

    정확성 (캐시 = 단건 fetch_financials 와 byte-동일):
        - prime 의 candidate set 은 단건 `WHERE code=:code`(+cutoff) 와 동일(IN 확장)
          → serve-time helper 가 동일 알고리즘으로 해소 → 동일 결과.
        - 캐시 키 (as_of, batch_cutoff). 다른 키 또는 미prime 요청은 inner 위임
          (silent truncate 절대 금지 — price 패턴 동일).
        - serve-time 에 Sql 과 동일하게 `assert_no_lookahead` 를 한 겹 덧씌운다.

    `FinancialRepository` Protocol 을 duck typing 구현 → DbFieldProvider 무변경 드롭인.
    inner 가 `fetch_all_financials_bulk` 미지원 시 graceful degrade(미prime → 매
    fetch inner 위임, 정확성 동일·perf 만 차이).

    Args:
        inner: 실제 PIT 조회를 수행하는 FinancialRepository (SQL 또는 Fake).
    """

    def __init__(self, inner: FinancialRepository) -> None:
        self._inner = inner
        # serve-time chain 해소용 — Sql/Fake 의 _enforcer 와 동일 stateless 인스턴스.
        self._enforcer = PITEnforcer()
        # code → 전체 vintage raw row (account/ifrs/date 필터·해소 이전).
        self._by_code: dict[str, tuple[FinancialRecord, ...]] = {}
        self._as_of: date | None = None
        self._batch_cutoff: BatchCutoff | None = None
        self._primed = False

    def prime(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> None:
        """universe codes 의 전체 vintage financial raw row 를 1회 bulk 적재.

        이전 prime 캐시를 교체(reset) — 시점별 재prime 시 메모리 bounded. inner 가
        bulk 미지원이면 미prime 상태로 두어(_primed=False) 매 fetch 를 inner 위임
        (graceful degrade — round-trip 절감 없으나 정확성 동일).
        """
        unique = list(dict.fromkeys(c for c in codes if c))
        bulk = getattr(self._inner, "fetch_all_financials_bulk", None)
        if bulk is not None:
            fetched = bulk(unique, batch_cutoff=batch_cutoff)
            self._by_code = {c: tuple(fetched.get(c, ())) for c in unique}
            self._primed = True
        else:
            self._by_code = {}
            self._primed = False
        self._as_of = as_of
        self._batch_cutoff = batch_cutoff

    def fetch_financials(
        self,
        code: str,
        *,
        as_of: date,
        account: str,
        ifrs_type: str | None = None,
        max_periods: int = 8,
        batch_cutoff: BatchCutoff | None = None,
    ) -> Sequence[FinancialRecord]:
        """캐시 키 일치 시 raw vintage 를 serve-time 해소, 불일치/미prime 시 inner 위임."""
        if (
            not self._primed
            or as_of != self._as_of
            or batch_cutoff != self._batch_cutoff
        ):
            # 다른 (as_of, batch_cutoff) 또는 미prime → 캐시가 cover 못 함 → inner
            # 위임(정확성 우선, silent truncate 금지).
            return self._inner.fetch_financials(
                code,
                as_of=as_of,
                account=account,
                ifrs_type=ifrs_type,
                max_periods=max_periods,
                batch_cutoff=batch_cutoff,
            )
        # 단건 SqlFinancialRepository.fetch_financials 와 동일 절차 — 공유 helper
        # (cutoff 는 prime 에서 이미 반영, account/ifrs/해소/sort/truncate 는 helper)
        # + Sql 과 동일한 사후 assert(defense-in-depth).
        resolved = tuple(
            resolve_financial_periods(
                list(self._by_code.get(code, ())),
                as_of,
                account=account,
                ifrs_type=ifrs_type,
                max_periods=max_periods,
                enforcer=self._enforcer,
            )
        )
        self._enforcer.assert_no_lookahead(resolved, as_of)
        return resolved

    def fetch_restatement_history(
        self,
        code: str,
        *,
        fiscal_period: str | None = None,
        as_of: date | None = None,
    ) -> list[FinancialRecord]:
        """정정공시 이력(전체 vintage 뷰)은 캐싱 무관 — inner 위임(Protocol 완전성)."""
        return self._inner.fetch_restatement_history(
            code, fiscal_period=fiscal_period, as_of=as_of,
        )

    def fetch_active_disclosure(
        self,
        code: str,
        fiscal_period: str,
        ifrs_type: str,
    ) -> tuple[str | None, tuple[FinancialRecord, ...]]:
        """DART 정정공시 배치 전용 active head 조회는 캐싱 무관 — inner 위임.

        본 데코레이터는 read-path(screen/backtest 평가) 전용이라 운영에서 호출되지
        않으나, drop-in 일관성을 위해 Protocol 의 모든 메서드를 inner 위임한다
        (fetch_restatement_history / update_superseded_by 와 동일 근거).
        """
        return self._inner.fetch_active_disclosure(code, fiscal_period, ifrs_type)

    def save_financials(self, records: Sequence[FinancialRecord]) -> None:
        """write 경로는 캐싱 무관 — inner 위임(Protocol 완전성)."""
        self._inner.save_financials(records)

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain set(ADR-0020 유일 UPDATE 경로)은 캐싱 무관 — inner 위임.

        본 데코레이터는 read-path(screen/backtest 평가) 전용이라 운영에서 호출되지
        않으나, drop-in 일관성을 위해 Sql/Fake 의 write 메서드를 모두 위임(oracle
        구현후 리뷰 M1 — save_*/fetch_restatement_history 와 대칭).
        """
        self._inner.update_superseded_by(record_id, successor_id)


class CachingTreasurySharesRepository:
    """TreasurySharesRepository 위임 + universe prefetch(bulk) 캐싱 (request-scoped).

    CachingFinancialRepository 와 동형 — raw vintage 를 code 별 bulk 적재 후
    serve-time 에 `resolve_latest_treasury`(공유 helper)로 단건과 byte-동일하게
    해소(최신 fiscal_period 1건). date 사전 필터 없음(oracle 설계검토 C1, chain
    integrity 등가성). 캐시 키 (as_of, batch_cutoff). inner 가 bulk 미지원 시
    graceful degrade(미prime → inner 위임).

    Args:
        inner: 실제 PIT 조회를 수행하는 TreasurySharesRepository (SQL 또는 Fake).
    """

    def __init__(self, inner: TreasurySharesRepository) -> None:
        self._inner = inner
        self._enforcer = PITEnforcer()
        self._by_code: dict[str, tuple[TreasurySharesRecord, ...]] = {}
        self._as_of: date | None = None
        self._batch_cutoff: BatchCutoff | None = None
        self._primed = False

    def prime(
        self,
        codes: Sequence[str],
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> None:
        """universe codes 의 전체 vintage 자사주 raw row 를 1회 bulk 적재(이전 캐시 reset)."""
        unique = list(dict.fromkeys(c for c in codes if c))
        bulk = getattr(self._inner, "fetch_all_treasury_bulk", None)
        if bulk is not None:
            fetched = bulk(unique, batch_cutoff=batch_cutoff)
            self._by_code = {c: tuple(fetched.get(c, ())) for c in unique}
            self._primed = True
        else:
            self._by_code = {}
            self._primed = False
        self._as_of = as_of
        self._batch_cutoff = batch_cutoff

    def fetch_latest_active(
        self,
        code: str,
        *,
        as_of: date,
        batch_cutoff: BatchCutoff | None = None,
    ) -> TreasurySharesRecord | None:
        """캐시 키 일치 시 raw vintage 를 serve-time 해소(최신 1건), 불일치/미prime 시 inner 위임."""
        if (
            not self._primed
            or as_of != self._as_of
            or batch_cutoff != self._batch_cutoff
        ):
            return self._inner.fetch_latest_active(
                code, as_of=as_of, batch_cutoff=batch_cutoff,
            )
        # 단건 SqlTreasurySharesRepository.fetch_latest_active 와 동일 절차 — 공유
        # helper + Sql 과 동일한 사후 assert(None 은 검사 대상 없음이라 skip).
        record = resolve_latest_treasury(
            list(self._by_code.get(code, ())), as_of, enforcer=self._enforcer,
        )
        if record is None:
            return None
        self._enforcer.assert_no_lookahead((record,), as_of)
        return record

    def fetch_active_treasury_disclosure(
        self,
        code: str,
        fiscal_period: str,
    ) -> tuple[str | None, TreasurySharesRecord | None]:
        """DART 정정공시 배치 전용 active head 조회는 캐싱 무관 — inner 위임
        (CachingFinancialRepository.fetch_active_disclosure 와 동일 근거)."""
        return self._inner.fetch_active_treasury_disclosure(code, fiscal_period)

    def save_treasury_shares(
        self, records: Sequence[TreasurySharesRecord],
    ) -> None:
        """write 경로는 캐싱 무관 — inner 위임(Protocol 완전성)."""
        self._inner.save_treasury_shares(records)

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain set(ADR-0020 유일 UPDATE 경로)은 캐싱 무관 — inner 위임
        (CachingFinancialRepository.update_superseded_by 와 동일 근거)."""
        self._inner.update_superseded_by(record_id, successor_id)


class CachingCorporateActionRepository:
    """CorporateActionRepository 위임 + universe prefetch(bulk) 캐싱 (request-scoped).

    CachingFinancialRepository 와 동형 — raw vintage 를 code 별 bulk 적재 후
    serve-time 에 `resolve_active_actions`(공유 helper)로 단건과 byte-동일 해소
    (action_types 필터 + announced_date chain 해소 + sort). backtest 보유수익률
    (`_portfolio_return → _adjusted_closes_at`)이 holdings 의 각 종목마다
    `fetch_actions(code, as_of=t_next)` 를 호출하는 per-code N+1 을 루프 전 1회
    bulk prime 으로 축약(R×H → R).

    raw 적재 이유(financial 과 동일): `action_types` 가 serve-time 가변(호출자가
    종류 필터를 달리)이라 resolved 가 아닌 raw 를 적재하고 serve-time 에 helper 로
    해소. date 사전필터 없음 — 단건 fetch 가 `WHERE code` 만 쓰고 announced_date
    해소를 Python 에서 하므로 전체 vintage 적재(chain integrity 등가성, C1 동일 근거).

    캐시 키 (as_of) — fetch_actions 는 batch_cutoff 가 없다(CA cutoff freeze 불가,
    ADR-0033 D4 상속 한계). 다른 as_of/미prime 요청은 inner 위임(silent truncate 금지).
    serve-time 에 Sql 과 동일하게 `assert_no_lookahead(date_of=announced_date)` 덧씌움.
    inner 가 bulk 미지원 시 graceful degrade(미prime → 매 fetch inner 위임).

    Args:
        inner: 실제 PIT 조회를 수행하는 CorporateActionRepository (SQL 또는 Fake).
    """

    def __init__(self, inner: CorporateActionRepository) -> None:
        self._inner = inner
        self._enforcer = PITEnforcer()
        # code → 전체 vintage raw 사건(action_types/해소 이전).
        self._by_code: dict[str, tuple[CorporateActionRecord, ...]] = {}
        self._as_of: date | None = None
        self._primed = False

    def prime(self, codes: Sequence[str], *, as_of: date) -> None:
        """universe codes 의 전체 vintage corporate action raw 를 1회 bulk 적재.

        이전 prime 캐시를 교체(reset). inner 가 bulk 미지원이면 미prime(_primed=
        False)으로 두어 매 fetch inner 위임(graceful degrade — 정확성 동일).
        """
        unique = list(dict.fromkeys(c for c in codes if c))
        bulk = getattr(self._inner, "fetch_all_actions_bulk", None)
        if bulk is not None:
            fetched = bulk(unique)
            self._by_code = {c: tuple(fetched.get(c, ())) for c in unique}
            self._primed = True
        else:
            self._by_code = {}
            self._primed = False
        self._as_of = as_of

    def fetch_actions(
        self,
        code: str,
        *,
        as_of: date,
        action_types: frozenset[str] | None = None,
    ) -> Sequence[CorporateActionRecord]:
        """캐시 키 일치 시 raw 를 serve-time 해소, 불일치/미prime 시 inner 위임."""
        if not self._primed or as_of != self._as_of:
            return self._inner.fetch_actions(
                code, as_of=as_of, action_types=action_types,
            )
        # 단건 SqlCorporateActionRepository.fetch_actions 와 동일 절차 — 공유 helper
        # + Sql 과 동일한 사후 assert(announced_date 축만 — effective_date 는 미래
        # 권리락일 정상이라 거짓양성 방지, M5_PLAN #1).
        resolved = tuple(
            resolve_active_actions(
                list(self._by_code.get(code, ())),
                as_of,
                action_types=action_types,
                enforcer=self._enforcer,
            )
        )
        self._enforcer.assert_no_lookahead(
            resolved, as_of, date_of=lambda r: r.announced_date,
        )
        return resolved

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain set(ADR-0020 유일 UPDATE 경로)은 캐싱 무관 — inner 위임."""
        self._inner.update_superseded_by(record_id, successor_id)


class CachingDividendRepository:
    """DividendRepository 위임 + universe prefetch(bulk) 캐싱 (request-scoped).

    CachingCorporateActionRepository 와 동형 — raw vintage(전체 corporate_action)를
    code 별 bulk 적재 후 serve-time 에 `resolve_dividends`(공유 helper)로 단건과
    byte-동일 해소(announced chain 해소 → cash_dividend 필터 → effective<=as_of 필터
    → sort). screen/backtest/snapshot 의 universe 순회에서 종목마다 호출되는
    `fetch_dividends`(total return §2.4 배당 재투자)의 per-code N+1 을 루프 전 1회
    bulk prime 으로 축약(N → 청크 몇 회).

    raw 적재 이유(CA 와 동일): cash_dividend 로 좁히지 않은 **전체 corporate_action
    집합**을 적재해야 cross-action_type chain(cash_dividend orig → split succ)에서
    부활 오판이 없다(resolve_dividends docstring). date 사전필터 없음 — 단건
    fetch_dividends 가 `WHERE code` 만 쓰고 announced chain 해소를 Python 에서 하므로
    전체 vintage 적재(chain integrity 등가성, C1 동일 근거).

    dual-PIT 보존: announced 축(가용성 + supersede chain) + effective 축(배당락 발생)
    두 필터를 serve-time helper 가 모두 적용 — 캐시가 dual-PIT 의미를 바꾸지 않는다.

    캐시 키 (as_of) — fetch_dividends 는 batch_cutoff 가 없다(CA/dividend cutoff
    freeze 불가, ADR-0033 D4 상속 한계). 다른 as_of/미prime 요청은 inner 위임(silent
    truncate 금지). serve-time 에 Sql 과 동일하게 `assert_no_lookahead(date_of=
    announced_date)` 덧씌움(announced 축만 — effective_date 는 미래 배당락일 정상이라
    거짓양성 방지, M5_PLAN #1). inner 가 bulk 미지원 시 graceful degrade(미prime →
    매 fetch inner 위임).

    Args:
        inner: 실제 PIT 조회를 수행하는 DividendRepository (SQL 또는 Fake).
    """

    def __init__(self, inner: DividendRepository) -> None:
        self._inner = inner
        self._enforcer = PITEnforcer()
        # code → 전체 vintage raw 사건(action_type/해소 이전 — 전체 corporate_action).
        self._by_code: dict[str, tuple[CorporateActionRecord, ...]] = {}
        self._as_of: date | None = None
        self._primed = False

    def prime(self, codes: Sequence[str], *, as_of: date) -> None:
        """universe codes 의 전체 vintage corporate action raw 를 1회 bulk 적재.

        이전 prime 캐시를 교체(reset). inner 가 bulk 미지원이면 미prime(_primed=
        False)으로 두어 매 fetch inner 위임(graceful degrade — 정확성 동일).
        """
        unique = list(dict.fromkeys(c for c in codes if c))
        bulk = getattr(self._inner, "fetch_all_dividends_bulk", None)
        if bulk is not None:
            fetched = bulk(unique)
            self._by_code = {c: tuple(fetched.get(c, ())) for c in unique}
            self._primed = True
        else:
            self._by_code = {}
            self._primed = False
        self._as_of = as_of

    def fetch_dividends(
        self,
        code: str,
        *,
        as_of: date,
    ) -> Sequence[CorporateActionRecord]:
        """캐시 키 일치 시 raw 를 serve-time 해소, 불일치/미prime 시 inner 위임."""
        if not self._primed or as_of != self._as_of:
            return self._inner.fetch_dividends(code, as_of=as_of)
        # 단건 SqlDividendRepository.fetch_dividends 와 동일 절차 — 공유 helper
        # (announced chain → cash_dividend 필터 → effective<=as_of 필터 → sort) +
        # Sql 과 동일한 사후 assert(announced_date 축만 — effective_date 는 미래
        # 배당락일 정상이라 거짓양성 방지, M5_PLAN #1).
        resolved = tuple(
            resolve_dividends(
                list(self._by_code.get(code, ())),
                as_of,
                enforcer=self._enforcer,
            )
        )
        self._enforcer.assert_no_lookahead(
            resolved, as_of, date_of=lambda r: r.announced_date,
        )
        return resolved

    def save_dividends(
        self, records: Sequence[CorporateActionRecord],
    ) -> None:
        """write 경로는 캐싱 무관 — inner 위임(Protocol 완전성)."""
        self._inner.save_dividends(records)

    def update_superseded_by(
        self, record_id: UUID, successor_id: UUID,
    ) -> None:
        """정정공시 chain set(ADR-0020 유일 UPDATE 경로)은 캐싱 무관 — inner 위임."""
        self._inner.update_superseded_by(record_id, successor_id)
