"""Disclaimer coverage 가드 — root layout 에 의무 component 가 mount 되었는지.

ADR-0006 D2 (동의 모달) + ADR-0007 D2 (footer disclaimer) 의 conformance.
M0_PLAN T41 의 CI 5 게이트 중 disclaimer coverage 항목.

검사 대상:
    client/app/layout.tsx

검사 항목:
    1. DisclaimerFooter import + JSX 사용
    2. ConsentModal import + JSX 사용

본 가드는 root layout 의 명시적 import + render 만 확인 — provider 의 nested
변경 (예: 다른 layout 으로 이동) 시에도 같은 component 가 mount 된다는 보장은
런타임 (Playwright E2E, T42) 책임.

Exit code:
    0  — 통과 (모든 의무 component mount).
    1  — 실패 (어느 하나라도 누락) + diagnostic 메시지.
    2  — 검사 대상 파일 누락 (구조 변경 의심).

운영:
    python tools/check_disclaimer_coverage.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final

# Repository root from this file's path — tools/ 의 상위.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_LAYOUT_PATH: Final[Path] = _REPO_ROOT / "client" / "app" / "layout.tsx"

# (display name, import 식별자 — `import { X } from ...` 의 X)
_REQUIRED_COMPONENTS: Final[tuple[tuple[str, str], ...]] = (
    ("DisclaimerFooter", "DisclaimerFooter"),
    ("ConsentModal", "ConsentModal"),
)


def _import_regex(name: str) -> re.Pattern[str]:
    """`import { ... <name> ... } from ...` — 단일 import 또는 분리된 named."""
    # named import 의 list 안에서 단어 경계로 일치.
    return re.compile(
        rf"import\s*\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}\s*from\s+",
        re.DOTALL,
    )


def _jsx_regex(name: str) -> re.Pattern[str]:
    """`<Name` — self-closing 또는 opening tag 둘 다 일치."""
    return re.compile(rf"<\s*{re.escape(name)}\b")


def main() -> int:
    if not _LAYOUT_PATH.exists():
        print(
            f"FAIL: 의무 layout 파일 누락 — {_LAYOUT_PATH.relative_to(_REPO_ROOT)}",
            file=sys.stderr,
        )
        return 2
    source = _LAYOUT_PATH.read_text(encoding="utf-8")

    missing: list[str] = []
    for display_name, identifier in _REQUIRED_COMPONENTS:
        has_import = _import_regex(identifier).search(source) is not None
        has_jsx = _jsx_regex(identifier).search(source) is not None
        if not has_import or not has_jsx:
            reason = []
            if not has_import:
                reason.append("import")
            if not has_jsx:
                reason.append("JSX 사용")
            missing.append(
                f"  - {display_name}: {' / '.join(reason)} 누락",
            )

    if missing:
        print(
            "FAIL: root layout 에 의무 disclaimer / consent component 누락. "
            "ADR-0006 D2 + ADR-0007 D2 위반.\n"
            f"검사 파일: {_LAYOUT_PATH.relative_to(_REPO_ROOT)}\n"
            "누락 항목:",
            file=sys.stderr,
        )
        for line in missing:
            print(line, file=sys.stderr)
        return 1

    print("OK: disclaimer + consent component 가 root layout 에 mount 됨")
    return 0


if __name__ == "__main__":
    # Windows console default cp949 → mojibake. 동일 패턴 (forbidden_words.py).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
