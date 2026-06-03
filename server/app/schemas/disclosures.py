"""Disclosure metadata wire schemas — `GET /api/stocks/{code}/disclosures`.

ADR-0026 (공시 metadata 표시) D1~D4 의 wire 계약. 제목·접수일자·DART 원문링크
3 필드만 노출 (본문/요약/자체 분류라벨/"중요도" 0 — §2.7 경계).

설계 (stocks.py schema 패턴 일관):
- Pydantic v2 strict + extra="forbid" + frozen=True
- snake_case wire (frontend 계약)
- `report_name` 은 EXTERNAL_QUOTE scope (ADR-0007 D4.5) — 회사 원문 제목이라
  forbidden_words 검사 제외 (main.py 의 `_EXTERNAL_QUOTE_EXCLUDE_KEYS`).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.adapters.dart_adapter import DisclosureItem

__all__ = ["DisclosureItemOut", "DisclosuresOut"]

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)


class DisclosureItemOut(BaseModel):
    """단일 공시 항목 wire — 제목·접수일·원문링크만 (ADR-0026 D1).

    Speculum 이 생성하는 라벨·배지·요약·"중요도" 필드 0 (SYSTEM scope 생성
    텍스트 0). 본문은 dart_url 클릭 시 DART 페이지로 이탈 (텍스트 자체 표시 X).
    """

    model_config = _STRICT_MODEL_CONFIG

    # 회사 원문 공시 제목 — EXTERNAL_QUOTE scope (forbidden_words 검사 제외).
    report_name: str
    # 접수일자 (YYYY-MM-DD wire). PIT 필터 (rcept_date <= as_of) 통과분만.
    rcept_date: date
    # DART 원문 viewer URL — 클릭 시 DART 페이지로 이탈.
    dart_url: str

    @classmethod
    def from_item(cls, item: DisclosureItem) -> DisclosureItemOut:
        """adapter `DisclosureItem` → wire (단방향 factory, stocks.py 패턴)."""
        return cls(
            report_name=item.report_name,
            rcept_date=item.rcept_date,
            dart_url=item.dart_url,
        )


class DisclosuresOut(BaseModel):
    """공시목록 응답 — `GET /api/stocks/{code}/disclosures`.

    Attributes:
        code: KRX 종목코드 (6자리 정규화).
        as_of: PIT 기준일 (YYYY-MM-DD). 이 시점 이후 접수 공시는 비표시
            (ADR-0026 D4 — look-ahead 0).
        disclosures: 최신순 (rcept_date 역순) 공시 항목. limit 적용 후.
    """

    model_config = _STRICT_MODEL_CONFIG

    code: str
    as_of: date
    disclosures: tuple[DisclosureItemOut, ...]
