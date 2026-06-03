"""AI 공시 사실추출 — 요약 아닌 구조화 사실 + LLM 출력 게이트 (ADR-0031).

CONCEPT §2.7 은 LLM 을 명시적으로 경계한다 — hallucination + 의제설정 위험. #6 은
그 경계를 처음 넘는 결정이므로 6 항목 중 가장 무거운 게이트가 필요하다. 본 모듈은
LLM 출력을 **자유생성 요약이 아니라 고정 스키마의 사실 필드 추출**로 재정의하고
(ADR-0031 D1), 추출된 모든 텍스트를 `forbidden_words.assert_clean(scope=SYSTEM)` 로
검사해 가치판단·hallucination 을 1 차 차단한다 (D2, fail-closed).

설계 결정 (ADR-0031 D1~D6):
- **D1 구조화 사실만** — `ExtractedFacts` frozen dataclass 는 공시유형·금액·일자·
  당사자·수량 슬롯만 보유. 자유서술/요약/전망/추천 필드를 **스키마에 두지 않는다**
  (구조적 차단 — 필드가 없으면 LLM 이 채울 곳도 없음).
- **D2 출력 게이트 (fail-closed)** — 추출된 모든 텍스트 필드를 SYSTEM scope 로 검사.
  "호재/유망/긍정적/매수" 등 1 개라도 검출 시 추출 결과 미반환 (`FactExtractionBlocked`).
  미들웨어(ForbiddenWordsGuardMiddleware) 의 응답 검사에 더해, 결과 조립 전에 차단해
  부분 노출조차 막는다.
- **D3 출처 강제 + 디스클레이머** — 결과에 DART 원문 rcept_no + viewer URL
  (EXTERNAL_QUOTE) + `disclaimer_required=True`. 원문 대조 가능성 보존.
- **D5 미래 전망 0** — 과거 접수 공시의 사실만. 스키마에 전망/평가 필드 부재.
- **D6 adapter 추상화** — `LlmFactExtractor` Protocol 로 구현체(Anthropic SDK 등)를
  분리. 본 모듈은 protocol + fake + 게이트 파이프라인만 — **실제 LLM SDK import 금지**
  (운영 연동은 release blocker). fake 만으로 파이프라인이 완전 동작한다.

stateless·결정적 (fake extractor 기준). 호출자(route)가 extractor 를 주입.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.services.forbidden_words import (
    CheckScope,
    ForbiddenWordsAssertError,
    assert_clean,
)

__all__ = [
    "ExtractedFacts",
    "FactExtractionBlocked",
    "FactExtractionResult",
    "FakeLlmFactExtractor",
    "LlmFactExtractor",
    "extract_disclosure_facts",
]


# =============================================================================
# Structured output 스키마 — 고정 사실 필드 슬롯만 (ADR-0031 D1/D5)
# =============================================================================

@dataclass(frozen=True, slots=True)
class ExtractedFacts:
    """LLM 이 공시에서 추출한 구조화 사실 — 고정 스키마 (ADR-0031 D1).

    "요약" 이 아니라 사실 필드 슬롯 채우기. 자유서술/해석/압축 문장 0. 본 스키마에
    **free-text summary / outlook / recommendation 필드를 두지 않는다** — 필드 자체가
    없으면 LLM 이 전망·가치판단을 채울 곳도 없다 (구조적 차단, D1/D5).

    모든 텍스트 필드는 `extract_disclosure_facts` 의 출력 게이트(D2)를 통과해야
    결과로 조립된다. 게이트는 본 dataclass 의 텍스트 슬롯 전부를 SYSTEM scope 로 검사.

    Attributes:
        disclosure_type: 공시유형 (예: "유상증자결정", "단일판매·공급계약체결").
            DART 원문에 명시된 사실 분류 — Speculum 의 자체 "중요도/호재" 라벨 아님.
        amounts: 금액 사실 tuple (예: "발행금액 100억원"). 빈 tuple 허용.
        dates: 일자 사실 tuple (예: "청약일 2024-05-20"). 빈 tuple 허용.
        parties: 당사자 사실 tuple (예: "계약상대방 ○○전자"). 빈 tuple 허용.
        quantities: 수량 사실 tuple (예: "신주 1,000,000주"). 빈 tuple 허용.

    Notes:
        전망/매매시사/추천/평가 필드 절대 0 (D5). 추가 시 ADR-0031 재검토 필요.
    """

    disclosure_type: str
    amounts: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    parties: tuple[str, ...] = ()
    quantities: tuple[str, ...] = ()

    def iter_text_fields(self) -> Iterable[tuple[str, str]]:
        """게이트 검사 대상인 모든 텍스트 슬롯을 (필드경로, 값) 으로 산출.

        출력 게이트(D2)가 본 메서드로 추출 텍스트 전부를 순회해 SYSTEM scope
        검사한다. tuple 필드는 항목 단위로 경로에 index 를 부여 — 차단 시 어느
        슬롯이 금지어휘를 포함했는지 server-side audit 가능.
        """
        yield ("disclosure_type", self.disclosure_type)
        for i, value in enumerate(self.amounts):
            yield (f"amounts[{i}]", value)
        for i, value in enumerate(self.dates):
            yield (f"dates[{i}]", value)
        for i, value in enumerate(self.parties):
            yield (f"parties[{i}]", value)
        for i, value in enumerate(self.quantities):
            yield (f"quantities[{i}]", value)


# =============================================================================
# LlmFactExtractor protocol + fake (ADR-0031 D6)
# =============================================================================

class LlmFactExtractor(Protocol):
    """공시 → 구조화 사실 추출기 — adapter 추상화 (ADR-0031 D6).

    구현체(Anthropic SDK 등 structured output 강제)는 주입한다. 본 모듈은 protocol
    정의 + fake 만 보유 — **실제 LLM SDK import 금지**. 운영 연동은 release blocker
    (§2.7 경계 + ADR-0006 자문 + API 키/비용). 게이트·스키마·디스클레이머로 방어해
    구현하되, 운영 노출은 자문 후.

    구현체는 `extract_facts` 가 반드시 `ExtractedFacts`(고정 스키마)를 반환하도록
    structured output 을 강제해야 한다 (자유생성 차단). 게이트(D2)는 그 위의 2 차
    방어선이다.
    """

    def extract_facts(
        self,
        *,
        disclosure_title: str,
        disclosure_context: str,
    ) -> ExtractedFacts:
        """공시 제목·맥락 → 구조화 사실. 자유서술 반환 금지 (스키마 강제)."""
        ...


@dataclass(frozen=True, slots=True)
class FakeLlmFactExtractor:
    """테스트용 결정적 stub — 고정 사실 필드 반환 (ADR-0031 D6).

    실제 LLM 없이 파이프라인을 완전 동작시키는 fake. `facts` 로 반환할
    `ExtractedFacts` 를 미리 지정 — 같은 입력에 같은 출력(결정적). 게이트 fail-closed
    검증 시 금지어휘를 포함한 `facts` 를 주입해 차단 동작을 확인할 수 있다.

    Attributes:
        facts: extract_facts 가 입력과 무관하게 반환할 고정 사실. 결정성 보장.
    """

    facts: ExtractedFacts

    def extract_facts(
        self,
        *,
        disclosure_title: str,
        disclosure_context: str,
    ) -> ExtractedFacts:
        # 결정적 stub — 입력과 무관하게 사전 지정한 facts 반환.
        return self.facts


# =============================================================================
# 게이트 파이프라인 결과 / 예외 (ADR-0031 D2/D3)
# =============================================================================

class FactExtractionBlocked(Exception):
    """출력 게이트 fail-closed — 추출 사실 미반환 (ADR-0031 D2).

    추출 텍스트 중 하나라도 금지어휘(SYSTEM scope)를 포함하면 raise. 호출자(route)
    가 catch 해 사실 미반환 + 사유 응답으로 변환한다. hallucination·가치판단의 1 차
    방어선이며, 부분 노출조차 막기 위해 결과 조립 전에 차단한다.

    `.field_path` 는 차단된 슬롯 경로(server-side audit 용). message 는 금지어휘를
    echo 하지 않는다 (응답 본문 노출 차단 — forbidden_words B4 정책 일관).
    """

    def __init__(self, message: str, *, field_path: str = "") -> None:
        super().__init__(message)
        self.field_path: str = field_path


@dataclass(frozen=True, slots=True)
class FactExtractionResult:
    """게이트 통과한 사실추출 결과 — 출처 + 디스클레이머 첨부 (ADR-0031 D3).

    Attributes:
        facts: 게이트(D2)를 통과한 구조화 사실. 전망/추천 필드 0 (D1/D5).
        rcept_no: DART 접수번호 — 원문 대조 키 (출처 강제, D3).
        dart_url: DART 공시 viewer URL (EXTERNAL_QUOTE). 클릭 시 원문 이탈.
        disclaimer_required: 항상 True (D3). "AI 가 추출한 사실이며 원문 확인이
            필수. hallucination 가능성" 디스클레이머 게이트 — 없이 렌더 차단
            (client 책임). 본 backend 는 플래그만 강제.
    """

    facts: ExtractedFacts
    rcept_no: str
    dart_url: str
    disclaimer_required: bool = field(default=True)


# DART 공시 viewer URL — dart_adapter 의 `_DART_VIEWER_URL_TEMPLATE` 와 동일 규약.
# 출처(D3) 강제 시 rcept_no 로 원문 링크 생성. adapter import 없이 동일 포맷 보유
# (서비스 layer 가 adapter 내부 상수에 결합되지 않도록).
_DART_VIEWER_URL_TEMPLATE = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def extract_disclosure_facts(
    extractor: LlmFactExtractor,
    *,
    rcept_no: str,
    disclosure_title: str,
    disclosure_context: str = "",
) -> FactExtractionResult:
    """공시 1 건의 구조화 사실 추출 — 출력 게이트 fail-closed 파이프라인.

    흐름 (ADR-0031 D2/D3):
        1. extractor.extract_facts 호출 → ExtractedFacts (고정 스키마, D1).
        2. **출력 게이트** — 추출된 모든 텍스트 슬롯을 `assert_clean(SYSTEM)` 검사.
           "호재/유망/긍정적/매수" 등 1 개라도 검출 시 `FactExtractionBlocked`
           (fail-closed — 부분 결과조차 반환 안 함). hallucination·가치판단 1 차 차단.
        3. 게이트 통과분에만 출처(rcept_no + DART URL) + disclaimer_required=True 첨부.

    Args:
        extractor: 주입된 LlmFactExtractor (fake / 운영 구현체). 본 함수는 SDK 비의존.
        rcept_no: DART 접수번호 — 출처 강제(D3)의 원문 대조 키.
        disclosure_title: 사용자가 명시 요청한 1 공시 제목 (D4).
        disclosure_context: extractor 에 넘길 추가 맥락 (선택). 기본 빈 문자열.

    Returns:
        FactExtractionResult — 게이트 통과한 사실 + 출처 + 디스클레이머.

    Raises:
        FactExtractionBlocked: 출력 게이트 검출 시 (fail-closed). 호출자가 사실
            미반환 + 사유 응답으로 변환.

    Note (stateless·결정적):
        fake extractor 기준 같은 입력에 같은 결과. 게이트는 순수 함수(forbidden_words)
        라 부수효과 없음.
    """
    facts = extractor.extract_facts(
        disclosure_title=disclosure_title,
        disclosure_context=disclosure_context,
    )

    # 출력 게이트 (D2, fail-closed) — 추출된 모든 텍스트 슬롯을 SYSTEM scope 검사.
    # 하나라도 금지어휘 포함 시 결과 조립 전에 차단(부분 노출 방지). assert_clean 의
    # ForbiddenWordsAssertError 를 본 도메인 예외(FactExtractionBlocked)로 변환 —
    # 금지어휘 echo 없이 차단 사유만 전달(응답 본문 노출 차단, forbidden_words B4).
    for field_path, value in facts.iter_text_fields():
        try:
            assert_clean(value, scope=CheckScope.SYSTEM, context=field_path)
        except ForbiddenWordsAssertError as exc:
            # fail-closed — 사실 미반환. field_path 만 audit(어휘 echo 금지).
            raise FactExtractionBlocked(
                "Extracted fact contained forbidden words; extraction blocked "
                "(fail-closed).",
                field_path=field_path,
            ) from exc

    # 게이트 통과 — 출처(D3) + 디스클레이머 첨부. disclaimer_required 는 항상 True.
    dart_url = _DART_VIEWER_URL_TEMPLATE.format(rcept_no=rcept_no)
    return FactExtractionResult(
        facts=facts,
        rcept_no=rcept_no,
        dart_url=dart_url,
        disclaimer_required=True,
    )
