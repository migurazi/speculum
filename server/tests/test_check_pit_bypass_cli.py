"""tools/check_pit_bypass.py CLI 가드 단위 테스트.

테스트 매트릭스:
    1. clean — fetch_X 가 as_of 인자 보유 → exit 0
    2. clean — fetch_X 가 keyword-only as_of → exit 0
    3. clean — fetch_X 가 없음 → exit 0
    4. clean — 명시적 `# pit-exempt:` 주석 → exit 0
    5. violation — fetch_X 가 as_of 없음 + exempt 없음 → exit 1
    6. violation — class 안 method 의 violation 도 검출
    7. save_X / list_X / delete_X 는 검사 제외
    8. find_X 도 query prefix 로 검사
    9. async def fetch_X 도 검사
   10. 디렉토리 누락 → exit 2
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI_SCRIPT = _REPO_ROOT / "tools" / "check_pit_bypass.py"


def _run_cli(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_CLI_SCRIPT),
            "--repositories-dir",
            str(repo_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _make_target_file(root: Path, name: str, content: str) -> Path:
    """검사 대상 파일 (target_files 안의 이름) 생성."""
    full = root / name
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


# =============================================================================
# Clean
# =============================================================================

def test_fetch_with_as_of_passes(tmp_path: Path) -> None:
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        (
            "from datetime import date\n"
            "class Repo:\n"
            "    def fetch_prices(self, code: str, *, as_of: date):\n"
            "        return []\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_no_query_methods_passes(tmp_path: Path) -> None:
    """fetch_*/find_*/query_* 가 없으면 검사 통과."""
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        "x = 1\n",
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_pit_exempt_marker_passes(tmp_path: Path) -> None:
    """fetch_X 의 as_of 없어도 명시적 # pit-exempt: 주석 있으면 통과."""
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        (
            "class Repo:\n"
            "    # pit-exempt: id-based immutable lookup\n"
            "    def fetch_by_id(self, id: str):\n"
            "        return None\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_save_list_delete_not_checked(tmp_path: Path) -> None:
    """save_/list_/delete_ prefix 는 query 아님 → 검사 제외."""
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        (
            "class Repo:\n"
            "    def save_prices(self, records): pass\n"
            "    def list_codes(self): return []\n"
            "    def delete_old(self): pass\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


# =============================================================================
# Violation
# =============================================================================

def test_fetch_without_as_of_fails(tmp_path: Path) -> None:
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        (
            "class Repo:\n"
            "    def fetch_prices(self, code: str):\n"
            "        return []\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "Repo.fetch_prices" in result.stderr
    assert "ADR-0008 D5" in result.stderr


def test_find_prefix_also_checked(tmp_path: Path) -> None:
    """find_* 도 query 의미 → 검사."""
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        (
            "class Repo:\n"
            "    def find_active(self, code: str):\n"
            "        return None\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "find_active" in result.stderr


def test_query_prefix_also_checked(tmp_path: Path) -> None:
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        (
            "def query_history():\n"
            "    return []\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "query_history" in result.stderr


def test_async_def_also_checked(tmp_path: Path) -> None:
    """async def fetch_X 도 검사."""
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        (
            "class Repo:\n"
            "    async def fetch_async(self, code: str):\n"
            "        return None\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "fetch_async" in result.stderr


def test_multiple_files_violations_collected(tmp_path: Path) -> None:
    """여러 target file 의 violation 모두 수집."""
    _make_target_file(
        tmp_path,
        "sql_repositories.py",
        "def fetch_x(): return []\n",
    )
    _make_target_file(
        tmp_path,
        "fakes.py",
        "def fetch_y(): return []\n",
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "fetch_x" in result.stderr
    assert "fetch_y" in result.stderr


# =============================================================================
# Misuse
# =============================================================================

def test_missing_repo_dir_returns_two(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    result = _run_cli(missing)
    assert result.returncode == 2
    assert "디렉토리 누락" in result.stderr
