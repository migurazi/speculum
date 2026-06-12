"""CorpCodeBootstrap 실 호출 smoke (ADR-0003 D2).

DART corpCode.xml endpoint(전체 상장사 ZIP)를 실제 fetch + 파싱하여 매핑을
구성한다. CI secret(DART_API_KEY) 미주입 환경에서는 skip. 본 smoke 의 목적은
**corpCode.xml 실 schema 회귀 방어** — mock 단위 테스트(test_batch/
test_corp_code_bootstrap.py)가 잡지 못하는 실 ZIP/XML 형식 변화(멤버명·`<list>`
구조·stock_code 필드)를 nightly 가 포착하고, 동시에 삼성전자 종목코드 →
corp_code 매핑의 실제 정확성을 검증한다.

테스트 매트릭스 (smoke):
    1. CorpCodeBootstrap.load — 전체 상장사 매핑 + 삼성전자(005930→00126380) 검증.

DART rate limit (20,000/일) 보호 — corpCode fetch 1 회. 캐시는 tmp_path 로
격리(테스트 간 오염 방지, 실제 운영 캐시 경로 미사용).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.base import AdapterError
from batch.corp_code_bootstrap import CorpCodeBootstrap

pytestmark = pytest.mark.integration


# 삼성전자 — KRX 종목코드 → DART 회사코드. corpCode.xml 매핑의 정확성 기준점.
# (test_dart_real.py 의 _SAMSUNG_CORP_CODE 와 동일 — 두 smoke 의 cross-check.)
_SAMSUNG_STOCK_CODE = "005930"
_SAMSUNG_CORP_CODE = "00126380"


def test_corpcode_bootstrap_smoke(
    dart_api_key: str | None,
    tmp_path: Path,
) -> None:
    """전체 corpCode.xml fetch → 매핑 구성 + 삼성전자 매핑 정확성."""
    if dart_api_key is None:
        pytest.skip("DART_API_KEY env 미설정 — secret 미주입 환경")

    bootstrap = CorpCodeBootstrap(
        api_key=dart_api_key,
        cache_path=tmp_path / "corpcode.zip",  # 테스트 격리 캐시.
    )
    try:
        mapping = bootstrap.load()
    except AdapterError as exc:
        # ZIP/네트워크 일시 장애는 fail 대신 skip (다른 real smoke 와 동일 가드).
        pytest.skip(f"DART corpCode 비가용: {exc}")
    finally:
        bootstrap.close()

    # 전체 상장사 — KOSPI+KOSDAQ+KONEX 합쳐 ~2,500 종목. 2000 하한은 파싱
    # 절반 손실 같은 부분 회귀까지 포착 (1000 은 너무 느슨 — 실제의 40%).
    assert mapping.size > 2000

    # 삼성전자 매핑 정확성 — 양방향 lookup.
    assert mapping.to_corp_code(_SAMSUNG_STOCK_CODE) == _SAMSUNG_CORP_CODE
    assert mapping.to_stock_code(_SAMSUNG_CORP_CODE) == _SAMSUNG_STOCK_CODE

    # 캐시 파일이 기록됨 — 두 번째 load 는 fetch 없이 캐시 사용 (디스크 캐시 회귀).
    assert (tmp_path / "corpcode.zip").is_file()
