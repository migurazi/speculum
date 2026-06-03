"""공시 사실추출 route 통합 테스트 — POST /api/stocks/{code}/disclosures/extract-facts.

ADR-0031 (AI 공시 사실추출) D1~D6.

테스트 매트릭스:
    1. 정상 — FakeLlmFactExtractor 주입 → 구조화 사실 + 출처 + disclaimer_required
    2. extractor 미주입 → 503 (운영 LLM 미연동 = 정상 상태, D6)
    3. 출력 게이트 fail-closed (D2) — 금지어휘 fake 출력 → 422 + 사실 미반환
    4. 가드레일 — 응답 facts 에 전망/요약/추천 필드 0 (고정 슬롯만, D1/D5)
    5. disclaimer_required=true (D3)
    6. zero-pad 정규화 (code 5930 → 경로 통과)
    7. 차단 응답 본문이 금지어휘를 echo 하지 않음 (B4)
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.disclosure_fact_extraction import (
    ExtractedFacts,
    FakeLlmFactExtractor,
)

_RCEPT_NO = "20240515000123"
_DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240515000123"


def _client(facts: ExtractedFacts | None) -> TestClient:
    extractor = FakeLlmFactExtractor(facts=facts) if facts is not None else None
    app = create_app(fact_extractor=extractor)
    return TestClient(app)


def _post(client: TestClient) -> object:
    return client.post(
        "/api/stocks/005930/disclosures/extract-facts",
        json={"rcept_no": _RCEPT_NO, "disclosure_title": "유상증자결정"},
    )


# =============================================================================
# 1. 정상 path
# =============================================================================

def test_extract_facts_happy_path() -> None:
    facts = ExtractedFacts(
        disclosure_type="유상증자결정",
        amounts=("발행금액 100억원",),
        dates=("청약일 2024-05-20",),
        parties=("주관사 OO증권",),
        quantities=("신주 1,000,000주",),
    )
    with _client(facts) as c:
        res = _post(c)
    assert res.status_code == 200
    body = res.json()
    assert body["facts"]["disclosure_type"] == "유상증자결정"
    assert body["facts"]["amounts"] == ["발행금액 100억원"]
    assert body["facts"]["dates"] == ["청약일 2024-05-20"]
    assert body["facts"]["parties"] == ["주관사 OO증권"]
    assert body["facts"]["quantities"] == ["신주 1,000,000주"]
    assert body["source"]["rcept_no"] == _RCEPT_NO
    assert body["source"]["dart_url"] == _DART_URL
    assert body["disclaimer_required"] is True


# =============================================================================
# 2. extractor 미주입 → 503 (운영 LLM 미연동 = 정상, D6)
# =============================================================================

def test_extract_facts_not_wired_503() -> None:
    with _client(None) as c:
        res = _post(c)
    assert res.status_code == 503


# =============================================================================
# 3. 출력 게이트 fail-closed (D2) — 금지어휘 → 422 + 사실 미반환
# =============================================================================

def test_extract_facts_gate_blocks_forbidden() -> None:
    facts = ExtractedFacts(
        disclosure_type="대형 매수 공시",  # "매수" 금지어휘.
        amounts=("100억원",),
    )
    with _client(facts) as c:
        res = _post(c)
    assert res.status_code == 422
    # 사실 미반환 — 응답에 facts payload 없음.
    assert "facts" not in res.json()


def test_extract_facts_gate_blocks_english_forbidden() -> None:
    facts = ExtractedFacts(
        disclosure_type="계약",
        parties=("Buy recommendation party",),  # "Buy" 금지어휘 (단어 경계).
    )
    with _client(facts) as c:
        res = _post(c)
    assert res.status_code == 422


# =============================================================================
# 4. 가드레일 — 응답 facts 에 전망/요약/추천 필드 0 (고정 슬롯만)
# =============================================================================

def test_extract_facts_no_outlook_fields() -> None:
    facts = ExtractedFacts(disclosure_type="단일판매계약")
    with _client(facts) as c:
        res = _post(c)
    body = res.json()
    # facts 는 정확히 5 슬롯만 — 전망/요약/추천/평가 필드 0.
    assert set(body["facts"].keys()) == {
        "disclosure_type", "amounts", "dates", "parties", "quantities",
    }
    # 응답 top-level 도 facts / source / disclaimer_required 3 키만.
    assert set(body.keys()) == {"facts", "source", "disclaimer_required"}
    # source 는 rcept_no / dart_url 2 키만.
    assert set(body["source"].keys()) == {"rcept_no", "dart_url"}


# =============================================================================
# 5. disclaimer_required=true (D3)
# =============================================================================

def test_extract_facts_disclaimer_required() -> None:
    with _client(ExtractedFacts(disclosure_type="계약")) as c:
        res = _post(c)
    assert res.json()["disclaimer_required"] is True


# =============================================================================
# 6. zero-pad 정규화 (code 5930 → 경로 통과)
# =============================================================================

def test_extract_facts_zero_pad() -> None:
    facts = ExtractedFacts(disclosure_type="계약")
    app = create_app(fact_extractor=FakeLlmFactExtractor(facts=facts))
    with TestClient(app) as c:
        res = c.post(
            "/api/stocks/5930/disclosures/extract-facts",
            json={"rcept_no": _RCEPT_NO, "disclosure_title": "x"},
        )
    assert res.status_code == 200


# =============================================================================
# 7. 차단 응답 본문이 금지어휘를 echo 하지 않음 (B4)
# =============================================================================

def test_extract_facts_block_no_echo() -> None:
    facts = ExtractedFacts(disclosure_type="유망한 종목 공시")  # "유망" 금지어휘.
    with _client(facts) as c:
        res = _post(c)
    assert res.status_code == 422
    assert "유망" not in res.text


# =============================================================================
# 8. 결정적 — 같은 입력에 같은 응답
# =============================================================================

def test_extract_facts_deterministic() -> None:
    facts = ExtractedFacts(disclosure_type="유상증자결정", amounts=("100억원",))
    with _client(facts) as c:
        r1 = _post(c).json()
        r2 = _post(c).json()
    assert r1 == r2
