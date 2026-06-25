"""Screen Run endpoint wire schemas — POST /api/screen + /api/runs.

설계 (oracle T26 자문 P0):
- ConditionIn strict — wire/도메인 타입 분리 (결정 2 + 6)
- Op enum — 허용 연산자만 (T18 합류 후 evaluator 가 same enum 파싱)
- factor canonical_id regex — text injection 차단 (Risk-X1)
- ScreenRunSnapshotOut.from_domain — 단방향 factory (stocks.py 패턴)
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.orm.stocks_master import validate_security_type
from app.services.screen_run import ScreenRunSnapshot

__all__ = [
    "ConditionIn",
    "CustomScreenRunQueryIn",
    "OpEnum",
    "ReproduceIn",
    "ReproduceOut",
    "RestatementLagOut",
    "ScreenResultOut",
    "ScreenRunExportOut",
    "ScreenRunListOut",
    "ScreenRunQueryIn",
    "ScreenRunSnapshotOut",
    "VersionsDiffOut",
]

_STRICT_MODEL_CONFIG = ConfigDict(strict=True, extra="forbid", frozen=True)

# Factor canonical_id 형식 — factor-pack-v1.json 의 pattern 과 일관.
# `[a-z][a-z0-9-]*(:[a-z][a-z0-9-]*)+` — e.g., "per:ttm-consolidated-ifrs".
# request body 의 text injection (예: factor 가 advisory vocabulary 포함) 차단 (oracle Risk-X1).
_FACTOR_ID_PATTERN: Final[str] = r"^[a-z][a-z0-9-]*(:[a-z][a-z0-9-]*)+$"

# Value 형식 — 숫자 string 또는 boolean-like. 본 사이클은 단순 str 길이 제한만.
_VALUE_MAX_LENGTH: Final[int] = 200


class OpEnum(str, Enum):
    """Screener condition 연산자 — T18 evaluator 의 same 집합."""

    LT = "<"
    LE = "<="
    EQ = "="
    GE = ">="
    GT = ">"
    NE = "!="


class ConditionIn(BaseModel):
    """Screener 조건 — wire 입력. POST body 의 element.

    Pydantic v2 의 enum coercion 위해 `strict=False` (json string `"<"` → OpEnum.LT).
    `extra="forbid"` + `frozen=True` 는 유지 — 알 수 없는 키 차단 + immutability.

    Attributes:
        factor: factor canonical_id (`per:ttm-...`). regex 강제 — text injection 차단.
        op: OpEnum 연산자.
        value: 비교값 (str, 호출자 normalize 책임).
    """

    model_config = ConfigDict(strict=False, extra="forbid", frozen=True)

    factor: str = Field(
        pattern=_FACTOR_ID_PATTERN,
        min_length=3, max_length=200,
        description="Factor canonical_id (예: 'per:ttm-consolidated-ifrs').",
    )
    op: OpEnum
    value: str = Field(min_length=1, max_length=_VALUE_MAX_LENGTH)


class ScreenRunQueryIn(BaseModel):
    """POST /api/screen + /api/runs 의 body — Screener 조건 + factor 선택.

    Wire type 은 `list` (JSON array 호환). domain 의 tuple 변환은 builder/factory
    책임 — Pydantic v2 strict 가 JSON list 를 tuple 로 coerce 하지 않음.

    Attributes:
        conditions: ConditionIn list (1~32 개).
        selected_factors: 결과 표시할 factor canonical_id list (1~32 개).
        security_types: universe 사전 필터할 자산군 (ADR-0023 D7). None (미지정)
            이면 default `["common"]` — universe 확장이 기본 동작 불변 (보통주만,
            D4/D7). 사용자가 명시 선택 시 그 자산군만 screen 모집단. 선택된
            집합은 canonical 정렬 후 ScreenRunQuery 일부 → result_hash 입력
            (자산군 선택이 재현에 포함, 추가 freeze 키 불요). 미지원 값은 422.
    """

    model_config = _STRICT_MODEL_CONFIG

    conditions: list[ConditionIn] = Field(min_length=1, max_length=32)
    selected_factors: list[str] = Field(min_length=1, max_length=32)
    # ADR-0023 D7 — universe 자산군 사전 필터. default None → 호출자가 ["common"]
    # 으로 해소 (기본 동작 = 보통주만, universe 확장이 기존 run 영향 0).
    security_types: list[str] | None = Field(default=None, max_length=4)

    @field_validator("selected_factors")
    @classmethod
    def _validate_factor_ids(cls, v: list[str]) -> list[str]:
        import re
        pattern = re.compile(_FACTOR_ID_PATTERN)
        for f in v:
            if not pattern.fullmatch(f):
                raise ValueError(
                    f"selected_factors[{f!r}] is not valid canonical_id format"
                )
        return v

    @field_validator("security_types")
    @classmethod
    def _validate_security_types(cls, v: list[str] | None) -> list[str] | None:
        """security_types 의 각 값을 ADR-0023 D2 허용 자산군으로 검증.

        미지원 값 (예: "bond") 이면 ValueError → FastAPI 가 422 (request
        validation). None (미지정) 은 통과 — 호출자가 ["common"] default 해소.
        빈 list 는 거부 (자산군 0 = 모집단 0 의도 모호, 명시 선택 강제).
        """
        if v is None:
            return None
        if len(v) == 0:
            raise ValueError(
                "security_types 가 빈 list 입니다 — 최소 1 개 자산군을 선택하세요."
            )
        for st in v:
            # 허용값 외면 ValueError (validate_security_type) → 422.
            validate_security_type(st)
        return v


# pack_slug 형식 — factor-pack-v1.json 의 pattern 과 일관 (user/... · community/...).
# 빌트인 사칭(speculum-builtin) 은 route 가 추가 차단(custom endpoint 는 custom only).
_PACK_SLUG_PATTERN: Final[str] = r"^[a-z][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?$"
# semver 형식 — x.y.z (custom pack version).
_PACK_VERSION_PATTERN: Final[str] = r"^[0-9]+\.[0-9]+\.[0-9]+$"


class CustomScreenRunQueryIn(ScreenRunQueryIn):
    """POST /api/screen/custom + /api/runs/custom 의 body — ScreenRunQueryIn + custom pack 식별자.

    ADR-0025 D4 — custom pack screen 실행은 익명 `/api/screen` 과 별도 endpoint
    (CurrentUserDep). 기존 ScreenRunQueryIn(conditions/selected_factors/
    security_types) 에 custom pack 식별자(pack_slug/version)를 추가. route 가
    `registry.resolve(pack_slug, version, user_id=user, custom_pack_repo=repo)` 로
    그 시점 custom pack 을 user 격리 조회(타 user → 404).

    Attributes:
        pack_slug: custom pack slug (예: "user/testuser-mypack"). 빌트인
            (speculum-builtin)은 route 가 거부(custom endpoint 전용 — 빌트인은
            기존 익명 /api/screen 사용).
        version: custom pack semver (예: "1.0.0").
    """

    pack_slug: str = Field(
        pattern=_PACK_SLUG_PATTERN, min_length=1, max_length=128,
        description="Custom pack slug (예: 'user/testuser-mypack').",
    )
    version: str = Field(
        pattern=_PACK_VERSION_PATTERN, min_length=5, max_length=32,
        description="Custom pack semver (예: '1.0.0').",
    )


class ScreenResultOut(BaseModel):
    """POST /api/screen 응답 — 실행 결과 (저장 없음).

    클라이언트가 본 결과를 받아 별도 POST /api/runs 로 Save Run 수행. 본
    response 의 `data_versions` 는 호출 시점 freeze — 디버깅 / UI freshness
    표시용.

    M0 한계 (oracle 2 차 C5):
        본 사이클의 POST /api/runs 는 body 의 data_versions 를 받지 않고 호출
        시점에 재수집. 두 호출 사이 정책 drift 시 silent 발생 가능. M0 에서는
        모든 정책 source 가 module-level Final (factor_pack DEFAULT_PACK,
        DEFAULT_CALENDAR, *_POLICY_VERSION) 이라 process lifetime 동안 stable —
        실질적 race 위험 0. T18 합류 후 외부 source (DART/KRX batch_id) 가
        data_versions 에 추가되면 본 한계 재검토 (body.data_versions 전달 또는
        token 패턴).

    투명성 필드 (S3, §2.1 Fidelity — additive, default 0 으로 하위호환):
        universe_size: 자산군 사전 필터(ADR-0023 D7) 후 실제 평가 대상 모집단 크기.
            "0건 매칭"이 "모집단 0" vs "조건 불충족" 인지 구별 가능.
        na_excluded_count: factor NA(데이터 부재)로 탈락한 종목 수. 임계 비교 실패
            (데이터 있으나 조건 불충족) 종목은 미포함 — "데이터 부재로 전부 제외"
            상황을 명시 (§2.1 Fidelity 위반 해소).

    **ScreenRunSnapshotOut 은 절대 변경 금지** — result_hash 격리 (H7).
    """

    model_config = _STRICT_MODEL_CONFIG

    result_codes: tuple[str, ...]
    total: int
    data_versions: dict[str, str]
    # S3 투명성 필드 — additive, default 0 으로 기존 클라이언트 하위호환 (H7).
    universe_size: int = 0
    na_excluded_count: int = 0


class ScreenRunSnapshotOut(BaseModel):
    """저장된 Screen Run snapshot — POST /api/runs + GET /api/runs/{id}."""

    model_config = _STRICT_MODEL_CONFIG

    id: UUID
    user_id: UUID
    as_of: date
    result_codes: tuple[str, ...]
    result_hash: str
    data_versions: dict[str, str]
    computed_at: datetime
    # 사용자 입력 보존 — UI 가 Run 재현 시 같은 conditions 표시.
    conditions: tuple[dict[str, str], ...]
    selected_factors: tuple[str, ...]
    # ADR-0023 D7 — universe 자산군 사전 필터 (canonical 정렬). result_hash 입력
    # 일부 — 재현 시 같은 자산군 모집단 보존.
    security_types: tuple[str, ...]

    @classmethod
    def from_domain(cls, snap: ScreenRunSnapshot) -> ScreenRunSnapshotOut:
        """도메인 → wire 단방향 factory."""
        return cls(
            id=snap.id,
            user_id=snap.user_id,
            as_of=snap.as_of,
            result_codes=snap.result_codes,
            result_hash=snap.result_hash,
            data_versions=dict(snap.data_versions),
            computed_at=snap.computed_at,
            conditions=tuple(dict(c) for c in snap.query.conditions),
            selected_factors=snap.query.selected_factors,
            security_types=snap.query.security_types,
        )


class ScreenRunListOut(BaseModel):
    """GET /api/runs — 사용자의 recent runs."""

    model_config = _STRICT_MODEL_CONFIG

    items: tuple[ScreenRunSnapshotOut, ...]
    total: int


class VersionsDiffOut(BaseModel):
    """GET /api/runs/{id}/diff — snapshot data_versions vs 현재 active.

    Attributes:
        snapshot_id: 비교 대상 Run.
        diff: 변경된 키만 — `{key: [old, new]}`. 빈 dict = 일치 (재현 OK).
    """

    model_config = _STRICT_MODEL_CONFIG

    snapshot_id: UUID
    diff: dict[str, tuple[str, str]]


# =============================================================================
# Export / Import-reproduce — M2 T80 self-identifying export + 재현 route
# =============================================================================

# export JSON 의 형식 마커 — 파일이 Speculum Screen Run export 임을 자기 식별.
# import(재현) route 가 이 값을 검증하여 잘못된 파일 타입 거부.
EXPORT_FORMAT_MARKER: Final[str] = "speculum-screen-run-export-v1"
# export 에 포함된 snapshot 스키마 버전 — ScreenRunSnapshotOut 구조 변경 시 bump.
SNAPSHOT_SCHEMA_VERSION: Final[str] = "1"


class ScreenRunExportOut(BaseModel):
    """GET /api/runs/{run_id}/export 응답 — self-identifying export wrapper.

    파일 자체가 Speculum Screen Run export 임을 자기 식별 (`export_format` 마커)
    하고, `run` 필드에 재현에 필요한 모든 정보(conditions, as_of, result_codes,
    result_hash, data_versions)를 담은 ScreenRunSnapshotOut 을 포함.

    ADR-0021 §4.1 (ROADMAP §4.1) — self-identifying JSON: conditions(조건 hash
    입력), data_versions(batch_id — frozen 기준), as_of, result_hash 가 자기완결.
    user_id 는 ScreenRunSnapshotOut 구조상 포함되나 재현 경로에서 무시 —
    export = 공유 가능, user 비종속 (ADR-0021 D5).

    Attributes:
        export_format: 고정값 `"speculum-screen-run-export-v1"` — 파일 타입 식별자.
        snapshot_schema_version: ScreenRunSnapshotOut 스키마 버전. 구조 변경 시 bump.
        run: 재현에 필요한 전체 snapshot — conditions / selected_factors / as_of /
            result_codes / result_hash / data_versions 자기완결 포함.
        pack: **custom run 전용** — 봉인된 custom pack body (content_hash 포함).
            custom screen 으로 만든 run 의 export 는 그 시점 custom pack 정의를
            자기완결로 포함해야 user/DB 무관 재현이 가능하다 (ADR-0025 D5, ADR-0021
            D5). 빌트인/reference run 은 `None` — registry 가 frozen slug/version
            으로 정본을 재로드하므로 body 불요. **빌트인 run export 의 byte 불변**
            을 위해 export route 가 `response_model_exclude_none=True` 로 None 키를
            직렬화에서 제외 (builtin export JSON 에 `pack` 키 미출현).
    """

    model_config = _STRICT_MODEL_CONFIG

    export_format: str = EXPORT_FORMAT_MARKER
    snapshot_schema_version: str = SNAPSHOT_SCHEMA_VERSION
    run: ScreenRunSnapshotOut
    # custom run 만 채움 — 빌트인/reference 는 None (route 가 exclude_none 직렬화).
    pack: dict[str, Any] | None = None


class _SnapshotIn(BaseModel):
    """POST /api/runs/reproduce 의 `run` 필드 입력용 lax 스키마.

    ScreenRunSnapshotOut 은 `strict=True` (UUID/date/tuple 이 Python 타입
    인스턴스 강제) 라 JSON 문자열 → Python 타입 coercion 이 차단된다.
    재현 body 는 JSON wire 로 들어오므로 `strict=False` (non-strict) 스키마가
    필요 — 문자열 UUID, 문자열 date, JSON array → tuple 모두 허용.

    `extra="ignore"` — import 된 파일의 미래 필드 무시 (포워드 호환).
    `frozen=True` — 불변성 (생성 후 수정 금지).
    """

    model_config = ConfigDict(strict=False, extra="ignore", frozen=True)

    id: UUID | None = None
    user_id: UUID | None = None
    as_of: date | None = None
    result_codes: tuple[str, ...] | None = None
    result_hash: str | None = None
    data_versions: dict[str, str] | None = None
    computed_at: datetime | None = None
    conditions: tuple[dict[str, str], ...] | None = None
    selected_factors: tuple[str, ...] | None = None
    # ADR-0023 D7 — 재현 시 같은 자산군 모집단 보존. 구 export (필드 부재) 는
    # None → 호출자가 ["common"] default (하위호환, 기존 보통주 run 재현 무영향).
    security_types: tuple[str, ...] | None = None


class ReproduceIn(BaseModel):
    """POST /api/runs/reproduce 요청 body — export JSON (또는 그 run 부분) 입력.

    export JSON 전체 (`ScreenRunExportOut` 형태) 를 그대로 paste 하거나,
    `run` 필드만 직접 전달하는 두 경로를 모두 수용.

    `strict=False` / `extra="ignore"` — JSON wire 입력 호환:
        - UUID/date 를 문자열로 coerce.
        - JSON array → tuple coerce.
        - 미래 추가 필드 무시 (포워드 호환).

    필수 필드 (재현에 필요한 최소 집합, run 내부):
        - conditions: Screener 조건 (factor/op/value).
        - as_of: PIT 기준 일자.
        - data_versions: batch_id (frozen 재현의 기준).
        - result_codes: 원 result_codes (matches 비교용).
        - result_hash: 원 result_hash.

    user 비종속 — export JSON 에 user_id 가 있어도 재현 경로에서 무시.
    공유된 export JSON 을 누구든 재현 가능 (ADR-0021 D5).
    """

    model_config = ConfigDict(strict=False, extra="ignore", frozen=True)

    # export wrapper 필드 — 있으면 export_format 검증 후 run 에서 추출.
    export_format: str | None = None
    snapshot_schema_version: str | None = None
    # run 필드 — export wrapper 전체 또는 run 직접 입력. lax _SnapshotIn 사용.
    run: _SnapshotIn | None = None
    # custom run 전용 — 봉인된 custom pack body (ADR-0025 D5). custom screen 으로
    # 만든 run 의 export 는 이 필드에 pack 정의를 자기완결로 싣는다. reproduce 가
    # frozen factor_pack_slug 가 custom 일 때 이 body 로 pack 을 재구성 (user/DB
    # 무관 — 삭제된 custom pack 도 재현). 빌트인/reference run 은 None (registry 가
    # frozen slug/version 으로 정본 재로드). 구 export(필드 부재)는 None.
    pack: dict[str, Any] | None = None

    def resolve_snapshot(self) -> _SnapshotIn:
        """body → _SnapshotIn 추출 (export wrapper / run 직접 입력 통합).

        export_format 이 있으면 EXPORT_FORMAT_MARKER 와 일치하는지 검증.
        run 이 없거나 필수 필드 누락 시 ValueError (호출자가 400 으로 변환).
        """
        if self.export_format is not None:
            if self.export_format != EXPORT_FORMAT_MARKER:
                raise ValueError(
                    f"export_format 이 '{EXPORT_FORMAT_MARKER}' 와 일치하지 않습니다."
                    f" (received: {self.export_format!r})"
                )
            if self.run is None:
                raise ValueError("export wrapper 입력에 'run' 필드가 없습니다.")

        if self.run is None:
            raise ValueError(
                "재현에 필요한 'run' 필드가 없습니다. "
                "export JSON 전체 또는 run 객체를 body 에 포함하세요."
            )

        snap = self.run
        # 필수 필드 존재 확인 — None 이면 재현 불가.
        missing = [
            name
            for name, val in [
                ("conditions", snap.conditions),
                ("as_of", snap.as_of),
                ("result_codes", snap.result_codes),
                ("result_hash", snap.result_hash),
                ("data_versions", snap.data_versions),
            ]
            if val is None
        ]
        if missing:
            raise ValueError(
                f"재현에 필요한 필드가 없습니다: {', '.join(missing)}"
            )
        return snap


class ReproduceOut(BaseModel):
    """POST /api/runs/reproduce 응답 — 재현 결과.

    M1 reproduce_run 재사용 — frozen batch_id cutoff 로 재실행한 result_codes 와
    원 snapshot 의 result_codes 의 byte-동일 여부(`matches`).

    Attributes:
        matches: True = 재현 성공 (byte-동일). False = 재현 불일치.
            False 의 주요 원인:
            1. data_versions 의 batch_id 에 해당 batch row 가 없음 (삭제/이전).
               → reproduced_result_codes 는 EXCLUDE_ALL cutoff 기준 결과.
            2. frozen batch 이후 정책·데이터 변화 (이론적 불일치).
        reproduced_result_codes: frozen batch cutoff 로 재실행한 결정적 종목코드
            (normalize 통과 — 6 자리·정렬·dedup).
        original_result_codes: 원 snapshot 의 result_codes (비교 기준).
        result_hash: 원 snapshot 의 result_hash (byte-동일 검증 식별자).
        note: 재현 결과에 대한 안내 메시지 (matches=False 시 원인 힌트).
    """

    model_config = _STRICT_MODEL_CONFIG

    matches: bool
    reproduced_result_codes: tuple[str, ...]
    original_result_codes: tuple[str, ...]
    result_hash: str
    note: str
    # pack_tampered: pack 변조(content_hash 불일치)로 재현 불가인 경우만 True.
    # 단순 부재(slug/version 미존재·custom body 없음)는 False — 변조와 구분.
    # client #6 use-시점 표시의 전제 — "변조" 배지를 명확히 판별(ADR-0033/M5 #3b).
    # result_hash 입력 스키마 불변(응답/결과 메타 전용). default False(하위호환).
    pack_tampered: bool = False


class RestatementLagOut(BaseModel):
    """POST /api/runs/restatement-lag 응답 — 정정 미반영 진단 (M5 #4b).

    frozen run 의 result_codes 중 frozen as_of 이후 정정공시가 후행된 종목을 사실로
    노출한다. 판정·해석 문구는 일절 없다 — 종목코드 목록과 건수 같은 **사실만**
    (§2.2 No Advice / §2.7 Observation). "위험"·"부정확" 등 평가 어휘 0.

    known limit (ADR-0033 D6 / M5_PLAN §3 #4b): result_codes *내* 종목 한정 —
    필터 탈락 후 정정으로 편입될 종목(result_codes *밖*)은 탐지 못함(구조적
    false-negative).

    Attributes:
        restated_codes: frozen as_of 이후 정정 후행 종목코드(정렬·dedup).
        count: ``len(restated_codes)`` — 정정 후행 종목 수(사실).
    """

    model_config = _STRICT_MODEL_CONFIG

    restated_codes: tuple[str, ...]
    count: int
