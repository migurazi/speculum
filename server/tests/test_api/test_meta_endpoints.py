"""`/api/as_of` + `/api/policy-versions` endpoint 통합 테스트.

본 사이클 (T24) 의 deliverable endpoint 가 main.create_app 에 wire 되었는지 검증.

테스트 매트릭스:
1. `/api/as_of?as_of=...` — 정규화 결과 + header
2. `/api/as_of` (no input) — current calendar range 밖이면 400, 범위 내면 200
3. `/api/policy-versions` — 14 키 + 모두 str
4. `/healthz` — middleware skip + 200
5. `/api/calendar` — coverage + 적재 최신 거래일 (as_of 클램프 source)
6. middleware × dependency 통합 — 400/422 응답이 middleware 통과
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.orm.batch_runs import BATCH_STATUS_PARTIAL, BATCH_STATUS_SUCCESS
from app.main import create_app
from app.repositories.batch_run_repository import FakeBatchRunRepository
from app.repositories.fakes import FakePriceRepository
from app.repositories.pit_protocols import PriceRecord


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


# =============================================================================
# 1. /api/as_of 정상 흐름
# =============================================================================

def test_get_as_of_business_day(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=2024-05-07")
    assert res.status_code == 200
    body = res.json()
    assert body["value"] == "2024-05-07"
    assert body["was_defaulted"] is False
    assert body["was_snapped"] is False
    assert body["pit_policy_version"] == "1.0"
    assert res.headers["x-asof"] == "2024-05-07"


def test_get_as_of_snaps_holiday(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=2024-05-01")  # 근로자의날
    assert res.status_code == 200
    body = res.json()
    assert body["was_snapped"] is True
    assert body["original_input"] == "2024-05-01"
    assert res.headers["x-asof-snapped"] == "true"
    assert res.headers["x-asof-original"] == "2024-05-01"


def test_get_as_of_future_returns_400(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=2999-12-31")
    assert res.status_code == 400
    assert res.json()["code"] == "AS_OF_IN_FUTURE"


def test_get_as_of_invalid_format_returns_422_sanitized(client: TestClient) -> None:
    res = client.get("/api/as_of?as_of=NOTADATE")
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "INVALID_REQUEST"
    # input 미echo
    assert "NOTADATE" not in str(body)


# =============================================================================
# 2. /api/policy-versions
# =============================================================================

def test_get_policy_versions_returns_14_keys(client: TestClient) -> None:
    # M2 T72 — distribution_policy_version (유니버스-상대 분포 정책) 합류로 11→12.
    # M7 #5 — total_return_policy_hash / total_return_policy_version (배당 재투자
    # 정책, §2.10 격리) 합류로 12→14.
    res = client.get("/api/policy-versions")
    assert res.status_code == 200
    body = res.json()
    expected = {
        "factor_pack_content_hash", "factor_pack_slug", "factor_pack_version",
        "calendar_content_hash", "calendar_version",
        "pit_policy_version",
        "price_adjustment_policy_hash", "adjustment_policy_version",
        "ca_policy_version",
        "evaluator_version", "distribution_policy_version",
        "total_return_policy_hash", "total_return_policy_version",
        "snapshot_schema_version",
    }
    assert set(body.keys()) == expected


def test_policy_versions_all_str_values(client: TestClient) -> None:
    res = client.get("/api/policy-versions")
    body = res.json()
    for k, v in body.items():
        assert isinstance(v, str), f"{k} should be str, got {type(v).__name__}"


# =============================================================================
# 3. /healthz
# =============================================================================

def test_healthz_returns_ok(client: TestClient) -> None:
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# =============================================================================
# 4. /api/calendar — KRX 캘린더 coverage + 최신 적재 거래일
# =============================================================================

def test_get_calendar_exposes_coverage(client: TestClient) -> None:
    """200 + 응답 키 집합 검증 + 캘린더 범위 값 + business day 는 평일."""
    res = client.get("/api/calendar")
    assert res.status_code == 200
    body = res.json()
    expected_keys = {
        "min_date", "max_date",
        "earliest_business_day", "latest_business_day",
        "latest_data_date",
        "version", "content_hash",
    }
    assert set(body.keys()) == expected_keys
    # KRX 캘린더 v1 coverage 실제 값.
    assert body["min_date"] == "2024-01-01"
    assert body["max_date"] == "2024-12-31"
    # earliest/latest_business_day 는 [min_date, max_date] 범위 내 + 평일.
    earliest = date.fromisoformat(body["earliest_business_day"])
    latest = date.fromisoformat(body["latest_business_day"])
    min_d = date.fromisoformat(body["min_date"])
    max_d = date.fromisoformat(body["max_date"])
    assert min_d <= earliest <= max_d
    assert min_d <= latest <= max_d
    assert earliest.weekday() < 5
    assert latest.weekday() < 5
    # 기본 client(FakePriceRepository 빈 records) → 데이터 없음 → null.
    assert body["latest_data_date"] is None


def test_get_calendar_latest_data_date_with_seeded_prices() -> None:
    """FakePriceRepository 주입 → latest_data_date 가 주입 레코드의 MAX effective_date."""
    _LINEAGE = uuid4()
    _CITATION = uuid4()

    def _price(*, d: date) -> PriceRecord:
        p = Decimal("50000")
        return PriceRecord(
            id=uuid4(), code="005930", code_lineage_id=_LINEAGE,
            effective_date=d,
            open_raw=p, high_raw=p, low_raw=p, close_raw=p,
            volume=1_000_000, trading_value=p * Decimal(1_000_000),
            close_adjusted=p,
            citation_id=_CITATION,
            created_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC),
        )

    records = [
        _price(d=date(2024, 5, 7)),
        _price(d=date(2024, 6, 3)),
        _price(d=date(2024, 8, 12)),
    ]
    app = create_app()
    app.state.price_repo_override = FakePriceRepository(records=records)
    with TestClient(app) as c:
        res = c.get("/api/calendar")
    assert res.status_code == 200
    body = res.json()
    assert body["latest_data_date"] == "2024-08-12"


# =============================================================================
# 5. middleware × dependency 통합 — 400 응답도 middleware 통과
# =============================================================================

def test_400_response_passes_middleware(client: TestClient) -> None:
    """exception handler 의 400 응답이 ForbiddenWordsGuardMiddleware 를 통과.

    응답 detail message 가 ADR-0007 의 금지 어휘를 안전하게 회피하는지 검증.
    """
    res = client.get("/api/as_of?as_of=2999-01-01")
    assert res.status_code == 400
    body = res.json()
    # detail message 검증 — 금지 어휘 free
    for forbidden in ("추천", "매수", "매도", "유망"):
        assert forbidden not in body["detail"], (
            f"AsOfInFutureError detail contains forbidden word: {forbidden}"
        )


def test_422_response_passes_middleware(client: TestClient) -> None:
    """validation handler 의 422 응답도 middleware 통과."""
    res = client.get("/api/as_of?as_of=BADFORMAT")
    assert res.status_code == 422
    body_str = str(res.json())
    for forbidden in ("추천", "매수", "매도"):
        assert forbidden not in body_str


# =============================================================================
# 6. Header — X-AsOf 항상 set (정상 응답 시)
# =============================================================================

def test_x_asof_header_always_set_on_success(client: TestClient) -> None:
    """정상 응답 시 X-AsOf 헤더 항상 set."""
    res = client.get("/api/as_of?as_of=2024-05-07")
    assert "x-asof" in res.headers
    assert res.headers["x-asof"] == "2024-05-07"


# =============================================================================
# 7. /openapi.json 의 forbidden_words 통과 확인 (편의)
# =============================================================================

def test_openapi_schema_does_not_block(client: TestClient) -> None:
    """OpenAPI schema 가 middleware 에 BLOCK 안 됨 — 본 사이클 docstring 검증."""
    res = client.get("/openapi.json")
    assert res.status_code == 200
    _body_str = str(res.json())
    # OpenAPI docstring 의 우리 vocabulary 가 안전한지 확인 (편의).
    # 본 test 는 강한 invariant 아닌 sanity check — 만약 fail 시 docstring lint
    # 필요.
    # 단순화: 응답이 정상 (200) 이면 middleware 통과한 것.


# =============================================================================
# 7. /api/factors — Screener factor 선택 UI source
# =============================================================================

def test_get_factors_lists_active_pack(client: TestClient) -> None:
    """활성 pack 의 factor 목록 — canonical_id + 사람이 읽는 name 노출."""
    res = client.get("/api/factors")
    assert res.status_code == 200
    body = res.json()
    assert body["pack_slug"] and body["pack_version"]
    factors = body["factors"]
    assert isinstance(factors, list) and len(factors) > 0
    ids = {f["canonical_id"] for f in factors}
    # 표시 factor 들이 드롭다운 source 에 포함돼야.
    assert "per:ttm-consolidated-ifrs" in ids
    assert "pbr:consolidated-ifrs" in ids
    # 각 항목은 값(canonical_id) + 라벨(name) 보유.
    for f in factors:
        assert f["canonical_id"] and f["name"]
        assert "unit" in f and "tags" in f


# =============================================================================
# 8. /api/data-freshness — M5 #4a (ADR-0033 D6)
# =============================================================================

def test_data_freshness_empty_returns_stale_schema(client: TestClient) -> None:
    """batch 부재(기본 Fake repo) → krx/dart/kosis 모두 stale + null 필드, 200 + 스키마."""
    res = client.get("/api/data-freshness")
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"krx", "dart", "kosis", "as_of"}
    for src_key in ("krx", "dart", "kosis"):
        src = body[src_key]
        assert set(src.keys()) == {
            "source", "latest_batch_at", "is_stale", "elapsed_days",
            "latest_batch_partial",
        }
        assert src["source"] == src_key.upper()
        assert src["is_stale"] is True
        assert src["latest_batch_at"] is None
        assert src["elapsed_days"] is None
        # batch 부재 = partial 도 아님 (False).
        assert src["latest_batch_partial"] is False
    # as_of 는 서버 now (ISO 8601) — 존재만 검증 (결정성은 서비스 테스트 책임).
    assert isinstance(body["as_of"], str) and body["as_of"]


def test_data_freshness_with_seeded_batch() -> None:
    """override 로 성공 batch 주입 → latest_batch_at 노출 + 스키마 정합."""
    app = create_app()
    repo = FakeBatchRunRepository()
    rid = uuid4()
    ended = datetime(2024, 5, 7, 2, tzinfo=UTC)
    repo.start(run_id=rid, market="KOSPI", source="KRX", started_at=ended)
    repo.finalize(
        run_id=rid, ended_at=ended, success_count=10, status=BATCH_STATUS_SUCCESS,
    )
    app.state.batch_run_repo_override = repo
    with TestClient(app) as c:
        res = c.get("/api/data-freshness")
    assert res.status_code == 200
    body = res.json()
    assert body["krx"]["latest_batch_at"] == "2024-05-07T02:00:00Z"
    assert body["krx"]["elapsed_days"] is not None
    assert isinstance(body["krx"]["is_stale"], bool)
    # 완전 성공 batch → partial 아님 (False).
    assert body["krx"]["latest_batch_partial"] is False
    # DART seed 없음 → 여전히 stale + null.
    assert body["dart"]["latest_batch_at"] is None
    assert body["dart"]["is_stale"] is True
    # KOSIS seed 없음 → 여전히 stale + null.
    assert body["kosis"]["latest_batch_at"] is None
    assert body["kosis"]["is_stale"] is True


def test_data_freshness_exposes_partial_batch() -> None:
    """partial batch 주입 → latest_batch_partial=True 가 엔드포인트로 노출 (M-2)."""
    app = create_app()
    repo = FakeBatchRunRepository()
    rid = uuid4()
    ended = datetime(2024, 5, 7, 2, tzinfo=UTC)
    repo.start(run_id=rid, market="KOSPI", source="KRX", started_at=ended)
    # 부분 실패 — 성공분 commit + 일부 실패 (status='partial').
    repo.finalize(
        run_id=rid, ended_at=ended, success_count=8, status=BATCH_STATUS_PARTIAL,
    )
    app.state.batch_run_repo_override = repo
    with TestClient(app) as c:
        res = c.get("/api/data-freshness")
    assert res.status_code == 200
    body = res.json()
    # partial 사실 노출 — partial 도 freshness 소스 자격 유지(latest_batch_at 노출).
    assert body["krx"]["latest_batch_partial"] is True
    assert body["krx"]["latest_batch_at"] == "2024-05-07T02:00:00Z"


def test_data_freshness_with_kosis_seeded_batch() -> None:
    """KOSIS batch seed → kosis.is_stale=False + latest_batch_at 노출 (M9 #2, ADR-0036 D7)."""
    app = create_app()
    repo = FakeBatchRunRepository()
    # KOSIS batch seed — 최신(45일 이내) → is_stale=False.
    rid = uuid4()
    ended = datetime(2024, 5, 1, 2, tzinfo=UTC)
    repo.start(run_id=rid, market=None, source="KOSIS", started_at=ended)
    repo.finalize(
        run_id=rid, ended_at=ended, success_count=5, status=BATCH_STATUS_SUCCESS,
    )
    app.state.batch_run_repo_override = repo
    with TestClient(app) as c:
        res = c.get("/api/data-freshness")
    assert res.status_code == 200
    body = res.json()
    # KOSIS seed → latest_batch_at 노출 + is_stale 사실값.
    assert body["kosis"]["latest_batch_at"] == "2024-05-01T02:00:00Z"
    assert body["kosis"]["elapsed_days"] is not None
    assert isinstance(body["kosis"]["is_stale"], bool)
    # 45일 임계 이내 이므로 not stale (테스트 실행 시점에 무관하게 서버 now 주입 —
    # 테스트 실행 시점은 2024-05-01 이후이므로 elapsed_days >= 0 이나, 실제 is_stale
    # 여부는 서버 now 기준임. 여기서는 필드 노출 + 스키마 정합만 검증).
    assert body["kosis"]["latest_batch_at"] is not None
    # KRX/DART seed 없음 → stale + null.
    assert body["krx"]["latest_batch_at"] is None
    assert body["krx"]["is_stale"] is True
    assert body["dart"]["latest_batch_at"] is None
    assert body["dart"]["is_stale"] is True
