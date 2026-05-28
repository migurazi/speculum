"""Source attribution 가드 — factor value 가 SourceAttribution wrap 거쳤는지.

ADR-0007 D2 + 8 기둥 §2.1 Fidelity 의 file-system level 강제. M0_PLAN T41
의 CI 5 게이트 중 source attribution 항목.

검사 의도:
    backend `FactorValue` 의 `value` 가 frontend UI 에 표시될 때 반드시
    `<SourceAttribution>` wrap 안에 위치. "값만 표시" 우회 차단.

검사 규칙 (heuristic — false positive 최소화):
    1. client 의 .ts/.tsx 운영 파일 중 `FactorValue` 를 import 하는 것:
       - 같은 파일에 `SourceAttribution` import 있음 — 직접 wrap.
       - 또는 같은 파일에 `MetricCard` 또는 `CompareGrid` import 있음 —
         인증된 wrapper 에 위임.
       - 또는 본 파일이 type 정의 / source inference / 테스트 파일.

검사 제외:
    - `__tests__/` 디렉토리 (fixture 의 의도적 위반 가능).
    - `lib/api/` 의 type 정의 모듈 (wire schema 정의 — 표시 X).
    - `lib/factor/` 의 inference helper (heuristic — 표시 X).

Exit code:
    0  — 통과 (모든 FactorValue 사용 파일이 wrap 거침).
    1  — 실패 (어느 하나라도 위반) + diagnostic.

운영:
    python tools/check_source_attribution.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_DEFAULT_CLIENT_DIR: Final[Path] = _REPO_ROOT / "client"

# 검사 대상 확장자.
_TARGET_EXTS: Final[tuple[str, ...]] = (".ts", ".tsx")

# 검사 제외 경로 prefix (CLIENT_DIR 상대).
_EXEMPT_PREFIXES: Final[tuple[str, ...]] = (
    "node_modules/",
    ".next/",
    "lib/api/",      # wire schema 정의 — 표시 책임 X
    "lib/factor/",   # source 추론 helper — 표시 책임 X
)

# 검사 제외 디렉토리명 (path 어디서든).
_EXEMPT_DIR_NAMES: Final[frozenset[str]] = frozenset({"__tests__"})

# 검사 대상 import 명. 운영 wrap component / direct wrap import.
_REQUIRED_IMPORTS: Final[tuple[str, ...]] = (
    "SourceAttribution",
    "MetricCard",
    "CompareGrid",
)

# `FactorValue` 가 등장하는 file 식별 — type-only 또는 value import 둘 다.
_FACTOR_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bFactorValue\b",
)


def _import_pattern(name: str) -> re.Pattern[str]:
    """`import { ... <name> ... } from ...` 의 named import 검출."""
    return re.compile(
        rf"import\s*(?:type\s*)?\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}\s*from\s+",
        re.DOTALL,
    )


_REQUIRED_IMPORT_REGEXES: Final[tuple[re.Pattern[str], ...]] = tuple(
    _import_pattern(name) for name in _REQUIRED_IMPORTS
)


def _is_exempt(relative_path: str) -> bool:
    """검사 제외 여부 — prefix 또는 path 안의 디렉토리명 일치."""
    if any(relative_path.startswith(p) for p in _EXEMPT_PREFIXES):
        return True
    parts = relative_path.replace("\\", "/").split("/")
    return any(p in _EXEMPT_DIR_NAMES for p in parts)


def _walk_client_files(client_dir: Path) -> list[Path]:
    """client_dir 의 모든 .ts/.tsx 파일 — exempt 제외 후 반환."""
    files: list[Path] = []
    for path in client_dir.rglob("*"):
        if not path.is_file() or path.suffix not in _TARGET_EXTS:
            continue
        rel = str(path.relative_to(client_dir)).replace("\\", "/")
        if _is_exempt(rel):
            continue
        files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Source attribution 의무 가드.",
    )
    parser.add_argument(
        "--client-dir",
        type=Path,
        default=_DEFAULT_CLIENT_DIR,
        help="client 디렉토리 경로 (default: repo-root/client). 테스트용.",
    )
    args = parser.parse_args(argv)
    client_dir: Path = args.client_dir

    if not client_dir.is_dir():
        print(
            f"FAIL: client 디렉토리 누락 — {client_dir}",
            file=sys.stderr,
        )
        return 2

    violators: list[tuple[str, str]] = []  # (path, reason)

    for path in _walk_client_files(client_dir):
        source = path.read_text(encoding="utf-8")
        if _FACTOR_VALUE_PATTERN.search(source) is None:
            continue  # FactorValue 미사용 — 검사 대상 X.

        # FactorValue 를 사용하면, 인증된 wrap 중 하나의 import 가 필수.
        if any(pat.search(source) is not None for pat in _REQUIRED_IMPORT_REGEXES):
            continue  # OK — 직접 wrap 또는 인증 wrapper 위임.

        rel = str(path.relative_to(client_dir)).replace("\\", "/")
        reason = (
            "FactorValue 를 사용하지만 SourceAttribution / MetricCard / "
            "CompareGrid 의 import 가 없음"
        )
        violators.append((rel, reason))

    if violators:
        print(
            "FAIL: source attribution 의무 위반 — ADR-0007 D2 + 8 기둥 "
            "§2.1 Fidelity. 모든 factor value 표시는 SourceAttribution wrap "
            "또는 인증된 wrapper (MetricCard / CompareGrid) 거쳐야 합니다.\n",
            file=sys.stderr,
        )
        for path, reason in violators:
            print(f"  - {path}: {reason}", file=sys.stderr)
        return 1

    print("OK: 모든 FactorValue 사용 파일이 source attribution wrap 거침")
    return 0


if __name__ == "__main__":
    # Windows console default 가 cp949 — Korean diagnostic 메시지가 mojibake.
    # CI (Linux) 는 utf-8 default 라 무영향이나, dev / CLI test 가 Windows 에서
    # 작동하도록 reconfigure (forbidden_words.py 와 동일 패턴).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
