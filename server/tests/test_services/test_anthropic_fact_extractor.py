"""운영 LLM 추출기 단위 테스트 — AnthropicFactExtractor (ADR-0031 D6).

client= 주입(fake)으로 SDK 미설치 환경에서도 동작한다(D6 경계 검증). 실제 네트워크
호출 0 — fake client 가 Messages API 응답을 모사한다.

테스트 매트릭스:
    1. 정상 — tool_use 응답 → ExtractedFacts 전 슬롯 매핑
    2. 요청 형태 — model / strict tool / tool_choice 강제 / system prompt
    3. 빈 슬롯 → 빈 tuple (없는 사실 0)
    4. tool_use 부재 → AnthropicFactExtractorError
    5. 잘못된 tool 이름 → error
    6. input 비-dict → error
    7. 슬롯 항목 비-str → error (조용한 형변환 금지, Fidelity)
    8. disclosure_type 비-str → error
    9. build_default_fact_extractor — pytest 중 항상 None (env 무관)
    10. 게이트 연동 — 운영 추출기 결과가 출력 게이트(D2)를 그대로 통과/차단
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_llm_fact_extraction_enabled
from app.services.disclosure_fact_extraction import (
    ExtractedFacts,
    FactExtractionBlocked,
    extract_disclosure_facts,
)
from app.services.llm.anthropic_fact_extractor import _TOOL_NAME as TOOL_NAME
from app.services.llm.anthropic_fact_extractor import (
    AnthropicFactExtractor,
    AnthropicFactExtractorError,
    build_default_fact_extractor,
)

# =============================================================================
# Fake Anthropic client — Messages API 응답 모사 (네트워크 0)
# =============================================================================

class _FakeBlock:
    """tool_use / text 블록 모사 — 실제 SDK 블록의 .type/.name/.input 접근만 흉내."""

    def __init__(self, *, type: str, name: str | None = None, input: Any = None) -> None:
        self.type = type
        self.name = name
        self.input = input


class _FakeMessage:
    def __init__(self, content: list[_FakeBlock]) -> None:
        self.content = content


class _FakeMessages:
    """create(**kwargs) 호출 인자를 capture 에 기록하고 고정 응답을 반환."""

    def __init__(self, response: _FakeMessage, capture: dict[str, Any]) -> None:
        self._response = response
        self._capture = capture

    def create(self, **kwargs: Any) -> _FakeMessage:
        self._capture.clear()
        self._capture.update(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeMessage, capture: dict[str, Any]) -> None:
        self.messages = _FakeMessages(response, capture)


def _extractor(payload: Any, *, tool_name: str = TOOL_NAME) -> tuple[
    AnthropicFactExtractor, dict[str, Any]
]:
    """payload 를 tool_use input 으로 반환하는 fake client 기반 추출기 + capture."""
    capture: dict[str, Any] = {}
    msg = _FakeMessage([_FakeBlock(type="tool_use", name=tool_name, input=payload)])
    return AnthropicFactExtractor(client=_FakeClient(msg, capture)), capture


# =============================================================================
# 1. 정상 path — tool_use 응답 → ExtractedFacts
# =============================================================================

def test_extract_facts_happy_path() -> None:
    extractor, _ = _extractor({
        "disclosure_type": "유상증자결정",
        "amounts": ["발행금액 100억원"],
        "dates": ["청약일 2024-05-20"],
        "parties": ["주관사 OO증권"],
        "quantities": ["신주 1,000,000주"],
    })
    facts = extractor.extract_facts(
        disclosure_title="유상증자결정", disclosure_context=""
    )
    assert facts == ExtractedFacts(
        disclosure_type="유상증자결정",
        amounts=("발행금액 100억원",),
        dates=("청약일 2024-05-20",),
        parties=("주관사 OO증권",),
        quantities=("신주 1,000,000주",),
    )


# =============================================================================
# 2. 요청 형태 — strict tool + tool_choice 강제 + system prompt + model
# =============================================================================

def test_request_forces_structured_tool() -> None:
    extractor, capture = _extractor({
        "disclosure_type": "계약",
        "amounts": [], "dates": [], "parties": [], "quantities": [],
    })
    extractor.extract_facts(disclosure_title="단일판매계약", disclosure_context="맥락")

    # 본 도구 호출을 강제 — 자유응답 차단(D1).
    assert capture["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    # 도구는 strict + 고정 슬롯 스키마 + additionalProperties=False.
    tool = capture["tools"][0]
    assert tool["name"] == TOOL_NAME
    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "disclosure_type", "amounts", "dates", "parties", "quantities",
    }
    # 기본 모델 + system prompt(Fidelity 지시) 존재. 제목·맥락이 user content 에 포함.
    assert capture["model"] == "claude-opus-4-8"
    assert "사실만" in capture["system"]
    assert "단일판매계약" in capture["messages"][0]["content"]
    assert "맥락" in capture["messages"][0]["content"]


def test_model_override() -> None:
    capture: dict[str, Any] = {}
    msg = _FakeMessage([_FakeBlock(
        type="tool_use", name=TOOL_NAME,
        input={"disclosure_type": "계약", "amounts": [], "dates": [],
               "parties": [], "quantities": []},
    )])
    extractor = AnthropicFactExtractor(
        client=_FakeClient(msg, capture), model="claude-sonnet-4-6"
    )
    extractor.extract_facts(disclosure_title="x", disclosure_context="")
    assert capture["model"] == "claude-sonnet-4-6"


# =============================================================================
# 3. 빈 슬롯 → 빈 tuple
# =============================================================================

def test_empty_slots_become_empty_tuples() -> None:
    extractor, _ = _extractor({
        "disclosure_type": "계약",
        "amounts": [], "dates": [], "parties": [], "quantities": [],
    })
    facts = extractor.extract_facts(disclosure_title="계약", disclosure_context="")
    assert facts.amounts == ()
    assert facts.dates == ()
    assert facts.parties == ()
    assert facts.quantities == ()


def test_missing_array_keys_default_empty() -> None:
    # strict 스키마가 보장하지만, 누락 키도 방어적으로 빈 tuple.
    extractor, _ = _extractor({"disclosure_type": "계약"})
    facts = extractor.extract_facts(disclosure_title="계약", disclosure_context="")
    assert facts.amounts == ()


# =============================================================================
# 4~8. 예상 외 응답 → AnthropicFactExtractorError (인프라 오류, 게이트와 무관)
# =============================================================================

def test_no_tool_use_block_raises() -> None:
    capture: dict[str, Any] = {}
    msg = _FakeMessage([_FakeBlock(type="text", input=None)])
    extractor = AnthropicFactExtractor(client=_FakeClient(msg, capture))
    with pytest.raises(AnthropicFactExtractorError):
        extractor.extract_facts(disclosure_title="계약", disclosure_context="")


def test_wrong_tool_name_raises() -> None:
    extractor, _ = _extractor(
        {"disclosure_type": "계약"}, tool_name="some_other_tool"
    )
    with pytest.raises(AnthropicFactExtractorError):
        extractor.extract_facts(disclosure_title="계약", disclosure_context="")


def test_non_dict_input_raises() -> None:
    extractor, _ = _extractor("not-a-dict")
    with pytest.raises(AnthropicFactExtractorError):
        extractor.extract_facts(disclosure_title="계약", disclosure_context="")


def test_non_str_slot_item_raises() -> None:
    # 조용한 형변환 금지 — 비-str 항목은 raise (Fidelity §2.1).
    extractor, _ = _extractor({
        "disclosure_type": "계약", "amounts": [123], "dates": [],
        "parties": [], "quantities": [],
    })
    with pytest.raises(AnthropicFactExtractorError):
        extractor.extract_facts(disclosure_title="계약", disclosure_context="")


def test_non_str_disclosure_type_raises() -> None:
    extractor, _ = _extractor({
        "disclosure_type": 42, "amounts": [], "dates": [],
        "parties": [], "quantities": [],
    })
    with pytest.raises(AnthropicFactExtractorError):
        extractor.extract_facts(disclosure_title="계약", disclosure_context="")


def test_non_array_slot_raises() -> None:
    extractor, _ = _extractor({
        "disclosure_type": "계약", "amounts": "발행금액 100억원", "dates": [],
        "parties": [], "quantities": [],
    })
    with pytest.raises(AnthropicFactExtractorError):
        extractor.extract_facts(disclosure_title="계약", disclosure_context="")


# =============================================================================
# 9. build_default_fact_extractor — pytest 중 항상 None (env 무관)
# =============================================================================

def test_build_default_returns_none_under_pytest(monkeypatch: Any) -> None:
    # OS 환경에 키가 있어도 pytest 중에는 None — 테스트 환경 격리(config 가드 철학).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-ignored")
    assert build_default_fact_extractor() is None


# =============================================================================
# 11. get_llm_fact_extraction_enabled — opt-in 플래그 단위 테스트 (ADR-0031 D6)
# =============================================================================

def test_llm_fact_extraction_enabled_default_false(monkeypatch: Any) -> None:
    """미설정 시 False — 키 존재만으로는 활성 안 됨 (우발 활성 방지)."""
    monkeypatch.delenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", raising=False)
    assert get_llm_fact_extraction_enabled() is False


def test_llm_fact_extraction_enabled_one(monkeypatch: Any) -> None:
    """'1' 설정 시 True."""
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "1")
    assert get_llm_fact_extraction_enabled() is True


def test_llm_fact_extraction_enabled_true(monkeypatch: Any) -> None:
    """'true' 설정 시 True (대소문자 무관)."""
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "true")
    assert get_llm_fact_extraction_enabled() is True


def test_llm_fact_extraction_enabled_yes(monkeypatch: Any) -> None:
    """'yes' 설정 시 True."""
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "yes")
    assert get_llm_fact_extraction_enabled() is True


def test_llm_fact_extraction_enabled_true_uppercase(monkeypatch: Any) -> None:
    """'TRUE' 대문자도 True."""
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "TRUE")
    assert get_llm_fact_extraction_enabled() is True


def test_llm_fact_extraction_enabled_zero(monkeypatch: Any) -> None:
    """'0' 설정 시 False — 명시 비활성."""
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "0")
    assert get_llm_fact_extraction_enabled() is False


def test_llm_fact_extraction_enabled_false_str(monkeypatch: Any) -> None:
    """'false' 설정 시 False."""
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "false")
    assert get_llm_fact_extraction_enabled() is False


def test_llm_fact_extraction_enabled_empty(monkeypatch: Any) -> None:
    """빈 문자열도 False — 미설정과 동일."""
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "")
    assert get_llm_fact_extraction_enabled() is False


# =============================================================================
# 12. build_default_fact_extractor — enable 플래그 off 시 키 있어도 None
#     (pytest 환경에서 pytest 분기가 먼저 발동하므로, 게이트 로직 자체는
#      get_llm_fact_extraction_enabled 단위 테스트 + 소스 주석으로 검증)
# =============================================================================

def test_build_default_returns_none_even_with_key_under_pytest(monkeypatch: Any) -> None:
    """pytest 분기로 인해 키 + enable 플래그 양쪽 설정 시에도 None.

    운영 환경(비pytest)에서 enable 플래그 off → None 의 로직은
    build_default_fact_extractor 소스의 get_llm_fact_extraction_enabled() 게이트와
    test_llm_fact_extraction_enabled_* 단위 테스트로 보증한다.
    pytest 중에는 pytest 분기가 먼저 발동해 이 경로에 도달하지 않는다.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")
    monkeypatch.setenv("SPECULUM_ENABLE_LLM_FACT_EXTRACTION", "1")
    # pytest 분기로 여전히 None — 테스트 환경 격리(config 가드 철학).
    assert build_default_fact_extractor() is None


# =============================================================================
# 10. 게이트 연동 — 운영 추출기 결과가 출력 게이트(D2)를 통과/차단
# =============================================================================

def test_operational_extractor_flows_through_gate_clean() -> None:
    # 깨끗한 사실 → 게이트 통과 → 출처(D3) + disclaimer 첨부.
    extractor, _ = _extractor({
        "disclosure_type": "유상증자결정", "amounts": ["100억원"],
        "dates": [], "parties": [], "quantities": [],
    })
    result = extract_disclosure_facts(
        extractor, rcept_no="20240515000123", disclosure_title="유상증자결정"
    )
    assert result.facts.disclosure_type == "유상증자결정"
    assert result.rcept_no == "20240515000123"
    assert result.disclaimer_required is True


def test_operational_extractor_blocked_by_gate_on_forbidden() -> None:
    # 금지어휘를 포함한 추출(hallucination/가치판단)은 게이트(D2) fail-closed 차단.
    extractor, _ = _extractor({
        "disclosure_type": "대형 매수 추천 공시",  # "매수/추천" 금지어휘.
        "amounts": [], "dates": [], "parties": [], "quantities": [],
    })
    with pytest.raises(FactExtractionBlocked):
        extract_disclosure_facts(
            extractor, rcept_no="20240515000123", disclosure_title="공시"
        )
