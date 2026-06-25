"""PackRegistry Phase 2c — custom pack screen 실행 + 재현 freeze 통합 테스트 (ADR-0025).

테스트 매트릭스 (절대 원칙 게이트):
- custom screen save → reproduce matches=True + result_hash byte 동일 (P0 핵심).
- custom run hash freeze — custom run result_hash != 빌트인 run result_hash
  (custom pack content_hash 반영).
- 삭제된 custom pack — export body 자기완결이라 pack 삭제 후에도 reproduce
  matches=True (body 재구성). body 없으면 matches=False.
- 변조 export body — content_hash 불일치 → matches=False fail-loud.
- user 격리 — 타 user pack_slug → 404. 무토큰(AUTH_SECRET 설정) → 401.
- 기존 익명 /api/screen 무변경 — 무토큰 200 (함정 D).
- 빌트인 export byte 불변 — builtin run export 에 `pack` 키 미출현.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.fakes import FakeMarketCapRepository
from app.repositories.pit_protocols import (
    CodeHistoryEntry,
    MarketCapRecord,
    StockMasterRecord,
)
from app.repositories.screen_run_repository import FakeScreenRunRepository
from app.repositories.stocks_master_repository import FakeStocksMasterRepository

# =============================================================================
# 공용 fixture 데이터 — shares_issued (market_cap.shares_outstanding) 기반 custom factor
# =============================================================================

_SAMSUNG_CODE = "005930"
_AS_OF_STR = "2024-05-07"
_AS_OF = date(2024, 5, 7)
_CUSTOM_FACTOR_ID = "alice:shares"
_USER_B = "00000000-0000-0000-0000-0000000000bb"

_SAMSUNG = StockMasterRecord(
    id=UUID("00000000-0000-0000-0000-000000000001"),
    current_code=_SAMSUNG_CODE,
    current_name="삼성전자",
    market="KOSPI",
    listing_date=date(1975, 6, 11),
    delisting_date=None,
    fiscal_month=12,
    code_history=(
        CodeHistoryEntry(_SAMSUNG_CODE, date(1975, 6, 11), None, "initial_listing"),
    ),
)

# shares_issued = 5_000_000 → custom factor 값 5_000_000 > 100 통과.
_MARKET_CAP = MarketCapRecord(
    id=UUID("00000000-0000-0000-0000-000000000031"),
    code=_SAMSUNG_CODE,
    code_lineage_id=UUID(int=int(_SAMSUNG_CODE)),
    effective_date=date(2024, 5, 6),
    market_cap=Decimal("400000000000000"),
    shares_outstanding=5_000_000,
    shares_treasury=None,
    citation_id=UUID("00000000-0000-0000-0000-000000000131"),
    created_at=datetime(2024, 5, 6, tzinfo=UTC),
)


def _custom_factor(*, canonical_id: str = _CUSTOM_FACTOR_ID) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "uuid": "00000000-0000-0000-0000-0000000000c1",
        "name": canonical_id.replace(":", " "),
        "description": canonical_id + " custom factor for screen testing",
        "formula": {
            "ast": {"field": "shares_issued"},
            "inputs": ["shares_issued"],
        },
        "unit": "ratio",
    }


def _custom_pack(
    *,
    pack_slug: str = "user/alice-mypack",
    version: str = "1.0.0",
    canonical_id: str = _CUSTOM_FACTOR_ID,
) -> dict[str, Any]:
    return {
        "$schema": "https://speculum.dev/schemas/factor-pack-v1.json",
        "type": "factor-pack",
        "pack_slug": pack_slug,
        "version": version,
        "publisher": "alice",
        "license": "MIT",
        "created_at": "2026-01-01",
        "citation": {"title": "Alice Custom Pack", "publisher": "alice"},
        "factors": [_custom_factor(canonical_id=canonical_id)],
        "content_hash": "sha256:" + "0" * 64,  # placeholder — save 가 재봉인.
    }


def _custom_body(
    *,
    pack_slug: str = "user/alice-mypack",
    version: str = "1.0.0",
    factor: str = _CUSTOM_FACTOR_ID,
    op: str = ">",
    value: str = "100",
) -> dict[str, Any]:
    return {
        "pack_slug": pack_slug,
        "version": version,
        "conditions": [{"factor": factor, "op": op, "value": value}],
        "selected_factors": [factor],
    }


@pytest.fixture
def client() -> Iterator[TestClient]:
    """custom pack screen 통합 fixture — stocks + market_cap + runs Fake."""
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_SAMSUNG]),
        market_cap_repository=FakeMarketCapRepository(records=[_MARKET_CAP]),
        runs_repository=FakeScreenRunRepository(),
    )
    with TestClient(app) as c:
        yield c


def _save_custom_pack(client: TestClient, *, pack: dict[str, Any] | None = None) -> dict:
    """custom pack 저장 후 메타 반환 (POST /api/factor-packs/saved)."""
    res = client.post(
        "/api/factor-packs/saved", json={"pack": pack or _custom_pack()},
    )
    assert res.status_code == 201, res.text
    return res.json()


# =============================================================================
# 1. custom screen 실행 (저장 X)
# =============================================================================

def test_custom_screen_executes_with_custom_pack(client: TestClient) -> None:
    """저장 custom pack 으로 /api/screen/custom 실행 → 종목 통과 + custom hash freeze."""
    saved = _save_custom_pack(client)
    res = client.post(f"/api/screen/custom?as_of={_AS_OF_STR}", json=_custom_body())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["result_codes"] == [_SAMSUNG_CODE]
    assert body["total"] == 1
    # custom pack content_hash 가 freeze (빌트인 pack 과 다른 hash).
    assert body["data_versions"]["factor_pack_slug"] == "user/alice-mypack"
    assert body["data_versions"]["factor_pack_content_hash"] == saved["content_hash"]


def test_custom_screen_unknown_pack_404(client: TestClient) -> None:
    """미존재 custom pack slug → 404 (저장 안 함)."""
    res = client.post(
        f"/api/screen/custom?as_of={_AS_OF_STR}",
        json=_custom_body(pack_slug="user/nonexistent"),
    )
    assert res.status_code == 404


def test_custom_screen_builtin_slug_rejected_400(client: TestClient) -> None:
    """빌트인 slug 는 custom endpoint 거부 → 400."""
    res = client.post(
        f"/api/screen/custom?as_of={_AS_OF_STR}",
        json=_custom_body(pack_slug="speculum-builtin"),
    )
    assert res.status_code == 400


# =============================================================================
# 2. custom screen save → reproduce matches=True (P0 핵심)
# =============================================================================

def test_custom_save_export_reproduce_matches_true(client: TestClient) -> None:
    """custom pack save → export(pack body 동봉) → reproduce → matches=True + byte 동일."""
    _save_custom_pack(client)
    save_res = client.post(f"/api/runs/custom?as_of={_AS_OF_STR}", json=_custom_body())
    assert save_res.status_code == 201, save_res.text
    run = save_res.json()
    run_id = run["id"]
    assert run["result_codes"] == [_SAMSUNG_CODE]

    export_res = client.get(f"/api/runs/{run_id}/export")
    assert export_res.status_code == 200
    export_json = export_res.json()
    # custom run export 는 pack body 를 자기완결로 동봉.
    assert export_json["pack"] is not None
    assert export_json["pack"]["pack_slug"] == "user/alice-mypack"

    reproduce_res = client.post("/api/runs/reproduce", json=export_json)
    assert reproduce_res.status_code == 200
    result = reproduce_res.json()
    assert result["matches"] is True
    assert result["reproduced_result_codes"] == result["original_result_codes"]
    assert result["result_hash"] == run["result_hash"]


def test_custom_run_hash_differs_from_builtin(client: TestClient) -> None:
    """custom run result_hash 는 custom pack content_hash 반영 — 빌트인 run 과 다름."""
    _save_custom_pack(client)
    custom_run = client.post(
        f"/api/runs/custom?as_of={_AS_OF_STR}", json=_custom_body(),
    ).json()

    # 같은 conditions 형태의 빌트인 run (PER factor) — data_versions 의 factor_pack
    # content_hash 가 다르므로 result_hash 도 달라야 한다.
    builtin_body = {
        "conditions": [{"factor": "per:ttm-consolidated-ifrs", "op": "<", "value": "10"}],
        "selected_factors": ["per:ttm-consolidated-ifrs"],
    }
    builtin_run = client.post(
        f"/api/runs?as_of={_AS_OF_STR}", json=builtin_body,
    ).json()

    assert custom_run["data_versions"]["factor_pack_content_hash"] != \
        builtin_run["data_versions"]["factor_pack_content_hash"]
    assert custom_run["result_hash"] != builtin_run["result_hash"]


# =============================================================================
# 3. 삭제된 custom pack — export body 자기완결 재현
# =============================================================================

def test_reproduce_after_pack_deleted_still_matches(client: TestClient) -> None:
    """custom pack 삭제 후에도 export body 로 reproduce matches=True (자기완결)."""
    saved = _save_custom_pack(client)
    save_res = client.post(f"/api/runs/custom?as_of={_AS_OF_STR}", json=_custom_body())
    run_id = save_res.json()["id"]
    export_json = client.get(f"/api/runs/{run_id}/export").json()
    assert export_json["pack"] is not None

    # custom pack 삭제.
    del_res = client.delete(f"/api/factor-packs/saved/{saved['id']}")
    assert del_res.status_code == 204

    # export body 자기완결 → 삭제 후에도 재현 성공.
    reproduce_res = client.post("/api/runs/reproduce", json=export_json)
    assert reproduce_res.status_code == 200
    assert reproduce_res.json()["matches"] is True


def test_reproduce_custom_run_without_pack_body_fails(client: TestClient) -> None:
    """custom run 인데 export body 의 pack 부재 → matches=False (body 필요)."""
    _save_custom_pack(client)
    save_res = client.post(f"/api/runs/custom?as_of={_AS_OF_STR}", json=_custom_body())
    run_id = save_res.json()["id"]
    export_json = client.get(f"/api/runs/{run_id}/export").json()

    # pack body 제거 (구 export / body 미동봉 모사).
    export_json.pop("pack", None)
    reproduce_res = client.post("/api/runs/reproduce", json=export_json)
    assert reproduce_res.status_code == 200
    result = reproduce_res.json()
    assert result["matches"] is False
    assert "body" in result["note"]


# =============================================================================
# 4. 변조 export body — content_hash 불일치 fail-loud
# =============================================================================

def test_reproduce_tampered_pack_body_fails_loud(client: TestClient) -> None:
    """export body 변조(content_hash 불일치) → matches=False fail-loud."""
    _save_custom_pack(client)
    save_res = client.post(f"/api/runs/custom?as_of={_AS_OF_STR}", json=_custom_body())
    run_id = save_res.json()["id"]
    export_json = client.get(f"/api/runs/{run_id}/export").json()

    # pack body 의 factor 정의를 변조 (content_hash 는 그대로 → 불일치).
    export_json["pack"]["factors"][0]["description"] = "변조된 설명 tampered tampered"
    reproduce_res = client.post("/api/runs/reproduce", json=export_json)
    assert reproduce_res.status_code == 200
    result = reproduce_res.json()
    assert result["matches"] is False
    assert result["note"] is not None


# =============================================================================
# 5. user 격리 — 타 user pack 404 / 무토큰 401 (AUTH_SECRET 설정)
# =============================================================================

def test_custom_screen_other_user_pack_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """user A 저장 custom pack 을 user B 가 /api/screen/custom → 404 (IDOR)."""
    _save_custom_pack(client)  # user A (SYSTEM_USER_ID default).

    # user B 로 전환.
    monkeypatch.setenv("SPECULUM_USER_ID", _USER_B)
    res = client.post(f"/api/screen/custom?as_of={_AS_OF_STR}", json=_custom_body())
    assert res.status_code == 404


def test_custom_screen_requires_auth_when_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_SECRET 설정 + 무토큰 → /api/screen/custom 401 (custom 은 인증 필수)."""
    monkeypatch.setenv("AUTH_SECRET", "test-secret-xyz-0123456789abcdef-0123")
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_SAMSUNG]),
        market_cap_repository=FakeMarketCapRepository(records=[_MARKET_CAP]),
        runs_repository=FakeScreenRunRepository(),
    )
    with TestClient(app) as c:
        res = c.post(f"/api/screen/custom?as_of={_AS_OF_STR}", json=_custom_body())
        assert res.status_code == 401


def test_custom_screen_works_with_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_SECRET 설정 + 유효 JWT → custom pack 저장·screen 정상 (인증 경로)."""
    secret = "test-secret-xyz-0123456789abcdef-0123"
    monkeypatch.setenv("AUTH_SECRET", secret)
    app = create_app(
        stocks_repository=FakeStocksMasterRepository(records=[_SAMSUNG]),
        market_cap_repository=FakeMarketCapRepository(records=[_MARKET_CAP]),
        runs_repository=FakeScreenRunRepository(),
    )
    token = jwt.encode(
        {"sub": "google-alice-123", "email": "alice@example.com", "exp": 9999999999},
        secret, algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as c:
        save = c.post(
            "/api/factor-packs/saved", json={"pack": _custom_pack()}, headers=headers,
        )
        assert save.status_code == 201, save.text
        res = c.post(
            f"/api/screen/custom?as_of={_AS_OF_STR}", json=_custom_body(),
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["result_codes"] == [_SAMSUNG_CODE]


# =============================================================================
# 6. 기존 익명 /api/screen 무변경 (함정 D) + 빌트인 export byte 불변
# =============================================================================

def test_anonymous_screen_unchanged_when_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_SECRET 설정에서도 익명 /api/screen 은 무토큰 200 (ADR-0021 D4 함정 회피)."""
    monkeypatch.setenv("AUTH_SECRET", "test-secret-xyz-0123456789abcdef-0123")
    app = create_app(stocks_repository=FakeStocksMasterRepository(records=[_SAMSUNG]))
    body = {
        "conditions": [{"factor": "per:ttm-consolidated-ifrs", "op": "<", "value": "10"}],
        "selected_factors": ["per:ttm-consolidated-ifrs"],
    }
    with TestClient(app) as c:
        res = c.post(f"/api/screen?as_of={_AS_OF_STR}", json=body)
        assert res.status_code == 200


def test_builtin_run_export_has_no_pack_key(client: TestClient) -> None:
    """빌트인 run export 는 `pack` 키 미출현 — byte 불변 보존 (response_exclude_none)."""
    body = {
        "conditions": [{"factor": "per:ttm-consolidated-ifrs", "op": "<", "value": "10"}],
        "selected_factors": ["per:ttm-consolidated-ifrs"],
    }
    save_res = client.post(f"/api/runs?as_of={_AS_OF_STR}", json=body)
    run_id = save_res.json()["id"]
    export_json = client.get(f"/api/runs/{run_id}/export").json()
    # 빌트인 run 은 pack body 불요 → 키 자체가 직렬화 안 됨.
    assert "pack" not in export_json
    # 빌트인 run reproduce 도 정상 (회귀 0).
    reproduce_res = client.post("/api/runs/reproduce", json=export_json)
    assert reproduce_res.status_code == 200
    assert reproduce_res.json()["matches"] is True


def test_save_custom_run_forwards_macro_repo(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/runs/custom (save_custom_run) 이 macro_repo 를 forward (§2.10 대칭).

    save_custom_run 도 custom screen 과 동일하게 macro_repo 를 screen_active_codes
    에 배선해야 저장 custom run 이 사용자가 본 custom screen 과 macro 조건에서
    일치한다. screen_custom 모듈 namespace 의 screen_active_codes 를 spy 로 가로채
    macro_repo forward 를 검증한다 (save_run 테스트와 동형).
    """
    import app.api.routes.screen_custom as sc_module

    _save_custom_pack(client)  # custom pack 등록 (pack_slug 매칭 위해 선행).

    captured: dict[str, object] = {}

    from app.api.routes.screen import ScreenCodesResult

    def _spy(**kwargs: object) -> ScreenCodesResult:
        captured.update(kwargs)
        return ScreenCodesResult(result_codes=(), universe_size=0, na_excluded_count=0)

    monkeypatch.setattr(sc_module, "screen_active_codes", _spy)

    res = client.post(f"/api/runs/custom?as_of={_AS_OF_STR}", json=_custom_body())
    assert res.status_code == 201
    # save_custom_run 이 macro_repo 를 screen_active_codes 에 forward 했는지.
    assert "macro_repo" in captured
