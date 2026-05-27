"""Source Citation — ADR-0002 D3 의 7-tuple 의무 출처 모델.

8 기둥 §2.1 Fidelity 의 backbone — 모든 fact 가 본 entity 로 1차 자료 출처를
영구 보유. AC-D-05 (모든 fact 의 effective_date 또는 as_of) + AC-P-01 (Fidelity
— 모든 값 옆에 식·출처·기준일) 의 implementation.

설계 원칙 (oracle 자문 P0+P1 반영):

1. **dataclass + slots + frozen** (oracle 결정 1) — Pydantic v2 거부. codebase
   의 모든 frozen 모델 (`LoadedPack`, `ScreenRunSnapshot`, `PriceRecord` 등) 과
   일관. Pydantic 은 T24 FastAPI 합류 시점의 `schemas/` 도메인 — 본 모듈은
   `models/` 도메인.
2. **`__post_init__` 검증** — frozen dataclass 의 invariant 강제. 위반 시
   `SourceCitationError` raise.
3. **SourceKind enum strict** (oracle 결정 2) — 7 종 fix. 새 source 도입 시 ADR
   amendment + enum value 추가 (M2+ community pack 의 EXTERNAL 도 backlog).
4. **`retrieved_at` UTC 강제** (oracle 결정 6) — tz-naive 또는 non-UTC 거부.
   silent KST/UTC bug 차단. `effective_date` 는 date (naive) 로 KST 기준 가정 —
   DART/KRX 가 KST 로 발표.
5. **`adapter_version` semver 강제** (oracle 결정 4) — `factor-pack v1.0.0` /
   `pit_policy_version` 등 codebase 의 semver 일관성. prerelease suffix 허용
   (M2 community alpha/beta).
6. **`url` minimal validation** (oracle 결정 5) — http(s):// prefix + max len
   2048. file:// / javascript: / data: 차단 (보안). domain allowlist 는 별도
   backlog.
7. **append-only invariant** — frozen=True 는 Python in-process 만 보장. DB
   layer 의 BEFORE UPDATE trigger 가 row UPDATE 차단하는 것은 T13 책임.
8. **PackCitation 분리** (oracle 결정 7) — factor_pack 의 citation 은 별도
   schema (factor-pack-v1.json $defs/citation) 유지. 본 모델은 fact 측 entity
   전용 — 의미·생명주기·CRUD 패턴 완전 별개.

관련 ADR / 문서:
- ADR-0002 D3 (7-tuple 명세), D5 (`source_citations` SQL schema)
- M0_PLAN T23 / AC-D-05 / AC-P-01
- 8 기둥 §2.1 Fidelity, §2.10 Reproducibility (citation 의 batch_id 가 freeze)

Out-of-scope (P2 backlog):
- source 별 identifier discriminated validator (DART 14자리 / KRX trd_dt+market)
- url 의 domain allowlist (DART/KRX 도메인만)
- UUIDv7 migration (현재 uuid4 random)
- DART 정정공시 시 `is_correction` / `original_rcept_no` field
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "CitationProducer",
    "SourceCitation",
    "SourceCitationError",
    "SourceKind",
]

# =============================================================================
# Source kind enum — ADR-0002 D3 line 99 의 7 종
# =============================================================================

class SourceKind(str, Enum):
    """Fact 의 1차 자료 출처 종류.

    str-mixin — JSON 직렬화 시 enum value 자연 변환 (`"DART"` 등).

    Future (M2+ community pack):
        `EXTERNAL` value 추가 검토. 본 사이클은 7 종 fix.
    """

    DART = "DART"            # 전자공시시스템 (재무제표, corporate action)
    KRX = "KRX"              # 한국거래소 (시가/거래량/시가총액)
    FDR = "FDR"              # FinanceDataReader (가격 시계열 교차검증)
    PYKRX = "PYKRX"          # pykrx 라이브러리 (KRX 공식 wrapper)
    ECOS = "ECOS"            # 한국은행 경제통계시스템 (M1+ 거시지표)
    KOSIS = "KOSIS"          # 통계청 KOSIS (M1+ 경제·산업 통계)
    USER_INPUT = "USER_INPUT"  # 사용자 manual 입력 (M2+ Factor Lab)


# =============================================================================
# Errors
# =============================================================================

class SourceCitationError(Exception):
    """SourceCitation 의 7-tuple invariant 위반.

    codebase 의 다른 service error 계층 (`PITError`, `FactorPackError`,
    `CalendarError`, `FactorEvaluatorError`) 와 일관 — `Exception` 직접 상속
    (oracle 2 차 리뷰 M3). `except ValueError` broad-catch 의 silent 흡수 차단.
    """


# =============================================================================
# Validation constants
# =============================================================================

# adapter_version 의 semver 패턴 — prerelease suffix 허용 (e.g., "1.0.0-alpha.1").
# `factor-pack-v1.json` 의 version pattern 보다 약간 관대 (M2 의 alpha/beta 대비).
_ADAPTER_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d+\.\d+\.\d+(-[a-z0-9.-]+)?$"
)

# URL prefix 의 minimal allowlist — javascript: / data: / file:// 등 차단.
_ALLOWED_URL_PREFIXES: Final[tuple[str, ...]] = ("http://", "https://")
_MAX_URL_LENGTH: Final[int] = 2048
_MAX_IDENTIFIER_LENGTH: Final[int] = 500


# =============================================================================
# SourceCitation dataclass — ADR-0002 D3
# =============================================================================

@dataclass(frozen=True, slots=True)
class SourceCitation:
    """모든 fact 의 1차 자료 provenance — 7-tuple immutable record.

    Attributes:
        id: citation row 고유 UUID.
        source: SourceKind enum — 어느 출처.
        identifier: source 내 고유 ID (예: DART rcept_no, KRX `trd_dt+market`).
            min_length=1, whitespace-only 거부. source 별 format 검증은 adapter
            책임 (oracle 자문 결정 3 — minimal invariant 만).
        retrieved_at: adapter 가 fetch 한 시각 — **UTC 강제** (oracle 결정 6).
            naive datetime 또는 non-UTC offset 거부.
        effective_date: 데이터의 발효일 — **KST 기준 date 가정**. DART rcept_dt 와
            KRX trd_dt 가 모두 KST. tz-naive `date` 이므로 본 가정이 운영 invariant.
        adapter_version: adapter 모듈 자체 코드 버전 (semver). `pykrx==1.0.45`
            같은 외부 의존 버전 X — 우리 adapter 의 `pykrx_adapter.py` 가 정한
            자체 버전.
        batch_id: 일배치 잡 식별자. 같은 batch 의 fact 들이 함께 freeze 가능
            (재현성). M0 = uuid4 random, M1 검토 UUIDv7.
        url: 1차 자료 원문 링크. None 허용 (FDR 등 일부 source 는 N/A). 있으면
            http(s):// prefix + max 2048 chars.
        created_at: row 생성 시각 (UTC). append-only marker — DB layer 가 UPDATE
            차단. T13 책임.

    Append-only invariant:
        `frozen=True` 는 in-process Python 보장만. DB layer 의 BEFORE UPDATE
        trigger 가 실제 row UPDATE 차단 (ADR-0002 D5 line 190). 본 모델은 인스턴스
        immutability 의 1차 방어.
    """

    id: UUID
    source: SourceKind
    identifier: str
    retrieved_at: datetime
    effective_date: date
    adapter_version: str
    batch_id: UUID
    url: str | None
    # default = 호출 시점 UTC. adapter boilerplate 회피 (oracle 2 차 M5). 단
    # deterministic test 필요 시 명시 주입.
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        # 1. source enum (이미 type-level 보장, runtime 추가 검증 불필요).
        if not isinstance(self.source, SourceKind):
            raise SourceCitationError(
                f"source must be SourceKind enum, got "
                f"{type(self.source).__name__}: {self.source!r}"
            )

        # 2. identifier — minimal invariant (oracle 결정 3) + control char 차단
        # (oracle 2 차 M6 — NULL byte / newline / CR 등 로그 인젝션 + PostgreSQL
        # TEXT NULL byte reject 호환).
        if not isinstance(self.identifier, str):
            raise SourceCitationError(
                f"identifier must be str, got {type(self.identifier).__name__}"
            )
        if not self.identifier or not self.identifier.strip():
            raise SourceCitationError(
                "identifier must be non-empty and not whitespace-only"
            )
        if len(self.identifier) > _MAX_IDENTIFIER_LENGTH:
            raise SourceCitationError(
                f"identifier exceeds max length {_MAX_IDENTIFIER_LENGTH}: "
                f"got {len(self.identifier)}"
            )
        # Control character (U+0000~U+001F 및 U+007F) 차단 — `isprintable()` 은
        # 한글·non-ASCII printable 통과 (M2+ USER_INPUT 호환). 단 일반 whitespace
        # (space) 도 isprintable True 이므로 정상.
        if not self.identifier.isprintable():
            raise SourceCitationError(
                f"identifier must be printable (no control characters): "
                f"got {self.identifier!r}"
            )

        # 3. retrieved_at — UTC 강제 (oracle 결정 6).
        _validate_utc_datetime(self.retrieved_at, field_name="retrieved_at")

        # 4. effective_date — date type 검사 (datetime 거부 — KST 가정 보존).
        # datetime 은 date subclass 이므로 type(...) is date 로 strict 검사.
        if type(self.effective_date) is not date:
            raise SourceCitationError(
                f"effective_date must be date (not datetime), got "
                f"{type(self.effective_date).__name__}"
            )

        # 5. adapter_version — semver 패턴 (oracle 결정 4).
        if not isinstance(self.adapter_version, str):
            raise SourceCitationError(
                f"adapter_version must be str, got "
                f"{type(self.adapter_version).__name__}"
            )
        if not _ADAPTER_VERSION_PATTERN.fullmatch(self.adapter_version):
            raise SourceCitationError(
                f"adapter_version must be semver (e.g., '1.0.0' or "
                f"'1.0.0-alpha.1'), got {self.adapter_version!r}"
            )

        # 6. url — None 또는 http(s):// prefix + max length (oracle 결정 5).
        # 검사 순서: type → unsafe scheme (보안) → length (oracle 2 차 C1 — 보안
        # 검사가 길이 검사보다 우선해 unsafe scheme 시도가 길이 위반으로 분류
        # 차단).
        if self.url is not None:
            if not isinstance(self.url, str):
                raise SourceCitationError(
                    f"url must be str or None, got {type(self.url).__name__}"
                )
            if not any(self.url.startswith(p) for p in _ALLOWED_URL_PREFIXES):
                raise SourceCitationError(
                    f"url must start with one of {_ALLOWED_URL_PREFIXES}, "
                    f"got {self.url[:32]!r}..."
                )
            if len(self.url) > _MAX_URL_LENGTH:
                raise SourceCitationError(
                    f"url exceeds max length {_MAX_URL_LENGTH}: "
                    f"got {len(self.url)}"
                )

        # 7. created_at — UTC 강제 (retrieved_at 과 일관).
        _validate_utc_datetime(self.created_at, field_name="created_at")


def _validate_utc_datetime(value: datetime, *, field_name: str) -> None:
    """datetime 이 tz-aware + UTC offset=0 인지 강제.

    oracle 결정 6 — adapter 가 naive datetime 전달 시 silent KST/UTC drift 차단.
    """
    if not isinstance(value, datetime):
        raise SourceCitationError(
            f"{field_name} must be datetime, got {type(value).__name__}"
        )
    if value.tzinfo is None:
        raise SourceCitationError(
            f"{field_name} must be tz-aware (UTC), got naive datetime"
        )
    if value.utcoffset() != timedelta(0):
        raise SourceCitationError(
            f"{field_name} must be UTC (offset=0), got offset={value.utcoffset()}"
        )


# =============================================================================
# CitationProducer Protocol — adapter contract (model layer 유지)
# =============================================================================
#
# Repository (저장/조회) 는 `app/repositories/citation_repository.py` 로 분리
# (oracle 2 차 M4 — `app/models/` 는 운영 도메인 entity 만, repository 는 별도
# layer). `CitationProducer` 는 adapter contract 라 도메인 의미에 가까워 model
# layer 유지.

@runtime_checkable
class CitationProducer(Protocol):
    """Adapter 가 implement 해야 하는 contract — 모든 adapter 가 citation 생성 강제.

    T14 (FDR) / T15 (pykrx) / T16 (DART) / T18 (KRX 일배치) / T19 (DART 일배치)
    가 본 Protocol 을 만족해야 type-level 강제 작동.
    """

    def make_citation(
        self,
        *,
        identifier: str,
        effective_date: date,
        batch_id: UUID,
        url: str | None = None,
    ) -> SourceCitation:
        """주어진 입력으로 SourceCitation 생성. adapter 가 자기 `source` /
        `adapter_version` 채움.
        """
        ...
