"""공시 metadata route 통합 테스트 — GET /api/stocks/{code}/disclosures (ADR-0026).

테스트 매트릭스:
    1. 정상 — corp_code 매핑 + 공시 wire (제목·접수일·dart_url 3 필드만)
    2. as_of PIT 필터 (ADR-0026 D4) — 미래 공시 숨김
    3. limit 적용 (최신순 앞 N 개)
    4. corp_code 매핑 부재 → 404
    5. 공시 부재 (status 013) → 200 + 빈 list
    6. DART 장애 (AdapterError) → 502
    7. zero-pad 정규화 (code 5930 → 005930)
    8. 가드레일 — 응답에 본문/요약/분류/중요도 필드 0 (3 키만)
    9. report_name 의 forbidden_words substring 통과 (EXTERNAL_QUOTE)
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.adapters.base import AdapterError
from app.adapters.dart_adapter import DartAdapter
from app.main import create_app
from app.services.corp_code_mapping import CorpCodeMapping

# KRX 005930 (삼성전자) → DART 00126380.
_MAPPING = CorpCodeMapping.from_dict({"005930": "00126380"})


def _disclosure_row(
    *,
    report_nm: str,
    rcept_dt: str,
    rcept_no: str,
) -> dict[str, Any]:
    return {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "report_nm": report_nm,
        "rcept_no": rcept_no,
        "flr_nm": "삼성전자",
        "rcept_dt": rcept_dt,
        "rm": "",
    }


def _make_adapter(rows: list[dict[str, Any]], *, status: str = "000") -> DartAdapter:
    """MockTransport 기반 DartAdapter — list.json 응답 stub."""
    payload: dict[str, Any] = {"status": status, "message": "정상"}
    if status == "000":
        payload["list"] = rows

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(_handler), timeout=5.0)
    return DartAdapter(http_client=client, api_key="test_key")


def _client(adapter: DartAdapter, *, mapping: CorpCodeMapping = _MAPPING) -> TestClient:
    app = create_app(dart_adapter=adapter, corp_code_mapping=mapping)
    return TestClient(app)


# =============================================================================
# 1. 정상 path
# =============================================================================

def test_disclosures_happy_path() -> None:
    adapter = _make_adapter([
        _disclosure_row(
            report_nm="분기보고서 (2024.03)",
            rcept_dt="20240515",
            rcept_no="20240515000123",
        ),
    ])
    with _client(adapter) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == "005930"
    # as_of 는 AsOfPolicy 가 영업일로 snap 할 수 있음 (12-31 휴장 가능) — header
    # 의 정규화 결과와 일치만 확인.
    assert body["as_of"] == res.headers["X-AsOf"]
    assert len(body["disclosures"]) == 1
    d = body["disclosures"][0]
    assert d["report_name"] == "분기보고서 (2024.03)"
    assert d["rcept_date"] == "2024-05-15"
    assert d["dart_url"] == (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240515000123"
    )


# =============================================================================
# 2. as_of PIT 필터 — 미래 공시 숨김 (ADR-0026 D4)
# =============================================================================

def test_disclosures_pit_hides_future() -> None:
    """as_of 이후 접수 공시는 비표시 (look-ahead 0)."""
    adapter = _make_adapter([
        _disclosure_row(report_nm="과거공시", rcept_dt="20240310", rcept_no="20240310000001"),
        _disclosure_row(report_nm="미래공시", rcept_dt="20240820", rcept_no="20240820000001"),
    ])
    with _client(adapter) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-05-01")
    assert res.status_code == 200
    names = [d["report_name"] for d in res.json()["disclosures"]]
    # 5-01 이전 접수만 — 미래공시 (8-20) 제외.
    assert names == ["과거공시"]


# =============================================================================
# 3. limit
# =============================================================================

def test_disclosures_limit() -> None:
    rows = [
        _disclosure_row(
            report_nm=f"공시{i}",
            rcept_dt=f"202403{10 + i:02d}",
            rcept_no=f"202403{10 + i:02d}000001",
        )
        for i in range(5)
    ]
    adapter = _make_adapter(rows)
    with _client(adapter) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-12-31&limit=2")
    assert res.status_code == 200
    disclosures = res.json()["disclosures"]
    assert len(disclosures) == 2
    # 최신순 앞 2 개 — 3-14, 3-13.
    assert disclosures[0]["rcept_date"] == "2024-03-14"
    assert disclosures[1]["rcept_date"] == "2024-03-13"


# =============================================================================
# 4. corp_code 매핑 부재 → 404
# =============================================================================

def test_disclosures_corp_code_not_found() -> None:
    adapter = _make_adapter([])
    # 000660 은 매핑에 없음.
    with _client(adapter) as c:
        res = c.get("/api/stocks/000660/disclosures?as_of=2024-12-31")
    assert res.status_code == 404


def test_disclosures_empty_mapping_404() -> None:
    """매핑 미주입 (빈 매핑) — 모든 종목 404."""
    adapter = _make_adapter([])
    app = create_app(dart_adapter=adapter)  # corp_code_mapping 미주입.
    with TestClient(app) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-12-31")
    assert res.status_code == 404


# =============================================================================
# 5. 공시 부재 (status 013) → 200 빈 list
# =============================================================================

def test_disclosures_no_data_empty() -> None:
    adapter = _make_adapter([], status="013")
    with _client(adapter) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-12-31")
    assert res.status_code == 200
    assert res.json()["disclosures"] == []


# =============================================================================
# 6. DART 장애 → 502
# =============================================================================

def test_disclosures_adapter_error_502() -> None:
    class _FailingAdapter:
        def fetch_disclosure_list(self, **kwargs: Any) -> Any:
            raise AdapterError("DART down")

    adapter = _FailingAdapter()
    app = create_app(dart_adapter=adapter, corp_code_mapping=_MAPPING)
    with TestClient(app) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-12-31")
    assert res.status_code == 502


# =============================================================================
# 7. zero-pad 정규화
# =============================================================================

def test_disclosures_zero_pad() -> None:
    adapter = _make_adapter([
        _disclosure_row(report_nm="공시", rcept_dt="20240515", rcept_no="20240515000001"),
    ])
    with _client(adapter) as c:
        res = c.get("/api/stocks/5930/disclosures?as_of=2024-12-31")
    assert res.status_code == 200
    assert res.json()["code"] == "005930"


# =============================================================================
# 8. 가드레일 — 본문/요약/분류/중요도 필드 0 (3 키만)
# =============================================================================

def test_disclosures_no_forbidden_extra_fields() -> None:
    adapter = _make_adapter([
        _disclosure_row(report_nm="공시", rcept_dt="20240515", rcept_no="20240515000001"),
    ])
    with _client(adapter) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-12-31")
    d = res.json()["disclosures"][0]
    # 정확히 3 필드만 — 본문/요약/분류라벨/중요도 0.
    assert set(d.keys()) == {"report_name", "rcept_date", "dart_url"}


# =============================================================================
# 9. report_name forbidden_words substring 통과 (EXTERNAL_QUOTE)
# =============================================================================

def test_disclosures_report_name_external_quote() -> None:
    """제목에 금지 어휘 substring 이 들어가도 redact/block 안 됨 (ADR-0026 D2).

    "매수" 같은 어휘가 회사 공시 제목에 포함돼도 EXTERNAL_QUOTE scope 라 통과.
    """
    adapter = _make_adapter([
        _disclosure_row(
            report_nm="주식매수선택권부여에관한신고",
            rcept_dt="20240515",
            rcept_no="20240515000001",
        ),
    ])
    with _client(adapter) as c:
        res = c.get("/api/stocks/005930/disclosures?as_of=2024-12-31")
    assert res.status_code == 200
    assert (
        res.json()["disclosures"][0]["report_name"]
        == "주식매수선택권부여에관한신고"
    )
