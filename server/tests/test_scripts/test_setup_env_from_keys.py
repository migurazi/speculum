"""scripts.setup_env_from_keys 단위 테스트 — keys.txt → server/.env 파싱.

순수 파싱 함수(_key_after/_looks_like_key/_is_korean/_mask) + main() 의 파일
I/O(tmp keys.txt → tmp .env). 네트워크/DB 무관 — 라벨 매칭 파싱의 회귀 가드.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import setup_env_from_keys as mod

# 실제 keys.txt 형식 모사 — 출처 URL 라벨 + 다음 줄에 키.
_SAMPLE_KEYS = (
    "opendart.fss.or.kr\n"
    "DART1234567890abcdef\n"
    "\n"
    "ecos.bok.or.kr\n"
    "ECOS12345678\n"
    "\n"
    "kosis.kr/openapi\n"
    "KOSIS1234567890ab\n"
    "\n"
    "data.go.kr\n"
    "주식배당정보 V2\n"
    "http://apis.data.go.kr/1160100/GetStocDiviInfoService_V2\n"
    "FSC1234567890abcdef1234\n"
)


# =============================================================================
# 순수 helper
# =============================================================================

def test_is_korean() -> None:
    assert mod._is_korean("주식배당") is True
    assert mod._is_korean("DART1234") is False
    assert mod._is_korean("http://x") is False


def test_looks_like_key() -> None:
    # 키 후보: 영숫자 8자+, URL/도메인/한글 아님.
    assert mod._looks_like_key("DART1234567890abcdef") is True
    assert mod._looks_like_key("http://apis.data.go.kr/x") is False  # URL.
    assert mod._looks_like_key("ecos.bok.or.kr") is False  # 도메인(.kr).
    assert mod._looks_like_key("주식배당정보") is False  # 한글.
    assert mod._looks_like_key("short") is False  # 8자 미만.
    assert mod._looks_like_key("") is False


def test_key_after() -> None:
    lines = [ln.strip() for ln in _SAMPLE_KEYS.splitlines()]
    assert mod._key_after(lines, "opendart") == "DART1234567890abcdef"
    assert mod._key_after(lines, "ecos.bok") == "ECOS12345678"
    assert mod._key_after(lines, "kosis") == "KOSIS1234567890ab"
    # data.go.kr 다음 줄은 한글/URL → skip 후 실제 키(FSC...).
    assert mod._key_after(lines, "data.go.kr") == "FSC1234567890abcdef1234"
    # 미존재 라벨.
    assert mod._key_after(lines, "nonexistent") == ""


def test_mask_no_value_leak() -> None:
    masked = mod._mask("DART1234567890abcdef")
    assert masked.startswith("DART")
    assert "len=20" in masked
    # 중간 값이 노출되지 않음(앞4·뒤2 만).
    assert "1234567890abcd" not in masked
    assert mod._mask("") == "(없음)"


# =============================================================================
# main() — tmp 파일 I/O
# =============================================================================

def test_main_writes_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """keys.txt → .env 4종 키 기록."""
    keys = tmp_path / "keys.txt"
    keys.write_text(_SAMPLE_KEYS, encoding="utf-8")
    env = tmp_path / ".env"
    monkeypatch.setattr(mod, "_KEYS_TXT", keys)
    monkeypatch.setattr(mod, "_ENV_PATH", env)

    assert mod.main() == 0
    content = env.read_text(encoding="utf-8")
    assert "DART_API_KEY=DART1234567890abcdef" in content
    assert "ECOS_API_KEY=ECOS12345678" in content
    assert "KOSIS_API_KEY=KOSIS1234567890ab" in content
    assert "FSC_API_KEY=FSC1234567890abcdef1234" in content


def test_main_missing_keys_txt_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """keys.txt 부재 → exit 1, .env 미생성."""
    monkeypatch.setattr(mod, "_KEYS_TXT", tmp_path / "no-such.txt")
    monkeypatch.setattr(mod, "_ENV_PATH", tmp_path / ".env")
    assert mod.main() == 1
    assert not (tmp_path / ".env").is_file()


def test_main_no_keys_found_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """라벨/키 없는 keys.txt → exit 1(전부 누락)."""
    keys = tmp_path / "keys.txt"
    keys.write_text("그냥 메모\nhttp://noidea\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_KEYS_TXT", keys)
    monkeypatch.setattr(mod, "_ENV_PATH", tmp_path / ".env")
    assert mod.main() == 1


def test_main_preserves_non_key_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기존 .env 의 비-API-KEY 라인(SPECULUM_DATABASE_URL 등)은 보존, 키만 갱신."""
    keys = tmp_path / "keys.txt"
    keys.write_text(_SAMPLE_KEYS, encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "SPECULUM_DATABASE_URL=sqlite:///./speculum_real.db\n"
        "DART_API_KEY=STALE_OLD_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_KEYS_TXT", keys)
    monkeypatch.setattr(mod, "_ENV_PATH", env)

    assert mod.main() == 0
    content = env.read_text(encoding="utf-8")
    # 비-키 라인 보존.
    assert "SPECULUM_DATABASE_URL=sqlite:///./speculum_real.db" in content
    # 키는 새 값으로 갱신(STALE 제거).
    assert "DART_API_KEY=DART1234567890abcdef" in content
    assert "STALE_OLD_KEY" not in content
