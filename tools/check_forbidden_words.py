"""CI gate — 금지 어휘 source code/콘텐츠 스캐너.

ADR-0007 §D4 + 8 기둥 §2.2 No Advice 의 CI 강제. `server/app/services/
forbidden_words.py::scan_text` 의 NFKC + allowed_phrases 인식 logic 을 재사용.

목적:
    1. 소스 코드 (Python / TypeScript / Markdown / JSON / HTML) 에 금지 어휘
       commit 차단.
    2. 빌드 시 `shared/forbidden-words.json` 의 단일 정의를 file system level
       에서 강제.

사용:
    python tools/check_forbidden_words.py                # 전체 repo 스캔
    python tools/check_forbidden_words.py path/to/dir    # 특정 dir
    python tools/check_forbidden_words.py file1.py file2.md  # 파일들
    python tools/check_forbidden_words.py --exclude '**/node_modules/**'

Exit code:
    0 — 위반 없음.
    1 — 위반 발견 (stdout 에 file:line:col message 형식).
    2 — 입력/설정 오류.

EXTERNAL_QUOTE scope (회사명·DART 공시 제목 등) 의 source code 처리:
    본 CLI 는 소스 파일 raw text 전체를 단일 string 으로 스캔 — 텍스트
    표현이 EXTERNAL_QUOTE 인지 판단 불가. 따라서 코드 안의 종목명/회사명 자체는
    `shared/forbidden-words.json` 의 allowed_phrases 에 명시되어 있음 (예:
    "이베스트", "베스트셀러"). allowed_phrases 가 substring match 제외하므로
    회사명 인용 코드는 자연스럽게 통과.

설계 결정:
    - **CLI script 단독** — pytest 의존 X, server 의존성 (sqlalchemy 등) X.
      `server/app/services/forbidden_words.py` 만 import (외부 deps 0).
    - **glob exclude** — venv / node_modules / .git / __pycache__ / dist
      등 default 제외. --exclude 추가 가능.
    - **default 대상 확장자** — .py / .ts / .tsx / .js / .jsx / .md / .json /
      .html / .css / .yaml / .yml.
    - **shared/forbidden-words.json 자체는 스캔 제외** — 정의 파일에는 금지
      어휘가 항상 있어야 함.
    - **scan_text 재사용** — server import path 가 필요. CLI 가 `sys.path`
      에 server 추가.

관련 ADR:
- ADR-0007 §D4 (금지 어휘 정책), §D9 (변경 절차)
- M0_PLAN T29 / AC-P-02 (금지 어휘 0)
"""

from __future__ import annotations

import argparse
import bisect
import fnmatch
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final


# Default exclude glob patterns — 운영 file 이 아니거나 vendor / tooling 디렉토리.
_DEFAULT_EXCLUDES: Final[tuple[str, ...]] = (
    "**/.git/**",
    "**/__pycache__/**",
    "**/node_modules/**",
    "**/.next/**",
    "**/dist/**",
    "**/build/**",
    "**/venv/**",
    "**/.venv/**",
    "**/env/**",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/coverage/**",
    "**/htmlcov/**",
    # 정의 파일 자체 — 운영 금지 어휘 list 가 본 file 에 정의.
    "shared/forbidden-words.json",
    # 정의 파일을 import 하는 builtin pack / schemas — 사용자 facing X.
    "shared/schemas/**",
    "server/builtin-packs/**",
    # 정책·검사 모듈 자체는 금지 어휘를 docstring 에서 "추천", "매수" 등 인용
    # — 본 모듈들도 스캔 제외 (ADR-0007 §D9 의 source-of-truth 보존).
    "server/app/services/forbidden_words.py",
    "server/app/middleware/forbidden_words_guard.py",
    "client/lib/forbidden-words.ts",
    "client/lib/__tests__/forbidden-words.test.ts",
    "client/eslint-rules/**",
    "tools/check_forbidden_words.py",
    # ADR 문서 자체는 어휘 인용 — exempt. M0_PLAN / 8 기둥 본문도 인용 필요.
    "docs/**",
    # README + OSS infra — 정책 설명에 금지 어휘 인용 필요 (T44).
    "README.md",
    "README.en.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "TROUBLESHOOTING.md",
    # 테스트 코드 — fixture 안에 금지 어휘 의도된 인용 (`scan_text` 검증 등).
    # oracle 리뷰 C-1: `**/test_*.py` / `**/*_test.py` 는 fnmatch 가 `**` 를
    # single `*` 로 해석하여 multi-level 매칭 안 됨. 디렉토리 단위 패턴
    # (`**/tests/**`, `**/__tests__/**`) 이 cover.
    "**/tests/**",
    "**/__tests__/**",
    # Alembic migration tools — `upgrade()` / `downgrade()` 가 method 이름.
    "server/alembic/**",
    # Demo route 안의 의도된 위반 ("Buy now" 등) — middleware 테스트용. 운영
    # `include_demo_routes=False` default 라 production binary 에 미포함.
    # oracle 리뷰 M-2 [TODO M0 출하 전]: demo route 를 `server/app/api/_demo.py`
    # 별도 모듈로 분리 + 본 exclude 제거. 그 동안 main.py 의 신규 운영
    # endpoint 가 CI 검사 누락될 위험 인지.
    "server/app/main.py",
)


_DEFAULT_INCLUDE_SUFFIXES: Final[frozenset[str]] = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json",
    ".html", ".css", ".yaml", ".yml",
})


def _scan_text() -> object:
    """server/app/services/forbidden_words.py 의 scan_text lazy import.

    sys.path 에 server 추가 후 import. CLI script 가 server deps 와 분리.
    """
    repo_root = Path(__file__).resolve().parent.parent
    server_dir = repo_root / "server"
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    from app.services.forbidden_words import scan_text  # noqa: PLC0415
    return scan_text


def _is_excluded(
    relative_path: Path,
    *,
    patterns: Iterable[str],
) -> bool:
    """glob pattern 의 어느 하나라도 match 하면 True.

    fnmatch 는 `**` 를 single `*` 처럼 처리 — `pathlib.Path.match` 가 정확.
    그러나 `**` 의 multi-level matching 은 `Path.match` 도 부분적. 가장 안전:
    posix string 기반 fnmatch + 명시 패턴 (e.g., `**/foo/**` 는 path 내 `foo`
    포함 시 match).
    """
    posix = relative_path.as_posix()
    for pat in patterns:
        if fnmatch.fnmatch(posix, pat):
            return True
        # `**/dir/**` 패턴 보강 — fnmatch 가 `**` 를 single `*` 처럼 보므로 직접
        # path component 검사.
        if pat.startswith("**/") and pat.endswith("/**"):
            target = pat[3:-3]  # 중간 부분.
            if f"/{target}/" in f"/{posix}/" or posix == target:
                return True
        elif pat.startswith("**/"):
            target = pat[3:]
            if posix.endswith(target) or f"/{target}" in posix:
                return True
    return False


def _walk_targets(
    roots: Iterable[Path],
    *,
    excludes: tuple[str, ...],
    repo_root: Path,
) -> Iterator[Path]:
    """root list 의 file iterator — exclude / suffix 필터 적용.

    Args:
        roots: 디렉토리 또는 단일 파일. 디렉토리는 재귀 walk.
        excludes: glob pattern list — 매칭 path 제외.
        repo_root: 상대 path 기준.
    """
    for root in roots:
        if root.is_file():
            rel = root.relative_to(repo_root) if root.is_absolute() else root
            if rel.suffix in _DEFAULT_INCLUDE_SUFFIXES and not _is_excluded(
                rel, patterns=excludes,
            ):
                yield root
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in _DEFAULT_INCLUDE_SUFFIXES:
                continue
            try:
                rel = path.relative_to(repo_root)
            except ValueError:
                rel = path
            if _is_excluded(rel, patterns=excludes):
                continue
            yield path


def _scan_file(
    path: Path,
    *,
    scan_text_fn: object,
) -> list[tuple[int, int, str]]:
    """파일 read + line/col 별 violation list 반환.

    Returns:
        list[(line, col, word)] — 1-indexed line + col.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # 바이너리 / non-UTF8 — skip.
        return []
    except OSError:
        return []

    matches = scan_text_fn(text)  # type: ignore[operator]
    if not matches:
        return []

    # line/col 변환 — NFKC normalize 가 길이 보존이라 대부분 정확. drift 시
    # 근사값 (운영 grep 용도라 정확도 충분).
    line_starts: list[int] = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    results: list[tuple[int, int, str]] = []
    for m in matches:
        # match.index 는 normalize 후 기준 — original text 와 일치 가정.
        idx = m.index
        line_no = bisect.bisect_right(line_starts, idx) - 1
        col = idx - line_starts[line_no]
        results.append((line_no + 1, col + 1, m.word))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ADR-0007 forbidden words CI scanner — source code / 콘텐츠 파일에 "
            "금지 어휘 등장 시 exit 1."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="스캔 대상 경로 (디렉토리 또는 파일). default = 현재 디렉토리.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="추가 glob exclude 패턴 (반복 가능).",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help=(
            "repo root path — 상대 path 표시 기준. default = 본 script 의 "
            "위치 기준 추론 (`tools/check_forbidden_words.py.parent.parent`)."
        ),
    )
    args = parser.parse_args(argv)

    # repo root 결정 — oracle 리뷰 M-3:
    # CI step 의 working_directory override / 외부 호출 시에도 robust 한
    # script 위치 기반 추론. tools/check_forbidden_words.py 가 repo_root/tools/
    # 에 위치 가정. 호출자가 명시 override 가능.
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = Path(__file__).resolve().parent.parent

    excludes: tuple[str, ...] = _DEFAULT_EXCLUDES + tuple(args.exclude)

    roots = [Path(p).resolve() for p in args.paths]
    if not roots:
        print("error: no input paths", file=sys.stderr)
        return 2

    scan_text_fn = _scan_text()

    violations: list[tuple[Path, int, int, str]] = []
    for file_path in _walk_targets(
        roots, excludes=excludes, repo_root=repo_root,
    ):
        file_violations = _scan_file(file_path, scan_text_fn=scan_text_fn)
        for line, col, word in file_violations:
            violations.append((file_path, line, col, word))

    if not violations:
        print("clean — no forbidden words found")
        return 0

    print(
        f"{len(violations)} forbidden word violation(s) found "
        f"(ADR-0007 §D4):",
        file=sys.stderr,
    )
    for path, line, col, word in violations:
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            rel = path
        print(f"{rel.as_posix()}:{line}:{col}: {word!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    # Windows CMD 의 default codec (CP949) 이 한글 출력 시 UnicodeEncodeError.
    # stdout/stderr 모두 UTF-8 강제 — subprocess capture 환경에서도 일관.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
