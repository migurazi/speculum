"""AsOfPolicy FastAPI dependency 단위 테스트.

테스트 매트릭스:
1. as_of 명시 영업일 → 그대로 + X-AsOf 헤더
2. as_of 휴장일 → snap + X-AsOf-Snapped + X-AsOf-Original
3. as_of 누락 → default fill + X-AsOf-Defaulted (calendar 범위 내에서만)
4. as_of 미래 → 400 AS_OF_IN_FUTURE
5. as_of 범위 밖 → 400 AS_OF_OUT_OF_RANGE
6. as_of 형식 위반 → 422 INVALID_REQUEST (input 미echo)
7. as_of 응답 body — value/was_defaulted/was_snapped/original_input/pit_policy_version
8. CurrentUser dependency — SYSTEM_USER_ID 자동 + env override fresh read
9. Endpoint 가 NormalizedAsOfDep 사용 시 headers 적용
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.as_of import (
    BrowseAsOfDep,
    NormalizedAsOfDep,
    get_normalized_as_of,
)
from app.api.dependencies.auth import CurrentUserDep, UserContext, get_current_user
from app.api.exception_handlers import register_exception_handlers

# =============================================================================
# Helpers — per-test app instance (oracle R4 — dependency_overrides leakage 회피)
# =============================================================================

def _make_app() -> FastAPI:
    """Per-test FastAPI app — exception handlers 만 등록한 최소 app."""
    app = FastAPI()
    register_exception_handlers(app)

    # Echo endpoint — dependency 의 정상 동작 검증.
    @app.get("/echo/as_of")
    async def echo_as_of(as_of: NormalizedAsOfDep) -> dict:
        return {
            "value": as_of.value.isoformat(),
            "was_defaulted": as_of.was_defaulted,
            "was_snapped": as_of.was_snapped,
            "original_input": (
                as_of.original_input.isoformat()
                if as_of.original_input is not None else None
            ),
            "pit_policy_version": as_of.pit_policy_version,
        }

    @app.get("/echo/user")
    async def echo_user(user: CurrentUserDep) -> dict:
        return {"user_id": str(user.user_id), "is_system": user.is_system}

    # browse(읽기) 경로 echo — verified 범위 밖 degrade 검증 (ROADMAP_v2 §2.3).
    @app.get("/echo/browse")
    async def echo_browse(as_of: BrowseAsOfDep) -> dict:
        return {
            "value": as_of.value.isoformat(),
            "was_degraded": as_of.was_degraded,
            "was_defaulted": as_of.was_defaulted,
        }

    return app


# =============================================================================
# 10. browse degrade — verified 범위 밖이 400 아닌 200 + X-AsOf-Degraded
# =============================================================================

def test_browse_out_of_range_degrades_to_200(client: TestClient) -> None:
    """browse: 2024 캘린더 밖 과거일(2025-01-02) → 400 아닌 200 + degrade 헤더.

    원래 strict(NormalizedAsOfDep)였다면 AS_OF_OUT_OF_RANGE 400. browse 는 가용성
    우선으로 weekday 근사 degrade(ROADMAP_v2 §2.3). 2025-01-02 은 목요일(평일)이라
    근사값=그대로. 시간 안정성: 2024 범위 밖이며 확실한 과거.
    """
    # 캘린더가 2024-01-01 ~ 2026-06-25(V1a B2)이므로 범위 밖은 pre-min(2023) 사용.
    # 2023-06-15(목)은 verified 범위 밖이며 과거(미래 거부 회피).
    res = client.get("/echo/browse?as_of=2023-06-15")
    assert res.status_code == 200
    body = res.json()
    assert body["value"] == "2023-06-15"
    assert body["was_degraded"] is True
    assert res.headers["x-asof-degraded"] == "true"
    assert res.headers["x-asof"] == "2023-06-15"


def test_browse_in_range_no_degrade_header(client: TestClient) -> None:
    """browse: 범위 내(2024) 정상 입력은 degrade 헤더 없음(strict 와 동일 정확)."""
    res = client.get("/echo/browse?as_of=2024-05-07")
    assert res.status_code == 200
    assert res.json()["was_degraded"] is False
    assert "x-asof-degraded" not in res.headers


def test_browse_future_still_400(client: TestClient) -> None:
    """browse 도 미래는 400 — degrade 는 가용성이지 미래 허용 아님."""
    res = client.get("/echo/browse?as_of=2999-12-31")
    assert res.status_code == 400
    assert res.json()["code"] == "AS_OF_IN_FUTURE"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = _make_app()
    with TestClient(app) as c:
        yield c


# =============================================================================
# 1. as_of 명시 영업일 → 그대로
# =============================================================================

def test_as_of_business_day_passthrough(client: TestClient) -> None:
    res = client.get("/echo/as_of?as_of=2024-05-07")  # 화 영업일
    assert res.status_code == 200
    body = res.json()
    assert body["value"] == "2024-05-07"
    assert body["was_defaulted"] is False
    assert body["was_snapped"] is False
    assert body["original_input"] == "2024-05-07"
    # 헤더 — X-AsOf 항상, snap/default 헤더 미적용
    assert res.headers["x-asof"] == "2024-05-07"
    assert "x-asof-defaulted" not in res.headers
    assert "x-asof-snapped" not in res.headers


# =============================================================================
# 2. as_of 휴장일 → snap
# =============================================================================

def test_as_of_holiday_snap(client: TestClient) -> None:
    """근로자의날 (2024-05-01 수) → 직전 영업일 4-30 (화)."""
    res = client.get("/echo/as_of?as_of=2024-05-01")
    assert res.status_code == 200
    body = res.json()
    assert body["value"] == "2024-04-30"
    assert body["was_snapped"] is True
    assert body["original_input"] == "2024-05-01"
    assert res.headers["x-asof"] == "2024-04-30"
    assert res.headers["x-asof-snapped"] == "true"
    assert res.headers["x-asof-original"] == "2024-05-01"


def test_as_of_weekend_snap(client: TestClient) -> None:
    """일요일 → 직전 금요일."""
    res = client.get("/echo/as_of?as_of=2024-05-05")  # 일
    assert res.status_code == 200
    body = res.json()
    assert body["value"] == "2024-05-03"  # 금
    assert body["was_snapped"] is True


# =============================================================================
# 3. as_of 누락 + 범위 내 → default fill
# =============================================================================

def test_as_of_missing_within_range_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """default fill 검증 — kst_today 가 calendar 범위 내인 케이스 (직접 검증 어려움 —
    AsOfPolicy._normalize_with_today 우회 imitation 으로 대체).
    """
    # AsOfPolicy.normalize 가 kst_today 사용 — calendar 범위 (2024) 밖이라 default
    # path 직접 호출 시 AS_OF_OUT_OF_RANGE. 본 test 는 정상 범위에서의 default fill
    # 시나리오 검증을 위해 dependency override.
    from app.services.as_of_policy import AsOfPolicy

    app = _make_app()

    def _override_default():
        # today=2024-05-10 (calendar 내) 으로 normalize.
        # 본 override 는 endpoint 가 직접 호출하므로 response 주입 불가 — 단순화로
        # AsOfPolicy._normalize_with_today 만 사용해 결과만 확인.
        return AsOfPolicy._normalize_with_today(None, today=date(2024, 5, 10))

    app.dependency_overrides[get_normalized_as_of] = _override_default

    with TestClient(app) as c:
        res = c.get("/echo/as_of")
        assert res.status_code == 200
        body = res.json()
        assert body["was_defaulted"] is True
        # default = 5/10 (금) 영업일
        assert body["value"] == "2024-05-10"


# =============================================================================
# 4. as_of 미래 → 400
# =============================================================================

def test_as_of_in_future_returns_400(client: TestClient) -> None:
    res = client.get("/echo/as_of?as_of=2999-12-31")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "AS_OF_IN_FUTURE"
    assert body["requested"] == "2999-12-31"
    # detail 메시지에 안전 vocabulary 만 (금지 어휘 echo X).
    assert "추천" not in body["detail"]


# =============================================================================
# 5. as_of 범위 밖 → 400
# =============================================================================

def test_as_of_out_of_calendar_range_returns_400(client: TestClient) -> None:
    res = client.get("/echo/as_of?as_of=2023-06-01")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "AS_OF_OUT_OF_RANGE"
    assert body["requested"] == "2023-06-01"
    assert body["min_date"] == "2024-01-01"
    # V1a B2 — 캘린더는 build_krx_calendar 재실행마다 max_date 가 확장되므로
    # 프로즌 리터럴 대신 라이브 DEFAULT_CALENDAR 와 비교(재확장 견고).
    from app.services.krx_calendar import DEFAULT_CALENDAR
    assert body["max_date"] == DEFAULT_CALENDAR.max_date.isoformat()


# =============================================================================
# 6. as_of 형식 위반 → 422 + input sanitize (oracle R1)
# =============================================================================

def test_as_of_invalid_format_returns_422_without_echo(client: TestClient) -> None:
    """`as_of=추천종목` 같은 dirty input — 응답 body 가 input 을 echo 하지 X."""
    res = client.get("/echo/as_of?as_of=NOT-A-DATE")
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "INVALID_REQUEST"
    # detail 은 generic, errors[*].msg 에도 input_value echo 없음.
    assert "NOT-A-DATE" not in str(body)


def test_as_of_dirty_input_does_not_echo_forbidden_word(client: TestClient) -> None:
    """ADR-0007 의 금지 어휘가 query 에 들어와도 응답 body 에 echo X."""
    res = client.get("/echo/as_of?as_of=%EC%B6%94%EC%B2%9C")  # URL-encoded "추천"
    assert res.status_code == 422
    body_str = str(res.json())
    # 응답에 "추천" 단어가 보이지 X — sanitize 동작.
    assert "추천" not in body_str


# =============================================================================
# 7. CurrentUser dependency
# =============================================================================

def test_current_user_default_is_system(client: TestClient) -> None:
    res = client.get("/echo/user")
    assert res.status_code == 200
    body = res.json()
    assert body["is_system"] is True
    # default UUID
    assert body["user_id"] == "00000000-0000-0000-0000-000000000001"


def test_current_user_env_override_lazy_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """oracle R3 — dependency 가 매 호출마다 env 재평가."""
    monkeypatch.setenv("SPECULUM_USER_ID", "abcdef00-0000-0000-0000-000000000099")
    app = _make_app()
    with TestClient(app) as c:
        res = c.get("/echo/user")
        body = res.json()
        assert body["user_id"] == "abcdef00-0000-0000-0000-000000000099"


# =============================================================================
# 8. UserContext 자체
# =============================================================================

def test_user_context_is_frozen() -> None:
    import dataclasses
    ctx = UserContext(user_id=UUID("00000000-0000-0000-0000-000000000001"),
                       is_system=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.user_id = UUID("00000000-0000-0000-0000-000000000002")  # type: ignore[misc]


def test_get_current_user_returns_user_context() -> None:
    ctx = get_current_user()
    assert isinstance(ctx, UserContext)
    assert ctx.is_system is True


# =============================================================================
# 9. dependency_overrides cleanup (oracle R4)
# =============================================================================

def test_default_fill_when_today_is_holiday_combines_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """oracle 2 차 NEW-C1 — `was_defaulted=True` + `was_snapped=True` + `original_input=None` 조합.

    today 자체가 휴장일이라 default fill 결과가 직전 영업일로 snap 되는 경우.
    클라이언트가 X-AsOf-Original 부재를 처리할 수 있어야 함 (X-AsOf-Snapped: true
    인데 original 헤더 없음).
    """
    from app.services.as_of_policy import AsOfPolicy

    app = _make_app()

    def _override_default_today_holiday():
        # today=2024-05-01 (수, 근로자의날 휴장). default fill → 4-30 (화) snap.
        return AsOfPolicy._normalize_with_today(None, today=date(2024, 5, 1))

    app.dependency_overrides[get_normalized_as_of] = _override_default_today_holiday

    with TestClient(app) as c:
        res = c.get("/echo/as_of")
        body = res.json()
        # default fill + snap 둘 다 True, original_input None.
        assert body["was_defaulted"] is True
        assert body["was_snapped"] is True
        assert body["original_input"] is None
        assert body["value"] == "2024-04-30"
        # 단, 본 override 는 직접 결과 반환이므로 dependency 의 header 주입
        # 코드 경로가 실행되지 않음 — 헤더 검증은 별도 (dependency 직접 호출).


def test_get_normalized_as_of_function_with_today_holiday_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dependency 본체 함수 호출 — today 가 휴장일인 default fill 시 헤더 동작.

    AsOfPolicy.normalize 의 today 를 monkeypatch (kst_today) 하기보다 직접 함수
    호출. Response mock 으로 header 누락/포함 검증.
    """
    from unittest.mock import MagicMock

    from app.services._jcs import HASH_PREFIX  # noqa: F401 (참조용)
    from app.services.as_of_policy import AsOfPolicy, NormalizedAsOf

    # Default fill 의 simulated result — was_defaulted + was_snapped + original=None
    simulated = NormalizedAsOf(
        value=date(2024, 4, 30),
        was_defaulted=True,
        was_snapped=True,
        original_input=None,
        pit_policy_version="1.0",
    )

    # AsOfPolicy.normalize 를 monkeypatch 하여 simulated 반환.
    monkeypatch.setattr(AsOfPolicy, "normalize", staticmethod(lambda v: simulated))

    response_mock = MagicMock()
    response_mock.headers = {}
    result = get_normalized_as_of(response_mock, as_of=None)

    assert result == simulated
    # 헤더 동작 — X-AsOf-Defaulted + X-AsOf-Snapped 둘 다 set,
    # X-AsOf-Original 은 original_input=None 이라 미적용.
    assert response_mock.headers["X-AsOf"] == "2024-04-30"
    assert response_mock.headers["X-AsOf-Defaulted"] == "true"
    assert response_mock.headers["X-AsOf-Snapped"] == "true"
    assert "X-AsOf-Original" not in response_mock.headers


def test_dependency_overrides_isolated_per_test() -> None:
    """per-test app instance 패턴 — override leakage 차단."""
    app1 = _make_app()
    app1.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id=UUID("99999999-9999-9999-9999-999999999999"), is_system=False,
    )
    with TestClient(app1) as c:
        res = c.get("/echo/user")
        assert res.json()["user_id"] == "99999999-9999-9999-9999-999999999999"
        assert res.json()["is_system"] is False

    # 별도 app — override 영향 없음.
    app2 = _make_app()
    with TestClient(app2) as c:
        res = c.get("/echo/user")
        assert res.json()["is_system"] is True
