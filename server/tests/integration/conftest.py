"""Integration test 공통 fixture (ADR-0003 D8).

본 파일의 fixture 는 외부 출처 가용성을 검사 + skip 처리. CI 의 야간
runner 가 일시적으로 외부 endpoint 도달 불가 시 fail 대신 skip 으로
신호.

설계:
    - `network_available()` — 단순 DNS resolve 로 1차 검증.
    - `dart_api_key()` — env DART_API_KEY 있을 때만 dart test 활성.
    - 모든 integration test 가 본 conftest 의 자동 skip 통과 후만 실행.
"""

from __future__ import annotations

import importlib.util
import os
import socket

import pytest


def _can_resolve(host: str) -> bool:
    """DNS resolve 가능 여부 — 빠른 가용성 1차 검증.

    실 연결 (TCP handshake) 까지 검증하면 느림 + flaky. DNS 만 검사 후
    adapter 가 실 호출에서 retry/error 분기. 더 강한 검사는 야간 CI 가
    실 호출에서 fail 시 보고.
    """
    try:
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def network_available() -> bool:
    """일반적인 외부 인터넷 가용성 — Google DNS 로 확인."""
    return _can_resolve("dns.google")


def _package_installed(name: str) -> bool:
    """package import 가능 여부 — local dev 가 의존 미설치인 경우 skip."""
    return importlib.util.find_spec(name) is not None


def _krx_credentials_present() -> bool:
    """KRX 정보데이터시스템 자격증명(KRX_ID/KRX_PW) 환경변수 존재 여부.

    최신 KRX 정보데이터시스템은 일부 데이터(OHLCV 거래대금 등)에 로그인을 요구한다.
    pykrx 는 자격증명이 없으면 로그인 실패 후 **불완전 DataFrame** 을 반환하고,
    adapter 가 거기서 '거래대금' 등 컬럼에 접근하다 `KeyError` 를 던진다 — 이는
    AdapterError 로 변환되지 않아 test 의 `except AdapterError` skip 가드를
    우회하여 fail 로 폭증한다. 자격증명을 가용성 조건에 포함해 미설정 시 사전
    skip 한다(dart_api_key 패턴과 동일 — secret 미주입 local/CI 환경에서 안전).
    """
    return bool(
        os.environ.get("KRX_ID", "").strip()
        and os.environ.get("KRX_PW", "").strip()
    )


@pytest.fixture(scope="session")
def pykrx_endpoint_available() -> bool:
    """pykrx 사용 가능 여부 — 패키지 설치 + KRX host 도달 + KRX 자격증명.

    pykrx 는 내부적으로 KRX 정보데이터시스템 + Naver finance crawling. KRX 가
    일부 데이터에 로그인을 요구하므로 KRX_ID/KRX_PW 미설정 시 비가용으로 간주해
    skip(상세: `_krx_credentials_present`). nightly secret 주입 환경에서만 실행.
    """
    return (
        _package_installed("pykrx")
        and _can_resolve("data.krx.co.kr")
        and _krx_credentials_present()
    )


@pytest.fixture(scope="session")
def fdr_endpoint_available() -> bool:
    """FDR 사용 가능 여부 — 패키지 설치 + Yahoo host 도달."""
    return (
        _package_installed("FinanceDataReader")
        and _can_resolve("finance.yahoo.com")
    )


@pytest.fixture(scope="session")
def dart_api_key() -> str | None:
    """DART OpenAPI key — 환경변수에서 fetch. 없으면 None.

    secret 누락 시 dart test 가 skip — CI runner 가 fork PR 등 secret 미주입
    환경에서도 안전.
    """
    key = os.environ.get("DART_API_KEY", "").strip()
    return key if key else None


@pytest.fixture(autouse=True)
def _skip_if_network_unavailable(request: pytest.FixtureRequest) -> None:
    """integration marker 가 있는 test 가 network 가용성 미충족 시 skip.

    `pykrx_endpoint_available` / `fdr_endpoint_available` 같은 fixture 를
    명시 요청하는 test 는 본 fixture 가 자동 적용되어 적절한 skip 신호.
    Custom fixture 가 truthy 인지 검사. False 면 skip.

    autouse scope = 본 conftest 위치 (tests/integration/) 하위 test 만. 상위
    tests/ 의 unit test 는 별도 conftest 가 없으면 본 fixture 미적용.
    """
    # session-scoped fixture 의 직접 검사는 request.getfixturevalue.
    for name in (
        "pykrx_endpoint_available",
        "fdr_endpoint_available",
    ):
        if name in request.fixturenames:
            available = request.getfixturevalue(name)
            if not available:
                pytest.skip(f"{name} = False — network 비가용 또는 host 도달 불가")
    # DART key 는 test 가 직접 fixture 요청 + None 검사 (yield 패턴 회피).
