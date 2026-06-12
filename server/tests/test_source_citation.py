"""SourceCitation 단위 테스트.

테스트 매트릭스:
1. 정상 생성 + 모든 필드 보존
2. SourceKind enum strict (7 종)
3. identifier 검증 — min_length, whitespace-only, max length
4. retrieved_at UTC 강제 — naive / non-UTC offset 거부
5. effective_date date 강제 — datetime 거부
6. adapter_version semver 강제
7. url 검증 — http(s):// prefix + max length
8. created_at UTC 강제
9. frozen invariant
10. Repository — save / fetch_by_id / fetch_by_batch
11. append-only — duplicate id raise
12. CitationProducer Protocol (runtime_checkable)
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.models.source_citation import (
    CitationProducer,
    SourceCitation,
    SourceCitationError,
    SourceKind,
)
from app.repositories.citation_repository import (
    CitationRepository,
    FakeCitationRepository,
)

_UTC = UTC
_KST = timezone(timedelta(hours=9))


def _make(**overrides) -> SourceCitation:
    """기본 valid citation. overrides 로 일부 필드만 변경."""
    defaults: dict = {
        "id": uuid4(),
        "source": SourceKind.DART,
        "identifier": "20240501000123",
        "retrieved_at": datetime(2024, 5, 1, 12, 0, tzinfo=_UTC),
        "effective_date": date(2024, 5, 1),
        "adapter_version": "1.0.0",
        "batch_id": uuid4(),
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20240501000123",
        "created_at": datetime(2024, 5, 1, 12, 0, 5, tzinfo=_UTC),
    }
    defaults.update(overrides)
    return SourceCitation(**defaults)


# =============================================================================
# 1. 정상 생성
# =============================================================================

def test_create_with_all_fields() -> None:
    c = _make()
    assert c.source == SourceKind.DART
    assert c.identifier == "20240501000123"
    assert c.url.startswith("https://")


def test_create_with_none_url() -> None:
    """FDR 등 일부 source 는 url=None 허용."""
    c = _make(source=SourceKind.FDR, url=None, identifier="N/A")
    assert c.url is None


# =============================================================================
# 2. SourceKind enum strict
# =============================================================================

@pytest.mark.parametrize("source", list(SourceKind))
def test_all_source_kinds_accepted(source: SourceKind) -> None:
    c = _make(source=source)
    assert c.source == source


def test_source_kind_value_consistency() -> None:
    """enum value == name (str-mixin 의 JSON 직렬화 자연 보장)."""
    for k in SourceKind:
        assert k.value == k.name


def test_source_must_be_enum() -> None:
    """str literal 거부 — type-level 보장."""
    with pytest.raises(SourceCitationError, match="SourceKind"):
        _make(source="DART")  # type: ignore[arg-type]


# =============================================================================
# 3. identifier 검증
# =============================================================================

def test_identifier_min_length() -> None:
    with pytest.raises(SourceCitationError, match="non-empty"):
        _make(identifier="")


def test_identifier_whitespace_only_rejected() -> None:
    with pytest.raises(SourceCitationError, match="whitespace-only"):
        _make(identifier="   ")


def test_identifier_max_length() -> None:
    with pytest.raises(SourceCitationError, match="max length"):
        _make(identifier="x" * 501)


def test_identifier_non_str_rejected() -> None:
    with pytest.raises(SourceCitationError, match="must be str"):
        _make(identifier=12345)  # type: ignore[arg-type]


# =============================================================================
# 4. retrieved_at UTC 강제
# =============================================================================

def test_retrieved_at_naive_datetime_rejected() -> None:
    naive = datetime(2024, 5, 1, 12, 0)  # tzinfo=None
    with pytest.raises(SourceCitationError, match="tz-aware"):
        _make(retrieved_at=naive)


def test_retrieved_at_non_utc_offset_rejected() -> None:
    kst_dt = datetime(2024, 5, 1, 12, 0, tzinfo=_KST)
    with pytest.raises(SourceCitationError, match="UTC"):
        _make(retrieved_at=kst_dt)


def test_retrieved_at_non_datetime_rejected() -> None:
    with pytest.raises(SourceCitationError, match="must be datetime"):
        _make(retrieved_at="2024-05-01")  # type: ignore[arg-type]


# =============================================================================
# 5. effective_date date 강제 (datetime 거부)
# =============================================================================

def test_effective_date_accepts_date() -> None:
    c = _make(effective_date=date(2024, 5, 1))
    assert c.effective_date == date(2024, 5, 1)


def test_effective_date_rejects_datetime() -> None:
    """KST 가정 보존 — datetime 의 timezone 모호성 차단."""
    with pytest.raises(SourceCitationError, match="date"):
        _make(effective_date=datetime(2024, 5, 1, 12, 0, tzinfo=_UTC))  # type: ignore[arg-type]


# =============================================================================
# 6. adapter_version semver
# =============================================================================

@pytest.mark.parametrize("version", ["1.0.0", "0.1.0", "10.20.30",
                                       "1.0.0-alpha.1", "1.0.0-rc1"])
def test_adapter_version_valid_semver(version: str) -> None:
    c = _make(adapter_version=version)
    assert c.adapter_version == version


@pytest.mark.parametrize("invalid", ["1.0", "1.0.0.0", "v1.0.0", "1.0.x",
                                       "", "1.0.0-", "abc"])
def test_adapter_version_invalid_semver_rejected(invalid: str) -> None:
    with pytest.raises(SourceCitationError, match="semver"):
        _make(adapter_version=invalid)


def test_adapter_version_non_str_rejected() -> None:
    with pytest.raises(SourceCitationError, match="must be str"):
        _make(adapter_version=1.0)  # type: ignore[arg-type]


# =============================================================================
# 7. url 검증
# =============================================================================

def test_url_https_accepted() -> None:
    c = _make(url="https://dart.fss.or.kr/path")
    assert c.url.startswith("https://")


def test_url_http_accepted() -> None:
    c = _make(url="http://internal.example.com/path")
    assert c.url.startswith("http://")


@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,<script>",
    "ftp://example.com",
    "/relative/path",
])
def test_url_unsafe_scheme_rejected(bad_url: str) -> None:
    with pytest.raises(SourceCitationError, match="must start with"):
        _make(url=bad_url)


def test_url_max_length() -> None:
    long_url = "https://example.com/" + "x" * 3000
    with pytest.raises(SourceCitationError, match="max length"):
        _make(url=long_url)


def test_url_non_str_rejected() -> None:
    with pytest.raises(SourceCitationError, match="must be str"):
        _make(url=123)  # type: ignore[arg-type]


# =============================================================================
# 8. created_at UTC 강제
# =============================================================================

def test_created_at_naive_rejected() -> None:
    with pytest.raises(SourceCitationError, match="tz-aware"):
        _make(created_at=datetime(2024, 5, 1, 12, 0))


# =============================================================================
# 9. frozen invariant
# =============================================================================

def test_citation_is_frozen() -> None:
    c = _make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.identifier = "tampered"  # type: ignore[misc]


# =============================================================================
# 10. FakeCitationRepository
# =============================================================================

def test_repo_save_and_fetch_by_id() -> None:
    repo = FakeCitationRepository()
    c = _make()
    repo.save(c)
    assert repo.fetch_by_id(c.id) == c


def test_repo_fetch_unknown_id_returns_none() -> None:
    repo = FakeCitationRepository()
    assert repo.fetch_by_id(uuid4()) is None


def test_repo_fetch_by_batch_returns_all_in_batch() -> None:
    repo = FakeCitationRepository()
    batch = uuid4()
    c1 = _make(batch_id=batch,
               created_at=datetime(2024, 5, 1, 10, 0, tzinfo=_UTC))
    c2 = _make(batch_id=batch,
               created_at=datetime(2024, 5, 1, 11, 0, tzinfo=_UTC))
    c3_other = _make(batch_id=uuid4())
    repo.save(c1)
    repo.save(c2)
    repo.save(c3_other)
    result = repo.fetch_by_batch(batch)
    assert len(result) == 2
    assert {c.id for c in result} == {c1.id, c2.id}
    # 결정적 ordering — created_at ascending.
    assert result[0].created_at <= result[1].created_at


def test_repo_fetch_by_batch_empty_for_unknown() -> None:
    repo = FakeCitationRepository()
    assert tuple(repo.fetch_by_batch(uuid4())) == ()


# =============================================================================
# 11. Append-only invariant
# =============================================================================

def test_repo_save_duplicate_id_raises() -> None:
    """ADR-0002 D5 의 append-only — UPDATE 금지 의 Fake 구현."""
    repo = FakeCitationRepository()
    c = _make()
    repo.save(c)
    with pytest.raises(SourceCitationError, match="append-only"):
        repo.save(c)


# =============================================================================
# 12. Protocol type compliance
# =============================================================================

def test_fake_repo_satisfies_protocol() -> None:
    repo = FakeCitationRepository()
    assert isinstance(repo, CitationRepository)


def test_citation_producer_protocol_is_runtime_checkable() -> None:
    """adapter 가 implement 강제 — runtime_checkable 로 isinstance 가능."""
    class _MockAdapter:
        def make_citation(self, *, identifier: str, effective_date: date,
                           batch_id: UUID, url: str | None = None) -> SourceCitation:
            return _make()
    assert isinstance(_MockAdapter(), CitationProducer)


def test_citation_producer_protocol_rejects_non_compliant() -> None:
    class _NotAdapter:
        def some_other_method(self) -> None: pass
    assert not isinstance(_NotAdapter(), CitationProducer)


# =============================================================================
# 13. SourceKind enum coverage
# =============================================================================

def test_source_kind_values() -> None:
    """ADR-0002 D3 의 base 7 종 + M7 #2 (ADR-0035) FSC + ⓓ PRECOMPUTE(derived)."""
    assert {k.value for k in SourceKind} == {
        "DART", "KRX", "FDR", "PYKRX", "ECOS", "KOSIS", "USER_INPUT", "FSC",
        "PRECOMPUTE",
    }


# =============================================================================
# 14. Equality / hashing — frozen dataclass 기본 동작
# =============================================================================

# =============================================================================
# 15. oracle 2 차 리뷰 회귀
# =============================================================================

def test_identifier_rejects_null_byte() -> None:
    """oracle 2 차 M6 — NULL byte 차단 (PostgreSQL TEXT reject + 로그 인젝션)."""
    with pytest.raises(SourceCitationError, match="printable"):
        _make(identifier="abc\x00def")


def test_identifier_rejects_newline() -> None:
    with pytest.raises(SourceCitationError, match="printable"):
        _make(identifier="abc\ndef")


def test_identifier_rejects_carriage_return() -> None:
    with pytest.raises(SourceCitationError, match="printable"):
        _make(identifier="abc\rdef")


def test_identifier_accepts_korean_printable() -> None:
    """한글 등 non-ASCII printable 은 통과 — M2+ USER_INPUT 호환."""
    c = _make(identifier="삼성전자-Q1")
    assert c.identifier == "삼성전자-Q1"


def test_url_unsafe_scheme_checked_before_length() -> None:
    """oracle 2 차 C1 — 보안 검사가 길이 검사보다 먼저.

    긴 unsafe-scheme URL 입력 시 에러 메시지가 "must start with" — 길이 위반으로
    silent 분류되지 않음.
    """
    long_unsafe = "javascript:" + "x" * 3000
    with pytest.raises(SourceCitationError, match="must start with"):
        _make(url=long_unsafe)


def test_url_https_uppercase_scheme_rejected() -> None:
    """startswith() 는 case-sensitive — HTTPS:// 거부."""
    with pytest.raises(SourceCitationError, match="must start with"):
        _make(url="HTTPS://example.com/path")


def test_created_at_default_is_utc_now() -> None:
    """oracle 2 차 M5 — created_at 미명시 시 자동 UTC now."""
    from datetime import timedelta
    c = SourceCitation(
        id=uuid4(),
        source=SourceKind.DART,
        identifier="x",
        retrieved_at=datetime(2024, 5, 1, 12, 0, tzinfo=_UTC),
        effective_date=date(2024, 5, 1),
        adapter_version="1.0.0",
        batch_id=uuid4(),
        url=None,
        # created_at 미지정 — default factory 가 UTC now
    )
    now = datetime.now(_UTC)
    assert c.created_at.tzinfo is not None
    assert c.created_at.utcoffset() == timedelta(0)
    # 호출 시점 부근.
    assert abs((c.created_at - now).total_seconds()) < 5


def test_error_class_extends_exception_not_value_error() -> None:
    """oracle 2 차 M3 — codebase 일관성 (Exception 직접 상속)."""
    assert issubclass(SourceCitationError, Exception)
    assert not issubclass(SourceCitationError, ValueError)


def test_citation_equality_by_field_value() -> None:
    """frozen dataclass 의 equality — 모든 field 동일 시 True."""
    cid = uuid4()
    bid = uuid4()
    ts = datetime(2024, 5, 1, 12, 0, tzinfo=_UTC)
    c1 = SourceCitation(
        id=cid, source=SourceKind.DART, identifier="x",
        retrieved_at=ts, effective_date=date(2024, 5, 1),
        adapter_version="1.0.0", batch_id=bid, url=None,
        created_at=ts,
    )
    c2 = SourceCitation(
        id=cid, source=SourceKind.DART, identifier="x",
        retrieved_at=ts, effective_date=date(2024, 5, 1),
        adapter_version="1.0.0", batch_id=bid, url=None,
        created_at=ts,
    )
    assert c1 == c2
    assert hash(c1) == hash(c2)
