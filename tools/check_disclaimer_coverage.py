"""Disclaimer coverage 가드 — root layout + 의무 disclaimer 페이지.

ADR-0006 D2 (동의 모달) + ADR-0006 D6.2 (처리방침 항시 공개) + ADR-0007 D2
(footer disclaimer) 의 conformance. M0_PLAN T41 의 CI 5 게이트 중 disclaimer
coverage 항목 + Momus M0 review V2 잔존 (페이지 존재 검사).

검사 대상:
    client/app/layout.tsx — 의무 component mount
    client/app/privacy/page.tsx — 개인정보처리방침 (제30조 항시 공개)
    client/app/terms/page.tsx — 이용약관
    client/app/disclaimer/page.tsx — 면책조항 (ADR-0007 D2 전문)

검사 항목:
    1. layout: DisclaimerFooter import + JSX 사용
    2. layout: ConsentModal import + JSX 사용
    3. 페이지 3 종 파일 존재 — Momus V2 (footer link / consent link 가 dead
       link 가 되는 silent regression 차단).

본 가드는 root layout 의 명시적 import + render + 페이지 file 존재 만 확인 —
페이지 본문의 의미·법적 정확성은 변호사 자문 ADR-0019 (M0 release 전 의무).

Exit code:
    0  — 통과.
    1  — 실패 (component 누락 또는 페이지 file 누락) + diagnostic 메시지.
    2  — 검사 대상 layout 파일 누락 (구조 변경 의심).

운영:
    python tools/check_disclaimer_coverage.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

# Default repository root — CLI `--root` 인자로 override 가능 (단위 test 가
# tmp_path fake repo 검사용).
_DEFAULT_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# (display name, import 식별자 — `import { X } from ...` 의 X)
_REQUIRED_COMPONENTS: Final[tuple[tuple[str, str], ...]] = (
    ("DisclaimerFooter", "DisclaimerFooter"),
    ("ConsentModal", "ConsentModal"),
)

# Momus M0 review V2 잔존 — 의무 disclaimer 페이지. footer / consent link 가
# dead link 가 되는 silent regression 차단. 각 path 는 repo root 기준.
_REQUIRED_PAGES: Final[tuple[tuple[str, str], ...]] = (
    ("개인정보처리방침", "client/app/privacy/page.tsx"),
    ("이용약관", "client/app/terms/page.tsx"),
    ("면책조항", "client/app/disclaimer/page.tsx"),
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


def check_coverage(repo_root: Path) -> int:
    """root 경로에서 coverage 검사. main 의 testable 형태."""
    layout_path = repo_root / "client" / "app" / "layout.tsx"
    if not layout_path.exists():
        print(
            f"FAIL: 의무 layout 파일 누락 — {layout_path.relative_to(repo_root)}",
            file=sys.stderr,
        )
        return 2
    source = layout_path.read_text(encoding="utf-8")

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

    # Momus M0 review V2 잔존 — 의무 페이지 file 존재 검사.
    missing_pages: list[str] = []
    for display_name, rel_path in _REQUIRED_PAGES:
        page_path = repo_root / rel_path
        if not page_path.exists():
            missing_pages.append(
                f"  - {display_name}: {rel_path} 파일 누락",
            )

    if missing or missing_pages:
        print(
            "FAIL: disclaimer / consent coverage 위반.\n"
            "ADR-0006 D2 (동의) + D6.2 (처리방침 항시 공개) + ADR-0007 D2 "
            "(footer disclaimer).",
            file=sys.stderr,
        )
        if missing:
            print(
                f"\nlayout 의무 component 누락 — {layout_path.relative_to(repo_root)}:",
                file=sys.stderr,
            )
            for line in missing:
                print(line, file=sys.stderr)
        if missing_pages:
            print(
                "\n의무 페이지 파일 누락 (Momus V2 — footer/consent link 의 dead link 차단):",
                file=sys.stderr,
            )
            for line in missing_pages:
                print(line, file=sys.stderr)
        return 1

    print(
        "OK: disclaimer + consent component layout mount, "
        "의무 페이지 3 종 file 존재.",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Disclaimer + consent coverage 검사.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_DEFAULT_REPO_ROOT,
        help="repository root. 운영 default 는 tools/ 의 상위.",
    )
    args = parser.parse_args()
    return check_coverage(args.root.resolve())


if __name__ == "__main__":
    # Windows console default cp949 → mojibake. 동일 패턴 (forbidden_words.py).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
