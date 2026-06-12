"""정정 미반영 진단 — M5 #4b (T-M5-04b, ADR-0033 D6).

frozen Screen Run snapshot 의 result_codes 종목 중, frozen 시점(`snapshot.as_of`)
**이후 정정공시된** financial 이 있는 종목을 **사실로** 식별한다 (8 기둥 §2.1
Fidelity — "이 결과는 N건 정정 후행"). 본 모듈은 판정·해석 문구를 일절 생성하지
않는다 — 정정 후행 종목코드 목록(`restated_codes`)과 건수(`count`) 같은 **사실만**
산출하고, "위험"·"부정확" 등 평가 어휘는 쓰지 않는다 (§2.2 No Advice / §2.7
Observation). client 표시 layer (#6) 가 중립 톤으로 그 사실을 노출한다.

정정 판정 (정정 = 같은 회계기간 group 에 frozen 전후 record 공존):
    financial 정정공시는 같은 ``(account, fiscal_period, ifrs_type)`` group 에 새
    row(새 effective_date) 가 추가되고 옛 row 의 ``superseded_by`` 가 새 row.id 로
    설정된다 (ADR-0009 supersede chain / ADR-0020 append-only). frozen as_of 기준
    "정정 후행" 판정은:

    - 그 group 에 ``effective_date <= snapshot.as_of`` 인 record(frozen 시점에
      사용된 데이터)와 ``effective_date > snapshot.as_of`` 인 record(frozen
      **이후** 공시된 정정) 가 **둘 다 존재** → 그 종목은 "정정 후행".
    - frozen 이후 첫 공시(같은 group 에 frozen 이전 record 가 없음) 는 **정정이
      아니라 신규 분기** → 제외 (group 에 as_of 이전 record 가 있어야 정정).

known limit (구조적 false-negative — ADR-0033 D6 / M5_PLAN §3 #4b):
    본 진단은 result_codes *내* 종목만 검사한다. frozen 시점 필터에서 탈락했으나
    이후 정정으로 편입될 종목(result_codes *밖*)은 탐지하지 못한다(구조적
    false-negative). 입력이 frozen run 의 result_codes 라는 닫힌 집합이기 때문이며,
    필터 탈락 종목까지 검사하려면 전체 universe × 정정 chain 재실행이 필요하다(범위
    외). financial factor 를 쓰지 않은 pack 의 run 이라도 그 종목에 정정이 있으면
    표시한다 — 보수적 사실 고지(정정 사실 자체는 종목 단위로 관측 가능).

read-only:
    `FinancialRepository.fetch_restatement_history(code)` 는 SELECT 만 수행한다
    (as_of/fiscal_period 미지정 = 전체 vintage). 어떠한 UPDATE/INSERT 도 없음
    (ADR-0020 append-only 불변식 미변경).

관련:
- ADR-0033 D6 — 정정 미반영 진단(result_codes 내 한정, false-negative known limit)
- M5_PLAN §3 #4 T-M5-04b — 정정 미반영 진단(chain-diff)
- `app/repositories/pit_protocols.py` — fetch_restatement_history(전체 vintage)
- `app/services/data_freshness.py` — #4a 신선도 진단(자매 모듈, 동일 사실-고지 스타일)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.repositories.pit_protocols import FinancialRepository
    from app.services.screen_run import ScreenRunSnapshot

__all__ = [
    "RestatementLag",
    "assess_restatement_lag",
]


@dataclass(frozen=True, slots=True)
class RestatementLag:
    """정정 미반영 진단 결과 — 사실만 (판정 라벨 0).

    Attributes:
        restated_codes: frozen as_of 이후 정정공시가 후행된 종목코드(정렬·dedup).
            그 종목의 어느 한 ``(account, fiscal_period, ifrs_type)`` group 이라도
            as_of 전후 record 가 공존하면 포함된다.
        count: ``len(restated_codes)`` — 정정 후행 종목 수(사실).
    """

    restated_codes: tuple[str, ...]
    count: int


def assess_restatement_lag(
    snapshot: ScreenRunSnapshot,
    financial_repo: FinancialRepository,
) -> RestatementLag:
    """frozen run 의 result_codes 중 as_of 이후 정정 후행 종목을 사실로 식별.

    각 code 에 대해 전체 vintage(`fetch_restatement_history(code)`, as_of/
    fiscal_period 미지정)를 ``(account, fiscal_period, ifrs_type)`` 로 group 화하고,
    어느 group 이라도 ``effective_date <= snapshot.as_of`` record AND
    ``effective_date > snapshot.as_of`` record 가 둘 다 있으면 그 code 를 정정
    후행으로 본다(즉시 다음 code 로 — 한 group 만 확인되면 충분).

    Args:
        snapshot: frozen Screen Run snapshot. `result_codes` 와 `as_of` 만 사용.
        financial_repo: 정정 이력 조회 repository (read-only SELECT).

    Returns:
        RestatementLag — 정정 후행 종목코드(정렬·dedup) + 건수. 판정 문구 0.
    """
    as_of = snapshot.as_of
    restated: set[str] = set()

    for code in snapshot.result_codes:
        # 전체 vintage(as_of/fiscal_period 미지정 = 현재까지 알려진 모든 정정 포함).
        # frozen 이후 공시된 정정까지 보려면 as_of 필터를 걸지 않아야 한다.
        history = financial_repo.fetch_restatement_history(code)

        # (account, fiscal_period, ifrs_type) group 별로 as_of 전/후 record 존재 여부.
        groups: dict[tuple[str, str, str], tuple[bool, bool]] = {}
        for r in history:
            key = (r.account, r.fiscal_period, r.ifrs_type)
            before, after = groups.get(key, (False, False))
            if r.effective_date <= as_of:
                before = True
            else:
                after = True
            groups[key] = (before, after)

            # 정정 후행 = 같은 group 에 frozen 전후 record 공존. before+after 둘 다
            # True 인 group 이 하나라도 있으면 그 code 는 정정 후행 — 즉시 다음 code.
            if before and after:
                restated.add(code)
                break

    sorted_codes = tuple(sorted(restated))
    return RestatementLag(restated_codes=sorted_codes, count=len(sorted_codes))
