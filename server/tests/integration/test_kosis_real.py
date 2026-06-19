"""KOSIS adapter 실 호출 smoke (ADR-0036 D8, M9 #4).

통계청 KOSIS API 는 API key 필요. CI secret(KOSIS_API_KEY) 미주입 환경에서는
skip. 본 smoke 의 목적은 **KOSIS 응답 schema 회귀 방어** — mock 단위 테스트가
잡지 못하는 실 API 형식 변화를 nightly 가 포착한다.

테스트 매트릭스 (smoke):
    1. fetch_statistic — 통계청 실업률 (경제활동인구조사 DT_1DA7001S/T80, 월별,
       성별 계 objL1=0) 호출 + 실데이터 반환.

엔드포인트 주의: KOSIS 통계자료 조회 정본은 `Param/statisticsParameterData.do`
(kosis_adapter `_KOSIS_API_ENDPOINT`). 과거 `statisticsData.do?method=getList`
오용으로 모든 호출이 err=20 이었음 — 2026-06-18 실측으로 endpoint + tblId/itmId/
objL1 전부 확정·검증 완료. 따라서 본 smoke 는 **데이터 반환까지 강제**한다(빈 결과·
영구 AdapterError 는 fail). 일시적 네트워크 오류(AdapterRetryError)만 skip.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.adapters.base import AdapterRetryError
from app.adapters.kosis_adapter import KosisAdapter

pytestmark = pytest.mark.integration


# 통계청 실업률 — 경제활동인구조사 월별 표 (라이브 검증 2026-06-18). kosis_daily
# 배치 kosis_unemployment_rate 지표와 동일.
_ORG_ID = "101"
_TBL_ID = "DT_1DA7001S"
_ITM_ID = "T80"
_OBJ_L = "0"


def test_fetch_unemployment_rate_smoke(kosis_api_key: str | None) -> None:
    """실업률 최근 12 개월 fetch — 엔드포인트 도달 + 인증 + 파싱 + 실데이터."""
    if kosis_api_key is None:
        pytest.skip("KOSIS_API_KEY env 미설정 — secret 미주입 환경")

    adapter = KosisAdapter(api_key=kosis_api_key)
    today = date.today()
    end = f"{today.year:04d}{today.month:02d}"
    start = f"{today.year - 1:04d}{today.month:02d}"

    try:
        try:
            result = adapter.fetch_statistic(
                org_id=_ORG_ID,
                tbl_id=_TBL_ID,
                itm_id=_ITM_ID,
                obj_l=_OBJ_L,
                prd_se="M",
                start_prd=start,
                end_prd=end,
                batch_id=uuid4(),
                observed_date=today,
            )
        except AdapterRetryError as exc:
            # 일시적 장애(HTTP 5xx/timeout/connect)만 관대 skip — 영구 AdapterError
            # (인증/형식/통계표 오류)는 config 가 검증됐으므로 fail 로 전파한다.
            pytest.skip(f"KOSIS 일시 비가용 ({start}~{end}): {exc}")

        # config 검증 완료 — 실데이터 반환을 강제(빈 결과 = 회귀).
        assert result.data, f"KOSIS 실업률 빈 결과 ({start}~{end}) — 회귀 의심"
        assert len(result.citations) >= 1

        # MacroIndicatorRow schema 회귀 검증 (ECOS smoke 대칭).
        row = result.data[0]
        # KOSIS indicator_id 규약: f"kosis/{org_id}/{tbl_id}/{itm_id}"
        # (kosis_adapter.py, MacroIndicatorRecord.indicator_id 와 1:1).
        assert row.indicator_id == f"kosis/{_ORG_ID}/{_TBL_ID}/{_ITM_ID}"
        assert isinstance(row.value, Decimal)
        assert row.vintage_date == today
        assert row.reference_date <= today
    finally:
        adapter.close()
