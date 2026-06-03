"""공시 사실추출 service 테스트 — ADR-0031 D1~D6.

테스트 매트릭스:
    1. 정상 — FakeLlmFactExtractor 로 구조화 사실 추출 + 출처/디스클레이머 첨부
    2. 출력 게이트 fail-closed (D2) — 금지어휘 포함 fake 출력 → FactExtractionBlocked
       (disclosure_type / amounts / dates / parties / quantities 각 슬롯)
    3. structured 스키마 (D1/D5) — 전망/요약/추천 필드 부재 (ExtractedFacts 슬롯 고정)
    4. 출처 강제 (D3) — rcept_no + DART viewer URL
    5. disclaimer_required 항상 True (D3)
    6. 결정적 — 같은 입력에 같은 결과
    7. FactExtractionBlocked 가 금지어휘를 message 에 echo 하지 않음 (B4)
"""

from __future__ import annotations

import pytest

from app.services.disclosure_fact_extraction import (
    ExtractedFacts,
    FactExtractionBlocked,
    FactExtractionResult,
    FakeLlmFactExtractor,
    extract_disclosure_facts,
)

_RCEPT_NO = "20240515000123"


def _extract(facts: ExtractedFacts) -> FactExtractionResult:
    extractor = FakeLlmFactExtractor(facts=facts)
    return extract_disclosure_facts(
        extractor,
        rcept_no=_RCEPT_NO,
        disclosure_title="유상증자결정",
    )


# =============================================================================
# 1. 정상 path — 구조화 사실 + 출처 + 디스클레이머
# =============================================================================

def test_extract_happy_path() -> None:
    facts = ExtractedFacts(
        disclosure_type="유상증자결정",
        amounts=("발행금액 100억원",),
        dates=("청약일 2024-05-20",),
        parties=("주관사 ○○증권",),
        quantities=("신주 1,000,000주",),
    )
    result = _extract(facts)
    assert result.facts == facts
    assert result.rcept_no == _RCEPT_NO
    assert result.dart_url == (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240515000123"
    )
    assert result.disclaimer_required is True


# =============================================================================
# 2. 출력 게이트 fail-closed (D2) — 금지어휘 → 차단
# =============================================================================

@pytest.mark.parametrize(
    "facts",
    [
        # disclosure_type 슬롯에 금지어휘 "매수".
        ExtractedFacts(disclosure_type="대형 매수 공시"),
        # amounts 슬롯에 금지어휘 "유망".
        ExtractedFacts(disclosure_type="단일판매계약", amounts=("유망한 100억원",)),
        # dates 슬롯에 금지어휘 "추천".
        ExtractedFacts(disclosure_type="계약", dates=("추천 전망 2024-05-20",)),
        # parties 슬롯에 금지어휘 "매수".
        ExtractedFacts(disclosure_type="계약", parties=("매수 상대방",)),
        # quantities 슬롯에 금지어휘 "Buy" (영문 단어 경계).
        ExtractedFacts(disclosure_type="계약", quantities=("Buy 1000주",)),
    ],
)
def test_output_gate_fail_closed(facts: ExtractedFacts) -> None:
    """추출 텍스트 중 하나라도 금지어휘 포함 시 결과 미반환 (fail-closed)."""
    extractor = FakeLlmFactExtractor(facts=facts)
    with pytest.raises(FactExtractionBlocked):
        extract_disclosure_facts(
            extractor,
            rcept_no=_RCEPT_NO,
            disclosure_title="x",
        )


def test_output_gate_reports_field_path() -> None:
    """차단 시 field_path 가 audit 가능 (어느 슬롯이 금지어휘인지)."""
    facts = ExtractedFacts(
        disclosure_type="계약",
        amounts=("정상 금액",),
        dates=("매수 일자",),  # dates[0] 차단.
    )
    extractor = FakeLlmFactExtractor(facts=facts)
    with pytest.raises(FactExtractionBlocked) as exc_info:
        extract_disclosure_facts(
            extractor, rcept_no=_RCEPT_NO, disclosure_title="x",
        )
    assert exc_info.value.field_path == "dates[0]"


def test_output_gate_message_no_echo() -> None:
    """차단 message 가 금지어휘를 echo 하지 않음 (forbidden_words B4 정책)."""
    facts = ExtractedFacts(disclosure_type="대형 유망주")
    extractor = FakeLlmFactExtractor(facts=facts)
    with pytest.raises(FactExtractionBlocked) as exc_info:
        extract_disclosure_facts(
            extractor, rcept_no=_RCEPT_NO, disclosure_title="x",
        )
    assert "유망" not in str(exc_info.value)


# =============================================================================
# 3. structured 스키마 (D1/D5) — 전망/요약/추천 필드 부재
# =============================================================================

def test_schema_has_no_outlook_summary_recommendation_fields() -> None:
    """ExtractedFacts 는 고정 사실 슬롯만 — 전망/요약/추천 필드 0 (구조적 차단)."""
    field_names = set(ExtractedFacts.__dataclass_fields__.keys())
    assert field_names == {
        "disclosure_type",
        "amounts",
        "dates",
        "parties",
        "quantities",
    }
    # 자유서술/요약/전망/추천 슬롯 부재 확인.
    forbidden_slots = {
        "summary", "outlook", "recommendation", "opinion",
        "forecast", "advice", "rating", "score",
    }
    assert field_names.isdisjoint(forbidden_slots)


# =============================================================================
# 4. 출처 강제 (D3)
# =============================================================================

def test_source_attached() -> None:
    result = _extract(ExtractedFacts(disclosure_type="계약"))
    assert result.rcept_no == _RCEPT_NO
    assert _RCEPT_NO in result.dart_url
    assert result.dart_url.startswith("https://dart.fss.or.kr/")


# =============================================================================
# 5. disclaimer_required 항상 True (D3)
# =============================================================================

def test_disclaimer_always_required() -> None:
    result = _extract(ExtractedFacts(disclosure_type="계약"))
    assert result.disclaimer_required is True
    # FactExtractionResult 의 default 도 True.
    assert FactExtractionResult(
        facts=ExtractedFacts(disclosure_type="x"),
        rcept_no="1",
        dart_url="u",
    ).disclaimer_required is True


# =============================================================================
# 6. 결정적
# =============================================================================

def test_deterministic() -> None:
    facts = ExtractedFacts(
        disclosure_type="유상증자결정",
        amounts=("100억원",),
    )
    r1 = _extract(facts)
    r2 = _extract(facts)
    assert r1 == r2


# =============================================================================
# 7. 빈 사실 슬롯 허용 (사실 부재 = 빈 tuple)
# =============================================================================

def test_empty_fact_slots_allowed() -> None:
    result = _extract(ExtractedFacts(disclosure_type="기타경영사항"))
    assert result.facts.amounts == ()
    assert result.facts.dates == ()
    assert result.facts.parties == ()
    assert result.facts.quantities == ()
