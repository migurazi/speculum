"""정정 미반영 진단 — M5 #4b (T-M5-04b, ADR-0033 D6) 서비스 테스트.

테스트 매트릭스:
    1. 정정 후행 — 같은 (account, fiscal_period) group 에 as_of 이전 + 이후
       effective_date record 공존 → restated_codes 포함.
    2. 정정 없음 — as_of 이후 record 0(모든 record 가 as_of 이하) → restated_codes 빈.
    3. frozen 후 신규 분기 — 같은 group 에 as_of 이전 record 없음, 이후만 →
       제외(정정 아님, 신규 분기).
    4. result_codes 밖 종목 정정 → 미탐지(구조적 false-negative known limit 확인).
    5. ifrs_type 다른 group 분리 — 연결/별도 별개 chain, 한쪽만 정정.
    6. count == len(restated_codes), restated_codes 정렬·dedup.

판정·해석 문구 0 — 종목코드·건수 사실만 검증(§2.2 / §2.7).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.repositories.fakes import FakeFinancialRepository
from app.repositories.pit_protocols import FinancialRecord
from app.services.restatement_lag import RestatementLag, assess_restatement_lag
from app.services.screen_run import ScreenRunBuilder

_AS_OF = date(2024, 5, 7)
_CODE_A = "005930"
_CODE_B = "000660"
_ACCOUNT = "net_income_consolidated_ifrs"


def _record(
    *,
    code: str,
    fiscal_period: str,
    effective_date: date,
    account: str = _ACCOUNT,
    ifrs_type: str = "consolidated",
    value: str = "1000",
    superseded_by: UUID | None = None,
) -> FinancialRecord:
    return FinancialRecord(
        id=uuid4(),
        code=code,
        code_lineage_id=UUID(int=int(code)),
        effective_date=effective_date,
        fiscal_period=fiscal_period,
        account=account,
        value=Decimal(value),
        unit="krw",
        ifrs_type=ifrs_type,
        citation_id=uuid4(),
        superseded_by=superseded_by,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _snapshot(*result_codes: str, as_of: date = _AS_OF):
    """result_codes·as_of 만 의미 있는 최소 ScreenRunSnapshot."""
    return ScreenRunBuilder.build(
        run_id=uuid4(),
        conditions=[{"factor": "per:ttm-consolidated-ifrs", "op": "<", "value": "10"}],
        selected_factors=["per:ttm-consolidated-ifrs"],
        as_of=as_of,
        result_codes=list(result_codes),
        data_versions={},
    )


# =============================================================================
# 1. 정정 후행 — as_of 전후 record 공존
# =============================================================================

def test_restated_when_correction_after_as_of() -> None:
    """같은 (account, fiscal_period) 에 as_of 이전 원공시 + 이후 정정 → 후행."""
    records = [
        # 원 공시 (as_of 이전)
        _record(
            code=_CODE_A, fiscal_period="2023Q4",
            effective_date=date(2024, 3, 30),
        ),
        # 정정 공시 (as_of 이후)
        _record(
            code=_CODE_A, fiscal_period="2023Q4",
            effective_date=date(2024, 6, 1), value="900",
        ),
    ]
    repo = FakeFinancialRepository(records)
    result = assess_restatement_lag(_snapshot(_CODE_A), repo)

    assert isinstance(result, RestatementLag)
    assert result.restated_codes == (_CODE_A,)
    assert result.count == 1


# =============================================================================
# 2. 정정 없음 — as_of 이후 record 0
# =============================================================================

def test_no_restatement_when_all_before_as_of() -> None:
    """모든 vintage 가 as_of 이하 → 정정 후행 없음."""
    records = [
        _record(code=_CODE_A, fiscal_period="2023Q3", effective_date=date(2023, 11, 14)),
        _record(code=_CODE_A, fiscal_period="2023Q4", effective_date=date(2024, 3, 30)),
    ]
    repo = FakeFinancialRepository(records)
    result = assess_restatement_lag(_snapshot(_CODE_A), repo)

    assert result.restated_codes == ()
    assert result.count == 0


# =============================================================================
# 3. frozen 후 신규 분기 — 같은 group 에 as_of 이전 record 없음 (정정 아님)
# =============================================================================

def test_new_quarter_after_as_of_is_not_restatement() -> None:
    """as_of 이후 첫 공시(같은 group 에 이전 record 없음) = 신규 분기 → 제외.

    2024Q1 은 frozen 이후 처음 공시된 분기(이전 vintage 없음) — 정정이 아니라
    새 데이터의 정상 도착. 같은 group 에 frozen 이전 record 가 있어야 정정이다.
    """
    records = [
        # frozen 이전 정상 분기 (정정 없음)
        _record(code=_CODE_A, fiscal_period="2023Q4", effective_date=date(2024, 3, 30)),
        # frozen 이후 신규 분기 (같은 group 에 이전 record 없음)
        _record(code=_CODE_A, fiscal_period="2024Q1", effective_date=date(2024, 5, 15)),
    ]
    repo = FakeFinancialRepository(records)
    result = assess_restatement_lag(_snapshot(_CODE_A), repo)

    assert result.restated_codes == ()
    assert result.count == 0


# =============================================================================
# 4. result_codes 밖 종목 정정 → 미탐지 (구조적 false-negative known limit)
# =============================================================================

def test_correction_outside_result_codes_is_false_negative() -> None:
    """result_codes 밖 종목(_CODE_B)의 정정은 탐지 못함 — 구조적 false-negative.

    ADR-0033 D6 / M5_PLAN §3 #4b 의 known limit 을 사실로 명시 검증: 입력은
    result_codes 라는 닫힌 집합이므로 그 밖의 종목 정정은 보이지 않는다.
    """
    records = [
        # _CODE_B 는 정정 후행이지만 result_codes 에 없음
        _record(code=_CODE_B, fiscal_period="2023Q4", effective_date=date(2024, 3, 30)),
        _record(
            code=_CODE_B, fiscal_period="2023Q4",
            effective_date=date(2024, 6, 1), value="900",
        ),
    ]
    repo = FakeFinancialRepository(records)
    # result_codes 에는 _CODE_A 만 (정정 없음).
    result = assess_restatement_lag(_snapshot(_CODE_A), repo)

    assert _CODE_B not in result.restated_codes
    assert result.restated_codes == ()
    assert result.count == 0


# =============================================================================
# 5. ifrs_type 별개 group — 연결/별도 분리 (한쪽만 정정)
# =============================================================================

def test_ifrs_type_groups_are_separate() -> None:
    """연결/별도는 별개 group — 한 ifrs_type 만 정정돼도 그 종목은 후행."""
    records = [
        # consolidated: as_of 이전만 (정정 없음)
        _record(
            code=_CODE_A, fiscal_period="2023Q4", ifrs_type="consolidated",
            effective_date=date(2024, 3, 30),
        ),
        # separate: as_of 전후 공존 (정정 후행)
        _record(
            code=_CODE_A, fiscal_period="2023Q4", ifrs_type="separate",
            effective_date=date(2024, 3, 30),
        ),
        _record(
            code=_CODE_A, fiscal_period="2023Q4", ifrs_type="separate",
            effective_date=date(2024, 6, 1), value="900",
        ),
    ]
    repo = FakeFinancialRepository(records)
    result = assess_restatement_lag(_snapshot(_CODE_A), repo)

    assert result.restated_codes == (_CODE_A,)
    assert result.count == 1


# =============================================================================
# 6. count / 정렬·dedup
# =============================================================================

def test_restated_codes_sorted_and_count() -> None:
    """여러 종목 정정 후행 → 정렬·dedup, count == len."""
    records = [
        # _CODE_A 정정 후행
        _record(code=_CODE_A, fiscal_period="2023Q4", effective_date=date(2024, 3, 30)),
        _record(
            code=_CODE_A, fiscal_period="2023Q4",
            effective_date=date(2024, 6, 1), value="900",
        ),
        # _CODE_B 정정 후행
        _record(code=_CODE_B, fiscal_period="2023Q4", effective_date=date(2024, 3, 30)),
        _record(
            code=_CODE_B, fiscal_period="2023Q4",
            effective_date=date(2024, 6, 1), value="900",
        ),
    ]
    repo = FakeFinancialRepository(records)
    # 입력 순서 비정렬 — 출력은 정렬돼야.
    result = assess_restatement_lag(_snapshot(_CODE_A, _CODE_B), repo)

    assert result.restated_codes == (_CODE_B, _CODE_A)  # 000660 < 005930
    assert result.count == 2
    assert result.count == len(result.restated_codes)


def test_boundary_record_at_as_of_is_before_not_after() -> None:
    """effective_date == as_of 인 record 는 frozen 시점 사용 데이터(before).

    경계(`<=` inclusive) — as_of 당일 공시는 frozen 에 포함된 데이터로 취급,
    그것만으로는 정정 후행이 아니다(이후 record 가 따로 있어야 후행).
    """
    records = [
        # effective_date == as_of (frozen 시점 데이터 = before)
        _record(code=_CODE_A, fiscal_period="2023Q4", effective_date=_AS_OF),
    ]
    repo = FakeFinancialRepository(records)
    result = assess_restatement_lag(_snapshot(_CODE_A), repo)

    assert result.restated_codes == ()
    assert result.count == 0
