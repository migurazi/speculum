"""tools/check_source_attribution.py CLI 가드 단위 테스트.

테스트 매트릭스:
    1. clean — FactorValue 미사용 → exit 0
    2. clean — FactorValue + SourceAttribution 직접 wrap → exit 0
    3. clean — FactorValue + MetricCard 위임 → exit 0
    4. clean — FactorValue + CompareGrid 위임 → exit 0
    5. violation — FactorValue 사용 + wrap import 없음 → exit 1 + diagnostic
    6. exempt — lib/api/ 안의 FactorValue → 검사 대상 X
    7. exempt — lib/factor/ 안의 FactorValue → 검사 대상 X
    8. exempt — __tests__/ 안의 위반 → 검사 대상 X
    9. client-dir 누락 → exit 2
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# tools 디렉토리 위치 — server/tests 에서 ../../tools.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI_SCRIPT = _REPO_ROOT / "tools" / "check_source_attribution.py"


def _run_cli(client_dir: Path) -> subprocess.CompletedProcess[str]:
    """CLI 실행 helper — stdout/stderr capture + exit code."""
    return subprocess.run(
        [sys.executable, str(_CLI_SCRIPT), "--client-dir", str(client_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _make_file(root: Path, relpath: str, content: str) -> Path:
    """tmp_path 안 파일 생성 (디렉토리 자동 생성)."""
    full = root / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


# =============================================================================
# Clean cases
# =============================================================================

def test_no_factor_value_usage_passes(tmp_path: Path) -> None:
    _make_file(
        tmp_path,
        "app/page.tsx",
        "export default function Page(): JSX.Element { return <div/>; }\n",
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_factor_value_with_source_attribution_passes(tmp_path: Path) -> None:
    _make_file(
        tmp_path,
        "components/Foo.tsx",
        (
            "import { SourceAttribution } from '@/components/SourceAttribution';\n"
            "import type { FactorValue } from '@/lib/api/stocks';\n"
            "export function Foo({ f }: { f: FactorValue }) {\n"
            "  return <SourceAttribution value={f.value} source='DART' "
            "formula={f.canonical_id} asOf='2024-09-30' />;\n"
            "}\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_factor_value_with_metric_card_delegation_passes(tmp_path: Path) -> None:
    """MetricCard 위임 — 표시 책임 위임 OK."""
    _make_file(
        tmp_path,
        "app/foo/page.tsx",
        (
            "import { MetricCard } from '@/components/StockDetail/MetricCard';\n"
            "import type { FactorValue } from '@/lib/api/stocks';\n"
            "export default function Page({ f }: { f: FactorValue }) {\n"
            "  return <MetricCard factor={f} asOf='2024-09-30' />;\n"
            "}\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_factor_value_with_compare_grid_delegation_passes(tmp_path: Path) -> None:
    _make_file(
        tmp_path,
        "app/bar/page.tsx",
        (
            "import { CompareGrid } from '@/components/Compare/CompareGrid';\n"
            "import type { FactorValue } from '@/lib/api/stocks';\n"
            "type _x = FactorValue;\n"
            "export default function Page() { return <CompareGrid stocks={[]} "
            "asOf='2024-09-30' />; }\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


# =============================================================================
# Violation
# =============================================================================

def test_factor_value_without_wrap_fails(tmp_path: Path) -> None:
    """FactorValue 사용 + wrap import 없음 → exit 1 + diagnostic."""
    _make_file(
        tmp_path,
        "components/Bad.tsx",
        (
            "import type { FactorValue } from '@/lib/api/stocks';\n"
            "export function Bad({ f }: { f: FactorValue }) {\n"
            "  return <div>{f.value}</div>;\n"  # naked 표시
            "}\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 1
    assert "components/Bad.tsx" in result.stderr
    assert "ADR-0007" in result.stderr


# =============================================================================
# Exempt paths
# =============================================================================

def test_lib_api_exempt(tmp_path: Path) -> None:
    """lib/api/ 의 type 정의는 검사 대상 X."""
    _make_file(
        tmp_path,
        "lib/api/stocks.ts",
        (
            "export interface FactorValue {\n"
            "  readonly value: string | null;\n"
            "}\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_lib_factor_exempt(tmp_path: Path) -> None:
    """lib/factor/ 의 inference 도 검사 대상 X."""
    _make_file(
        tmp_path,
        "lib/factor/source.ts",
        (
            "import type { FactorValue } from '@/lib/api/stocks';\n"
            "export function inferSource(_f: FactorValue) { return 'DART'; }\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


def test_tests_directory_exempt(tmp_path: Path) -> None:
    """__tests__/ 안의 위반은 검사 대상 X (fixture 가능)."""
    _make_file(
        tmp_path,
        "components/__tests__/Bad.test.tsx",
        (
            "import type { FactorValue } from '@/lib/api/stocks';\n"
            "const f: FactorValue = {} as FactorValue;\n"
            "console.log(f.value);\n"
        ),
    )
    result = _run_cli(tmp_path)
    assert result.returncode == 0, result.stderr


# =============================================================================
# Misuse
# =============================================================================

def test_missing_client_dir_returns_two(tmp_path: Path) -> None:
    """지정한 client-dir 가 존재하지 않으면 exit 2."""
    missing = tmp_path / "missing"
    result = _run_cli(missing)
    assert result.returncode == 2
    assert "디렉토리 누락" in result.stderr
