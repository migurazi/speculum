"""DART adapter 실 호출 smoke (ADR-0003 D8, T43).

DART OpenAPI 는 API key 필요. CI secret 미주입 환경에서는 skip.

테스트 매트릭스 (smoke):
    1. fetch_financial_statement — 005930 의 2 년 전 사업보고서 (Q4 / 연결)

DART API 의 rate limit (20,000/일) 보호 — smoke 1 개만.

연도 선택 사유 (oracle T43-A 리뷰 C4 반영):
    `today.year - 1` 사업보고서 (Q4) 의 신고기한 = 사업연도 종료 후 90 일
    (다음해 3 월 31 일). nightly 가 1~3 월 초중순 실행 시 직전 사업연도
    Q4 미공시 → fail 폭증. `today.year - 2` 로 항상 공시 완료된 가장 최근
    안정 fiscal year 사용. smoke 의 의미 ("DART 응답 schema OK") 는 동일.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.adapters.base import IfrsType
from app.adapters.dart_adapter import DartAdapter

pytestmark = pytest.mark.integration


# 삼성전자 DART 회사코드 (8자리). 본 smoke 는 hardcoded — corp_code mapping
# 의 정확성은 별도 service 단위 테스트 (corp_code_mapping.py) 책임.
_SAMSUNG_CORP_CODE = "00126380"


def test_fetch_financial_statement_smoke(
    dart_api_key: str | None,
) -> None:
    """삼성전자 (year - 2) Q4 사업보고서 (연결) — net_income account 응답."""
    if dart_api_key is None:
        pytest.skip("DART_API_KEY env 미설정 — secret 미주입 환경")

    adapter = DartAdapter(api_key=dart_api_key)
    # year - 2 = 항상 공시 완료. (year - 1 은 1~3월 초 fail 위험 — module
    # docstring 의 oracle C4 참조.)
    fiscal_year = date.today().year - 2
    result = adapter.fetch_financial_statement(
        code="005930",
        corp_code=_SAMSUNG_CORP_CODE,
        fiscal_year=fiscal_year,
        fiscal_quarter=4,
        ifrs_type=IfrsType.CONSOLIDATED,
        batch_id=uuid4(),
    )
    # DART standardized account list 는 보통 50+. 10 은 안전 마진 — 미매핑
    # account drop 후에도 회귀 신호.
    assert len(result.data) > 10
    # citation 7-tuple 의무 (ADR-0002 D3).
    assert len(result.citations) >= 1
