"""DbUniverseDistributionProvider — 유니버스-상대 연산 (zscore/percentile/
min_max_scale) 의 분포 컨텍스트 생산용 구현체 (ADR-0022 D2/D3).

T70b 가 `factor_evaluator.UniverseDistributionProvider` Protocol 과 evaluator
dispatch (`_apply_universe_relative`) 만 깔아뒀고 (분포 미주입 시 정식 N/A
`universe_distribution_required`), 본 모듈이 그 provider 를 구현해 실제 분포를
산출한다.

설계 원칙 (ADR-0022 D2/D3 + market.py 의 유니버스 순회 패턴):

1. **field 별 분포 lazy 계산 + 캐싱** (ADR-0022 D2) — 어떤 field 에 대해
   zscore/percentile/min_max_scale 이 처음 요청되면, **as_of active universe 의
   각 종목을 `DbFieldProvider` 로 평가**해 그 field 값들의 분포 (정렬 배열 +
   통계량) 를 1 회 계산·캐싱 (O(N)). 같은 field 의 재요청은 캐시 hit (O(log N)
   percentile bisect / O(1) zscore·min_max). 분포는 종목마다 재계산하면 O(N²)
   이므로, 본 provider 를 request 단위 1 인스턴스로 공유하면 field별 1 회 보장.

2. **PIT 분포 규칙** (ADR-0022 D3 + ADR-0023 D5 — 재현성 핵심):
   - 모집단 = `stocks_repo.list_active(as_of=self.as_of)` 중 **평가 대상 종목과
     같은 security_type** (ADR-0023 D5 partition). as_of 시점 active universe 를
     자산군별로 쪼개, 어떤 종목의 percentile/zscore/min_max 는 **그 종목과 같은
     자산군 (common/preferred/etf/reit) 모집단** 으로만 산출한다. 재무 factor 는
     N/A 제외 (D3-3) 로 이종 자산군이 자동 배제되나, **가격·시총 등 N/A 가 아닌
     field** 는 partition 이 없으면 이종 혼합되어 §2.7 의미가 붕괴 ("이 보통주
     시총이 ETF 포함 전체에서 몇 %" 는 무의미). partition 이 이 구멍을 막는다.
   - list_active 는 `listing_date <= as_of AND (delisting_date is None
     OR delisting_date > as_of)` 이므로, **as_of 시점에 살아있던 종목** (이후
     폐지 예정이어도 포함) 만 — survivorship bias 방지 (오늘 시점 생존 종목이
     아닌 as_of 시점 유니버스). as_of 이전 폐지 종목 / as_of 이후 상장 종목 제외.
   - 분포 입력값 PIT — 각 종목 field 를 그 종목의 as_of active fact 로 평가
     (`DbFieldProvider` 가 effective_date <= as_of 강제).
   - N/A 종목 모집단 제외 — percentile/zscore/min_max 분모는 valid (non-N/A)
     모집단만 (factor_evaluator N/A propagation 일관).
   - tie-breaking 결정성 — 동값 percentile 처리 방식 고정 (아래 percentile docstring).
   - provider.as_of == evaluate(as_of) (Protocol invariant — evaluator 가 검증).

3. **Decimal 결정성** — mean/stddev/percentile 모두 Decimal 산술 (float 경유
   없음). evaluator / price_adjuster / market.py 와 동일 의미론. 같은 as_of +
   batch_id → 같은 fact → 같은 분포 → 같은 산출값 (M1 batch_id freeze 연장).

4. **분포 자체의 freeze** — 분포는 batch_id (fact 버전, M1) 로 이미 freeze 됨.
   본 provider 의 **모집단 정의 / N-A 제외 / tie-break 규칙** 은 정책이며,
   `snapshot_versions.DISTRIBUTION_POLICY_VERSION` 으로 버전화 (규칙 변경 포착).

관련 ADR / 문서:
- ADR-0022 D2 (유니버스-상대 vs 종목-국소), D3 (PIT 분포 규칙).
- factor_evaluator.py (UniverseDistributionProvider Protocol, _apply_universe_relative).
- db_field_provider.py (DbFieldProvider — 단일 종목 factor 평가).
- market.py compute_market_overview (유니버스 순회 + 종목별 DbFieldProvider 패턴).
- snapshot_versions.py (DISTRIBUTION_POLICY_VERSION freeze).
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from app.repositories.pit_protocols import (
    CorporateActionRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    TreasurySharesRepository,
)
from app.services.db_field_provider import DbFieldProvider

if TYPE_CHECKING:
    from app.repositories.stocks_master_repository import StocksMasterRepository
    from app.services.factor_evaluator import FactorEvaluator
    from app.services.factor_pack import LoadedPack

__all__ = [
    "DEFAULT_TARGET_SECURITY_TYPE",
    "DISTRIBUTION_POLICY_VERSION",
    "SMALL_SAMPLE_THRESHOLD",
    "DbUniverseDistributionProvider",
    "FieldDistribution",
]


# 분포 메서드의 default 평가 대상 자산군 (ADR-0023 D5/D4). 호출자가
# `bind_target_security_type` 로 명시 설정하기 전까지의 모집단 = 보통주 (common).
# 기존 common-only universe (M0~M1) 동작 불변 — partition 미설정 컨텍스트 (직접
# zscore/percentile 호출하는 단위 테스트 포함) 는 보통주 모집단으로 산출.
DEFAULT_TARGET_SECURITY_TYPE: Final[str] = "common"


# off-universe(as_of active partition 어디에도 없는) code 의 sentinel target —
# V-M2-2 (m2-conformance-review). bind_target_security_type 이 active universe 에
# 없는 code(미상장/폐지 직후 등)를 받으면 이 sentinel 로 설정한다. 어떤 실제
# security_type(common/preferred/etf/reit)과도 겹치지 않으므로 partition 조회가
# 빈 모집단 → universe-상대 op(percentile/zscore/min_max) 자연 N/A + sample_size 0.
# off-universe code 를 default "common" 모집단으로 귀속시켜 **모집단 불일치 값**을
# 산출하던 갭을 차단한다(§2.1 Fidelity — 모집단 외 종목의 percentile 은 무의미하므로
# 틀린 값 대신 정직한 N/A). schema 의 security_type pattern(소문자 단어)과 겹치지
# 않는 형식(`__` prefix)이라 실제 자산군과 충돌 불가.
_OFF_UNIVERSE_TARGET: Final[str] = "__off_universe__"


# 유니버스-상대 분포 정책 버전 — 모집단 정의 (as_of active, 폐지 포함/미상장
# 제외) / N-A 종목 제외 / tie-breaking (<= 기반 cumulative-count) 규칙의 버전.
# 이 규칙들이 바뀌면 같은 as_of+batch_id 라도 percentile 이 달라질 수 있으므로,
# Screen Run snapshot 의 data_versions 에 freeze (snapshot_versions.py 합류).
# 분포의 fact 자체는 batch_id 로 이미 freeze — 본 버전은 "규칙" 변경을 포착.
#
# "1.0" → "1.1" (ADR-0023 D5/D6 — M2 T78 우선주 트랙): 모집단을 security_type
# 별로 **partition** 하는 규칙을 도입. 같은 as_of+batch_id 라도 모집단 정의가
# "as_of active 전체" 에서 "as_of active 중 같은 security_type" 으로 바뀌므로
# percentile/zscore/min_max 가 달라질 수 있다 → 정책 변경 = 버전 bump (ADR-0023
# D6: 옛 저장 run 은 옛 "1.0" 으로 freeze 재현, 신 run 은 신 "1.1"). ADR-0022
# D3-5 가 "모집단 정의를 이 버전으로 버전화" 라고 이미 명문화 — universe 확장은
# 이 버전을 올리는 명시적 사건.
DISTRIBUTION_POLICY_VERSION: Final[str] = "1.1"


# 소표본 디스클로저 트리거 임계값 (ADR-0024 D1) — 모집단 n < 30 인 universe-상대
# 산출 (percentile/zscore/min_max_scale) 을 "소표본" 으로 표식하기 위한 경계.
#
# 30 은 **단일 디스클로저 트리거**일 뿐 통계적 정설이 아니다 (ADR-0024 D1). 연산별
# 소표본 문제의 결이 다르다: percentile/min_max 의 소표본 문제는 **분위/극값 해상도**
# (n=3 이면 percentile 이 0/33/67/100 의 거친 계단함수, min_max 는 극값 2 점 의존)
# 이지 중심극한정리 (CLT) 가 아니다 — CLT 는 평균·표준편차에 의존하는 zscore 에만
# 느슨히 닿는다. 따라서 percentile/min_max 에 CLT 근거를 대지 않으며, 30 은 세 연산의
# "모집단이 작아 상대지표 해석이 거칠어지는" 구간을 같은 임계로 묶는 실용 선택이다.
#
# **result_hash 무관 (ADR-0024 D4 b)**: 본 임계값은 *표시 게이트* 측이지 *분포 정책
# freeze* 측이 아니다. percentile/zscore/min_max_scale 의 반환 Decimal 값이 임계값을
# 참조조차 하지 않으므로 (분포 op 코드가 임계 미개입), 임계값을 바꿔도 값·result_codes
# 불변 → result_hash·DISTRIBUTION_POLICY_VERSION 무영향. 그러므로 본 임계는
# DISTRIBUTION_POLICY_VERSION 에 넣지 않는다 (과잉 freeze 회피). 변경 시 changelog
# 명기로만 추적. 디스클로저는 값을 만든 *뒤* 표시층 (evaluate route) 에서만 부착한다
# (ADR-0024 D4 c — result_codes 불변식; 분포 op 는 이 상수를 참조하면 안 됨).
SMALL_SAMPLE_THRESHOLD: Final[int] = 30


@dataclass(frozen=True, slots=True)
class FieldDistribution:
    """단일 field 의 유니버스 분포 통계 — lazy 계산 후 캐싱 단위.

    valid (non-N/A) 종목 값들만으로 구성 (ADR-0022 D3 — N/A 종목 모집단 제외).

    Attributes:
        sorted_values: 오름차순 정렬된 valid 종목 값들. percentile 의 tie-break
            결정성을 위해 정렬 (동값은 인접). 비어있으면 모집단 부족 (전 종목 N/A).
        n: 모집단 크기 (= len(sorted_values)). zscore/percentile/min_max 분모.
        mean: 산술 평균 (Decimal). n==0 이면 None.
        stddev: 모표준편차 (population stddev, ddof=0; Decimal). n==0 또는 전
            종목 동값 (분산 0) 이면 stddev==0 → zscore N/A. n<=1 이면 None.
        min_value: 최소값 (sorted_values[0]). n==0 이면 None.
        max_value: 최대값 (sorted_values[-1]). n==0 이면 None.
    """

    sorted_values: tuple[Decimal, ...]
    n: int
    mean: Decimal | None
    stddev: Decimal | None
    min_value: Decimal | None
    max_value: Decimal | None


def _build_field_distribution(values: Sequence[Decimal]) -> FieldDistribution:
    """valid 종목 값들 → FieldDistribution 통계 (Decimal 산술).

    - mean = Σv / n.
    - stddev = sqrt(Σ(v - mean)² / n) — **모표준편차** (ddof=0). 유니버스 전체가
      모집단 (표본 아님) 이므로 ddof=0. n<=1 이면 표준편차 정의 불가 → None
      (zscore N/A). Decimal.sqrt (localcontext 불필요 — Decimal 기본 context
      prec=28 충분, evaluator 의 localcontext 밖에서 호출되나 통계 quantize 는
      하지 않고 raw Decimal 유지하여 결정성 보존).
    """
    n = len(values)
    if n == 0:
        return FieldDistribution(
            sorted_values=(), n=0, mean=None, stddev=None,
            min_value=None, max_value=None,
        )
    ordered = tuple(sorted(values))
    total = sum(ordered, Decimal(0))
    mean = total / Decimal(n)
    if n <= 1:
        # 표준편차 정의 불가 (단일 종목) — zscore N/A. min_max 도 max==min → N/A.
        stddev: Decimal | None = None
    else:
        variance = sum(((v - mean) ** 2 for v in ordered), Decimal(0)) / Decimal(n)
        stddev = variance.sqrt()
    return FieldDistribution(
        sorted_values=ordered, n=n, mean=mean, stddev=stddev,
        min_value=ordered[0], max_value=ordered[-1],
    )


class DbUniverseDistributionProvider:
    """`UniverseDistributionProvider` Protocol 의 생산용 구현체 (ADR-0022 D2/D3).

    한 request (as_of + pack + evaluator 고정) 의 유니버스-상대 op 분포 컨텍스트.
    field 별 분포를 lazy 하게 1 회 계산·캐싱하므로 **request 단위 1 인스턴스로
    공유** 해야 O(N) 보장 (종목마다 새 인스턴스면 field별 분포가 O(N²)).

    Attributes:
        as_of: 분포가 freeze 된 PIT 기준 일자 (Protocol 요구; evaluate(as_of=...)
            와 일치 강제 — evaluator 가 검증).
    """

    as_of: date

    def __init__(
        self,
        *,
        as_of: date,
        stocks_repo: StocksMasterRepository,
        pack: LoadedPack,
        evaluator: FactorEvaluator,
        price_repo: PriceRepository,
        financial_repo: FinancialRepository,
        corporate_action_repo: CorporateActionRepository | None = None,
        market_cap_repo: MarketCapRepository | None = None,
        treasury_repo: TreasurySharesRepository | None = None,
        macro_repo: MacroIndicatorRepository | None = None,
    ) -> None:
        """Args:
            as_of: PIT 기준 — 모집단 (list_active) + 분포 입력값 모두 이 시점.
            stocks_repo: 모집단 (as_of active universe) source.
            pack: 파생 필드 (derived_factor) 해소용 LoadedPack — 종목별
                DbFieldProvider 구성에 전달 (market_cap_ex_treasury 등).
            evaluator: 파생 필드 해소용 FactorEvaluator — DbFieldProvider 에 전달.
                **유니버스-상대 op 의 분포 산출은 primitive/파생 field 의 종목값
                만 필요** (get_scalar) 하므로, 본 evaluator 는 DbFieldProvider 의
                derived_factor 해소에만 쓰이고 유니버스-상대 op 을 재귀 호출하지
                않는다 (분포 계산 중 분포 요청 = 무한재귀 없음).
            price_repo / financial_repo / corporate_action_repo / market_cap_repo
            / treasury_repo: 종목별 DbFieldProvider 구성용 fact repo. market.py 의
                compute_market_overview 와 동일 set.
            macro_repo: ECOS 거시지표 repository (M2 T81). None 이면 ECOS field
                (ecos_base_rate / ecos_cpi) 정식 N/A. 분포 모집단 산출에도 적용.
        """
        self.as_of = as_of
        self._stocks_repo = stocks_repo
        self._pack = pack
        self._evaluator = evaluator
        self._price_repo = price_repo
        self._financial_repo = financial_repo
        self._ca_repo = corporate_action_repo
        self._market_cap_repo = market_cap_repo
        self._treasury_repo = treasury_repo
        self._macro_repo = macro_repo

        # (security_type, field) → 분포 캐시 (ADR-0023 D5 partition). 같은
        # 자산군·field 의 zscore/percentile/min_max 가 분포를 공유 — partition별
        # field별 1 회 O(N_partition) 계산 후 재사용 (ADR-0022 D2 캐싱 연장).
        self._distribution_cache: dict[tuple[str, str], FieldDistribution] = {}
        # security_type → as_of active 종목코드 partition (ADR-0023 D5). 1 회
        # list_active 후 자산군별로 분리 캐싱 (모든 field 공유).
        self._codes_by_security_type: dict[str, tuple[str, ...]] | None = None

        # 현재 평가 대상 종목의 security_type — 분포 메서드 (zscore/percentile/
        # min_max_scale) 가 어느 자산군 모집단으로 산출할지 결정 (ADR-0023 D5).
        # Protocol 시그니처 (field, value) 를 보존하기 위해 provider 상태로 들고,
        # 호출자가 종목별 평가 전 `bind_target_security_type(code)` 로 설정한다.
        # default = "common" — 기존 common-only 동작 불변 (ADR-0023 D4/D7 — 미설정
        # 컨텍스트는 보통주 모집단, 기존 단위 테스트·common universe 회귀 무영향).
        self._target_security_type: str = DEFAULT_TARGET_SECURITY_TYPE

    # ---------------------------------------------------------------------
    # 분포 lazy 계산 + 캐싱
    # ---------------------------------------------------------------------

    def bind_target_security_type(self, code: str) -> str:
        """평가 대상 종목 (code) 의 security_type 으로 분포 partition 컨텍스트 설정.

        ADR-0023 D5 — 분포 메서드 (zscore/percentile/min_max_scale) 는 Protocol
        시그니처 (field, value) 만 받으므로 평가 대상의 자산군을 직접 알 수 없다.
        호출자 (factor_packs.py 의 종목별 평가 루프 등) 가 각 code 평가 직전 본
        메서드를 호출하면, provider 가 그 code 의 security_type 을 모집단 partition
        키로 들고 이후 분포 산출이 **같은 자산군 모집단** 으로 이뤄진다.

        code 의 security_type 은 as_of active partition 에서 조회 (list_active
        Record 의 security_type 필드). active 가 아니거나 (미상장/폐지 직후 등)
        partition 에 없으면 **off-universe** — `_OFF_UNIVERSE_TARGET` sentinel 로
        설정해 이후 universe-상대 분포가 **빈 모집단(자연 N/A)** 이 되게 한다.

        V-M2-2 (m2-conformance-review): 이전엔 off-universe code 를 default
        "common" 으로 귀속시켜 common 모집단 percentile(모집단 불일치 값)을
        산출했다. off-universe 종목(예: 폐지 직후 우선주)의 percentile 은 어느
        모집단에도 속하지 않아 무의미하므로, 틀린 값 대신 정직한 N/A 가 옳다
        (§2.1 Fidelity). 종목-국소 factor(PER 등)는 분포 무관이라 영향 없음.

        Returns:
            설정된 target — active 면 그 security_type, off-universe 면
            `_OFF_UNIVERSE_TARGET` sentinel. 멱등 — 같은 code 재호출 시 동일.
        """
        partitions = self._codes_by_security_type_map()
        # 못 찾으면 off-universe → sentinel(빈 모집단 → universe-상대 op 자연 N/A).
        resolved = _OFF_UNIVERSE_TARGET
        for security_type, codes in partitions.items():
            if code in codes:
                resolved = security_type
                break
        self._target_security_type = resolved
        return resolved

    def _codes_by_security_type_map(self) -> dict[str, tuple[str, ...]]:
        """as_of active universe 를 security_type 별로 partition — 1 회 조회 캐싱.

        모집단 정의 (ADR-0022 D3 + ADR-0023 D5): list_active = as_of 시점 active
        (이후 폐지 예정 종목 포함 — survivorship bias 방지, as_of 이전 폐지 / 이후
        상장 제외) 를 security_type (common/preferred/etf/reit) 별로 분리. 같은
        자산군 모집단으로만 분포를 산출하기 위함 (D5 — 이종 자산군 혼합 차단).
        current_code 없는 lineage (폐지 lineage) 는 fact 조회 키 부재로 제외
        (market.py 와 동일 방어).
        """
        if self._codes_by_security_type is not None:
            return self._codes_by_security_type
        buckets: dict[str, list[str]] = {}
        for record in self._stocks_repo.list_active(as_of=self.as_of):
            if not record.current_code:
                continue
            buckets.setdefault(record.security_type, []).append(record.current_code)
        partitions = {st: tuple(codes) for st, codes in buckets.items()}
        self._codes_by_security_type = partitions
        return partitions

    def _list_active_codes(self, security_type: str) -> tuple[str, ...]:
        """as_of active universe 중 `security_type` partition 의 종목코드.

        ADR-0023 D5 — 분포 모집단을 자산군별로 partition. 해당 자산군에 active
        종목이 없으면 빈 tuple (그 자산군 분포는 빈 모집단 → 전부 N/A).
        """
        return self._codes_by_security_type_map().get(security_type, ())

    def _get_distribution(
        self, field: str, security_type: str | None = None,
    ) -> FieldDistribution:
        """`security_type` 자산군 내 field 의 유니버스 분포 — lazy 계산 후 캐싱.

        ADR-0023 D5 — 모집단을 security_type 별로 partition. `security_type` 미지정
        (None) 시 현재 바인딩된 평가 대상 자산군 (`_target_security_type`, default
        "common") 사용. 분포 메서드 (zscore/percentile/min_max) 의 단일 진입점이며,
        cache 는 `(security_type, field)` 키로 partition별 1 회 O(N_partition) 계산.

        모집단 각 종목을 `DbFieldProvider` 로 만들어 `get_scalar(field)` 로 그
        종목의 field 값을 평가 (market.py 의 유니버스 순회 패턴 재사용). N/A
        (None) 종목은 모집단에서 제외 (ADR-0022 D3 N/A 제외). valid 값들로
        통계량 구성 후 캐싱.

        **PIT** — 각 DbFieldProvider 는 as_of 고정 → effective_date <= as_of
        fact 만 (provider 가 강제). 분포 입력값 PIT (ADR-0022 D3).
        """
        st = security_type if security_type is not None else self._target_security_type
        cache_key = (st, field)
        cached = self._distribution_cache.get(cache_key)
        if cached is not None:
            return cached

        valid_values: list[Decimal] = []
        for code in self._list_active_codes(st):
            provider = DbFieldProvider(
                code=code,
                as_of=self.as_of,
                price_repo=self._price_repo,
                financial_repo=self._financial_repo,
                corporate_action_repo=self._ca_repo,
                market_cap_repo=self._market_cap_repo,
                treasury_repo=self._treasury_repo,
                macro_repo=self._macro_repo,
                factor_pack=self._pack,
                evaluator=self._evaluator,
            )
            value = provider.get_scalar(field)
            if value is not None:
                # N/A 종목 모집단 제외 (ADR-0022 D3). None 은 산입하지 않음.
                valid_values.append(value)

        distribution = _build_field_distribution(valid_values)
        self._distribution_cache[cache_key] = distribution
        return distribution

    # ---------------------------------------------------------------------
    # UniverseDistributionProvider Protocol — 유니버스-상대 산출
    # ---------------------------------------------------------------------

    def zscore(self, field: str, value: Decimal) -> Decimal | None:
        """value 의 유니버스 분포 내 z-score = (value - μ) / σ (모표준편차).

        모집단 = 현재 바인딩된 자산군 (`_target_security_type`, ADR-0023 D5
        partition). 호출자가 `bind_target_security_type(code)` 미호출이면 보통주
        (common) 모집단 — 기존 common-only 동작 불변.

        N/A (None):
        - 모집단 부족 (valid 종목 0 또는 1) → stddev None.
        - σ == 0 (전 valid 종목 동값) → 분모 0, z-score 정의 불가.
        Decimal 산술 — float 경유 없음 (결정성).
        """
        dist = self._get_distribution(field)
        if dist.mean is None or dist.stddev is None:
            return None
        if dist.stddev == 0:
            # 전 종목 동값 — 분산 0, z-score 정의 불가 (ADR-0022 / 지시서 경계).
            return None
        return (value - dist.mean) / dist.stddev

    def percentile(self, field: str, value: Decimal) -> Decimal | None:
        """value 의 유니버스 분포 내 percentile [0, 100].

        **정의 (tie-breaking 결정성 — `<=` 기반 cumulative-count):**
            percentile = (분포 내 value 이하인 종목 수 / n) × 100
                       = (count(v <= value) / n) × 100

        - 동값 (tie) 은 모두 "이하" 로 함께 카운트 → 동값 종목은 **같은
          percentile** (서로 다른 임의 순위 부여 금지 — 비결정 정렬 회피). 이
          방식 (`<=` cumulative)을 택한 이유: (1) 정렬 배열에서 `bisect_right`
          로 O(log N) 결정적 계산, (2) "유니버스의 X% 가 이 값 이하" 라는 §2.7
          Observation 의미가 직관적, (3) ADR-0007 D2.2 의 percentile 표시 (분포
          내 위치) 와 정합. min 값 percentile > 0 (자기 자신 포함), max 값
          percentile == 100.
        - value 가 모집단 밖이어도 (분포에 없는 종목값) 사이 위치로 정의됨 —
          bisect 가 삽입 위치를 결정적으로 산출. 본 op 의 호출자 (evaluator) 는
          평가 중 종목값을 같은 분포 field 로 fetch 하므로 통상 모집단 내.

        N/A (None): 모집단 비어있음 (valid 종목 0). Decimal 산술.
        """
        dist = self._get_distribution(field)
        if dist.n == 0:
            return None
        # bisect_right = count(v <= value) — 동값을 모두 "이하" 로 포함 (tie 동률).
        count_le = bisect.bisect_right(dist.sorted_values, value)
        return (Decimal(count_le) / Decimal(dist.n)) * Decimal(100)

    def min_max_scale(self, field: str, value: Decimal) -> Decimal | None:
        """value 의 (value - min) / (max - min) ∈ [0, 1] (분포 정규화).

        N/A (None):
        - 모집단 비어있음 (valid 종목 0) → min/max None.
        - max == min (전 valid 종목 동값, 모집단 1 포함) → 분모 0, 정의 불가.
        Decimal 산술 — float 경유 없음.
        """
        dist = self._get_distribution(field)
        if dist.min_value is None or dist.max_value is None:
            return None
        span = dist.max_value - dist.min_value
        if span == 0:
            # 전 종목 동값 (모집단 1 종목 포함) — 분모 0, 정규화 정의 불가.
            return None
        return (value - dist.min_value) / span

    # ---------------------------------------------------------------------
    # 소표본 디스클로저 조회 (ADR-0024 D2/D5) — 표시 전용
    # ---------------------------------------------------------------------

    def sample_size(self, field: str) -> int:
        """현재 바인딩된 자산군 내 `field` 의 universe-상대 분포 모집단 크기 (= n).

        ADR-0024 D5 — universe-상대 산출 (percentile/zscore/min_max_scale) 의 값을
        *만든 뒤*, 표시층 (evaluate route) 이 그 값이 어느 크기의 모집단에서 나왔는지
        디스클로저하기 위한 **별도 조회 메서드**. percentile/zscore/min_max_scale 의
        반환 시그니처 (`Decimal | None`) 는 불변 — evaluator 무수정 (ADR-0024 D5: 반환
        확장은 evaluator 코어 전면 수정 + composite sample_size 전파 규칙 필요 → 기각,
        조회 메서드 채택).

        모집단 = 현재 바인딩된 자산군 (`_target_security_type`, ADR-0023 D5 partition)
        의 `field` 분포 — `bind_target_security_type(code)` 로 설정한 종목과 같은
        security_type 의 as_of active 모집단. 미바인딩 시 보통주 (common). n 은
        `FieldDistribution.n` (= N/A 종목 제외 valid 모집단 크기) 를 그대로 반환한다.

        **n 은 모집단의 결정적 함수** (ADR-0024 D4 a) — 같은 as_of·batch_id·partition
        규칙이면 byte 동일. 모집단 정의는 이미 `DISTRIBUTION_POLICY_VERSION` 이 freeze
        하므로 추가 freeze 키 불요. 본 메서드는 그 n 을 표면으로 노출만 한다.

        **소표본 판정은 표시층 책임** (ADR-0024 D4 c) — 본 메서드는 n 만 반환하고
        `SMALL_SAMPLE_THRESHOLD` 와 비교조차 하지 않는다. 임계 비교 (`n <
        SMALL_SAMPLE_THRESHOLD`) 는 값 생성 후 표시층에서만 수행 (분포 op 와 동일하게,
        분포 산출 경로가 임계를 참조하면 result_codes 가 임계에 의존하게 되어 D4 c
        불변식이 붕괴).

        n==1 percentile=100 케이스 (ADR-0024 D2): percentile 값은 100 으로 보존되고,
        본 메서드가 n=1 을 반환하여 표시층이 small_sample 디스클로저를 붙인다 (값을
        N/A 로 강등하지 않음 — "자기 혼자뿐인 분포의 100 percentile" 오인 방지).
        """
        # 분포 캐시 진입점 — 이미 산출된 분포면 캐시 hit, 아니면 lazy 1 회 계산.
        # universe-상대 op 평가 직후 호출되는 통상 경로에서는 cache hit 이라 추가
        # O(N) 비용 없음 (같은 (security_type, field) 키 공유).
        return self._get_distribution(field).n
