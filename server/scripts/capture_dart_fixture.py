"""V1 골든 fixture 캡처 — 실 DART 재무제표(005930) → JSON (네트워크 1회성).

V0 의 `krx_ohlcv_005930.json`(실 OHLCV 캡처) 과 동형 — V1 스크리너 acceptance
e2e(`tests/test_db/test_v1_screen_e2e.py`)가 외부 호출 없이 실데이터를 관통
검증하도록, **실 DART 응답을 한 번 캡처해 fixture 로 박제**한다.

핵심: fixture 값은 consumer 기대값이 아니라 **adapter 출력(실 DART)** 에서 유도 —
그래서 e2e 가 tautology(합성 fixture 자기검증)가 아니다. ROADMAP_v2 §3 의
"ready = 실데이터 e2e ≥1" 게이트가 이 캡처 위에 선다.

대상: 삼성전자(005930, DART corp_code=00126380) 의 trailing 5분기 연결(CFS)
재무제표. TTM(4분기) EPS standalone 변환(B1)에 직전 분기까지 5개 연속 필요.

실행(DART_API_KEY 필요, server/.env 보유):
    cd server
    python -m scripts.capture_dart_fixture

출력: tests/fixtures/captured/dart_financials_005930.json (재실행 시 덮어씀).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from app.adapters.base import IfrsType
from app.adapters.dart_adapter import DartAdapter

# server/.env 의 DART_API_KEY 를 os.environ 으로 로드(어댑터가 env 직접 조회).
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("capture_dart_fixture")

# 삼성전자 — KRX 005930 ↔ DART corp_code 00126380(공지된 안정 매핑, corpCode.zip
# 부트스트랩 없이 단일 종목 캡처라 하드코딩. 다종목 확장 시 CorpCodeBootstrap 사용).
_CODE = "005930"
_CORP_CODE = "00126380"
# trailing 5분기 — B1 standalone 변환에 직전 분기(2023Q1)까지 필요(5개 연속).
_PERIODS: tuple[tuple[int, int], ...] = (
    (2023, 1), (2023, 2), (2023, 3), (2023, 4), (2024, 1),
)
_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "captured" / "dart_financials_005930.json"
)


def main() -> int:
    adapter = DartAdapter()
    batch_id = uuid4()
    captured: list[dict[str, object]] = []

    for year, quarter in _PERIODS:
        result = adapter.fetch_financial_statement(
            code=_CODE,
            corp_code=_CORP_CODE,
            fiscal_year=year,
            fiscal_quarter=quarter,
            ifrs_type=IfrsType.CFS,
            batch_id=batch_id,
        )
        for row in result.data:
            captured.append({
                "code": row.code,
                "fiscal_year": row.fiscal_year,
                "fiscal_quarter": row.fiscal_quarter,
                "effective_date": row.effective_date.isoformat(),
                "account": row.account,
                "value": str(row.value),  # Decimal 무손실 문자열 왕복.
                "unit": row.unit,
                "ifrs_type": row.ifrs_type.value,  # "consolidated".
                "rcept_no": row.rcept_no,
                "currency": row.currency,
                "effective_date_precise": row.effective_date_precise,
            })
        logger.info(
            "  %dQ%d: %d rows (eps=%s)",
            year, quarter, len(result.data),
            next((r.value for r in result.data if r.account == "basic_eps"), "N/A"),
        )

    if not captured:
        logger.error("캡처 0건 — DART 응답 비어있음(키/네트워크/종목 확인).")
        return 1

    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(
        json.dumps({"rows": captured}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("캡처 완료 — %d rows → %s", len(captured), _FIXTURE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
