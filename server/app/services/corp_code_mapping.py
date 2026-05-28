"""CorpCodeMapping — KRX 종목코드 ↔ DART corp_code 매핑.

DART OpenAPI 의 corp_code 는 8 자리 numeric (예: "00126380" — 삼성전자). KRX
종목코드는 6 자리 (예: "005930"). 두 식별자는 1:1 대응이나 namespace 가 달라
명시 매핑 layer 가 필요.

설계 결정 (M0 MVP):

1. **in-memory dict + factory** — `CorpCodeMapping.from_dict(...)` 로 dict
   주입. test fixture / 운영 초기 boot strap (DART corpCode endpoint 에서
   1회 fetch 후 cache) 모두 같은 인터페이스.

2. **bidirectional lookup** — `to_corp_code(stock_code)` + `to_stock_code(
   corp_code)`. 한 쪽 lookup 만 운영 hot path (DART fetch 는 corp_code 기반).

3. **immutable** — frozen dataclass + MappingProxyType 으로 외부 mutation
   차단. T19 일배치가 batch context 안에서 안정적.

4. **미매핑 시 None 반환** — silent drop X. 호출자 (T19) 가 결정 (skip + warning
   vs raise).

5. **명시 검증** — KRX 6자리 / DART 8자리 numeric. 잘못된 format 은 factory
   에서 raise.

운영 시 매핑 source:
- DART corpCode.xml endpoint (전체 list 의 ZIP) — 운영 boot strap (별도 cycle).
- Pykrx universe 와 cross-check (T18 의 universe fetch 와 일관).

관련 ADR:
- ADR-0003 D2 — DART corp_code 는 ADR-0003 의 canonical 외부.
- ADR-0002 D3 — citation identifier (DART rcept_no) 와 무관.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = ["CorpCodeMapping", "CorpCodeMappingError"]


class CorpCodeMappingError(Exception):
    """CorpCodeMapping invariant 위반 — format 오류, 중복, 양방향 불일치."""


@dataclass(frozen=True, slots=True)
class CorpCodeMapping:
    """KRX stock code ↔ DART corp_code 1:1 매핑 (immutable).

    Args (factory `from_dict` 만 사용 권장):
        stock_to_corp: KRX 6자리 → DART 8자리. immutable Mapping.
        corp_to_stock: DART 8자리 → KRX 6자리. immutable Mapping (factory 가 자동).
    """

    stock_to_corp: Mapping[str, str]
    corp_to_stock: Mapping[str, str]

    @classmethod
    def from_dict(cls, stock_to_corp: dict[str, str]) -> CorpCodeMapping:
        """dict → 양방향 매핑 + format 검증.

        Args:
            stock_to_corp: KRX 6자리 → DART 8자리 dict.

        Returns:
            CorpCodeMapping — 양방향 lookup 가능.

        Raises:
            CorpCodeMappingError: format 위반 (6자리/8자리 numeric 아님),
                중복 corp_code (양방향 1:1 위반), 빈 dict 허용 (운영 boot 전).
        """
        forward: dict[str, str] = {}
        reverse: dict[str, str] = {}
        for stock_code, corp_code in stock_to_corp.items():
            _validate_stock_code(stock_code)
            _validate_corp_code(corp_code)
            if corp_code in reverse:
                # 양방향 1:1 위반.
                raise CorpCodeMappingError(
                    f"duplicate corp_code {corp_code} maps to multiple stock "
                    f"codes: {reverse[corp_code]!r} and {stock_code!r}"
                )
            forward[stock_code] = corp_code
            reverse[corp_code] = stock_code
        return cls(
            stock_to_corp=MappingProxyType(forward),
            corp_to_stock=MappingProxyType(reverse),
        )

    def to_corp_code(self, stock_code: str) -> str | None:
        """KRX 6자리 → DART 8자리. 미매핑 시 None."""
        return self.stock_to_corp.get(stock_code)

    def to_stock_code(self, corp_code: str) -> str | None:
        """DART 8자리 → KRX 6자리. 미매핑 시 None."""
        return self.corp_to_stock.get(corp_code)

    @property
    def size(self) -> int:
        """매핑된 회사 수."""
        return len(self.stock_to_corp)


def _validate_stock_code(stock_code: str) -> None:
    """KRX 6자리 numeric — adapter 의 validate 와 동일 정책."""
    if not isinstance(stock_code, str):
        raise CorpCodeMappingError(
            f"stock_code must be str, got {type(stock_code).__name__}"
        )
    if len(stock_code) != 6 or not stock_code.isdigit():
        raise CorpCodeMappingError(
            f"stock_code must be 6-digit numeric, got {stock_code[:32]!r}"
        )


def _validate_corp_code(corp_code: str) -> None:
    """DART 8자리 numeric — DartAdapter._validate_corp_code 와 동일 정책."""
    if not isinstance(corp_code, str):
        raise CorpCodeMappingError(
            f"corp_code must be str, got {type(corp_code).__name__}"
        )
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise CorpCodeMappingError(
            f"corp_code must be 8-digit numeric, got {corp_code[:32]!r}"
        )
