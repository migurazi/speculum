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

from pydantic import BaseModel, ConfigDict, Field

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

    입력 검증 (방어선) — 추출 자체는 게이트(D2)가 막지만, 그 전에 식별자·입력의
    형식을 강제해 잘못된 값이 출처 URL/응답에 새는 것을 차단한다(§2.1 Fidelity):

    - **rcept_no = 14 자리 ASCII 숫자** (`^[0-9]{14}$`). DART 접수번호는
      YYYYMMDD(8) + 순번(6) 의 14 자리. 이 값은 `_DART_VIEWER_URL_TEMPLATE` 의
      `?rcpNo=` 와 응답 출처(`FactSourceOut`)에 그대로 들어가므로, 임의 문자열이면
      (a) 원문 대조 키 무결성 훼손, (b) query-string 주입 여지가 생긴다. 형식
      강제로 둘 다 차단. (정규식은 `\\d` 가 아니라 `[0-9]` — `\\d` 는 유니코드
      숫자 클래스라 전각 숫자 `１２３` 등이 통과해 깨진 dart_url 을 만들 수 있다. ASCII
      한정으로 §2.1 Fidelity 의 원문 대조 키 무결성을 완전히 보장한다.)
      (자사주 placeholder identifier 같은 비-숫자 rcept_no 는 공시가 아니므로
      사실추출 대상이 아니다 — disclosures route 가 주는 값은 실 14 자리 접수번호.)
    - **disclosure_title** — 빈 문자열 거부(min 1) + 길이 상한(max 200). extractor
      입력으로 들어가므로 빈 제목은 무의미하고, 상한은 과길이 입력 방어(운영 LLM
      프롬프트 비용/오용 — release blocker 와 무관하게 wire 레벨에서 차단).

    Attributes:
        rcept_no: DART 접수번호 14 자리 — 추출 대상 공시 식별 + 출처 대조 키(D3).
        disclosure_title: 공시 제목 (DART 원문, EXTERNAL_QUOTE). extractor 입력.
    """

    model_config = _STRICT_MODEL_CONFIG

    rcept_no: str = Field(pattern=r"^[0-9]{14}$")
    disclosure_title: str = Field(min_length=1, max_length=200)


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
