"""GET /api/factor-packs/reference — speculum reference pack 목록 (M2.1, ADR-0023 D8).

reference pack 은 빌트인(active universe pack)이 아니라 Factor Lab 에서 불러와 쓰는
참조 정의(리츠 ffo-multiple 등). 익명 공개(fact tier — 로그인 무관). 빌트인
content_hash 무관(재현성 무영향).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

_REIT_SLUG = "community/speculum-reit-reference"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_reference_packs_includes_reit(client: TestClient) -> None:
    """reference 목록에 리츠 reference pack 포함."""
    res = client.get("/api/factor-packs/reference")
    assert res.status_code == 200
    packs = res.json()["packs"]
    slugs = {p["pack_slug"] for p in packs}
    assert _REIT_SLUG in slugs


def test_reference_pack_has_reit_factors(client: TestClient) -> None:
    """리츠 reference pack body 에 ffo-multiple:reit / dividend-yield:reit factor."""
    res = client.get("/api/factor-packs/reference")
    reit = next(p for p in res.json()["packs"] if p["pack_slug"] == _REIT_SLUG)
    cids = {f["canonical_id"] for f in reit["body"]["factors"]}
    assert "ffo-multiple:reit" in cids
    assert "dividend-yield:reit" in cids
    # factor_count 가 body factors 와 일치.
    assert reit["factor_count"] == len(reit["body"]["factors"])
    # content_hash 봉인된 body(load_pack validate_hash 통과 — 변조 없음).
    assert reit["body"]["content_hash"].startswith("sha256:")


def test_reference_pack_anonymous_access(client: TestClient) -> None:
    """익명(무토큰) 접근 가능 — 참조 정의는 fact tier (CurrentUserDep 미사용).

    ADR-0021 D4 미인증 fact 익명 허용 정합 — AUTH_SECRET 설정 운영에서도 무토큰 200.
    """
    res = client.get("/api/factor-packs/reference")
    assert res.status_code == 200
    assert isinstance(res.json()["packs"], list)
