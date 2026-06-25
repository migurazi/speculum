"""dart_account_mapper — IFRS taxonomy ID → canonical account key.

DART 공시의 재무제표는 K-IFRS taxonomy ID (e.g., `ifrs-full_Assets`) 로 발표.
service / repository / factor evaluator 는 canonical key (e.g.,
`total_assets`) 로 작동 — 본 mapper 가 정규화 layer.

설계 결정:

1. **빌트인 minimum viable mapping (~15 계정)** — M0 의 factor pack (ADR-0004
   PER/PBR/ROE/PSR/부채비율) 산출에 필요한 핵심 계정. 운영 데이터 수집 후 확장.

2. **미매핑 시 warning + identifier 보존** — silent drop 안 함. canonical key
   는 `unmapped:<ifrs_id>` 형식 → service 가 알 수 있음.

3. **dual variant (consolidated / separate)** — ADR-0005 의 multi-id 패턴.
   같은 IFRS ID 가 sj_div (BS/IS/CF) 와 결합되어 canonical key 결정. 하지만
   본 cycle MVP 는 IFRS ID 만 — ifrs_type 은 별도 컬럼.

4. **immutable mapping dict** — `MappingProxyType` 로 read-only 보장.

관련 ADR:
- ADR-0003 D2 — canonical schema (account)
- ADR-0004 — 시가총액·EPS·PER 산출 정의 (필요 계정)
- ADR-0005 — K-IFRS 연결/별도 (ifrs_type 별도 차원)
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

__all__ = [
    "UNMAPPED_PREFIX",
    "is_no_standard_code",
    "is_unmapped",
    "map_ifrs_account",
]


# 미매핑 계정의 canonical key prefix. service 가 본 prefix 로 미매핑 식별.
UNMAPPED_PREFIX: Final[str] = "unmapped:"

# DART account_id 가 "표준계정코드 없음"을 의미하는 sentinel 값.
#
# DART `fnlttSinglAcntAll` 응답에서 표준계정코드가 없는 line item 은 `account_id`
# 컬럼이 빈 문자열이거나, 문자 그대로 "-표준계정코드 미사용-" 으로 채워진다.
# 이 행들은 표준 계정 식별자가 없어 canonical 화가 불가능하다 — 서로 다른 실
# line item 이 전부 동일한 `unmapped:<empty>` 또는 `unmapped:-표준계정코드 미사용-`
# 키로 collapse 되어, 단일 fetch 내에 같은 canonical key 가 2건 이상 생긴다.
# 그 결과 적재 단계의 PIT 무결성 가드("1 fetch = canonical 계정당 1 row",
# dart_daily) 가 **정상 데이터를 corruption 으로 오인**해 종목 전체 적재를 실패시킨다
# (특히 반기보고서처럼 sentinel 행이 여러 개일 때). 어떤 factor 도 이 unmapped 키를
# 소비하지 않으므로 (db_field_provider 의 어떤 FieldResolution 도 참조 안 함),
# 적재 가치가 0이면서 가드만 깨뜨리는 노이즈다 → 파싱 단계에서 제외한다.
_NO_STANDARD_CODE_SENTINELS: Final[frozenset[str]] = frozenset({
    "",
    "-표준계정코드 미사용-",
})


# M0 minimum viable mapping — ADR-0004 의 PER/PBR/ROE/PSR/부채비율 산출에 필요한
# 핵심 계정. K-IFRS Taxonomy 2022 기준.
#
# IFRS ID → canonical key:
#   "ifrs-full_X" = K-IFRS 공식 taxonomy
#   "dart_X"      = DART 자체 정의 (한국 추가 계정)
_MAPPING: Final[Mapping[str, str]] = MappingProxyType({
    # ===== 재무상태표 (Balance Sheet) =====
    "ifrs-full_Assets": "total_assets",
    "ifrs-full_CurrentAssets": "current_assets",
    "ifrs-full_NoncurrentAssets": "noncurrent_assets",
    "ifrs-full_Liabilities": "total_liabilities",
    "ifrs-full_CurrentLiabilities": "current_liabilities",
    "ifrs-full_NoncurrentLiabilities": "noncurrent_liabilities",
    "ifrs-full_Equity": "total_equity",
    # 지배기업 소유주 지분 (자기자본) — ROE 분모.
    "ifrs-full_EquityAttributableToOwnersOfParent": "equity_attributable_to_owners",

    # ===== 손익계산서 (Income Statement) =====
    "ifrs-full_Revenue": "revenue",
    # 영업이익.
    "dart_OperatingIncomeLoss": "operating_income",
    # 당기순이익.
    "ifrs-full_ProfitLoss": "net_income",
    # 지배기업 소유주 귀속 순이익 — EPS 분자.
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "net_income_attributable_to_owners",

    # ===== 주식 발행 정보 =====
    # 기본주당이익 (EPS).
    "ifrs-full_BasicEarningsLossPerShare": "basic_eps",
    # 희석주당이익.
    "ifrs-full_DilutedEarningsLossPerShare": "diluted_eps",

    # ===== 현금흐름표 (Cash Flow) =====
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "cash_flow_operating",
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": "cash_flow_investing",
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": "cash_flow_financing",

    # ===== 리츠(REIT) FFO/배당 구성 계정 — ADR-0023 D3-c / D8 =====
    # P0 실 DART 검증(2026-06-02, 신한알파/ESR/SK리츠)으로 account_id 실재 확인.
    # 이들은 *데이터 정규화*(canonical key 승격)이지 factor 정의가 아니므로 factor
    # pack content_hash 무변경 → 재현성 무관. 리츠 factor(ffo-multiple:reit 등)는
    # reference custom pack / PackRegistry 로 별도 제공(ADR-0023 D8 재현성 경로).
    #
    # FFO = net_income(ifrs-full_ProfitLoss, 기존 매핑) + depreciation. 감가상각은
    # 현금흐름표의 손익조정 항목 — 일부 리츠만 별도 보고(ESR), 일부는 통합조정
    # (AdjustmentsForReconcileProfitLoss)이라 감가상각 미보고 리츠는 FFO 자연 N/A.
    "ifrs-full_AdjustmentsForDepreciationExpense": "depreciation_expense",
    # 재무활동 배당금 지급 — dividend-yield:reit 분자(연 배당총액). 전 리츠 실재.
    "ifrs-full_DividendsPaidClassifiedAsFinancingActivities": "dividends_paid_annual",
    # 투자부동산 — 리츠 NAV 구성 자산(자산총계-부채총계 NAV 의 핵심 항목). 전 리츠 실재.
    "ifrs-full_InvestmentProperty": "investment_property",
})


def map_ifrs_account(ifrs_account_id: str) -> str:
    """IFRS taxonomy ID → canonical key.

    Args:
        ifrs_account_id: DART 응답의 account_id 컬럼 (예: "ifrs-full_Assets").

    Returns:
        canonical key (예: "total_assets"). 미매핑 시 `UNMAPPED_PREFIX +
        ifrs_account_id` (예: "unmapped:ifrs-full_SomeRareAccount"). silent
        drop 안 함 — service 가 미매핑 인식 가능.

    Notes:
        매핑되지 않은 계정도 DB 에 그대로 저장 → 운영 데이터 분석으로
        매핑 확장 가능. 본 함수는 lookup only — DB write 의무 없음.
    """
    if not isinstance(ifrs_account_id, str) or not ifrs_account_id.strip():
        # 빈 입력은 명시 unmapped — DART 응답 schema drift 의 fail-soft.
        return f"{UNMAPPED_PREFIX}<empty>"
    return _MAPPING.get(ifrs_account_id, f"{UNMAPPED_PREFIX}{ifrs_account_id}")


def is_unmapped(canonical_key: str) -> bool:
    """canonical key 가 미매핑 (`unmapped:...` prefix) 인지 검사.

    service / factor evaluator 가 본 helper 로 미매핑 row skip / warning 분기.
    """
    return canonical_key.startswith(UNMAPPED_PREFIX)


def is_no_standard_code(ifrs_account_id: object) -> bool:
    """DART account_id 가 "표준계정코드 없음" sentinel 인지 검사.

    빈 문자열 또는 "-표준계정코드 미사용-" (`_NO_STANDARD_CODE_SENTINELS`) 이면 True.
    adapter 의 `_parse_response` 가 본 helper 로 sentinel 행을 파싱 단계에서 제외해,
    canonical 화 불가한 비표준 행들이 동일 `unmapped:` 키로 충돌하여 PIT 무결성
    가드를 false-positive 로 트리거하는 것을 차단한다(상수 docstring 참조).

    `ifrs_account_id` 가 str 이 아니면(schema drift) True 로 간주해 안전하게 제외한다.
    """
    if not isinstance(ifrs_account_id, str):
        return True
    return ifrs_account_id.strip() in _NO_STANDARD_CODE_SENTINELS
