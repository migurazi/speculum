"""tools/check_forbidden_words.py CLI 스캐너 단위 테스트.

테스트 매트릭스:
    1. clean 파일만 → exit 0
    2. 금지 어휘 포함 파일 → exit 1 + 출력에 file:line:col:word
    3. exclude pattern 으로 dirty 파일 제외 → exit 0
    4. allowed_phrases 인식 ("이베스트투자증권" — 회사명) → 통과
    5. binary 파일 (utf-8 decode 실패) → skip
    6. 무지원 확장자 (.bin, .png) → skip
    7. 디렉토리 + 파일 list 혼합 인자
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# tools 디렉토리 위치 — server/tests 에서 ../../tools.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI_SCRIPT = _REPO_ROOT / "tools" / "check_forbidden_words.py"


def _run_cli(
    *args: str,
    tmp_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """CLI 실행 helper — stdout/stderr capture + exit code 반환."""
    cwd = tmp_path or _REPO_ROOT
    return subprocess.run(
        [sys.executable, str(_CLI_SCRIPT), *args, "--repo-root", str(cwd)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# =============================================================================
# 1. clean 파일만 → exit 0
# =============================================================================

def test_clean_directory_exits_zero(tmp_path: Path) -> None:
    """금지 어휘 없는 파일만 → exit 0."""
    (tmp_path / "clean.py").write_text(
        "def hello():\n    return 'world'\n",
        encoding="utf-8",
    )
    result = _run_cli(str(tmp_path), tmp_path=tmp_path)
    assert result.returncode == 0, (
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# =============================================================================
# 2. 금지 어휘 → exit 1 + 출력 형식
# =============================================================================

def test_dirty_file_exits_one_with_diagnostic(tmp_path: Path) -> None:
    """금지 어휘 포함 파일 → exit 1 + stderr 에 file:line:col:word."""
    (tmp_path / "dirty.md").write_text(
        "오늘의 추천 종목입니다.\n",
        encoding="utf-8",
    )
    result = _run_cli(str(tmp_path), tmp_path=tmp_path)
    assert result.returncode == 1
    assert "violation" in result.stderr
    # file:line:col 형식.
    assert "dirty.md:" in result.stderr
    assert "추천" in result.stderr


def test_dirty_file_english_word(tmp_path: Path) -> None:
    """영문 금지 어휘 — case insensitive word boundary."""
    (tmp_path / "dirty.md").write_text(
        "This is a strong buy recommendation.\n",
        encoding="utf-8",
    )
    result = _run_cli(str(tmp_path), tmp_path=tmp_path)
    assert result.returncode == 1
    # "Strong Buy" + "Recommendation" 두 위반.
    assert result.stderr.lower().count("dirty.md") >= 2


# =============================================================================
# 3. exclude pattern — dirty 파일 제외
# =============================================================================

def test_exclude_pattern_skips_dirty_file(tmp_path: Path) -> None:
    """--exclude 로 dirty 파일 제외 → exit 0."""
    (tmp_path / "clean.py").write_text(
        "# clean code\n",
        encoding="utf-8",
    )
    dirty_dir = tmp_path / "dirty_dir"
    dirty_dir.mkdir()
    (dirty_dir / "dirty.md").write_text("오늘의 추천 종목\n", encoding="utf-8")

    result = _run_cli(
        str(tmp_path),
        "--exclude", "**/dirty_dir/**",
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, (
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# =============================================================================
# 4. allowed_phrases — 회사명 통과
# =============================================================================

def test_allowed_phrases_pass_through(tmp_path: Path) -> None:
    """`이베스트` 같은 회사명 (allowed_phrases) 는 violation 없음.

    `이베스트` substring 의 `매` (없음) 또는 `투자증권` — 깨끗.
    `이베스트투자증권` 의 substring 안에 `이베스트` 단어가 `매수` 같은 금지
    어휘 substring 도 없으나, allowed_phrases 의 ("이베스트" 등) 이 명시.
    """
    (tmp_path / "company_name.json").write_text(
        '{"name": "이베스트투자증권"}\n',
        encoding="utf-8",
    )
    result = _run_cli(str(tmp_path), tmp_path=tmp_path)
    assert result.returncode == 0


def test_allowed_phrases_korean_관심종목(tmp_path: Path) -> None:
    """`관심 종목` (allowed) vs `관심 종목 픽` (forbidden 의 substring) 분리."""
    (tmp_path / "ui.md").write_text(
        "관심 종목 리스트 표시 — 사용자가 추가한 종목.\n",
        encoding="utf-8",
    )
    result = _run_cli(str(tmp_path), tmp_path=tmp_path)
    assert result.returncode == 0


# =============================================================================
# 5. binary 파일 — skip
# =============================================================================

def test_binary_file_skipped(tmp_path: Path) -> None:
    """binary file (UTF-8 decode 불가) 는 silent skip — exit 0 영향 X."""
    # `.json` 확장자지만 binary content (UTF-8 invalid).
    (tmp_path / "binary.json").write_bytes(b"\xff\xfe\x00\x00binary")
    (tmp_path / "clean.py").write_text("# clean\n", encoding="utf-8")
    result = _run_cli(str(tmp_path), tmp_path=tmp_path)
    assert result.returncode == 0


# =============================================================================
# 6. 무지원 확장자 — skip
# =============================================================================

def test_unsupported_extension_skipped(tmp_path: Path) -> None:
    """.bin / .png 등 무지원 확장자는 스캔 대상에 포함되지 않음."""
    # .bin 안에 금지 어휘 가 있어도 검사 X.
    (tmp_path / "image.bin").write_text("매수 추천\n", encoding="utf-8")
    result = _run_cli(str(tmp_path), tmp_path=tmp_path)
    assert result.returncode == 0


# =============================================================================
# 7. 디렉토리 + 파일 list 혼합
# =============================================================================

def test_mixed_directory_and_file_args(tmp_path: Path) -> None:
    """디렉토리 + 단일 파일 동시 인자."""
    (tmp_path / "clean.py").write_text("# clean\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "also_clean.md").write_text("관심 종목 표시\n", encoding="utf-8")

    result = _run_cli(
        str(tmp_path / "clean.py"),
        str(sub),
        tmp_path=tmp_path,
    )
    assert result.returncode == 0
