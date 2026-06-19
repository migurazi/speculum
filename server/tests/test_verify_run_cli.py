"""verify_run.py CLI 테스트 — ADR-0033 / M5 T-M5-03c.

핵심 보증: Screen Run export 의 선언 result_hash 가 동봉 입력과 정합하면 exit 0,
일부 변조/직렬화 drift 면 exit 1, 형식·입력 오류면 exit 2. hash 재유도는 server
`ScreenRunBuilder` 단일 SoT 재사용(재구현 0) — fixture 도 동일 builder 로 생성해
"정합" 의 기준이 server 와 일치함을 보장.

CLI 는 subprocess 로 실행(test_validate_pack_cli.py 패턴) — server 디렉토리 sys.path
주입만으로 독립 동작함을 함께 검증.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from app.services.screen_run import ScreenRunBuilder, ScreenRunSnapshot

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI = _REPO_ROOT / "tools" / "verify_run.py"

# 결정성: 명시 data_versions 주입(collect_active_policy_versions DB 호출 회피).
_DATA_VERSIONS = {"krx_batch_id": "krx-001", "dart_batch_id": "dart-001"}
_CONDITIONS = [{"factor": "per:trailing", "op": "<", "value": "10"}]
_SELECTED = ["per:trailing", "roe:trailing"]
_SECURITY_TYPES = ["common"]
_AS_OF = date(2024, 6, 28)
_RESULT_CODES = ["005930", "000660"]


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CLI), *[str(p) for p in paths]],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _build_snapshot() -> ScreenRunSnapshot:
    """server SoT 로 정합 snapshot 생성 — fixture 의 result_hash 가 정확함을 보장."""
    return ScreenRunBuilder.build(
        run_id=UUID(int=1),
        user_id=UUID(int=1),
        conditions=_CONDITIONS,
        selected_factors=_SELECTED,
        security_types=_SECURITY_TYPES,
        as_of=_AS_OF,
        result_codes=_RESULT_CODES,
        data_versions=_DATA_VERSIONS,
    )


def _run_dict(snap: ScreenRunSnapshot) -> dict[str, Any]:
    """snapshot → export 의 `run` 부분 JSON dict (ScreenRunSnapshotOut 형태 미러)."""
    return {
        "id": str(snap.id),
        "user_id": str(snap.user_id),
        "as_of": snap.as_of.isoformat(),
        "result_codes": list(snap.result_codes),
        "result_hash": snap.result_hash,
        "data_versions": dict(snap.data_versions),
        "conditions": [dict(c) for c in snap.query.conditions],
        "selected_factors": list(snap.query.selected_factors),
        "security_types": list(snap.query.security_types),
    }


def _export_dict(snap: ScreenRunSnapshot) -> dict[str, Any]:
    """run 을 export wrapper 로 감싼 dict (speculum-screen-run-export-v1)."""
    return {
        "export_format": "speculum-screen-run-export-v1",
        "snapshot_schema_version": "1.3",
        "run": _run_dict(snap),
    }


def _write(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# =============================================================================
# 1. 정합 (exit 0)
# =============================================================================

def test_valid_export_passes(tmp_path: Path) -> None:
    p = _write(tmp_path, "run.json", _export_dict(_build_snapshot()))
    res = _run(p)
    assert res.returncode == 0, res.stderr
    assert "정합 정상" in res.stdout


def test_run_direct_form_passes(tmp_path: Path) -> None:
    # export wrapper 없이 run 부분만 paste 한 경우도 수용(reproduce 와 동일 관용).
    p = _write(tmp_path, "run-direct.json", _run_dict(_build_snapshot()))
    res = _run(p)
    assert res.returncode == 0, res.stderr


def test_missing_security_types_still_passes(tmp_path: Path) -> None:
    # 구 export(security_types 부재) → builder default ("common",) 적용 → 동일 hash.
    snap = _build_snapshot()  # security_types=["common"] 로 build 됨.
    run = _run_dict(snap)
    del run["security_types"]
    p = _write(tmp_path, "old-export.json", run)
    res = _run(p)
    assert res.returncode == 0, res.stderr


# =============================================================================
# 2. 불일치 — 부분 변조/직렬화 drift (exit 1)
# =============================================================================

def test_tampered_result_code_fails(tmp_path: Path) -> None:
    snap = _build_snapshot()
    export = _export_dict(snap)
    # result_codes 만 변조, result_hash 는 그대로 → 재유도 불일치.
    export["run"]["result_codes"] = ["005930", "035420"]
    p = _write(tmp_path, "tampered-codes.json", export)
    res = _run(p)
    assert res.returncode == 1, res.stdout
    assert "불일치" in res.stderr


def test_tampered_result_hash_fails(tmp_path: Path) -> None:
    snap = _build_snapshot()
    export = _export_dict(snap)
    export["run"]["result_hash"] = "sha256:" + "0" * 64
    p = _write(tmp_path, "tampered-hash.json", export)
    res = _run(p)
    assert res.returncode == 1, res.stdout


def test_tampered_condition_value_fails(tmp_path: Path) -> None:
    snap = _build_snapshot()
    export = _export_dict(snap)
    export["run"]["conditions"] = [{"factor": "per:trailing", "op": "<", "value": "20"}]
    p = _write(tmp_path, "tampered-cond.json", export)
    res = _run(p)
    assert res.returncode == 1, res.stdout


def test_tampered_data_version_fails(tmp_path: Path) -> None:
    snap = _build_snapshot()
    export = _export_dict(snap)
    export["run"]["data_versions"]["krx_batch_id"] = "krx-999"
    p = _write(tmp_path, "tampered-dv.json", export)
    res = _run(p)
    assert res.returncode == 1, res.stdout


# =============================================================================
# 3. 형식·입력 오류 (exit 2)
# =============================================================================

def test_wrong_export_format_marker(tmp_path: Path) -> None:
    export = _export_dict(_build_snapshot())
    export["export_format"] = "speculum-backtest-export-v1"
    p = _write(tmp_path, "wrong-marker.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout
    assert "지원하지 않는 export 형식" in res.stderr


def test_not_an_export(tmp_path: Path) -> None:
    p = _write(tmp_path, "random.json", {"hello": "world"})
    res = _run(p)
    assert res.returncode == 2, res.stdout


def test_missing_required_input(tmp_path: Path) -> None:
    export = _export_dict(_build_snapshot())
    del export["run"]["data_versions"]
    p = _write(tmp_path, "missing-dv.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout
    assert "누락" in res.stderr


def test_missing_result_hash(tmp_path: Path) -> None:
    export = _export_dict(_build_snapshot())
    del export["run"]["result_hash"]
    p = _write(tmp_path, "no-hash.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout


def test_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not valid json", encoding="utf-8")
    res = _run(p)
    assert res.returncode == 2, res.stdout


def test_invalid_as_of(tmp_path: Path) -> None:
    export = _export_dict(_build_snapshot())
    export["run"]["as_of"] = "not-a-date"
    p = _write(tmp_path, "bad-asof.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout


def test_result_codes_object_not_array_is_format_error(tmp_path: Path) -> None:
    # 구조 손상: result_codes 가 배열이 아닌 객체 → 변조(1)가 아니라 형식오류(2).
    # 타입 가드 없으면 normalize_stock_codes 가 dict 키를 순회해 오분류됨.
    export = _export_dict(_build_snapshot())
    export["run"]["result_codes"] = {"0": "005930"}
    p = _write(tmp_path, "codes-object.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout
    assert "타입 손상" in res.stderr


def test_conditions_object_not_array_is_format_error(tmp_path: Path) -> None:
    export = _export_dict(_build_snapshot())
    export["run"]["conditions"] = {"factor": "per:trailing"}
    p = _write(tmp_path, "cond-object.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout


def test_data_versions_not_object_is_format_error(tmp_path: Path) -> None:
    export = _export_dict(_build_snapshot())
    export["run"]["data_versions"] = ["krx-001"]  # 배열 — dict 여야 함.
    p = _write(tmp_path, "dv-array.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout


def test_security_types_wrong_type_is_format_error(tmp_path: Path) -> None:
    export = _export_dict(_build_snapshot())
    export["run"]["security_types"] = "common"  # str — list 여야 함.
    p = _write(tmp_path, "sectype-str.json", export)
    res = _run(p)
    assert res.returncode == 2, res.stdout


# =============================================================================
# 4. 다중 파일 — 가장 나쁜 exit code 채택
# =============================================================================

def test_multiple_files_worst_code_wins(tmp_path: Path) -> None:
    good = _write(tmp_path, "good.json", _export_dict(_build_snapshot()))
    tampered = _export_dict(_build_snapshot())
    tampered["run"]["result_codes"] = ["999999"]
    bad = _write(tmp_path, "bad.json", tampered)
    res = _run(good, bad)
    # 하나라도 불일치면 worst=1.
    assert res.returncode == 1, res.stdout
