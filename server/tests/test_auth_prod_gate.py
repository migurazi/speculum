"""운영(PROD) AUTH_SECRET 필수 startup 게이트 — M2.1 conformance 이슈 3.

AUTH_SECRET 미설정 = 인증 single-user fallback(SYSTEM_USER_ID). dev/test 는 의도
(회귀 0)이나 운영(PROD)에서는 전 user-scoped 요청이 익명 통과하는 silent degrade.
PROD + AUTH_SECRET 부재 시 startup fail 로 차단.
"""

from __future__ import annotations

import pytest

from app.core.config import assert_auth_config_for_environment
from app.main import create_app

_SECRET = "test-auth-secret-32bytes-minimum-xx"


def test_prod_without_auth_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROD + AUTH_SECRET 부재 → RuntimeError (silent degrade 차단)."""
    monkeypatch.setenv("SPECULUM_ENV", "prod")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        assert_auth_config_for_environment()


def test_prod_with_auth_secret_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROD + AUTH_SECRET 설정 → 통과."""
    monkeypatch.setenv("SPECULUM_ENV", "prod")
    monkeypatch.setenv("AUTH_SECRET", _SECRET)
    assert_auth_config_for_environment()  # raise 없음.


def test_dev_without_auth_secret_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """DEV 는 fallback 의도 — AUTH_SECRET 없어도 통과(회귀 0)."""
    monkeypatch.setenv("SPECULUM_ENV", "dev")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    assert_auth_config_for_environment()


def test_staging_without_auth_secret_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAGING 도 강제 대상 아님(인증 도입 전 staging 테스트 편의)."""
    monkeypatch.setenv("SPECULUM_ENV", "staging")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    assert_auth_config_for_environment()


def test_unset_env_defaults_dev_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPECULUM_ENV 미설정 → DEV → 통과(테스트 기본 무영향)."""
    monkeypatch.delenv("SPECULUM_ENV", raising=False)
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    assert_auth_config_for_environment()


def test_create_app_prod_without_auth_secret_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_app 부팅이 PROD + AUTH_SECRET 부재 시 fail (게이트 wiring 검증)."""
    monkeypatch.setenv("SPECULUM_ENV", "prod")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        create_app()


def test_create_app_prod_with_auth_secret_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_app 이 PROD + AUTH_SECRET 설정 시 정상 부팅."""
    monkeypatch.setenv("SPECULUM_ENV", "prod")
    monkeypatch.setenv("AUTH_SECRET", _SECRET)
    app = create_app()
    assert app is not None
