"""Integration tests — 외부 출처 (pykrx / FDR / DART OpenAPI) 실 호출 (ADR-0003 D8).

본 디렉토리의 test 는 `@pytest.mark.integration` 강제 + 야간 CI (또는
수동 실행) 에서만 동작. server-ci.yml 의 default 는 `pytest -m "not
integration"` 이라 평소 PR/push 에는 영향 없음.

실행:
    cd server
    python -m pytest tests/integration -m integration

야간 CI = `.github/workflows/integration-nightly.yml` (KST 02:00 = UTC 17:00).
"""
