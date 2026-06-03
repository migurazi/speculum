"""공시 사실추출 wire schemas — `POST /api/stocks/{code}/disclosures/extract-facts`.

ADR-0031 (AI 공시 사실추출) D1~D6 의 wire 계약. 구조화 사실(공시유형·금액·일자·
당사자·수량) + 출처 + 디스클레이머만 노출 — **전망/요약/추천 필드 0** (D1/D5
구조적 차단).

설계 (disclosures.py / stocks.py schema 패턴 일관):
- Pydantic v2 strict + extra="forbid" + frozen=True
- snake_case wire (frontend 계약)
- request body 는 사용자 명시 1 공시만 (rcept_no + disclosure_title, D4)
- 응답 facts 는 자유서술/요약/전망/추천 필드 부재 — 고정 슬롯만
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.services.disclosure_fact_extraction import (
    ExtractedFacts,
    FactExtractionResult,
)

__all__ = [
    "ExtractFactsRequest",
    "ExtractedFactsOut",
    "FactExtractionResultOut",
    "FactSourceOut",
]

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class ExtractFactsRequest(BaseModel):
    """사실추출 요청 body — 사용자 명시 1 공시만 (ADR-0031 D4).

    자동 추출·feed·일괄 처리 0 (on-demand only). rcept_no + 제목으로 1 건만 지정.

    Attributes:
        rcept_no: DART 접수번호 — 추출 대상 공시 식별 + 출처 대조 키(D3).
        disclosure_title: 공시 제목 (DART 원문, EXTERNAL_QUOTE). extractor 입력.
    """

    model_config = _STRICT_MODEL_CONFIG

    rcept_no: str
    disclosure_title: str


class ExtractedFactsOut(BaseModel):
    """추출된 구조화 사실 wire — 고정 슬롯만 (ADR-0031 D1/D5).

    **free-text summary / outlook / recommendation 필드 0** — 필드 자체가 없어
    전망·가치판단을 채울 곳이 없다 (구조적 차단). 모든 슬롯은 출력 게이트(D2)를
    통과한 사실.
    """

    model_config = _STRICT_MODEL_CONFIG

    # 공시유형 — DART 원문 사실 분류 (Speculum 의 "중요도/호재" 라벨 아님).
    disclosure_type: str
    # 금액·일자·당사자·수량 사실 — 빈 list 허용. 자유서술 0.
    amounts: tuple[str, ...]
    dates: tuple[str, ...]
    parties: tuple[str, ...]
    quantities: tuple[str, ...]

    @classmethod
    def from_facts(cls, facts: ExtractedFacts) -> ExtractedFactsOut:
        """service `ExtractedFacts` → wire (단방향 factory)."""
        return cls(
            disclosure_type=facts.disclosure_type,
            amounts=facts.amounts,
            dates=facts.dates,
            parties=facts.parties,
            quantities=facts.quantities,
        )


class FactSourceOut(BaseModel):
    """출처 wire — DART 원문 대조 키 + 링크 (ADR-0031 D3).

    추출 사실 옆 원문 인용 강제. dart_url 클릭 시 DART viewer 로 이탈 — 사용자가
    원문 대조 가능 (검증 가능성 보존, §2.1).
    """

    model_config = _STRICT_MODEL_CONFIG

    rcept_no: str
    dart_url: str


class FactExtractionResultOut(BaseModel):
    """사실추출 응답 — `POST .../disclosures/extract-facts` (ADR-0031).

    Attributes:
        facts: 게이트(D2) 통과한 구조화 사실. 전망/추천 필드 0 (D1/D5).
        source: DART 원문 출처 (rcept_no + viewer URL, D3).
        disclaimer_required: 항상 True (D3). client 가 "AI 추출 사실 — 원문 확인
            필수, hallucination 가능성" 디스클레이머 게이트 없이 렌더 차단.
    """

    model_config = _STRICT_MODEL_CONFIG

    facts: ExtractedFactsOut
    source: FactSourceOut
    disclaimer_required: bool

    @classmethod
    def from_result(cls, result: FactExtractionResult) -> FactExtractionResultOut:
        """service `FactExtractionResult` → wire (단방향 factory)."""
        return cls(
            facts=ExtractedFactsOut.from_facts(result.facts),
            source=FactSourceOut(
                rcept_no=result.rcept_no,
                dart_url=result.dart_url,
            ),
            disclaimer_required=result.disclaimer_required,
        )
