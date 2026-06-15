"""공유 lineage helper — `code → code_lineage_id` 결정적 placeholder UUID.

T13 Phase B 의 stocks_master 합류 전까지 prices / market_cap / financials /
treasury / dividend(CA) 가 같은 lineage 해소를 공유하기 위한 단일 출처.

**위치 근거**: 본 helper 는 `batch/` (krx_daily·survivorship_backfill·dart_daily)
와 `app/adapters/` (fsc_dividend_adapter) 양쪽에서 호출된다. adapter(저 layer)
가 batch(고 layer)를 import 하면 layering inversion + `batch/__init__.py` 가
adapter 를 다시 import 하는 순환 위험이 있어, 양쪽이 안전하게 의존할 수 있는
`app/services/` 에 둔다(batch·adapter 모두 이미 app.services 에 의존).

**공식 불변 (절대조건)**:
    `uuid5(NAMESPACE_OID, f"lineage|{code}")`

    이 공식으로 이미 영속화된 row 의 `code_lineage_id` 와 byte-동일해야 한다 —
    공식을 한 글자라도 바꾸면(namespace·prefix·구분자) 신규 row 의 lineage_id 가
    기존 데이터와 어긋나 같은 lineage 시계열 fetch 가 깨진다(전 데이터 손상).
    따라서 본 함수는 4 경로(krx_daily·survivorship_backfill·dart_daily·
    fsc_dividend_adapter)에 흩어져 있던 동일 인라인 공식을 **그대로** 추출한 것이며,
    일관성 테스트가 이 공식을 잠가 미래 divergence 를 방어한다.
"""

from __future__ import annotations

from uuid import NAMESPACE_OID, UUID, uuid5


def lineage_id_for_code(code: str) -> UUID:
    """code → code_lineage_id (결정적 placeholder).

    공식은 `uuid5(NAMESPACE_OID, f"lineage|{code}")` — 기존 영속 데이터의
    code_lineage_id 와 byte-동일해야 하므로 변경 금지(모듈 docstring 참조).
    """
    return uuid5(NAMESPACE_OID, f"lineage|{code}")
