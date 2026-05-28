"""PIT bypass 가드 — historical query 메서드가 as_of 인자 통과 강제.

ADR-0008 D5 (PIT Enforcer) + ADR-0002 D3 (Source Citation) 의 file-system
level 강제. M0_PLAN T41 의 CI 5 게이트 중 PIT bypass 항목.

검사 의도:
    PIT-aware repository module 의 모든 query 메서드 (`fetch_*` /
    `find_*` / `query_*`) 가 `as_of: date` 매개변수 보유. 새 historical
    query 가 무심코 as_of 없이 추가되어 raw query path 가 생기는 silent
    regression 차단.

검사 대상 module (PIT-aware):
    - server/app/repositories/sql_repositories.py
    - server/app/repositories/stocks_master_repository.py
    - server/app/repositories/fakes.py
    - server/app/repositories/pit_protocols.py (Protocol 정의)

검사 제외 module (mutable user data 또는 immutable id-lookup):
    - citation_repository.py — citation 은 id-based immutable lookup
    - watchlist_repository.py — user data (PIT 무관)
    - screen_run_repository.py — append-only Run snapshot
    - sql_user_repositories.py — user data

검사 규칙:
    1. 검사 대상 module 에서 `def fetch_*|find_*|query_*` AST 추출.
    2. 함수의 parameter list 에 `as_of` 가 있거나, decorator/docstring 직전에
       `# pit-exempt: <reason>` 주석 있으면 통과.
    3. 그 외는 violation.

`save_*` / `delete_*` / `list_*` 는 mutation 또는 user-data 의미로 제외.

Exit code:
    0 — 통과
    1 — violation 발견 + diagnostic
    2 — 검사 module 누락

운영:
    python tools/check_pit_bypass.py
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_DEFAULT_REPO_DIR: Final[Path] = (
    _REPO_ROOT / "server" / "app" / "repositories"
)

# 검사 대상 파일 (PIT-aware).
_TARGET_FILES: Final[tuple[str, ...]] = (
    "sql_repositories.py",
    "stocks_master_repository.py",
    "fakes.py",
    "pit_protocols.py",
)

# query 의미를 갖는 method name prefix.
_QUERY_PREFIXES: Final[tuple[str, ...]] = ("fetch_", "find_", "query_")

# 통과 인정 — parameter 이름 또는 명시적 escape 주석 marker.
_PIT_PARAM_NAME: Final[str] = "as_of"
_EXEMPT_MARKER: Final[str] = "pit-exempt"


def _function_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """함수의 모든 parameter 이름 — args + kwonlyargs."""
    names: list[str] = []
    names.extend(a.arg for a in func.args.args)
    names.extend(a.arg for a in func.args.kwonlyargs)
    if func.args.vararg is not None:
        names.append(func.args.vararg.arg)
    if func.args.kwarg is not None:
        names.append(func.args.kwarg.arg)
    return names


def _has_exempt_marker(source_lines: list[str], func_lineno: int) -> bool:
    """함수 정의 직전 line 들에 `# pit-exempt:` 주석 있는지.

    decorator / docstring 가 아닌 def 라인 바로 위에서 검색 — 일관성.
    """
    # def line 은 1-indexed. 위로 2~5 line scan.
    start = max(0, func_lineno - 6)
    end = func_lineno - 1
    snippet = "\n".join(source_lines[start:end])
    return _EXEMPT_MARKER in snippet


def _collect_violations(
    source_path: Path,
) -> list[tuple[str, int, str]]:
    """파일 안 violation 목록 — (qualname, lineno, reason) 튜플."""
    source = source_path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(source_path))

    violations: list[tuple[str, int, str]] = []

    def _walk(
        node: ast.AST, qualname_prefix: str = "",
    ) -> None:
        for child in ast.iter_child_nodes(node):
            child_name = ""
            if isinstance(child, ast.ClassDef):
                child_name = (
                    f"{qualname_prefix}{child.name}"
                    if qualname_prefix == ""
                    else f"{qualname_prefix}.{child.name}"
                )
                _walk(child, child_name)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
                if not any(name.startswith(p) for p in _QUERY_PREFIXES):
                    continue
                qualname = (
                    f"{qualname_prefix}.{name}"
                    if qualname_prefix
                    else name
                )
                params = _function_params(child)
                if _PIT_PARAM_NAME in params:
                    continue
                if _has_exempt_marker(source_lines, child.lineno):
                    continue
                violations.append((
                    qualname,
                    child.lineno,
                    f"`{name}` 시그니처에 `{_PIT_PARAM_NAME}` 매개변수 없음 "
                    f"+ `# {_EXEMPT_MARKER}:` 주석도 없음",
                ))
            # 다른 statement (if/for/with 등) 안의 def 도 walk.
            _walk(child, qualname_prefix)

    _walk(tree)
    return violations


def _iter_target_files(repo_dir: Path) -> Iterable[Path]:
    for name in _TARGET_FILES:
        path = repo_dir / name
        if path.is_file():
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PIT bypass 가드 — repository fetch_X 의 as_of 검증.",
    )
    parser.add_argument(
        "--repositories-dir",
        type=Path,
        default=_DEFAULT_REPO_DIR,
        help="repositories 디렉토리 (default: server/app/repositories). 테스트용.",
    )
    args = parser.parse_args(argv)
    repo_dir: Path = args.repositories_dir

    if not repo_dir.is_dir():
        print(
            f"FAIL: repositories 디렉토리 누락 — {repo_dir}",
            file=sys.stderr,
        )
        return 2

    all_violations: list[tuple[str, str, int, str]] = []  # (file, qual, line, reason)
    for path in _iter_target_files(repo_dir):
        for qualname, lineno, reason in _collect_violations(path):
            rel = path.name
            all_violations.append((rel, qualname, lineno, reason))

    if all_violations:
        print(
            "FAIL: PIT bypass 의무 위반 — ADR-0008 D5 + ADR-0002 D3. "
            "Historical query 메서드는 `as_of: date` 매개변수 또는 명시적 "
            "`# pit-exempt: <reason>` 주석 필수.\n",
            file=sys.stderr,
        )
        for file, qual, line, reason in all_violations:
            print(f"  - {file}:{line} {qual}: {reason}", file=sys.stderr)
        return 1

    print("OK: 모든 PIT-aware fetch_* 메서드가 as_of 인자 또는 exempt 명시")
    return 0


if __name__ == "__main__":
    # Windows console UTF-8 — Korean diagnostic 표시.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
