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
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.screen_run import ScreenRunSnapshot

__all__ = [
    "ConditionIn",
    "OpEnum",
    "ScreenResultOut",
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
    """

    model_config = _STRICT_MODEL_CONFIG

    conditions: list[ConditionIn] = Field(min_length=1, max_length=32)
    selected_factors: list[str] = Field(min_length=1, max_length=32)

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
    """

    model_config = _STRICT_MODEL_CONFIG

    result_codes: tuple[str, ...]
    total: int
    data_versions: dict[str, str]


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
