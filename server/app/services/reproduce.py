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

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.repositories.batch_run_repository import (
    EXCLUDE_ALL_CUTOFF,
    BatchCutoff,
    BatchRunRepository,
)
from app.repositories.pit_protocols import (
    CorporateActionRepository,
    FinancialRepository,
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
    "ReproduceResult",
    "reproduce_run",
]

# data_versions 의 batch_id 키 — snapshot_versions._BATCH_VERSION_KEYS 와 일치.
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
    """

    pack: LoadedPack | None
    note: str | None


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
    is_builtin = slug == BUILTIN_PACK_SLUG

    try:
        resolved = pack_registry.resolve(slug, version)
    except FactorPackError as exc:
        # 재로드 자체가 hash/검증 실패 (변조된 빌트인 파일 등) — fail-loud.
        return _PackResolution(
            pack=None,
            note=f"재현 불가 — frozen factor pack 재로드 실패: {exc}",
        )

    if resolved is None:
        # 빌트인/reference 가 아닌 slug — custom run. export body 로 재구성 (D5).
        if not is_builtin and custom_pack_body is not None:
            try:
                resolved = pack_registry.resolve_from_body(custom_pack_body)
            except FactorPackError as exc:
                # export body content_hash 변조 — fail-loud (ADR-0021 D5).
                return _PackResolution(
                    pack=None,
                    note=(
                        "재현 불가 — custom pack export body 재로드 실패"
                        f" (변조 의심): {exc}"
                    ),
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
        # 재로드 pack 의 hash 가 frozen 과 불일치 — pack 변조 / hash 불일치.
        # custom 경로도 동일 게이트 — export body 가 frozen 과 다른 pack 이면 거부.
        return _PackResolution(
            pack=None,
            note=(
                "재현 불가 — frozen factor pack content_hash 불일치 (pack 변조"
                " 또는 정의 변경)."
            ),
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
        return ReproduceResult(
            result_codes=(),
            matches=False,
            reproduce_note=resolution.note,
        )
    active_pack = resolution.pack

    # 1. frozen batch_id → BatchCutoff 해소 (빈 문자열 → EXCLUDE_ALL, H#5).
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
        # ADR-0023 D7 — frozen 자산군 선택으로 재현 (같은 모집단). 구 run 은
        # ScreenRunQuery default ("common",) 이라 보통주 universe 재현 무영향.
        security_types=snapshot.query.security_types,
        krx_batch_cutoff=krx_cutoff,
        dart_batch_cutoff=dart_cutoff,
    )

    # 4. byte-동일 검증 — 양쪽 모두 normalize_stock_codes 정규화 (6 자리·정렬·
    #    dedup). snapshot.result_codes 는 build 시 정규화됐으나, 비교의 대칭성을
    #    위해 명시 정규화 후 동치 판정.
    normalized = normalize_stock_codes(result_codes)
    matches = normalized == snapshot.result_codes
    return ReproduceResult(result_codes=normalized, matches=matches)
