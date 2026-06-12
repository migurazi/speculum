"""ECOS adapter 실 호출 smoke (ADR-0003 D8, T64).

한국은행 ECOS API 는 API key 필요. CI secret(ECOS_API_KEY) 미주입 환경에서는
skip. 본 smoke 의 목적은 **ECOS 응답 schema 회귀 방어** — mock 단위 테스트
(test_adapters/test_ecos_adapter.py) 가 잡지 못하는 실 API 형식 변화(StatisticSearch
키·row 구조·DATA_VALUE/TIME 형식)를 nightly 가 포착한다.

테스트 매트릭스 (smoke):
    1. fetch_statistic — 한국은행 기준금리 (722Y001/0101000, 월별) 최근 12 개월.

ECOS rate limit 보호 — smoke 1 개만. 지표는 ecos_daily._ECOS_INDICATORS 의
ecos_base_rate 와 동일 (배치가 실제 fetch 하는 지표를 검증).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.adapters.base import AdapterRetryError
from app.adapters.ecos_adapter import EcosAdapter, make_indicator_id

pytestmark = pytest.mark.integration


# 한국은행 기준금리 — ECOS 통계표/항목 코드 (월별). db_field_provider 의
# ecos_base_rate 및 ecos_daily 배치 지표와 동일.
_BASE_RATE_STAT = "722Y001"
_BASE_RATE_ITEM = "0101000"


def test_fetch_base_rate_smoke(ecos_api_key: str | None) -> None:
    """기준금리 최근 12 개월 fetch — MacroIndicatorRow schema 응답."""
    if ecos_api_key is None:
        pytest.skip("ECOS_API_KEY env 미설정 — secret 미주입 환경")

    adapter = EcosAdapter(api_key=ecos_api_key)
    today = date.today()
    # 최근 12 개월 윈도우 — 월별(M) cycle 은 YYYYMM 형식. today 를 단일 변수로
    # 계산해 fetch(observed_date) + assert(vintage_date) 양쪽에 재사용 — 자정
    # 경계 race 없음 (adapter 가 observed_date 를 vintage_date 로 그대로 복사).
    end = f"{today.year:04d}{today.month:02d}"
    start = f"{today.year - 1:04d}{today.month:02d}"

    try:
        try:
            result = adapter.fetch_statistic(
                stat_code=_BASE_RATE_STAT,
                item_code=_BASE_RATE_ITEM,
                cycle="M",
                start=start,
                end=end,
                batch_id=uuid4(),
                observed_date=today,
            )
        except AdapterRetryError as exc:
            # 일시 장애(타임아웃/서버 5xx/ECOS ERROR-500·600)만 skip. 인증
            # (ERROR-100)·형식 오류는 AdapterError 로 전파 → nightly fail (over-skip
            # 회피 — schema/인증 회귀를 묵살하지 않음). AdapterRetryError 는
            # AdapterError 의 서브클래스라 비-retry AdapterError 는 여기 안 잡힘.
            pytest.skip(f"ECOS 일시 장애 ({start}~{end}): {exc}")

        # ECOS INFO-200 (해당 기간 데이터 없음) 은 data=() citations=() — 윈도우
        # 문제로 간주해 skip (schema 검증 의미 없음). 기준금리는 매월 존재가 정상.
        if not result.data:
            pytest.skip(
                f"ECOS 기준금리 빈 결과 ({start}~{end}) — 윈도우/일시 이슈"
            )

        # citation 7-tuple 의무 (ADR-0002 D3) — data 있으면 citation 1 개.
        assert len(result.citations) >= 1

        # MacroIndicatorRow schema 회귀 검증 — indicator_id 규약 + value Decimal.
        row = result.data[0]
        assert row.indicator_id == make_indicator_id(
            _BASE_RATE_STAT, _BASE_RATE_ITEM,
        )
        assert isinstance(row.value, Decimal)
        # 기준금리 합리 범위 [0, 100]% — 단위 오파싱(bp↔%, 지수 혼입 등) 회귀를
        # 포착. 0 단순 비교는 schema 깨짐을 못 잡으므로 상한도 검증.
        assert Decimal("0") <= row.value <= Decimal("100")
        # vintage_date = observed_date (관측 근사, ECOS 공표일 미제공).
        assert row.vintage_date == today
        # reference_date <= observed_date (미래 row 금지).
        assert row.reference_date <= today
    finally:
        adapter.close()
