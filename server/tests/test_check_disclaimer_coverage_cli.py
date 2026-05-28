"""tools/check_disclaimer_coverage.py CLI 가드 단위 test.

Momus M0 review V2 잔존 fix — 의무 disclaimer 페이지 (`/privacy`, `/terms`,
`/disclaimer`) 의 존재 검사가 CI 게이트에 추가됨.

테스트 매트릭스:
    1. clean — layout component 2 mount + 페이지 3 종 모두 존재 → exit 0
    2. violation — layout 의 ConsentModal import 누락 → exit 1
    3. violation — /privacy 페이지 파일 누락 → exit 1
    4. violation — /terms 페이지 누락 → exit 1
    5. violation — /disclaimer 페이지 누락 → exit 1
    6. violation — layout 파일 자체 누락 → exit 2

본 test 는 도구의 `--root` 인자로 tmp_path 의 fake repo 검사. 실 repo
무관하게 도구의 알고리즘 회귀 가드.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI_SCRIPT = _REPO_ROOT / "tools" / "check_disclaimer_coverage.py"


def _run_cli(repo_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CLI_SCRIPT), "--root", str(repo_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _make_clean_repo(root: Path) -> None:
    """layout (2 component mount) + 3 페이지 file 모두 존재."""
    layout_path = root / "client" / "app" / "layout.tsx"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text(
        (
            'import { ConsentModal } from "@/components/ConsentModal";\n'
            'import { DisclaimerFooter } from "@/components/DisclaimerFooter";\n'
            "export default function RootLayout() {\n"
            "  return (\n"
            "    <html>\n"
            "      <body>\n"
            "        <ConsentModal />\n"
            "        <DisclaimerFooter />\n"
            "      </body>\n"
            "    </html>\n"
            "  );\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    for page_dir in ("privacy", "terms", "disclaimer"):
        page_path = root / "client" / "app" / page_dir / "page.tsx"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(
            "export default function Page() { return <div />; }\n",
            encoding="utf-8",
        )


# =============================================================================
# Clean — 1 case
# =============================================================================


def test_clean_layout_and_all_pages_present(tmp_path: Path) -> None:
    _make_clean_repo(tmp_path)
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# =============================================================================
# Violation — component 누락
# =============================================================================


def test_consent_modal_missing_layout(tmp_path: Path) -> None:
    _make_clean_repo(tmp_path)
    # ConsentModal 의 import + JSX 모두 제거.
    layout_path = tmp_path / "client" / "app" / "layout.tsx"
    layout_path.write_text(
        (
            'import { DisclaimerFooter } from "@/components/DisclaimerFooter";\n'
            "export default function RootLayout() {\n"
            "  return (\n"
            "    <html>\n"
            "      <body>\n"
            "        <DisclaimerFooter />\n"
            "      </body>\n"
            "    </html>\n"
            "  );\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "ConsentModal" in result.stderr


# =============================================================================
# Violation — 페이지 누락 (Momus V2 잔존 fix)
# =============================================================================


def test_privacy_page_missing(tmp_path: Path) -> None:
    _make_clean_repo(tmp_path)
    (tmp_path / "client" / "app" / "privacy" / "page.tsx").unlink()
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "개인정보처리방침" in result.stderr
    assert "privacy/page.tsx" in result.stderr


def test_terms_page_missing(tmp_path: Path) -> None:
    _make_clean_repo(tmp_path)
    (tmp_path / "client" / "app" / "terms" / "page.tsx").unlink()
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "이용약관" in result.stderr
    assert "terms/page.tsx" in result.stderr


def test_disclaimer_page_missing(tmp_path: Path) -> None:
    _make_clean_repo(tmp_path)
    (tmp_path / "client" / "app" / "disclaimer" / "page.tsx").unlink()
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "면책조항" in result.stderr
    assert "disclaimer/page.tsx" in result.stderr


# =============================================================================
# Layout 자체 누락 → exit 2
# =============================================================================


def test_layout_file_missing_exit_2(tmp_path: Path) -> None:
    # 페이지만 생성, layout 미생성.
    for page_dir in ("privacy", "terms", "disclaimer"):
        page_path = tmp_path / "client" / "app" / page_dir / "page.tsx"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text("export default () => <div />", encoding="utf-8")
    result = _run_cli(tmp_path)
    assert result.returncode == 2
    assert "layout 파일 누락" in result.stderr
