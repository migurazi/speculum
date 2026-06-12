"""정정공시 chain 해소 절차의 단일 출처 — Sql/Fake/Caching repository 공유.

`financials` / `treasury_shares` 의 fetch 는 세 구현체(SQL·Fake·Caching prime)가
**동일한** PIT 해소 절차를 거친다:

    1. (financial 한정) account + ifrs_type 필터 — 그룹화 이전 (ADR-0005 연결/별도
       별개 chain 충돌 방지).
    2. `PITEnforcer.latest_active_by_key(key=fiscal_period)` 로 fiscal_period 별
       정정공시 chain 을 as_of-time 해소.
    3. 결정적 sort `(effective_date, fiscal_period, str(id))` — id 가 final
       tiebreaker (oracle 2 차 리뷰 C6).
    4. (financial) `[-max_periods:]` 절사 / (treasury) max 1 건.

이 절차를 본 모듈의 free function 으로 추출하여 **단일 출처**로 만든다.
`CachingFinancialRepository` 가 bulk prime 한 raw row 를 serve-time 에 해소할 때도
SqlFinancialRepository.fetch_financials 와 **byte-동일** 한 결과를 내야 하므로
(§2.10 재현성), 해소 로직을 세 곳에 복제하면 회귀 위험이 크다 — 한 곳에 둔다.

경계 (oracle 설계검토 M3 — 흐리면 회귀):
    - **cutoff 필터는 본 helper 의 책임이 아니다.** Sql 은 `_batch_cutoff_predicate`
      (SQL 술어), Fake 는 `_passes_cutoff`(Python) 로 candidate fetch 단계에서
      처리한다. 본 helper 는 **이미 cutoff-필터된 candidate records** 를 받아 2~4
      단계만 수행한다(`records` 계약).
    - **`assert_no_lookahead`(defense-in-depth)도 본 helper 가 하지 않는다.** Sql
      호출부가 helper 결과에 한 겹 덧씌운다(Fake 는 reference 라 미포함 — 기존
      동작 보존). Caching 은 SQL-backed 운영 경로를 대체하므로 Sql 과 동일하게
      assert 를 덧씌운다.

PIT 안전성 (oracle 설계검토 C1 — load-bearing):
    본 helper 에 넘기는 `records` 는 단건 fetch 의 candidate set 과 **동일**해야
    한다 — 즉 `effective_date <= as_of` 같은 사전 date 필터를 적용하지 **않은**
    전체 vintage. `latest_active_by_key → latest_active_record` 가 chain integrity
    (`_validate_chain_integrity`)를 candidate **전체**에 대해 검증하므로, date>as_of
    인 successor 를 사전에 drop 하면 chain depth/cycle 검사 결과가 단건 경로와
    달라져(한쪽 PITDataCorruptionError, 한쪽 정상) §2.10 byte-동일이 깨진다.
    bulk prime(SqlFinancialRepository.fetch_all_financials_bulk)도 date 필터 없이
    전체 vintage 를 적재하는 이유다.
"""

from __future__ import annotations

from datetime import date

from app.repositories.pit_protocols import (
    CorporateActionRecord,
    FinancialRecord,
    TreasurySharesRecord,
)
from app.services.pit_enforcer import PITEnforcer

__all__ = [
    "resolve_active_actions",
    "resolve_dividends",
    "resolve_financial_periods",
    "resolve_latest_treasury",
]


def resolve_financial_periods(
    records: list[FinancialRecord],
    as_of: date,
    *,
    account: str,
    ifrs_type: str | None,
    max_periods: int,
    enforcer: PITEnforcer,
) -> list[FinancialRecord]:
    """cutoff-필터된 candidate 에서 (account, ifrs_type) 의 fiscal_period 별 latest
    active record 를 결정적 정렬·절사하여 반환 (assert 미포함 — 호출부 책임).

    Args:
        records: 한 code 의 cutoff-필터된 candidate (account 필터 **이전**, 전체
            vintage). SqlFinancialRepository 는 `WHERE code=:code`(+cutoff) 결과,
            Fake 는 `_by_code[code]`(+cutoff) 결과, Caching 은 bulk prime 한
            `_by_code[code]` 를 그대로 넘긴다.
        as_of: PIT 기준 일자.
        account: 정규화된 계정 과목 (그룹화 이전 필터).
        ifrs_type: K-IFRS 연결/별도 (ADR-0005). None 이면 ifrs_type 무관(legacy).
        max_periods: 반환 fiscal_period 최대 개수 (최신순 tail 절사).
        enforcer: chain 해소를 수행할 PITEnforcer (구현체가 보유한 인스턴스 주입).

    Returns:
        fiscal_period 별 latest active record 의 list — (effective_date,
        fiscal_period, id) asc 정렬 후 최신 max_periods 개. 반환 타입은 list —
        Sql 은 호출부가 `tuple(...)` 로 감싸 기존 반환 타입(tuple) 보존, Fake 는
        그대로 반환(기존 list 반환 보존).
    """
    # account + ifrs_type 필터는 그룹화 이전 — 연결/별도가 같은 (account,
    # fiscal_period) 에 공존할 때 그룹 key 충돌 + max_periods 희석 방지 (ADR-0005).
    filtered = [
        r
        for r in records
        if r.account == account
        and (ifrs_type is None or r.ifrs_type == ifrs_type)
    ]
    # fiscal_period 별 latest active (effective_date 기준 정정공시 chain 해소).
    latest_by_period = enforcer.latest_active_by_key(
        filtered, as_of, key=lambda r: r.fiscal_period
    )
    # 결정성 보장 sort — id 가 final tiebreaker (oracle 2 차 리뷰 C6).
    sorted_periods = sorted(
        latest_by_period.values(),
        key=lambda r: (r.effective_date, r.fiscal_period, str(r.id)),
    )
    return sorted_periods[-max_periods:]


def resolve_latest_treasury(
    records: list[TreasurySharesRecord],
    as_of: date,
    *,
    enforcer: PITEnforcer,
) -> TreasurySharesRecord | None:
    """cutoff-필터된 candidate 에서 fiscal_period chain 해소 후 가장 최신 1 건 반환.

    Args:
        records: 한 code 의 cutoff-필터된 candidate (전체 vintage).
        as_of: PIT 기준 일자.
        enforcer: chain 해소를 수행할 PITEnforcer.

    Returns:
        가장 최신 fiscal_period 의 active record. active 가 없으면 None (정식 N/A).
        assert 미포함 — Sql 호출부가 반환 직전 `assert_no_lookahead` 를 덧씌운다.
    """
    latest_by_period = enforcer.latest_active_by_key(
        records, as_of, key=lambda r: r.fiscal_period
    )
    if not latest_by_period:
        return None
    # 가장 최신 fiscal_period — (effective_date, fiscal_period, id) final tiebreaker.
    return max(
        latest_by_period.values(),
        key=lambda r: (r.effective_date, r.fiscal_period, str(r.id)),
    )


def resolve_active_actions(
    records: list[CorporateActionRecord],
    as_of: date,
    *,
    action_types: frozenset[str] | None,
    enforcer: PITEnforcer,
) -> list[CorporateActionRecord]:
    """corporate action raw 에서 `announced_date <= as_of` active 사건을 결정적 정렬.

    `fetch_actions` 의 해소 절차(action_types 필터 + announced_date 기준 정정 chain
    해소 + 결정적 sort)를 Sql/Fake/Caching 단일 출처로 추출(financial/treasury 와
    동형). assert 미포함 — Sql/Caching 호출부가 `assert_no_lookahead(date_of=
    announced_date)` 를 덧씌운다(Fake 는 reference 라 미포함, 기존 동작 보존).

    PIT 의미론(`filter_active_records` 위임):
        announced_date 가 가용성 축 — `announced_date <= as_of` 인 active 사건만
        반환(effective_date 는 권리락일 등 미래일 수 있어 무관). 여러 별개 사건
        (split/dividend/buyback)이 한 record list 에 공존 가능하므로 group key 없이
        record 단위 active 검사(`filter_active_records`, oracle 2 차 NEW-C1).

    Args:
        records: 한 code 의 raw 사건(action_types·해소 이전, 전체 vintage).
        as_of: PIT 기준 일자(announced_date 축).
        action_types: 종류 필터(None 이면 전 종류 — 그룹화 이전 필터).
        enforcer: chain 해소를 수행할 PITEnforcer.

    Returns:
        active 사건 list — (effective_date, created_at, id) asc 정렬. Sql 은 호출부가
        tuple 로 감싸 기존 반환 타입 보존, Fake 는 그대로(기존 list 반환 보존).
    """
    filtered = (
        records
        if action_types is None
        else [r for r in records if r.action_type in action_types]
    )
    active = enforcer.filter_active_records(
        filtered, as_of, date_of=lambda r: r.announced_date,
    )
    # 결정성 sort — effective_date, created_at, id final tiebreaker.
    return sorted(
        active,
        key=lambda r: (r.effective_date, r.created_at, str(r.id)),
    )


def resolve_dividends(
    records: list[CorporateActionRecord],
    as_of: date,
    *,
    enforcer: PITEnforcer,
) -> list[CorporateActionRecord]:
    """cash_dividend 이중 PIT(announced 가용성 축 + effective 발생 축) 해소.

    `SqlDividendRepository.fetch_dividends` / `FakeDividendRepository.fetch_dividends`
    의 해소 절차(announced_date chain 해소 → cash_dividend 필터 → effective<=as_of
    필터 → 결정성 sort)를 Sql/Fake/Caching 단일 출처로 추출(resolve_active_actions
    와 동형). assert 미포함 — Sql/Caching 호출부가 `assert_no_lookahead(date_of=
    announced_date)` 를 덧씌운다(Fake 는 reference 라 미포함 — resolve_active_actions
    와 동일 경계).

    이중 PIT 의미론 (ADR-0035 D6 — DividendRepository Protocol docstring):
        announced_date 축 — `announced_date <= as_of` active 사건만 통과(정보 가용성
        + supersede chain 해소). effective_date 축 — `effective_date <= as_of` 배당락
        실제 발생 사건만 통과(total return 재투자 §2.4 PIT 위반 차단 — 공시됐으나
        배당락 미발생인 미래 배당 제외).

    chain 해소 **후** cash_dividend 필터하는 이유 (chain truncation 방지):
        chain 해소는 **해당 code 의 전체 corporate_action 집합**에서 수행한다
        (cash_dividend 로 먼저 좁힌 부분 차집합이 아님). cash_dividend(orig) 가 다른
        action_type(예: split) successor 로 superseded 되는 cross-action_type chain
        에서, fetch 단계에서 action_type 을 좁히면 successor 가 record_by_id 에 부재
        → `_is_active_one_hop` 가 orig 을 보수적 active 복원 → superseded 된 orig
        부활 오판(§2.4) + `fetch_actions(action_types={"cash_dividend"})` 의 corruption
        검출(전체 chain integrity)과 비대칭(§2.10 hole). 따라서 전체 chain 을 받아
        해소한 뒤 cash_dividend 필터 — `fetch_actions(action_types={"cash_dividend"})`
        한 뒤 effective<=as_of 추가 필터와 byte-동일.

    Args:
        records: 한 code 의 raw 사건(action_type·해소 이전, 전체 vintage — 전체
            corporate_action 집합). SqlDividendRepository 는 `WHERE code=:code`(필터
            없음) 결과, Fake 는 `_by_code[code]`, Caching 은 bulk prime 한
            `_by_code[code]` 를 그대로 넘긴다.
        as_of: PIT 기준 일자(announced·effective 두 축 동시 적용).
        enforcer: announced 축 chain 해소를 수행할 PITEnforcer.

    Returns:
        active cash_dividend list — (effective_date, created_at, id) asc 정렬.
        Sql 은 호출부가 tuple 로 감싸 기존 반환 타입 보존, Fake 는 그대로 반환
        (기존 list 반환 보존).
    """
    # 1축: announced_date PIT + supersede chain 해소 (전체 set 기준 — fetch_actions
    # 동일, cross-action_type chain truncation 방지).
    active = enforcer.filter_active_records(
        records, as_of, date_of=lambda r: r.announced_date,
    )
    # chain 해소 **후** cash_dividend 필터 — fetch_actions(action_types=
    # {"cash_dividend"}) 와 byte-동일. superseded 된 cash_dividend 는 active 에서 이미
    # 빠졌고, non-cash_dividend successor 는 여기서 제거된다.
    cash_dividends = [r for r in active if r.action_type == "cash_dividend"]
    # 2축: effective_date <= as_of — 배당락 사건 발생 필터.
    active_effective = [r for r in cash_dividends if r.effective_date <= as_of]
    # 결정성 sort — effective_date, created_at, id final tiebreaker.
    return sorted(
        active_effective,
        key=lambda r: (r.effective_date, r.created_at, str(r.id)),
    )
