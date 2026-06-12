"""CorpCodeBootstrap 단위 테스트 — XML 파싱 / ZIP 추출 / 캐시 / fetch mock.

테스트 매트릭스:
    파싱 (parse_corpcode_xml):
        1. 정상 상장사 매핑 + 비상장 skip.
        2. format 위반 (이상 stock_code/corp_code) skip + 통계.
        3. 중복 stock_code skip (첫 항목 우선).
        4. 중복 corp_code skip (1:1 invariant 보호).
    ZIP (extract_corpcode_xml):
        5. 정상 ZIP → CORPCODE.xml bytes.
        6. ZIP 아님 (에러 응답) → AdapterError.
        7. XML 멤버 부재 → AdapterError.
    bootstrap (CorpCodeBootstrap.load):
        8. 캐시 미스 → fetch → CorpCodeMapping (양방향 lookup).
        9. 캐시 히트 (fresh) → fetch 호출 0.
        10. 캐시 만료 → 재fetch.
        11. force_refresh → 캐시 무시 재fetch.
        12. api_key 미설정 (캐시 미스) → AdapterError.
        13. HTTP 4xx → AdapterError.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from app.adapters.base import AdapterError
from app.services.corp_code_mapping import CorpCodeMapping
from batch.corp_code_bootstrap import (
    CorpCodeBootstrap,
    extract_corpcode_xml,
    parse_corpcode_xml,
)

# =============================================================================
# XML / ZIP 빌드 helper
# =============================================================================

def _build_corpcode_xml(
    entries: list[tuple[str, str, str]],
) -> bytes:
    """(corp_code, corp_name, stock_code) tuple list → CORPCODE.xml bytes."""
    parts = ["<result>"]
    for corp_code, corp_name, stock_code in entries:
        parts.append(
            "<list>"
            f"<corp_code>{corp_code}</corp_code>"
            f"<corp_name>{corp_name}</corp_name>"
            f"<stock_code>{stock_code}</stock_code>"
            "<modify_date>20240101</modify_date>"
            "</list>"
        )
    parts.append("</result>")
    return "".join(parts).encode("utf-8")


def _build_corpcode_zip(
    entries: list[tuple[str, str, str]],
    *,
    member_name: str = "CORPCODE.xml",
) -> bytes:
    """entries → CORPCODE.xml 을 담은 ZIP bytes."""
    xml_bytes = _build_corpcode_xml(entries)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, xml_bytes)
    return buf.getvalue()


# =============================================================================
# 1~4. parse_corpcode_xml
# =============================================================================

def test_parse_listed_and_unlisted() -> None:
    """상장사 매핑 + 비상장 (빈 stock_code) skip."""
    xml = _build_corpcode_xml([
        ("00126380", "삼성전자", "005930"),
        ("00164779", "SK하이닉스", "000660"),
        ("00999999", "비상장회사", ""),        # 비상장 — skip.
        ("00888888", "공백종목", " "),          # 공백 → strip 후 빈값, skip.
    ])
    result = parse_corpcode_xml(xml)

    assert result.stock_to_corp == {
        "005930": "00126380",
        "000660": "00164779",
    }
    assert result.total_entries == 4
    assert result.listed_count == 2
    assert result.skipped_unlisted == 2


def test_parse_invalid_format_skipped() -> None:
    """이상 format (stock_code 5자리, corp_code 비숫자) skip + 통계."""
    xml = _build_corpcode_xml([
        ("00126380", "정상", "005930"),
        ("00164779", "짧은종목", "00066"),       # stock_code 5자리 — skip.
        ("ABCD1234", "비숫자corp", "035420"),    # corp_code 비숫자 — skip.
    ])
    result = parse_corpcode_xml(xml)

    assert result.stock_to_corp == {"005930": "00126380"}
    assert result.skipped_invalid_format == 2


def test_parse_duplicate_stock_code_first_wins() -> None:
    """같은 stock_code 두 번 등장 → 첫 항목 우선, 둘째 skip."""
    xml = _build_corpcode_xml([
        ("00126380", "삼성전자", "005930"),
        ("00999999", "중복종목", "005930"),    # 같은 stock_code — skip.
    ])
    result = parse_corpcode_xml(xml)

    assert result.stock_to_corp == {"005930": "00126380"}
    assert result.skipped_duplicate_stock == 1


def test_parse_duplicate_corp_code_skipped() -> None:
    """같은 corp_code 가 두 stock_code 에 → 1:1 보호 위해 둘째 skip."""
    xml = _build_corpcode_xml([
        ("00126380", "삼성전자", "005930"),
        ("00126380", "삼성전자우", "005935"),   # 같은 corp_code — skip.
    ])
    result = parse_corpcode_xml(xml)

    assert result.stock_to_corp == {"005930": "00126380"}
    assert result.skipped_duplicate_corp == 1
    # from_dict 가 raise 하지 않아야 함 (깨끗한 dict).
    mapping = CorpCodeMapping.from_dict(result.stock_to_corp)
    assert mapping.to_corp_code("005930") == "00126380"


def test_parse_audit_identity() -> None:
    """감사 항등식: listed_count = mapped + dup_stock + dup_corp."""
    xml = _build_corpcode_xml([
        ("00126380", "삼성전자", "005930"),
        ("00164779", "SK하이닉스", "000660"),
        ("00126380", "삼성전자우", "005935"),   # dup_corp.
        ("00999999", "중복종목", "005930"),     # dup_stock.
        ("00888888", "비상장", ""),             # unlisted (listed 제외).
    ])
    result = parse_corpcode_xml(xml)
    assert result.listed_count == (
        len(result.stock_to_corp)
        + result.skipped_duplicate_stock
        + result.skipped_duplicate_corp
    )


def test_parse_malformed_xml_raises() -> None:
    """손상 XML → AdapterError."""
    with pytest.raises(AdapterError, match="파싱 실패"):
        parse_corpcode_xml(b"<result><list><corp_code>00126380")


# =============================================================================
# 5~7. extract_corpcode_xml
# =============================================================================

def test_extract_normal_zip() -> None:
    """정상 ZIP → CORPCODE.xml bytes."""
    zip_bytes = _build_corpcode_zip([("00126380", "삼성전자", "005930")])
    xml = extract_corpcode_xml(zip_bytes)
    assert b"00126380" in xml
    assert b"005930" in xml


def test_extract_non_zip_raises() -> None:
    """ZIP 아님 (DART XML 에러 응답) → AdapterError."""
    error_body = b"<result><status>013</status><message>no data</message></result>"
    with pytest.raises(AdapterError, match="ZIP 이 아님"):
        extract_corpcode_xml(error_body)


def test_extract_zip_without_xml_member_raises() -> None:
    """ZIP 에 XML 멤버 없음 → AdapterError."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", b"not xml")
    with pytest.raises(AdapterError, match="XML 멤버 없음"):
        extract_corpcode_xml(buf.getvalue())


def test_extract_alternate_member_name() -> None:
    """멤버명이 CORPCODE.xml 이 아니어도 첫 .xml fallback."""
    zip_bytes = _build_corpcode_zip(
        [("00126380", "삼성전자", "005930")], member_name="corpcode_data.xml",
    )
    xml = extract_corpcode_xml(zip_bytes)
    assert b"005930" in xml


# =============================================================================
# 8~13. CorpCodeBootstrap.load
# =============================================================================

def _mock_client(
    zip_bytes: bytes,
    *,
    status_code: int = 200,
    call_counter: list[int] | None = None,
) -> httpx.Client:
    """corpCode endpoint 응답을 zip_bytes 로 고정하는 mock httpx.Client."""

    def handler(request: httpx.Request) -> httpx.Response:
        if call_counter is not None:
            call_counter.append(1)
        return httpx.Response(status_code, content=zip_bytes)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_load_cache_miss_fetches(tmp_path: Path) -> None:
    """캐시 미스 → fetch → CorpCodeMapping 양방향 lookup."""
    zip_bytes = _build_corpcode_zip([
        ("00126380", "삼성전자", "005930"),
        ("00164779", "SK하이닉스", "000660"),
    ])
    cache = tmp_path / "corpcode.zip"
    calls: list[int] = []

    bootstrap = CorpCodeBootstrap(
        api_key="test-key",
        http_client=_mock_client(zip_bytes, call_counter=calls),
        cache_path=cache,
    )
    mapping = bootstrap.load()

    assert mapping.size == 2
    assert mapping.to_corp_code("005930") == "00126380"
    assert mapping.to_stock_code("00164779") == "000660"
    assert len(calls) == 1
    assert cache.is_file()  # 캐시 기록됨.


def test_load_cache_hit_skips_fetch(tmp_path: Path) -> None:
    """신선한 캐시 존재 → fetch 호출 0."""
    zip_bytes = _build_corpcode_zip([("00126380", "삼성전자", "005930")])
    cache = tmp_path / "corpcode.zip"
    cache.write_bytes(zip_bytes)
    calls: list[int] = []

    bootstrap = CorpCodeBootstrap(
        api_key="test-key",
        http_client=_mock_client(zip_bytes, call_counter=calls),
        cache_path=cache,
        max_age_days=1.0,
    )
    mapping = bootstrap.load()

    assert mapping.size == 1
    assert len(calls) == 0  # 캐시 히트 — fetch 안 함.


def test_load_cache_expired_refetches(tmp_path: Path) -> None:
    """캐시 만료 (mtime 과거) → 재fetch."""
    zip_bytes = _build_corpcode_zip([("00126380", "삼성전자", "005930")])
    cache = tmp_path / "corpcode.zip"
    cache.write_bytes(zip_bytes)
    # mtime 을 2 일 전으로 — max_age_days=1 보다 오래됨.
    old = time.time() - 2 * 86400
    os.utime(cache, (old, old))
    calls: list[int] = []

    bootstrap = CorpCodeBootstrap(
        api_key="test-key",
        http_client=_mock_client(zip_bytes, call_counter=calls),
        cache_path=cache,
        max_age_days=1.0,
    )
    bootstrap.load()

    assert len(calls) == 1  # 만료 — 재fetch.


def test_load_force_refresh_ignores_cache(tmp_path: Path) -> None:
    """force_refresh=True → 신선한 캐시 있어도 재fetch."""
    zip_bytes = _build_corpcode_zip([("00126380", "삼성전자", "005930")])
    cache = tmp_path / "corpcode.zip"
    cache.write_bytes(zip_bytes)
    calls: list[int] = []

    bootstrap = CorpCodeBootstrap(
        api_key="test-key",
        http_client=_mock_client(zip_bytes, call_counter=calls),
        cache_path=cache,
    )
    bootstrap.load(force_refresh=True)

    assert len(calls) == 1


def test_load_no_api_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """api_key 미설정 + 캐시 미스 → AdapterError."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    zip_bytes = _build_corpcode_zip([("00126380", "삼성전자", "005930")])
    bootstrap = CorpCodeBootstrap(
        api_key=None,
        http_client=_mock_client(zip_bytes),
        cache_path=tmp_path / "corpcode.zip",
    )
    with pytest.raises(AdapterError, match="API key not configured"):
        bootstrap.load()


def test_load_http_4xx_raises(tmp_path: Path) -> None:
    """HTTP 4xx → AdapterError."""
    bootstrap = CorpCodeBootstrap(
        api_key="bad-key",
        http_client=_mock_client(b"unauthorized", status_code=401),
        cache_path=tmp_path / "corpcode.zip",
    )
    with pytest.raises(AdapterError, match="HTTP 401"):
        bootstrap.load()


def test_load_env_api_key_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """api_key 명시 없으면 DART_API_KEY env 사용."""
    monkeypatch.setenv("DART_API_KEY", "env-key")
    zip_bytes = _build_corpcode_zip([("00126380", "삼성전자", "005930")])
    bootstrap = CorpCodeBootstrap(
        api_key=None,
        http_client=_mock_client(zip_bytes),
        cache_path=tmp_path / "corpcode.zip",
    )
    mapping = bootstrap.load()
    assert mapping.size == 1
