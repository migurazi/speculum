"""Screen Run 재현 — frozen batch 기준 재실행 + byte-동일 검증 (M1 T48c).

8 기둥 §2.10 Reproducibility 의 정점 — 저장된 Screen Run snapshot 을 그 시점의
frozen batch (`data_versions.krx_batch_id` / `dart_batch_id`) 기준으로 재실행하여
저장된 result_codes 와 byte-동일한지 검증.

설계 (work-order m1-t48c-reproduction.md §4 / Momus rev2 OKAY):

1. **frozen batch_id → BatchCutoff 해소** — snapshot.data_versions 의 krx/dart
   batch_id 를 `BatchRunRepository.get_run` 으로 해소하여 `BatchCutoff(started_at,
   id)` 구성. cutoff 는 `(started_at, id)` lexicographic 상한 (C#2 — freeze 의
   `ORDER BY started_at DESC, id DESC` 와 대칭).

2. **빈 batch_id = EXCLUDE_ALL (H#5)** — snapshot 시점에 그 source 의 성공
   batch 가 없었으면 (batch_id="") cutoff 는 "무필터" 가 아니라 `EXCLUDE_ALL_CUTOFF`
   — 그 source 의 fact 전부 제외. as_of 시점 데이터 없음 상태를 정확히 재현
   (look-ahead 누출 차단).

3. **batch_id 키 부재 = 무필터 (하위호환)** — 기존 "1.0" schema run (batch_id
   키 부재, `snapshot_versions.py:70-74`) 은 cutoff None (무필터) — 재현 시 현재
   데이터 기준 (batch 추적 도입 전 run 의 정직한 한계).

4. **cutoff 주입 screen_active_codes 재실행** — frozen as_of + conditions 로
   `screen_active_codes` 를 cutoff 주입하여 재실행 → result_codes. `matches` 는
   normalize 후 저장 result_codes 와의 동치.

관련 ADR / 문서:
- m1-milestone.md T48c / docs/work-orders/m1-t48c-reproduction.md
- ADR-0008 D7 (screen_runs.data_versions), 8 기둥 §2.10 Reproducibility
- snapshot_versions.py:119-125 (_BATCH_VERSION_KEYS), batch_run_repository.py
  (BatchCutoff / get_run / EXCLUDE_ALL_CUTOFF)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.repositories.batch_run_repository import (
    EXCLUDE_ALL_CUTOFF,
    BatchCutoff,
    BatchRunRepository,
)
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    DividendRepository,
    FinancialRepository,
    MacroIndicatorRepository,
    MarketCapRepository,
    PriceRepository,
    TreasurySharesRepository,
)
from app.repositories.stocks_master_repository import StocksMasterRepository
from app.schemas.screen import ConditionIn, OpEnum
from app.services.factor_evaluator import FactorEvaluator
from app.services.factor_pack import FactorPackError, LoadedPack
from app.services.pack_registry import BUILTIN_PACK_SLUG, PackRegistry
from app.services.screen_run import ScreenRunSnapshot, normalize_stock_codes

__all__ = [
    "BacktestReproduceResult",
    "ReproduceResult",
    "reproduce_backtest",
    "reproduce_run",
]

# data_versions 의 batch_id 키 — snapshot_versions._BATCH_VERSION_KEYS 와 일치.
# 단, **cutoff freeze 는 krx/dart 만** 적용 (price/financial fetch 전용). 세 번째
# batch 키 `dividend_batch_id`(source="FSC")는 의도적으로 미참조 — corporate action
# (dart) 과 동일하게 cutoff 미적용, diff 표시용 (reproduce_run §1 주석 / ADR-0033 D4
# / ADR-0035 D8 known-limit 참조).
_KRX_BATCH_KEY = "krx_batch_id"
_DART_BATCH_KEY = "dart_batch_id"

# data_versions 의 factor pack 키 — snapshot_versions.collect_active_policy_versions
# 가 freeze 하는 3 키. reproduce 는 이 frozen 값으로만 pack 을 재로드(ADR-0025 D5).
_PACK_SLUG_KEY = "factor_pack_slug"
_PACK_VERSION_KEY = "factor_pack_version"
_PACK_HASH_KEY = "factor_pack_content_hash"


@dataclass(frozen=True, slots=True)
class ReproduceResult:
    """재현 결과 — 재실행 result_codes + 저장 snapshot 과의 byte-동일 여부.

    Attributes:
        result_codes: frozen batch cutoff 로 재실행한 결정적 종목코드 (normalize
            통과 — 6 자리·정렬·dedup). snapshot.result_codes 와 동일 정규화. pack
            재로드 실패 시 빈 tuple.
        matches: `result_codes == snapshot.result_codes` (byte-동일 재현 성공).
            False 면 frozen batch 이후의 데이터/정책 변화 또는 재현 불가 (예:
            frozen batch row 삭제, frozen pack 재로드 불가/변조) 를 시사 — UI
            freshness banner / 운영 조사.
        reproduce_note: 재현 실패의 구체 사유 (pack 재로드 불가/변조 등). 성공
            (matches=True) 또는 batch 데이터 변화로 인한 단순 불일치면 None —
            endpoint 의 기본 note 가 적용. ADR-0025 D5 의 frozen pack 재로드 결함
            을 사용자에게 정확히 안내하기 위한 보조 필드.
    """

    result_codes: tuple[str, ...]
    matches: bool
    reproduce_note: str | None = None
    # pack_tampered: pack 변조(content_hash 불일치)로 재현 불가인 경우만 True.
    # 단순 부재(slug/version 미존재·custom body 없음)는 False — 변조와 구분.
    # client 가 #6 use-시점 표시 시 "변조" 배지를 명확히 판별하는 전제(ADR-0033/M5 #3b).
    # 기존 호출 무영향 — default False (하위호환).
    pack_tampered: bool = False


def _resolve_cutoff(
    batch_id_value: str | None,
    *,
    batch_run_repo: BatchRunRepository,
) -> BatchCutoff | None:
    """data_versions 의 batch_id 문자열 → BatchCutoff | None (재현 cutoff 해소).

    - 키 부재 (None) → None (무필터, 하위호환 — batch 추적 전 "1.0" run).
    - 빈 문자열 "" → EXCLUDE_ALL_CUTOFF (그 source fact 전부 제외, H#5).
    - 유효 UUID → get_run 으로 batch row 조회 후 `BatchCutoff(started_at, id)`.
      row 부재 (frozen batch 삭제 등) → EXCLUDE_ALL_CUTOFF (그 시점 데이터를
      정확히 재현할 수 없으므로 보수적으로 전부 제외 — look-ahead 누출 < 빈 결과).
    """
    if batch_id_value is None:
        # batch_id 키 자체가 없음 — 무필터 (batch 추적 도입 전 run, 하위호환).
        return None
    if batch_id_value == "":
        # snapshot 시점 그 source 성공 batch 없음 — 전부 제외 (H#5).
        return EXCLUDE_ALL_CUTOFF
    batch_uuid = UUID(batch_id_value)
    record = batch_run_repo.get_run(batch_uuid)
    if record is None:
        # frozen batch row 부재 — 그 시점 데이터를 재현할 근거 없음. 보수적으로
        # 전부 제외 (현재 데이터로 재현하면 look-ahead 누출).
        return EXCLUDE_ALL_CUTOFF
    return BatchCutoff(started_at=record.started_at, id=record.id)


@dataclass(frozen=True, slots=True)
class _PackResolution:
    """frozen data_versions → pack 재로드 결과 (성공 pack 또는 실패 사유).

    pack 이 None 이면 재현 불가(matches=False) — note 가 사유. pack 이 있으면
    그 pack 으로 screen 재실행.

    Attributes:
        tampered: True 이면 **pack 변조(content_hash 불일치)** 로 인한 실패 —
            단순 부재(slug/version 미존재·body 없음)는 False. client 가 #6 표시
            시 "변조" 와 "재로드 불가" 를 구분할 수 있도록 구조화 (M5 #3b /
            ADR-0033). `pack_tampered=True` 는 변조만 — 부재는 False 유지.
    """

    pack: LoadedPack | None
    note: str | None
    tampered: bool = False


def _resolve_frozen_pack(
    data_versions: Mapping[str, str],
    *,
    fallback_pack: LoadedPack,
    pack_registry: PackRegistry,
    custom_pack_body: dict | None = None,
) -> _PackResolution:
    """frozen data_versions 의 slug/version/content_hash 로만 pack 재로드 (D5).

    ADR-0025 D5 — 재현은 **운영 활성 pack 을 무조건 사용하지 않는다**. frozen
    data_versions 의 `factor_pack_slug`/`factor_pack_version`/
    `factor_pack_content_hash` 로 PackRegistry 가 그 시점 pack 을 재로드하고,
    재로드 pack 의 computed_hash 가 frozen hash 와 일치해야만 재현에 사용.

    경로:
    - frozen pack 키 부재 (slug 또는 version None) → fallback_pack 사용. batch_id
      추적 도입 전의 구 "1.0" run 또는 pack 키를 안 싣는 직접 호출 경로의 하위
      호환 (그 run 의 정직한 한계 — 현재 active pack 기준).
    - **custom slug (빌트인/reference 아님)** → export body 의 pack
      (`custom_pack_body`) 로 `registry.resolve_from_body` 재구성 (ADR-0025 D5,
      ADR-0021 D5 — user/DB 무관 자기완결). body 부재(구 export) → pack None +
      "custom pack body 필요" note (matches=False). 변조 body → HashMismatch
      (FactorPackError) → "재로드 실패" note.
    - 빌트인/reference → `registry.resolve` (frozen slug/version 정본). None
      (미존재 reference) → "재로드 불가" note (matches=False).
    - 재로드 성공 but computed_hash != frozen hash → pack None + "pack 변조" note
      (matches=False, fail-loud). frozen hash 키가 없으면 hash 검증 skip(slug/
      version 만으로 정본 재로드).
    - 일치 → 재로드 pack.
    """
    slug = data_versions.get(_PACK_SLUG_KEY)
    version = data_versions.get(_PACK_VERSION_KEY)
    frozen_hash = data_versions.get(_PACK_HASH_KEY)

    if not slug or not version:
        # pack 키 부재 — 구 run / pack 미freeze 직접 호출. 하위호환(active pack).
        return _PackResolution(pack=fallback_pack, note=None)

    # custom slug 분기 — 빌트인(canonical)/reference 가 아니면 export body 자기완결
    # 경로 (ADR-0025 D5). reference 는 derive_tier 가 community 로 볼 수 있으나,
    # 빌트인이 아닌 모든 slug 는 먼저 registry.resolve(빌트인/reference)를 시도하고
    # 거기서 None 이면 custom body 경로로 떨어진다.
    #
    # ADR-0034 D2/D6 (identity v2): frozen v2 pack(`@{publisher}/{slug}`)도 빌트인이
    # 아니므로 본 custom export-body 경로로 재현된다(별도 v2 분기 불필요). frozen v1
    # run 은 `@` 없는 v1 slug 라 자동 v1 경로(byte-불변). custom export-body 재로드
    # (load_pack_from_body)는 **hash-only**(validate_schema 미호출)라 v2 schema
    # dispatch 가 필요 없다 — content_hash 봉인이 무결성 강제(D6). data_versions 신규
    # 키 0 → result_hash 입력 불변(D2 — frozen run hash drift 0).
    is_builtin = slug == BUILTIN_PACK_SLUG

    try:
        resolved = pack_registry.resolve(slug, version)
    except FactorPackError as exc:
        # 빌트인 파일 자체의 hash/검증 실패 — pack 변조. fail-loud.
        # tampered=True: 변조/hash 불일치. #6 표시 전제(ADR-0033 / M5 #3b).
        return _PackResolution(
            pack=None,
            note=f"재현 불가 — frozen factor pack 재로드 실패: {exc}",
            tampered=True,
        )

    if resolved is None:
        # 빌트인/reference 가 아닌 slug — custom run. export body 로 재구성 (D5).
        if not is_builtin and custom_pack_body is not None:
            try:
                resolved = pack_registry.resolve_from_body(custom_pack_body)
            except FactorPackError as exc:
                # custom export body content_hash 변조 의심 — fail-loud.
                # tampered=True: HashMismatch = 변조. 부재와 구분(ADR-0033/M5 #3b).
                return _PackResolution(
                    pack=None,
                    note=(
                        "재현 불가 — custom pack export body 재로드 실패"
                        f" (변조 의심): {exc}"
                    ),
                    tampered=True,
                )
        elif not is_builtin:
            # custom slug 인데 export body 부재 (구 export / body 미동봉) — 자기완결
            # 아님 → 재현 불가 (DB/user 에 의존하지 않는다는 D5 불변식 보존).
            return _PackResolution(
                pack=None,
                note=(
                    "재현 불가 — custom pack run 재현에는 export body 의 pack"
                    f" 정의가 필요합니다 ({slug} v{version}, body 부재)."
                ),
            )
        else:
            # 빌트인 slug 인데 resolve None — 미존재 빌트인 version. (정상 미발생)
            return _PackResolution(
                pack=None,
                note=(
                    "재현 불가 — frozen factor pack 재로드 불가"
                    f" ({slug} v{version} 미존재/미지원)."
                ),
            )

    if frozen_hash and resolved.computed_hash != frozen_hash:
        # 재로드 pack 의 hash 가 frozen 과 불일치 — pack 변조/hash 불일치.
        # custom 경로도 동일 게이트 — export body 가 frozen 과 다른 pack 이면 거부.
        # tampered=True: content_hash 불일치 = 변조. client #6 표시 전제(ADR-0033/M5 #3b).
        return _PackResolution(
            pack=None,
            note=(
                "재현 불가 — frozen factor pack content_hash 불일치 (pack 변조"
                " 또는 정의 변경)."
            ),
            tampered=True,
        )

    return _PackResolution(pack=resolved, note=None)


def reproduce_run(
    snapshot: ScreenRunSnapshot,
    *,
    batch_run_repo: BatchRunRepository,
    stocks_repo: StocksMasterRepository,
    pack: LoadedPack,
    evaluator: FactorEvaluator,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    corporate_action_repo: CorporateActionRepository,
    market_cap_repo: MarketCapRepository,
    treasury_repo: TreasurySharesRepository,
    macro_repo: MacroIndicatorRepository | None = None,
    dividend_repo: DividendRepository | None = None,
    pack_registry: PackRegistry | None = None,
    custom_pack_body: dict | None = None,
) -> ReproduceResult:
    """저장된 Screen Run 을 frozen batch 기준으로 재실행 — byte-동일 검증 (T48c).

    `snapshot.data_versions` 의 krx/dart batch_id 를 `BatchCutoff` 로 해소하여
    cutoff 주입 `screen_active_codes` 를 frozen as_of + conditions 로 재실행.
    재현 result_codes 와 저장 result_codes 의 동치 (`matches`) 를 반환.

    cutoff 분배 (DbFieldProvider 내부): price/market_cap → krx, financial/treasury
    → dart. conditions 는 snapshot.query.conditions (canonical, `{factor, op,
    value}` mapping tuple) → ConditionIn 재구성.

    Args:
        snapshot: 재현 대상 저장 Screen Run (frozen query + as_of + result_codes
            + data_versions).
        batch_run_repo: frozen batch_id → BatchCutoff 해소용 (get_run).
        stocks_repo ~ treasury_repo: screen 재실행의 universe + fact repository.
            cutoff 가 주입돼 frozen 이하 batch 가 생산한 row 만 통과.
        macro_repo: ECOS/KOSIS 거시지표 repository (M2 T81). live save (save_run /
            execute_screen) 가 macro_repo 를 배선하므로 reproduce 도 동일 배선해야
            macro field (ecos_base_rate 등) 조건이 silent N/A 가 아닌 같은 값으로
            재현 → matches=True (§2.10 break 수정 — dividend_repo 와 동일 결함).
            None 이면 macro field 정식 N/A (구 run / macro 미사용 컨텍스트 하위호환).
            **batch_cutoff 불요**: macro 는 vintage_date<=as_of 이중 PIT 가 재현
            축이라 (krx/dart 와 달리 batch_cutoff 없음, pit_protocols.
            MacroIndicatorRepository) as_of 고정만으로 byte-동일 재현이 보장된다 —
            잠정→확정 정정도 vintage 로 격리되어 dividend/corporate action 의
            cutoff-freeze 한계(ADR-0033 D4)에 해당하지 않는다.
            ⚠ 전제: 이 불변성은 **vintage_date 가 실제 공표 시점의 근사**일 때만
            성립한다 (ECOS=관측일, KOSIS=ingested_at, ADR-0036 D3). 과거 기간을
            한참 뒤에 적재(backfill)하여 vintage_date 가 공표 시점보다 크게
            뒤처지면, as_of 고정 재현이 다른 값을 볼 수 있어 dividend 와 동일한
            known-limit 이 된다.
            배선 메커니즘: 본 함수가 받은 macro_repo 는 screen_active_codes 진입
            시점에 request-scoped CachingMacroIndicatorRepository 로 자동 래핑되어
            종목 간 N×M fetch 가 M 으로 축약된다 (이중 래핑 방어 idempotent).
        dividend_repo: cash_dividend repository (M7 #4, §2.10 break 수정). live
            save 경로가 dividend_repo 를 배선하므로 reproduce 도 동일 배선해야
            `dividend_per_share_trailing_annual` / `total_return_trailing_1y`
            (따라서 `dividend-yield:trailing-annual` factor) 가 silent N/A 가
            아닌 같은 값으로 산출 → matches=True. None 이면 두 field 정식 N/A
            (구 run / dividend 미사용 컨텍스트 하위호환).
        pack: **fallback factor pack** — frozen data_versions 에 pack 키
            (factor_pack_slug/version/content_hash) 가 있으면 무시되고
            `pack_registry` 가 frozen 값으로 재로드한 pack 이 사용됨 (ADR-0025 D5
            — 운영 활성 pack 무조건 사용 금지). pack 키가 없는 구 run / 직접
            호출 경로만 본 인자를 사용 (하위호환).
        evaluator: 조건 매칭의 factor evaluator.
        pack_registry: frozen slug/version → pack 재로드기. None 이면 본 함수가
            기본 PackRegistry() 1 개 생성 (프로세스 캐시 재사용은 호출자가 공유
            인스턴스 주입 시).
        custom_pack_body: **custom run 전용** — export JSON 에 동봉된 봉인 custom
            pack body (ADR-0025 D5). frozen slug 가 빌트인/reference 가 아니면 이
            body 로 `registry.resolve_from_body` 재구성 (user/DB 무관 자기완결,
            ADR-0021 D5 — 삭제된 custom pack 도 재현). 빌트인/reference run 은 None
            (registry 가 frozen slug/version 으로 정본 재로드). 빌트인 run 재현
            경로는 본 인자 무관 — byte 재현 보존.

    Returns:
        ReproduceResult(result_codes, matches, reproduce_note). frozen pack 재로드
        불가/변조면 result_codes=빈 tuple + matches=False + reproduce_note.
    """
    registry = pack_registry if pack_registry is not None else PackRegistry()
    data_versions = snapshot.data_versions

    # 0. frozen data_versions 의 slug/version/content_hash 로만 pack 재로드 (D5).
    #    pack 키 부재면 fallback `pack` (하위호환). 재로드 불가/변조면 matches=False.
    resolution = _resolve_frozen_pack(
        data_versions, fallback_pack=pack, pack_registry=registry,
        custom_pack_body=custom_pack_body,
    )
    if resolution.pack is None:
        # pack 재로드 불가 — 변조(tampered=True) 또는 부재(False) 구조화 전달(M5 #3b).
        return ReproduceResult(
            result_codes=(),
            matches=False,
            reproduce_note=resolution.note,
            pack_tampered=resolution.tampered,
        )
    active_pack = resolution.pack

    # 1. frozen batch_id → BatchCutoff 해소 (빈 문자열 → EXCLUDE_ALL, H#5).
    #
    # ⚠ batch-cutoff freeze 적용 범위 (ADR-0033 D4 / ADR-0035 D8 known-limit) —
    # krx/dart batch_cutoff 는 **price / financial (및 market_cap / treasury) fetch
    # 전용**이다. DbFieldProvider 가 krx_cutoff → price/market_cap, dart_cutoff →
    # financial/treasury 로만 분배 (db_field_provider.py:618-624,718-720,782,814,862).
    # corporate action (`corporate_action_repo.fetch_actions`) 과 dividend
    # (`dividend_repo.fetch_dividends`) fetch 는 cutoff 를 받지 않고 **as_of 필터만**
    # 적용한다 (db_field_provider.py:728-730,921-923,1008-1014).
    #
    # 따라서 data_versions 의 `dart_batch_id` 가 corporate action 보정을 freeze 하지
    # 않듯, `dividend_batch_id`(source="FSC") 도 cutoff 미적용 — **diff_versions
    # 표시(데이터 버전 변화 가시화)용**이다 (snapshot_versions.py:166-180,
    # _BATCH_VERSION_KEYS). 일관된 의도 (D8 = D4 상속).
    #
    # 재현 한계: 배당 / total-return factor (`dividend-yield:trailing-annual`,
    # `total_return_trailing_1y`) 에 의존하는 run 은 **배당이 사후 정정되면** 동일
    # as_of 재실행 시 다른 입력(정정된 배당)을 볼 수 있어 byte-동일 재현이
    # 미보장이다 (배당 미정정 가정 하에서만 byte-불변). 배당 batch-cutoff freeze
    # 마커는 별도 cycle (ADR-0035 D8 known-limit). corporate action 정정 동일 한계.
    krx_cutoff = _resolve_cutoff(
        data_versions.get(_KRX_BATCH_KEY), batch_run_repo=batch_run_repo,
    )
    dart_cutoff = _resolve_cutoff(
        data_versions.get(_DART_BATCH_KEY), batch_run_repo=batch_run_repo,
    )

    # 2. snapshot.query.conditions (canonical mapping tuple) → ConditionIn 재구성.
    #    screen_active_codes 의 입력 형태 일치 (op 은 OpEnum 으로 변환).
    conditions = [
        ConditionIn(
            factor=c["factor"],
            op=OpEnum(c["op"]),
            value=c["value"],
        )
        for c in snapshot.query.conditions
    ]

    # 3. cutoff 주입 재실행 — circular import 회피 위해 함수 내 import.
    from app.api.routes.screen import screen_active_codes

    # ScreenCodesResult 반환 — .result_codes 로 unwrap (reproduce 는 result_codes 만 필요).
    result_codes = screen_active_codes(
        as_of=snapshot.as_of,
        conditions=conditions,
        stocks_repo=stocks_repo,
        pack=active_pack,
        evaluator=evaluator,
        price_repo=price_repo,
        financial_repo=financial_repo,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        # M2 T81 (§2.10 break) — macro field 배선. live save 가 macro_repo 를
        # 배선하므로 reproduce 도 동일 배선해야 macro 조건(ecos_base_rate 등)이
        # silent N/A 가 아닌 같은 값으로 재현. macro 는 vintage PIT 가 재현 축이라
        # cutoff 주입 불요 — as_of 고정만으로 byte-동일.
        macro_repo=macro_repo,
        # M7 #4 (§2.10 break) — dividend / total return field 배선. live save 가
        # dividend_repo 를 배선하므로 reproduce 도 동일 배선해야 dividend-yield/
        # total-return 조건이 silent N/A 가 아닌 같은 값으로 재현(byte-동일).
        dividend_repo=dividend_repo,
        # ADR-0023 D7 — frozen 자산군 선택으로 재현 (같은 모집단). 구 run 은
        # ScreenRunQuery default ("common",) 이라 보통주 universe 재현 무영향.
        security_types=snapshot.query.security_types,
        krx_batch_cutoff=krx_cutoff,
        dart_batch_cutoff=dart_cutoff,
    ).result_codes

    # 4. byte-동일 검증 — 양쪽 모두 normalize_stock_codes 정규화 (6 자리·정렬·
    #    dedup). snapshot.result_codes 는 build 시 정규화됐으나, 비교의 대칭성을
    #    위해 명시 정규화 후 동치 판정.
    normalized = normalize_stock_codes(result_codes)
    matches = normalized == snapshot.result_codes
    return ReproduceResult(result_codes=normalized, matches=matches)


# =============================================================================
# Backtest reproduce — frozen backtest 재실행 + equity_curve 동일 검증 (M5 #3a-2)
# =============================================================================
#
# ADR-0033 D2~D4 — screen `reproduce_run` 의 backtest 등가물. frozen freeze
# (self-contained: pack 식별·conditions·data_versions·기간·주기·거래비용) 와
# baseline equity_curve 를 받아, frozen batch cutoff 로 backtest 를 재실행하고
# 재실행 equity_curve 가 baseline 과 byte-동일(`matches`)한지 검증한다.
#
# screen reproduce 와의 대칭(재사용):
# - `_resolve_frozen_pack` (D2 — frozen slug/version/content_hash 로만 pack 재로드,
#   운영 활성 pack 무조건 사용 금지; custom 은 export body 자기완결).
# - `_resolve_cutoff` (D3 — frozen krx/dart batch_id → BatchCutoff. 빈 문자열 =
#   EXCLUDE_ALL, 키 부재 = 무필터(구 run 하위호환)).
# - cutoff 주입 재실행은 `run_backtest(krx_batch_cutoff=, dart_batch_cutoff=)`
#   (직전 마일스톤에서 배선됨). corporate action 보정은 cutoff freeze 불가 —
#   재실행 시점 정정 corporate action 이 보정에 반영될 수 있음(ADR-0033 D4 known
#   limit, screen 상속 한계).


@dataclass(frozen=True, slots=True)
class BacktestReproduceResult:
    """Backtest 재현 결과 — 재실행 equity_curve + baseline 과의 동일 여부.

    `ReproduceResult` 동형 (screen). result_codes 대신 equity_curve 를 운반한다.

    Attributes:
        equity_curve: frozen batch cutoff 로 재실행한 결정적 equity curve —
            `(date isoformat, value str)` 튜플의 튜플 (wire Freeze/equity 그대로).
            pack 재로드 실패 시 빈 tuple.
        matches: `equity_curve == baseline_equity_curve` (byte-동일 재현 성공).
            False 면 frozen batch 이후의 데이터/정책 변화 또는 재현 불가(예:
            frozen pack 재로드 불가/변조)를 시사.
        reproduce_note: 재현 실패의 구체 사유(pack 재로드 불가/변조 등). 성공 또는
            단순 데이터 변화 불일치면 None — endpoint 기본 note 적용.
    """

    equity_curve: tuple[tuple[str, str], ...]
    matches: bool
    reproduce_note: str | None = None
    # pack_tampered: pack 변조(content_hash 불일치)로 재현 불가인 경우만 True.
    # ReproduceResult 와 동형(ADR-0033/M5 #3b). 기존 호출 무영향 — default False.
    pack_tampered: bool = False
    # engine_version_superseded: freeze 의 산식 세대가 현재 엔진과 다를 때 True(M1).
    # matches 와 독립 — benign 세대 bump 를 데이터 드리프트와 구분. frozen 버전
    # 미상(구 freeze None)이면 False(하위호환).
    engine_version_superseded: bool = False


def reproduce_backtest(
    *,
    pack_slug: str,
    pack_version: str,
    conditions: Sequence[Mapping[str, str]],
    start: date,
    end: date,
    rebalance: str,
    cost_assumptions: object,
    data_versions: Mapping[str, str],
    baseline_equity_curve: tuple[tuple[str, str], ...],
    batch_run_repo: BatchRunRepository,
    stocks_repo: StocksMasterRepository,
    pack: LoadedPack,
    evaluator: FactorEvaluator,
    price_repo: PriceRepository,
    financial_repo: FinancialRepository,
    corporate_action_repo: CorporateActionRepository,
    market_cap_repo: MarketCapRepository,
    treasury_repo: TreasurySharesRepository,
    macro_repo: MacroIndicatorRepository | None = None,
    dividend_repo: DividendRepository | None = None,
    pack_registry: PackRegistry | None = None,
    custom_pack_body: dict | None = None,
    # M1 — freeze 의 산식 세대(BACKTEST_ENGINE_VERSION 당시 값). 현재 엔진과 다르면
    # engine_version_superseded=True 로 benign 세대 bump 를 진단. 구 freeze(부재)는
    # None → 비교 skip(하위호환).
    frozen_engine_version: str | None = None,
) -> BacktestReproduceResult:
    """frozen backtest 를 frozen batch 기준으로 재실행 — equity_curve 동일 검증.

    screen `reproduce_run` 의 backtest 등가물 (ADR-0033 D2~D4). frozen
    `data_versions` 의 pack 키(slug/version/content_hash)로만 pack 을 재로드하고
    (운영 활성 pack 무조건 사용 금지 — ADR-0025 D5), krx/dart batch_id 를
    `BatchCutoff` 로 해소하여 cutoff 주입 `run_backtest` 를 frozen 입력으로 재실행.
    재실행 equity_curve 와 baseline 의 동치(`matches`)를 반환.

    Args:
        pack_slug / pack_version: freeze 의 pack 식별 (참조용 — pack 재로드는 D2
            대로 `data_versions` 의 키로 수행. 본 인자는 향후 진단/로깅 여지).
        conditions: freeze 의 universe 필터 조건 — `{factor, op, value}` mapping
            tuple (canonical). `BacktestCondition` 으로 역변환 (threshold Decimal).
        start / end: 백테스트 기간.
        rebalance: "quarterly" | "monthly".
        cost_assumptions: 적용 거래비용 가정 (`CostAssumptions`). object 타입은
            circular import 회피 — 내부에서 그대로 run_backtest 에 전달.
        data_versions: freeze 의 전체 data_versions (정책 pack 키 + krx/dart
            batch_id). `collect_run_data_versions` 산출 — screen 과 동일 키 호환.
        baseline_equity_curve: freeze 와 함께 보관된 원본 equity curve —
            `(date isoformat, value str)` 튜플 (wire 그대로). 재실행 결과와 동치
            비교 대상.
        batch_run_repo: frozen batch_id → BatchCutoff 해소용 (get_run).
        stocks_repo ~ macro_repo: backtest 재실행의 universe + fact repository.
            cutoff 가 주입돼 frozen 이하 batch 가 생산한 row 만 통과.
        dividend_repo: cash_dividend repository (M7 #4, §2.10 break 수정). live
            backtest 가 dividend_repo 를 배선하므로 reproduce 도 동일 배선해야
            universe 필터의 `dividend-yield:trailing-annual` / total-return 조건이
            silent N/A 아닌 같은 값 → equity_curve byte-동일 재현. None 이면 두
            field 정식 N/A (구 freeze / dividend 미사용 컨텍스트 하위호환).
        pack: **fallback factor pack** — frozen data_versions 에 pack 키가 있으면
            무시되고 pack_registry 가 frozen 값으로 재로드 (ADR-0025 D5). pack 키가
            없는 구 freeze / 직접 호출만 본 인자 사용 (하위호환).
        evaluator: 조건 매칭의 factor evaluator.
        pack_registry: frozen slug/version → pack 재로드기. None 이면 기본
            PackRegistry() 1 개 생성.
        custom_pack_body: custom run 전용 — export 동봉 봉인 custom pack body
            (ADR-0025 D5 / ADR-0021 D5, user/DB 무관 자기완결).

    Returns:
        BacktestReproduceResult(equity_curve, matches, reproduce_note). frozen pack
        재로드 불가/변조면 equity_curve=빈 tuple + matches=False + reproduce_note.
    """
    # circular import 회피 — reproduce.py 는 service 저층이라 backtest_engine
    # (역시 service) 를 함수 내 지연 import (screen_active_codes 선례 동형).
    from app.services.backtest_engine import (
        BACKTEST_ENGINE_VERSION,
        BacktestCondition,
        run_backtest,
    )

    # M1 — 산식 세대 불일치 진단. frozen 버전이 주어지고(구 freeze 는 None) 현재
    # 엔진과 다르면 True. matches 와 독립 신호라 pack 재로드/재실행 결과와 무관하게
    # 산정(아래 모든 반환 경로에 동일 적용 — pack 재로드 실패 경로 포함).
    engine_version_superseded = (
        frozen_engine_version is not None
        and frozen_engine_version != BACKTEST_ENGINE_VERSION
    )

    registry = pack_registry if pack_registry is not None else PackRegistry()

    # 0. frozen data_versions 의 slug/version/content_hash 로만 pack 재로드 (D2).
    #    pack 키 부재면 fallback `pack` (하위호환). 재로드 불가/변조면 matches=False.
    resolution = _resolve_frozen_pack(
        data_versions, fallback_pack=pack, pack_registry=registry,
        custom_pack_body=custom_pack_body,
    )
    if resolution.pack is None:
        # pack 재로드 불가 — 변조(tampered=True) 또는 부재(False) 구조화 전달(M5 #3b).
        return BacktestReproduceResult(
            equity_curve=(),
            matches=False,
            reproduce_note=resolution.note,
            pack_tampered=resolution.tampered,
            engine_version_superseded=engine_version_superseded,
        )
    active_pack = resolution.pack

    # 1. frozen batch_id → BatchCutoff 해소 (빈 문자열 → EXCLUDE_ALL, D3).
    krx_cutoff = _resolve_cutoff(
        data_versions.get(_KRX_BATCH_KEY), batch_run_repo=batch_run_repo,
    )
    dart_cutoff = _resolve_cutoff(
        data_versions.get(_DART_BATCH_KEY), batch_run_repo=batch_run_repo,
    )

    # 2. freeze conditions(canonical {factor,op,value} dict) → BacktestCondition
    #    역변환. threshold 는 Decimal (freeze 가 str 로 운반 — JCS 결정성).
    domain_conditions = [
        BacktestCondition(
            factor=c["factor"], op=c["op"], threshold=Decimal(c["value"]),
        )
        for c in conditions
    ]

    # 3. cutoff 주입 재실행 — frozen pack/conditions/기간/주기/거래비용 + frozen
    #    batch cutoff. corporate action 보정은 cutoff freeze 불가 (D4 상속 한계).
    result = run_backtest(
        pack=active_pack,
        conditions=domain_conditions,
        start=start,
        end=end,
        frequency=rebalance,  # type: ignore[arg-type]
        stocks_repo=stocks_repo,
        price_repo=price_repo,
        financial_repo=financial_repo,
        evaluator=evaluator,
        corporate_action_repo=corporate_action_repo,
        market_cap_repo=market_cap_repo,
        treasury_repo=treasury_repo,
        # macro_repo 는 run_backtest 진입부에서 request-scoped
        # CachingMacroIndicatorRepository 로 자동 래핑된다(N×R×M → R×M, screen
        # reproduce 와 동일 N+1 완화). raw repo 를 넘기면 됨 — 이중 래핑 방어.
        macro_repo=macro_repo,
        # M7 #4 (§2.10 break) — dividend / total return field 배선. live backtest
        # 와 동일 배선해야 universe 필터의 dividend-yield/total-return 조건이 같은
        # 값 → equity_curve byte-동일 재현.
        dividend_repo=dividend_repo,
        cost_assumptions=cost_assumptions,  # type: ignore[arg-type]
        krx_batch_cutoff=krx_cutoff,
        dart_batch_cutoff=dart_cutoff,
    )

    # 4. 재실행 equity_curve → wire 형태((date isoformat, value str)) 변환 후
    #    baseline 과 동치 비교. 양쪽 모두 동일 wire 표현이라 byte-동일 판정.
    reproduced = tuple(
        (p.date.isoformat(), str(p.value)) for p in result.equity_curve
    )
    matches = reproduced == baseline_equity_curve
    return BacktestReproduceResult(
        equity_curve=reproduced,
        matches=matches,
        engine_version_superseded=engine_version_superseded,
    )
