"""save_run (POST /api/runs) ↔ macro_repo 배선 회귀 (§2.10 대칭).

execute_screen (POST /api/screen) 이 macro_repo 를 screen_active_codes 에
배선하므로, save_run 도 동일 배선해야 저장 result_codes 가 사용자가 본 스크린과
일치한다(macro 조건에서 저장 run 이 silent N/A 로 어긋나는 §2.10 break 방지).
본 테스트는 save_run 이 주입받은 macro_repo 를 screen_active_codes 에 forward
하는지 spy 로 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.api.routes.runs as runs_module
from app.main import create_app
from app.repositories.fakes import FakeMacroIndicatorRepository


@pytest.fixture
def _macro() -> FakeMacroIndicatorRepository:
    return FakeMacroIndicatorRepository(records=[])


@pytest.fixture
def _client(_macro: FakeMacroIndicatorRepository) -> Iterator[TestClient]:
    """macro_indicator_repository 를 명시 주입한 앱."""
    app = create_app(macro_indicator_repository=_macro)
    with TestClient(app) as c:
        yield c


def test_save_run_forwards_macro_repo(
    monkeypatch: pytest.MonkeyPatch,
    _client: TestClient,
    _macro: FakeMacroIndicatorRepository,
) -> None:
    """POST /api/runs 가 주입 macro_repo 를 screen_active_codes 에 forward."""
    captured: dict[str, object] = {}

    def _spy(**kwargs: object) -> tuple[str, ...]:
        captured.update(kwargs)
        return ()  # 빈 result_codes — snapshot 저장은 정상 진행.

    # runs 모듈 namespace 의 screen_active_codes 를 가로챔 (모듈 상단 import).
    monkeypatch.setattr(runs_module, "screen_active_codes", _spy)

    body = {
        "conditions": [
            {"factor": "eps:basic-ttm-consolidated-ifrs", "op": ">", "value": "1"},
        ],
        "selected_factors": ["eps:basic-ttm-consolidated-ifrs"],
    }
    res = _client.post("/api/runs?as_of=2024-05-07", json=body)

    assert res.status_code == 201
    # save_run 이 주입받은 그 macro_repo 를 screen_active_codes 에 그대로 전달.
    assert captured.get("macro_repo") is _macro
