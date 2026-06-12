"""Anthropic SDK 기반 운영 LlmFactExtractor — ADR-0031 D6 운영 연동.

`disclosure_fact_extraction.py` 는 protocol + fake + 게이트 파이프라인만 보유하고
**실제 LLM SDK 를 import 하지 않는다**(D6). 본 모듈이 그 운영 구현체다 — Anthropic
Messages API 를 호출해 공시에서 구조화 사실을 추출하고 `ExtractedFacts`(고정 스키마)
로 반환한다. 게이트(D2)·출처(D3)·디스클레이머는 호출자(`extract_disclosure_facts`)
가 본 추출 결과 위에 그대로 적용 — 본 모듈은 "추출" 만 담당하고 정책은 모른다.

설계 핵심 (ADR-0031 D1/D6 + CONCEPT §2.1 Fidelity / §2.7 경계):

- **구조화 사실만 — strict tool use 로 강제** (D1). 자유생성 요약이 아니라, 고정
  JSON 스키마(공시유형·금액·일자·당사자·수량 슬롯)를 `tools` 로 선언하고
  `tool_choice={"type":"tool", ...}` 로 그 도구 호출을 **강제**한다. `strict: True`
  로 스키마 일탈을 차단 — LLM 은 정해진 슬롯만 채울 수 있고 자유서술/전망/추천을
  끼워 넣을 곳이 구조적으로 없다. 게이트(D2)는 그 위의 2 차 방어선.
- **system prompt = "사실만, 원문에 있는 것만"** (§2.1 Fidelity). 추론·전망·가치
  판단·요약을 금지하고 공시 원문에 명시된 사실만 슬롯에 옮기도록 지시. 부재 시 빈
  list. hallucination 1 차 억제(2 차는 게이트).
- **SDK 는 lazy import** (D6 경계). 본 모듈 import 만으로 `anthropic` 를 끌어오지
  않는다 — 운영 구현체를 주입(`client=`)하는 테스트는 SDK 미설치로도 동작하고,
  운영 부팅만 실제 SDK 를 요구한다(`pip install -e .[llm]`).

stateless. 같은 입력에 대해 결정성은 보장하지 않는다(LLM 호출) — 이는 fake
(`FakeLlmFactExtractor`)와의 차이이며, 게이트는 매 응답을 검사하므로 비결정성이
정책 우회로 이어지지 않는다.
"""

from __future__ import annotations

import os
from typing import Any

from app.services.disclosure_fact_extraction import ExtractedFacts

__all__ = [
    "AnthropicFactExtractor",
    "AnthropicFactExtractorError",
    "build_default_fact_extractor",
]

# 운영 기본 모델 — 가장 유능한 Claude 모델(claude-api 스킬 기본). 환경변수
# `ANTHROPIC_MODEL` 로 override 가능(`config.get_anthropic_model`).
_DEFAULT_MODEL = "claude-opus-4-8"

# 추출 응답 max_tokens — 고정 슬롯 1 회 tool call 이라 작게 충분. 과길이 출력 방어.
_DEFAULT_MAX_TOKENS = 1024

# 강제 호출할 도구 이름 — 응답에서 본 이름의 tool_use 블록을 파싱.
_TOOL_NAME = "record_disclosure_facts"

# 구조화 사실 추출 도구 스키마 — `ExtractedFacts` 슬롯과 1:1 (D1). strict=True 로
# 스키마 일탈 차단. additionalProperties=False 로 임의 필드(전망/요약 등) 주입 봉쇄.
_EXTRACT_TOOL: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "공시 원문에 명시된 구조화 사실만 기록한다. 요약·전망·추천·가치판단을 "
        "생성하지 말 것 — 원문에 적힌 사실만 해당 슬롯에 옮긴다. 슬롯에 해당하는 "
        "사실이 없으면 빈 배열로 둔다."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "disclosure_type": {
                "type": "string",
                "description": (
                    "공시유형 — 원문에 명시된 사실 분류(예: '유상증자결정', "
                    "'단일판매·공급계약체결'). 자체 중요도/호재 라벨이 아님."
                ),
            },
            "amounts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "금액 사실(예: '발행금액 100억원'). 없으면 빈 배열.",
            },
            "dates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "일자 사실(예: '청약일 2024-05-20'). 없으면 빈 배열.",
            },
            "parties": {
                "type": "array",
                "items": {"type": "string"},
                "description": "당사자 사실(예: '계약상대방 OO전자'). 없으면 빈 배열.",
            },
            "quantities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "수량 사실(예: '신주 1,000,000주'). 없으면 빈 배열.",
            },
        },
        "required": [
            "disclosure_type",
            "amounts",
            "dates",
            "parties",
            "quantities",
        ],
        "additionalProperties": False,
    },
}

# system prompt — Fidelity(§2.1) 강제. "사실만, 원문에 있는 것만, 슬롯만".
_SYSTEM_PROMPT = (
    "당신은 한국 공시(DART) 원문에서 구조화 사실만 추출하는 도구입니다. "
    "반드시 record_disclosure_facts 도구를 호출해 결과를 기록하십시오.\n\n"
    "규칙:\n"
    "1. 공시 원문에 명시된 사실만 옮긴다. 추론·전망·예측·가치판단·투자의견을 "
    "절대 생성하지 않는다.\n"
    "2. '호재/악재/유망/긍정적/매수/매도' 같은 평가 어휘를 사용하지 않는다.\n"
    "3. 금액·일자·당사자·수량 슬롯에 해당 사실이 없으면 빈 배열로 둔다 — "
    "없는 사실을 지어내지 않는다.\n"
    "4. 요약 문장을 만들지 않는다 — 사실 단편만 각 슬롯에 넣는다."
)


class AnthropicFactExtractorError(RuntimeError):
    """운영 추출기 구성/응답 오류 — SDK 미설치·키 부재·예상 외 응답 형태.

    forbidden-words 게이트(D2)와 무관한 **인프라/구성 오류**다. 호출자(route)는
    이를 catch 하지 않으므로 500 으로 전파된다(운영 미구성/장애 = 내부 오류). 사실
    추출 결과의 정책 위반(게이트)과는 경로가 다르다(그쪽은 422).
    """


class AnthropicFactExtractor:
    """Anthropic Messages API 로 공시 → `ExtractedFacts` 추출 (LlmFactExtractor 구현).

    `LlmFactExtractor` Protocol(`extract_facts(*, disclosure_title,
    disclosure_context) -> ExtractedFacts`)을 구현한다. strict tool use 로 고정
    스키마를 강제(D1)하고, system prompt 로 Fidelity(§2.1)를 1 차 지시한다.

    Args:
        api_key: Anthropic API 키. None 이면 `ANTHROPIC_API_KEY` env var 사용.
            `client` 를 주입하면 무시(테스트).
        model: 사용할 Claude 모델. 기본 claude-opus-4-8.
        max_tokens: 응답 토큰 상한. 고정 슬롯 1 회 호출이라 작게 충분(기본 1024).
        timeout: HTTP 호출 timeout(초). 기본 30.
        client: 주입된 Anthropic client(테스트용 fake / 운영 reuse). 주입 시
            **SDK import 를 건너뛴다** — fake client 로 SDK 미설치 환경에서도 단위
            테스트가 가능(D6 경계). None 이면 본 생성자가 `anthropic.Anthropic`
            을 lazy import 해 생성한다(미설치/키부재면 즉시 raise — fail-loud).

    Raises:
        AnthropicFactExtractorError: client 미주입 + (SDK 미설치 | 키 부재).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        timeout: float = 30.0,
        client: Any = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens

        if client is not None:
            # 주입(테스트 fake / 운영 reuse) — SDK import 경로를 타지 않는다.
            self._client = client
            return

        # 운영 경로 — SDK 를 lazy import. 본 모듈 import 만으로는 anthropic 을
        # 끌어오지 않으므로(D6 경계), 주입 테스트는 SDK 미설치로도 동작한다.
        try:
            import anthropic
        except ImportError as exc:
            raise AnthropicFactExtractorError(
                "anthropic SDK 가 설치되지 않았습니다 — 운영 LLM 추출을 사용하려면 "
                "`pip install -e .[llm]` 로 설치하거나, client= 로 구현체를 주입하세요."
            ) from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
        if not key:
            raise AnthropicFactExtractorError(
                "ANTHROPIC_API_KEY 가 설정되지 않았습니다 — 운영 LLM 추출 키를 "
                "설정하거나, client= 로 구현체를 주입하세요."
            )
        self._client = anthropic.Anthropic(api_key=key, timeout=timeout)

    def extract_facts(
        self,
        *,
        disclosure_title: str,
        disclosure_context: str,
    ) -> ExtractedFacts:
        """공시 제목·맥락 → 구조화 사실. strict tool use 로 고정 스키마 강제(D1).

        자유서술 반환은 스키마(`_EXTRACT_TOOL`) + `tool_choice` 강제로 차단된다.
        게이트(D2)는 본 결과를 SYSTEM scope 로 검사하는 호출자의 책임.

        Raises:
            AnthropicFactExtractorError: 응답에 기대한 tool_use 가 없거나 형태가
                예상 외일 때(인프라 오류, 게이트와 무관).
        """
        user_content = _build_user_content(disclosure_title, disclosure_context)
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_PROMPT,
            tools=[_EXTRACT_TOOL],
            # 본 도구 호출을 강제 — 모델이 자유응답 대신 반드시 스키마를 채운다.
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": user_content}],
        )
        return _parse_tool_use(message)


def _build_user_content(disclosure_title: str, disclosure_context: str) -> str:
    """추출 입력 텍스트 조립 — 제목 + (선택) 맥락. EXTERNAL_QUOTE(원문)."""
    if disclosure_context:
        return f"공시 제목: {disclosure_title}\n\n공시 맥락:\n{disclosure_context}"
    return f"공시 제목: {disclosure_title}"


def _parse_tool_use(message: Any) -> ExtractedFacts:
    """Messages 응답에서 강제 tool_use 블록을 찾아 `ExtractedFacts` 로 변환.

    `tool_choice` 강제로 정상 응답엔 반드시 `_TOOL_NAME` tool_use 가 1 개 있다.
    부재/형태이상은 인프라 오류로 간주해 raise(게이트와 다른 경로).
    """
    content = getattr(message, "content", None) or []
    for block in content:
        if getattr(block, "type", None) == "tool_use" and \
                getattr(block, "name", None) == _TOOL_NAME:
            payload = getattr(block, "input", None)
            if not isinstance(payload, dict):
                raise AnthropicFactExtractorError(
                    "tool_use input 이 dict 가 아닙니다(예상 외 응답)."
                )
            return _facts_from_payload(payload)
    raise AnthropicFactExtractorError(
        "응답에서 record_disclosure_facts tool_use 를 찾지 못했습니다(예상 외 응답)."
    )


def _facts_from_payload(payload: dict[str, Any]) -> ExtractedFacts:
    """tool_use input(dict) → `ExtractedFacts`. 타입 방어 + tuple 동결.

    strict 스키마가 형태를 보장하지만, 응답 변형(strict 미적용 모델 등)에 대비해
    방어적으로 검증한다 — disclosure_type 은 str, 나머지는 str tuple 로 강제.
    """
    disclosure_type = payload.get("disclosure_type")
    if not isinstance(disclosure_type, str):
        raise AnthropicFactExtractorError(
            "disclosure_type 이 문자열이 아닙니다(예상 외 응답)."
        )
    return ExtractedFacts(
        disclosure_type=disclosure_type,
        amounts=_coerce_str_tuple(payload.get("amounts")),
        dates=_coerce_str_tuple(payload.get("dates")),
        parties=_coerce_str_tuple(payload.get("parties")),
        quantities=_coerce_str_tuple(payload.get("quantities")),
    )


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    """배열 슬롯 값을 str tuple 로 강제 — None/누락은 빈 tuple, 비-str 항목은 거부.

    빈 배열·누락은 ADR-0031 D1 상 정상(해당 사실 없음)이라 빈 tuple. 항목 타입이
    str 이 아니면 응답 변형으로 보고 raise(조용한 형변환 금지 — Fidelity).
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise AnthropicFactExtractorError(
            "사실 슬롯이 배열이 아닙니다(예상 외 응답)."
        )
    for item in value:
        if not isinstance(item, str):
            raise AnthropicFactExtractorError(
                "사실 슬롯 항목이 문자열이 아닙니다(예상 외 응답)."
            )
    return tuple(value)


def build_default_fact_extractor() -> AnthropicFactExtractor | None:
    """env 기반 운영 추출기 factory — 명시 opt-in + 키 설정 + 비테스트 환경에서만 활성.

    `create_app` 이 명시 주입(`fact_extractor=`) 없을 때 호출한다(ADR-0031 D6).

    활성 조건 (모두 충족해야 함):
        1. pytest 미실행 — 테스트 환경 격리 (config._load_dotenv 가드 동일 철학).
        2. `SPECULUM_ENABLE_LLM_FACT_EXTRACTION=1` 명시 opt-in — **키만으로는 활성
           안 됨**. 키를 타 인프라와 공유·재배포 시 ADR-0031 D6("자문 전 운영 노출 0")
           우발 위반을 차단한다. ADR-0006 자문 완료 후 운영 설정할 것.
        3. `ANTHROPIC_API_KEY` 존재.
        4. `anthropic` SDK 설치 (`pip install -e .[llm]`).

    위 조건 중 하나라도 빠지면 None → route 503 (fail-closed, 운영 미연동 정상 상태).

    Returns:
        모든 활성 조건 충족 시 `AnthropicFactExtractor`, 그 외 None(미연동 정상).
    """
    import sys

    if "pytest" in sys.modules:
        return None

    # 지연 import — config 의 env getter(.env 로드 후) 사용.
    from app.core.config import (
        get_anthropic_api_key,
        get_anthropic_model,
        get_llm_fact_extraction_enabled,
    )

    # 명시 opt-in 게이트 — 키 존재만으로는 활성 안 됨 (ADR-0031 D6).
    # SPECULUM_ENABLE_LLM_FACT_EXTRACTION=1 이 없으면 키가 있어도 None 반환.
    if not get_llm_fact_extraction_enabled():
        return None

    api_key = get_anthropic_api_key()
    if api_key is None:
        return None
    return AnthropicFactExtractor(api_key=api_key, model=get_anthropic_model())
