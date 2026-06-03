"""Factor Pack 검증 endpoint wire schemas — POST /api/factor-packs/validate.

M2 T71 Phase 1 (backend). Factor Lab editor 가 사용자 정의 pack JSON 을 실시간
검증하기 위한 API 의 응답 스키마.

설계:
- valid: bool — issues 가 빈 목록일 때 True. editor 가 "저장 가능" 상태 판단 기준.
- issues: tuple[ValidationIssueOut, ...] — 각 검증 단계의 실패. 빈 tuple = valid.
- ValidationIssueOut: {stage, message} — stage 는 어느 검증 단계인지,
  message 는 해당 예외의 상세 (내부 기준, 클라이언트에 그대로 전달).

stage 값:
  "schema"         — JSON schema 구조 위반 (필수 필드 누락, 금지 op 등).
  "identity"       — canonical_id / uuid 중복.
  "acyclic"        — factor 의존 그래프 순환.
  "citation"       — pack / factor-level citation 누락.
  "forbidden_vocab"— factor name / description 금지 어휘.

custom pack 과 builtin pack 의 차이:
- "schema" stage 에서 Composite op (weighted_sum / zscore / percentile /
  min_max_scale)는 허용 — 사용자 pack 의 핵심 use-case. ADR-0022 D5 금지는
  빌트인 전용.
- "hash" stage 없음 — editor 에서 실시간 검증 시 hash 미채워진 상태가 정상.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "CustomPackEvaluateOut",
    "CustomPackValidateOut",
    "EvaluatedFactorOut",
    "EvaluatedStockOut",
    "FactorConflictOut",
    "FactorPackExportOut",
    "ImportApplyOut",
    "ImportCheckOut",
    "ReferencePackListOut",
    "ReferencePackOut",
    "ResolutionAppliedOut",
    "ValidationIssueOut",
]

_STRICT_FROZEN = ConfigDict(strict=True, extra="forbid", frozen=True)

# export wrapper 마커 — 파일이 Speculum Factor Pack export 임을 자기 식별
# (ADR-0022 D8.5). import 측이 이 값으로 파일 타입을 검증. screen export
# (`speculum-screen-run-export-v1`)와 동형 패턴.
FACTOR_PACK_EXPORT_MARKER = "speculum-factor-pack-export-v1"
# export 에 포함된 pack 스키마 버전 — factor-pack-v1.json 구조 변경 시 bump.
FACTOR_PACK_EXPORT_SCHEMA_VERSION = "1"


class ValidationIssueOut(BaseModel):
    """단일 검증 단계 실패 정보.

    Attributes:
        stage: 실패한 검증 단계. 값: "schema" | "identity" | "acyclic" |
               "citation" | "forbidden_vocab".
        message: 실패 상세 메시지 (내부 예외 str()).
    """

    model_config = _STRICT_FROZEN

    stage: str
    message: str


class CustomPackValidateOut(BaseModel):
    """POST /api/factor-packs/validate 응답.

    Attributes:
        valid: issues 가 빈 tuple 일 때 True — 모든 검증 단계 통과.
               False 이면 issues 에 1 개 이상 항목.
        issues: 수집된 검증 이슈. 순서는 검증 단계 순:
                schema → identity → acyclic → citation → forbidden_vocab.
                schema 실패 시 이후 단계 이슈 없음 (fail-fast).
    """

    model_config = _STRICT_FROZEN

    valid: bool
    issues: tuple[ValidationIssueOut, ...]


# =============================================================================
# Custom pack ad-hoc 평가 (M2 T74 Phase 1) — POST /api/factor-packs/evaluate
# =============================================================================
#
# Factor Lab editor 가 정의한 custom pack 을 **저장 없이 즉시 평가**(미리보기)한
# 결과 스키마. validate 와 같은 router 의 별도 endpoint.
#
# No Advice 경계 (backend): 결과는 **값/N-A 사실만** 노출. 랭킹·정렬·점수 라벨 0
# (프론트 시각 게이트는 T74 Phase2). 종목 식별자(code)는 사용자가 직접 입력한
# codes 이므로 노출 OK — market-overview 의 "종목 식별자 0"(유니버스 전체 집계)와
# 다른 맥락(사용자 명시 종목 조회).


class EvaluatedFactorOut(BaseModel):
    """단일 종목 × 단일 factor 의 평가 결과 — 값 또는 N-A 사실만.

    Attributes:
        canonical_id: 평가한 factor 의 canonical_id (custom pack 내 정의).
        name: factor 표시명 (pack body 의 `name`).
        unit: factor 단위 (pack body 의 `unit` — "ratio" / "krw" 등).
        value: 산출값 (Decimal → str wire, IEEE 754 drift 회피). N-A 면 None.
        is_na: `value is None` 의 alias (N-A 여부).
        na_reason: N-A 의 근본 원인 (debug). N-A 가 아니면 None.
        sample_size: universe-상대 op (percentile/zscore/min_max_scale) 의 분포
            모집단 크기 (ADR-0024 D2/D5). 그 factor 에 universe-상대 op 이 있으면
            쓰인 분포 field 들의 **min(n)** (가장 빈약한 모집단이 전체 신뢰도 상한,
            ADR-0024 D5), 없으면 None. **표시 전용** — screener pass/fail 이나
            result_codes 와 무관 (ADR-0024 D4 c).
        small_sample: `sample_size is not None and sample_size <
            SMALL_SAMPLE_THRESHOLD(30)` (ADR-0024 D1/D2). 소표본 디스클로저 트리거
            플래그. universe-상대 op 없는 factor 는 False. N/A 강등이 아닌 사실 +
            한계 디스클로저용 — 값은 보존되고 이 플래그가 통계적 해석 한계를 표식
            (n==1 percentile=100 케이스도 값 100 보존 + small_sample=True).
    """

    model_config = _STRICT_FROZEN

    canonical_id: str
    name: str
    unit: str
    value: str | None
    is_na: bool
    na_reason: str | None
    sample_size: int | None
    small_sample: bool


class EvaluatedStockOut(BaseModel):
    """단일 종목의 전체 factor 평가 결과.

    Attributes:
        code: 평가 대상 종목코드 (요청 codes 의 정규화된 6 자리). 사용자 명시
            종목이므로 노출 OK (No Advice 경계 — 식별자 자체는 사실).
        factors: pack 의 각 factor 산출 결과 (pack 정의 순서 보존).
    """

    model_config = _STRICT_FROZEN

    code: str
    factors: tuple[EvaluatedFactorOut, ...]


class CustomPackEvaluateOut(BaseModel):
    """POST /api/factor-packs/evaluate 응답.

    Attributes:
        valid: pack 이 validate_custom_pack 통과(issues 빈)일 때 True. False 면
            평가를 진행하지 않고 results 는 빈 tuple (issues 에 사유).
        issues: 검증 이슈(valid=False 일 때만 채워짐). validate endpoint 와 동형.
        results: valid=True 일 때 각 종목의 factor 평가 결과. 요청 codes 순서 보존.
    """

    model_config = _STRICT_FROZEN

    valid: bool
    issues: tuple[ValidationIssueOut, ...]
    results: tuple[EvaluatedStockOut, ...]


# =============================================================================
# Pack 간 identity 충돌 import + self-identifying export (M2 T75 — ADR-0022 D8)
# =============================================================================


class FactorConflictOut(BaseModel):
    """import pack 과 기존(빌트인/영속) factor 의 정의 충돌 1 건 — ADR-0022 D8.1.

    Attributes:
        canonical_id: 충돌한 이름 (같은 canonical_id, 다른 정의).
        incoming_hash: import factor 의 정의 hash (uuid 제외, D8.1).
        existing_hash: 기존 factor 의 정의 hash.
        existing_tier: 기존 factor 의 tier ("canonical" | "community" | "custom").
            canonical 은 불가침 — replace 불가 (D8.2).
        allowed_resolutions: 이 충돌에 허용되는 resolution. canonical tier 면
            ("skip", "rename"), 그 외 ("skip", "rename", "replace").
    """

    model_config = _STRICT_FROZEN

    canonical_id: str
    incoming_hash: str
    existing_hash: str
    existing_tier: str
    allowed_resolutions: tuple[str, ...]


class ImportCheckOut(BaseModel):
    """POST /api/factor-packs/import-check 응답 — dry-run 충돌 탐지 (ADR-0022 D8.4 Pass 1).

    자동 적용 0 — 충돌 목록만 반환해 사용자가 각 충돌의 resolution 을 선택하게 한다.

    Attributes:
        valid: import pack 이 validate_custom_pack 통과 시 True. False 면 issues
            에 사유 (충돌 검사 이전의 구조/identity 문제).
        issues: 검증 이슈 (valid=False 일 때). validate endpoint 와 동형.
        conflicts: 정의 충돌 목록 (같은 canonical_id, 다른 hash). 빈 tuple = 충돌 0.
        clean: 충돌 없이 import 가능한 factor 의 canonical_id 목록.
    """

    model_config = _STRICT_FROZEN

    valid: bool
    issues: tuple[ValidationIssueOut, ...]
    conflicts: tuple[FactorConflictOut, ...]
    clean: tuple[str, ...]


class ResolutionAppliedOut(BaseModel):
    """import 시 한 충돌에 적용된 resolution 결과 — 사용자 확인용 요약.

    Attributes:
        canonical_id: 원래(충돌) canonical_id.
        action: 적용된 action ("skip" | "rename" | "replace").
        new_canonical_id: rename 시 새 이름, 그 외 None.
    """

    model_config = _STRICT_FROZEN

    canonical_id: str
    action: str
    new_canonical_id: str | None


class ImportApplyOut(BaseModel):
    """POST /api/factor-packs/import 응답 — resolution 적용 결과 (ADR-0022 D8.4 Pass 2).

    Attributes:
        valid: validate_custom_pack 통과 + 모든 충돌 해소 시 True. False 면 issues
            에 사유 (pack 은 None).
        issues: 검증 이슈 (valid=False 일 때).
        pack: 충돌 해소 + content_hash 재봉인된 최종 pack body (valid=True 일 때).
            editor 가 이 body 를 작업 상태로 수용. None 이면 무효.
        applied: 적용된 resolution 요약 (skip/rename/replace 각각).
    """

    model_config = _STRICT_FROZEN

    valid: bool
    issues: tuple[ValidationIssueOut, ...]
    pack: dict[str, Any] | None
    applied: tuple[ResolutionAppliedOut, ...]


class FactorPackExportOut(BaseModel):
    """POST /api/factor-packs/export 응답 — self-identifying export wrapper (ADR-0022 D8.5).

    파일 자체가 Speculum Factor Pack export 임을 자기 식별 (`export_format` 마커)
    하고, content_hash 를 봉인해 수입측이 정의 동일성을 판정할 수 있게 한다.

    Attributes:
        export_format: 고정값 `"speculum-factor-pack-export-v1"` — 파일 타입 식별자.
        pack_schema_version: factor-pack 스키마 버전 (구조 변경 시 bump).
        tier: pack tier ("community" | "user"). 빌트인(canonical) export 는 무의미
            (repo 에 이미 존재)하므로 거부 — community/custom 만.
        pack: content_hash 봉인된 전체 factor pack body.
    """

    model_config = _STRICT_FROZEN

    export_format: str = FACTOR_PACK_EXPORT_MARKER
    pack_schema_version: str = FACTOR_PACK_EXPORT_SCHEMA_VERSION
    tier: str
    pack: dict[str, Any]


# =============================================================================
# Reference pack 목록 (M2.1 — ADR-0023 D8) — GET /api/factor-packs/reference
# =============================================================================


class ReferencePackOut(BaseModel):
    """speculum 제공 reference pack 1개 — Factor Lab 에서 불러와 쓰는 참조 정의.

    빌트인(active universe pack)이 아니라 ADR-0023 D8 의 리츠 ffo-multiple 등.
    body 전체를 제공해 클라이언트가 editor 로 불러오거나 evaluate 에 전달.

    Attributes:
        pack_slug: reference pack slug (`community/speculum-reit-reference` 등).
        version: semver.
        factor_count: pack 내 factor 수.
        body: content_hash 봉인된 전체 factor pack body(불러오기/평가용).
    """

    model_config = _STRICT_FROZEN

    pack_slug: str
    version: str
    factor_count: int
    body: dict[str, Any]


class ReferencePackListOut(BaseModel):
    """GET /api/factor-packs/reference 응답 — reference pack 목록.

    익명 공개(참조 정의는 fact tier — 로그인 무관). 빈 목록 = reference 미배포.
    """

    model_config = _STRICT_FROZEN

    packs: tuple[ReferencePackOut, ...]
