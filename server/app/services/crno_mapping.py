"""CrnoMapping — KRX 종목코드 → 법인등록번호(crno) 매핑.

FscDividendAdapter(금융위 주식배당정보)는 배당 조회 키로 **crno(법인등록번호,
13자리)** 를 받는다. 우리는 KRX 종목코드(6자리)만 보유하므로 종목코드 → crno
매핑 layer 가 필요하다. corpCode.xml(종목코드↔corp_code)에는 crno 가 없어,
corp_code 별 DART `company.json` 의 `jurir_no` 필드로 보강한다(CrnoBootstrap).

설계 결정 (CorpCodeMapping 패턴 미러 — 일관성):

1. **in-memory dict + factory** — `CrnoMapping.from_dict(...)`. test fixture /
   운영 부트스트랩(CrnoBootstrap) 모두 같은 인터페이스.

2. **단방향 lookup** — `to_crno(stock_code)`. FSC 배당 fetch 의 hot path 는
   종목코드 → crno 한 방향뿐이라 역방향(crno → 종목코드)은 두지 않는다.
   CorpCodeMapping 은 DART fetch 가 corp_code 기반이라 양방향이 필요했으나,
   본 매핑은 단방향이면 충분(불필요한 역인덱스 미생성 — YAGNI).

3. **N:1 허용 (crno 유일성 미강제 — CorpCodeMapping 과의 핵심 차이)** —
   crno 는 **법인** 식별자라 같은 법인의 보통주·우선주가 **동일 crno 를 공유**
   한다(예 삼성전자 005930 / 삼성전자우 005935 → 같은 crno). 따라서 여러
   종목코드가 한 crno 로 매핑되는 것은 정상이며 1:1 invariant 를 강제하지 않는다
   (CorpCodeMapping 은 corp_code 역중복을 1:1 위반으로 skip 했으나, 본 매핑은
   stock_code 키 유일성만 보장하고 crno 중복은 허용). 종목코드(키) 중복은
   from_dict 의 dict 의미상 자연히 마지막 값으로 병합되나, 호출자(CrnoBootstrap)
   가 종목코드 유일 dict 를 전달하므로 실제 충돌은 없다.

4. **immutable** — frozen dataclass + MappingProxyType 으로 외부 mutation 차단.
   배치 context 안에서 안정적.

5. **미매핑 시 None 반환** — silent drop X. 호출자(배당 배치)가 결정
   (skip + warning vs raise).

6. **명시 검증** — KRX 6자리 / crno 13자리 numeric. 잘못된 format 은 factory
   에서 raise (CorpCodeMapping 의 6/8자리 검증과 동형, crno 는 13자리).

관련 ADR:
- ADR-0035 D3 (배당 출처 = 금융위 공공데이터, crno 입력 키)
- ADR-0002 D3 (citation identifier 와 무관 — crno 는 매핑 layer)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = ["CrnoMapping", "CrnoMappingError"]


class CrnoMappingError(Exception):
    """CrnoMapping invariant 위반 — format 오류."""


@dataclass(frozen=True, slots=True)
class CrnoMapping:
    """KRX 종목코드 → 법인등록번호(crno) 매핑 (immutable, 단방향).

    Args (factory `from_dict` 만 사용 권장):
        stock_to_crno: KRX 6자리 → crno 13자리. immutable Mapping.
    """

    stock_to_crno: Mapping[str, str]

    @classmethod
    def from_dict(cls, stock_to_crno: dict[str, str]) -> CrnoMapping:
        """dict → 매핑 + format 검증.

        Args:
            stock_to_crno: KRX 6자리 → crno 13자리 dict. 빈 dict 허용
                (운영 boot 전 / 매핑 0 — 호출자가 가시화).

        Returns:
            CrnoMapping — 종목코드 → crno lookup.

        Raises:
            CrnoMappingError: format 위반 (6자리/13자리 numeric 아님).
                crno 중복은 허용(N:1 — 보통주·우선주 공유) — 검증 대상 아님.
        """
        validated: dict[str, str] = {}
        for stock_code, crno in stock_to_crno.items():
            _validate_stock_code(stock_code)
            _validate_crno(crno)
            validated[stock_code] = crno
        return cls(stock_to_crno=MappingProxyType(validated))

    def to_crno(self, stock_code: str) -> str | None:
        """KRX 6자리 → crno 13자리. 미매핑 시 None."""
        return self.stock_to_crno.get(stock_code)

    @property
    def size(self) -> int:
        """매핑된 종목 수."""
        return len(self.stock_to_crno)


def _validate_stock_code(stock_code: str) -> None:
    """KRX 6자리 numeric — CorpCodeMapping._validate_stock_code 와 동일 정책."""
    if not isinstance(stock_code, str):
        raise CrnoMappingError(
            f"stock_code must be str, got {type(stock_code).__name__}"
        )
    if len(stock_code) != 6 or not stock_code.isdigit():
        raise CrnoMappingError(
            f"stock_code must be 6-digit numeric, got {stock_code[:32]!r}"
        )


def _validate_crno(crno: str) -> None:
    """법인등록번호(crno) — 13자리 numeric (하이픈 제거 후 표준 형식)."""
    if not isinstance(crno, str):
        raise CrnoMappingError(
            f"crno must be str, got {type(crno).__name__}"
        )
    if len(crno) != 13 or not crno.isdigit():
        raise CrnoMappingError(
            f"crno must be 13-digit numeric, got {crno[:32]!r}"
        )
