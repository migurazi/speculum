"""증권거래세 계산 service — M3 #5 (ADR-0030 D1/D2/D4).

매도 거래 시점의 **공개 산식(거래금액 × 시장구분별 세율)** 만 계산하는 stateless·
결정적 service. 증권거래세는 보유기간·대주주 판정·손익통산 같은 개별 상황과 무관한
**산술 사실**이라 세무 판단이 들어가지 않는다(ADR-0030 D2).

세무사법 경계 — 명시 미구현(ADR-0030 D1):
    본 모듈은 **증권거래세만** 계산한다. 양도소득세는 보유기간·대주주 여부·연간
    손익통산 등 개별 상황 의존이라 세무 판단(세무사법 영역)을 요하므로 미구현이다.
    양도세 스텁·TODO 도 두지 않는다(자문 완료 전 유보, ADR-0030 D1/D6). 본 service
    는 "이 거래금액에 이 세율을 곱하면 이 세액"이라는 곱셈 사실만 반환한다.

효력일 freeze + 법령 출처(ADR-0030 D4):
    세율은 **효력일 구간**으로 버전 관리한다. 과거 거래일 계산은 그 시점에 효력
    있던 세율을 적용한다(factor_pack 의 효력일 보존 정신, ADR-0012). 각 세율에는
    법령 출처(증권거래세법·농어촌특별세법 조항)와 효력일을 명시해 표시층의
    디스클레이머·출처 표기에 사용한다.

세율 구성 — 증권거래세 + 농어촌특별세:
    상장주식 매도에는 증권거래세(증권거래세법)와 농어촌특별세(농어촌특별세법)가
    함께 부과된다. 정부의 단계적 증권거래세 인하 로드맵으로 증권거래세율 자체는
    낮아졌으나, 유가증권시장(코스피)은 농어촌특별세 0.15% 가 별도 부과되어 합산
    세율이 유지된다. 본 service 는 **합산 세율(증권거래세 + 농특세)** 을 단일
    세율로 적용한다(거래 시 실제 원천징수되는 총액 기준).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Final

__all__ = [
    "MARKET_KOSDAQ",
    "MARKET_KONEX",
    "MARKET_KOSPI",
    "MARKET_UNLISTED",
    "VALID_MARKETS",
    "SecuritiesTransactionTaxError",
    "TaxRateEntry",
    "TaxResult",
    "calculate_securities_transaction_tax",
]

# 세액 산출의 Decimal precision/rounding — 원 단위 절사 전 충분한 정밀.
# ROUND_HALF_UP — 세액 산출의 통상 반올림(거래세 원천징수 관행).
_DECIMAL_PRECISION: Final[int] = 28

# 세액 표시 단위 — 원(KRW) 정수. 세액은 원 단위로 quantize(소수 원 없음).
_KRW_QUANTUM: Final[Decimal] = Decimal("1")

# 시장구분 상수 — wire 입력값(소문자)과 일치. route 가 본 상수로 검증.
MARKET_KOSPI: Final[str] = "kospi"
MARKET_KOSDAQ: Final[str] = "kosdaq"
MARKET_KONEX: Final[str] = "konex"
MARKET_UNLISTED: Final[str] = "unlisted"

VALID_MARKETS: Final[frozenset[str]] = frozenset(
    {MARKET_KOSPI, MARKET_KOSDAQ, MARKET_KONEX, MARKET_UNLISTED}
)


class SecuritiesTransactionTaxError(Exception):
    """증권거래세 계산 입력 위반 — 잘못된 시장구분, 음수 거래금액, 효력일 미매칭."""


@dataclass(frozen=True, slots=True)
class TaxRateEntry:
    """단일 효력일 구간의 시장구분별 세율 + 법령 출처(ADR-0030 D4).

    Attributes:
        effective_from: 본 세율이 효력을 갖기 시작한 날(포함). 구간의 시작.
        rate: 합산 세율(증권거래세 + 농어촌특별세), 비율(예: 0.0015 = 0.15%).
            거래금액에 곱하는 단일 비율.
        legal_source: 법령 출처 문구 — 증권거래세법/농어촌특별세법 조항 + 효력일.
            표시층의 디스클레이머·출처 표기에 그대로 노출(Fidelity §2.1).
    """

    effective_from: date
    rate: Decimal
    legal_source: str


@dataclass(frozen=True, slots=True)
class TaxResult:
    """증권거래세 계산 결과 — **순수 산술 사실**(ADR-0030 D2).

    개별 상황 판단(대주주·보유기간·손익통산) 0. "절세/유리" 같은 라벨 없음.
    세액 + 적용 세율 + 효력일 + 법령 출처만 사실로 반환한다.

    Attributes:
        tax_amount: 세액(원, KRW). 거래금액 × 적용 세율을 원 단위로 quantize.
        applied_rate: 적용된 합산 세율(비율). 효력일 구간 매칭으로 선택된 값.
        effective_date: 적용 세율이 효력을 갖기 시작한 날(구간 시작, ADR-0030 D4).
        legal_source: 법령 출처 문구(증권거래세법/농어촌특별세법 조항 + 효력일).
    """

    tax_amount: Decimal
    applied_rate: Decimal
    effective_date: date
    legal_source: str


# =============================================================================
# 세율 테이블 — 효력일 구간 + 법령 출처(ADR-0030 D4)
# =============================================================================
#
# 출처: 증권거래세법(법률) §8(세율) + 동법 시행령 §5(탄력세율) + 농어촌특별세법
#       §5(과세표준·세율). 정부의 단계적 증권거래세 인하 로드맵(2023→2024→2025)에
#       따라 시행령상 탄력세율이 하향됐다. 본 테이블은 **상장주식 매도 시 실제
#       원천징수되는 합산 세율(증권거래세 + 농어촌특별세)** 을 효력일 구간으로
#       기록한다.
#
# 구성 원리:
#   - 코스피(유가증권시장): 증권거래세 탄력세율 + 농어촌특별세 0.15%(농특세법 §5).
#       2023: 거래세 0.05% + 농특세 0.15% = 0.20%
#       2024: 거래세 0.03% + 농특세 0.15% = 0.18%
#       2025~: 거래세 0.00% + 농특세 0.15% = 0.15%
#       (코스피는 농특세 부과 대상 — 합산 세율이 0.15% 아래로 내려가지 않음)
#   - 코스닥: 증권거래세 탄력세율만(농어촌특별세 비부과 대상).
#       2023: 0.20% / 2024: 0.18% / 2025~: 0.15%
#       (코스닥은 농특세 비부과라 거래세율 = 합산 세율)
#   - 코넥스: 증권거래세 시행령상 별도 탄력세율 0.10%(코넥스 활성화 목적 저율).
#   - 비상장(장외 양도): 증권거래세법 §8 기본세율 0.35%(농특세 비부과).
#
# 효력일 구간 매칭: 거래일이 속한 구간의 세율을 적용한다(과거 거래 = 그 시점 세율).
# 각 시장은 effective_from 내림차순으로 정렬해 두어, 거래일 이하 첫 entry 가
# 그 시점 효력 세율이다.
#
# 불확실성 주석: 정부 세율 인하 로드맵은 시행령 개정으로 확정·시행됐으나, 본
# 프로젝트는 외부 법령 데이터를 실시간 조달하지 않으므로 위 합산 세율은 코드
# 테이블로 freeze 한다. 세율 개정 시 본 테이블에 효력일 구간을 추가한다(법령
# 출처 주석 필수, ADR-0030 D4). 합산 정확성은 보수적으로 검증된 값이며, 더
# 정밀한 시점별 세율이 필요하면 법령 원문 확인 후 구간을 세분한다.

# 각 시장의 효력일 구간 — effective_from **내림차순** 정렬(최신 구간이 앞).
# 거래일 이하 첫 entry 매칭 = 그 시점 효력 세율(효력일 freeze, ADR-0030 D4).
_RATE_TABLE: Final[dict[str, tuple[TaxRateEntry, ...]]] = {
    MARKET_KOSPI: (
        TaxRateEntry(
            effective_from=date(2025, 1, 1),
            rate=Decimal("0.0015"),  # 증권거래세 0.00% + 농어촌특별세 0.15%
            legal_source=(
                "증권거래세법 시행령 §5(탄력세율 0.00%) + 농어촌특별세법 §5"
                "(0.15%) — 2025-01-01 효력, 유가증권시장 매도 합산 0.15%"
            ),
        ),
        TaxRateEntry(
            effective_from=date(2024, 1, 1),
            rate=Decimal("0.0018"),  # 증권거래세 0.03% + 농어촌특별세 0.15%
            legal_source=(
                "증권거래세법 시행령 §5(탄력세율 0.03%) + 농어촌특별세법 §5"
                "(0.15%) — 2024-01-01 효력, 유가증권시장 매도 합산 0.18%"
            ),
        ),
        TaxRateEntry(
            effective_from=date(2023, 1, 1),
            rate=Decimal("0.0020"),  # 증권거래세 0.05% + 농어촌특별세 0.15%
            legal_source=(
                "증권거래세법 시행령 §5(탄력세율 0.05%) + 농어촌특별세법 §5"
                "(0.15%) — 2023-01-01 효력, 유가증권시장 매도 합산 0.20%"
            ),
        ),
    ),
    MARKET_KOSDAQ: (
        TaxRateEntry(
            effective_from=date(2025, 1, 1),
            rate=Decimal("0.0015"),  # 증권거래세 0.15%(농특세 비부과)
            legal_source=(
                "증권거래세법 시행령 §5(코스닥 탄력세율 0.15%) — 2025-01-01 효력, "
                "코스닥시장 매도(농어촌특별세 비부과)"
            ),
        ),
        TaxRateEntry(
            effective_from=date(2024, 1, 1),
            rate=Decimal("0.0018"),  # 증권거래세 0.18%(농특세 비부과)
            legal_source=(
                "증권거래세법 시행령 §5(코스닥 탄력세율 0.18%) — 2024-01-01 효력, "
                "코스닥시장 매도(농어촌특별세 비부과)"
            ),
        ),
        TaxRateEntry(
            effective_from=date(2023, 1, 1),
            rate=Decimal("0.0020"),  # 증권거래세 0.20%(농특세 비부과)
            legal_source=(
                "증권거래세법 시행령 §5(코스닥 탄력세율 0.20%) — 2023-01-01 효력, "
                "코스닥시장 매도(농어촌특별세 비부과)"
            ),
        ),
    ),
    MARKET_KONEX: (
        TaxRateEntry(
            effective_from=date(2023, 1, 1),
            rate=Decimal("0.0010"),  # 증권거래세 0.10%(코넥스 저율, 농특세 비부과)
            legal_source=(
                "증권거래세법 시행령 §5(코넥스 탄력세율 0.10%) — 코넥스시장 매도"
                "(중소기업 전용 시장 저율, 농어촌특별세 비부과)"
            ),
        ),
    ),
    MARKET_UNLISTED: (
        TaxRateEntry(
            effective_from=date(2023, 1, 1),
            rate=Decimal("0.0035"),  # 증권거래세법 §8 기본세율 0.35%(농특세 비부과)
            legal_source=(
                "증권거래세법 §8(기본세율 0.35%) — 비상장주식(장외) 양도"
                "(농어촌특별세 비부과)"
            ),
        ),
    ),
}


def _resolve_rate_entry(market: str, trade_date: date) -> TaxRateEntry:
    """시장구분 + 거래일 → 그 시점 효력 세율 entry(효력일 구간 매칭, ADR-0030 D4).

    각 시장의 entry 들은 effective_from 내림차순 정렬돼 있어, 거래일 이상의 효력
    개시일을 가진 첫 entry(= 거래일 이하 effective_from 중 최신)가 그 시점 효력
    세율이다. 거래일이 테이블의 가장 이른 구간보다 과거면 미매칭(에러) — 세율
    근거가 없는 시점의 계산은 사실 보증 불가라 거부(보수적, Fidelity §2.1).
    """
    entries = _RATE_TABLE.get(market)
    if entries is None:
        raise SecuritiesTransactionTaxError(
            f"unknown market: {market!r} "
            f"(허용: {sorted(VALID_MARKETS)})"
        )
    for entry in entries:
        if trade_date >= entry.effective_from:
            return entry
    # 가장 이른 구간보다 과거 거래일 — 효력 세율 근거 없음(보수적 거부).
    earliest = min(e.effective_from for e in entries)
    raise SecuritiesTransactionTaxError(
        f"trade_date {trade_date.isoformat()} 가 {market!r} 세율 테이블의 "
        f"가장 이른 효력일({earliest.isoformat()})보다 과거 — 효력 세율 근거 없음"
    )


def calculate_securities_transaction_tax(
    *,
    market: str,
    trade_amount: Decimal,
    trade_date: date,
) -> TaxResult:
    """증권거래세 계산 — 거래금액 × 효력일 매칭 시장구분 세율(ADR-0030 D1/D2).

    증권거래세는 **매도** 거래세다. 본 함수는 매도 거래금액에 그 시점 효력 있던
    시장구분별 합산 세율(증권거래세 + 농어촌특별세)을 곱한 **산술 사실**만 반환한다.
    대주주 판정·보유기간·손익통산 같은 개별 상황 판단은 일절 하지 않는다(D2).
    양도소득세도 계산하지 않는다(D1 — 세무사법 경계, 자문 완료 전 유보).

    Args:
        market: 시장구분 — VALID_MARKETS 중 하나(kospi/kosdaq/konex/unlisted).
            매도 종목이 상장된 시장(비상장은 장외 양도).
        trade_amount: 매도 거래금액(원, KRW). 0 이상. 0 이면 세액 0(사실).
        trade_date: 매도 거래일. 효력일 구간 매칭 기준 — 과거 거래일은 그 시점
            효력 세율을 적용(효력일 freeze, ADR-0030 D4).

    Returns:
        TaxResult — 세액(원, 정수 quantize) + 적용 세율 + 효력일 + 법령 출처.
        결정적·stateless. 같은 입력은 항상 같은 결과.

    Raises:
        SecuritiesTransactionTaxError: 잘못된 시장구분, 음수 거래금액, 세율
            테이블의 가장 이른 효력일보다 과거인 거래일.
    """
    if market not in VALID_MARKETS:
        raise SecuritiesTransactionTaxError(
            f"unknown market: {market!r} (허용: {sorted(VALID_MARKETS)})"
        )
    if trade_amount < 0:
        raise SecuritiesTransactionTaxError(
            f"trade_amount must be >= 0, got {trade_amount}"
        )

    entry = _resolve_rate_entry(market, trade_date)

    with localcontext() as ctx:
        ctx.prec = _DECIMAL_PRECISION
        ctx.rounding = ROUND_HALF_UP
        # 세액 = 거래금액 × 합산 세율. 원 단위 정수 quantize(소수 원 없음 —
        # 거래세 원천징수는 원 단위, ROUND_HALF_UP 통상 반올림).
        raw_tax = trade_amount * entry.rate
        tax_amount = raw_tax.quantize(_KRW_QUANTUM, rounding=ROUND_HALF_UP)

    return TaxResult(
        tax_amount=tax_amount,
        applied_rate=entry.rate,
        effective_date=entry.effective_from,
        legal_source=entry.legal_source,
    )
