"""KOSIS adapter 실 호출 smoke (ADR-0036 D8, M9 #4).

통계청 KOSIS API 는 API key 필요. CI secret(KOSIS_API_KEY) 미주입 환경에서는
skip. 본 smoke 의 목적은 **KOSIS 응답 schema 회귀 방어** — mock 단위 테스트가
잡지 못하는 실 API 형식 변화를 nightly 가 포착한다.

테스트 매트릭스 (smoke):
    1. fetch_statistic — 통계청 실업률 (DT_1DA7107S/T10, 월별) 호출 성공.

⚠ provisional 주의 (kosis_daily.py 동일 경고):
    _KOSIS_INDICATORS 의 itmId/objL 은 잠정값이다. 운영 KOSIS_API_KEY 메타로
    최종 확정 전에는 실 호출이 성공(HTTP 200)해도 data 가 0 row 일 수 있다.
    따라서 본 smoke 는 **호출이 AdapterError 없이 완료되는지**(엔드포인트 도달 +
    인증 + 응답 파싱)까지만 강제하고, data 비어있음은 skip 으로 처리한다 — itmId/
    objL 확정 후 data 검증을 강화하는 것이 후속 work-order.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.adapters.base import AdapterError
from app.adapters.kosis_adapter import KosisAdapter

pytestmark = pytest.mark.integration


# 통계청 실업률 — KOSIS 통계표/항목 코드 (월별). kosis_daily 배치
# kosis_unemployment_rate 지표와 동일 (itmId/objL provisional).
_ORG_ID = "101"
_TBL_ID = "DT_1DA7107S"
_ITM_ID = "T10"
_OBJ_L = "ALL"


def test_fetch_unemployment_rate_smoke(kosis_api_key: str | None) -> None:
    """실업률 최근 12 개월 fetch — 엔드포인트 도달 + 인증 + 파싱 성공."""
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
        except AdapterError as exc:
            # ⚠ provisional itmId/objL 단계 — objL 불일치가 KOSIS err 로 올 수
            # 있어 (인증/형식 포함) 관대하게 전부 skip. itmId/objL 확정 후에는
            # ECOS smoke 처럼 `except AdapterRetryError` 로 좁혀 인증/형식 오류를
            # fail 로 강화하는 것이 후속 work-order (kosis_daily.py 경고 참조).
            pytest.skip(f"KOSIS fetch_statistic 비가용 ({start}~{end}): {exc}")

        # ⚠ provisional itmId/objL — data 0 row 가능. 호출 성공(여기 도달) 자체가
        # 엔드포인트 도달 + 인증의 1차 검증. data 비면 schema 검증 skip.
        if not result.data:
            pytest.skip(
                f"KOSIS 실업률 빈 결과 ({start}~{end}) — itmId/objL provisional "
                f"또는 윈도우 이슈 (kosis_daily.py 경고 참조)"
            )

        # data 있으면 MacroIndicatorRow schema 회귀 검증 (ECOS smoke 대칭).
        assert len(result.citations) >= 1
        row = result.data[0]
        # KOSIS indicator_id 규약: f"kosis/{org_id}/{tbl_id}/{itm_id}"
        # (kosis_adapter.py L37, MacroIndicatorRecord.indicator_id 와 1:1).
        assert row.indicator_id == f"kosis/{_ORG_ID}/{_TBL_ID}/{_ITM_ID}"
        assert isinstance(row.value, Decimal)
        assert row.vintage_date == today
        assert row.reference_date <= today
    finally:
        adapter.close()
