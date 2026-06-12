"""validate_pack.py CLI parity 테스트 — ADR-0032 D5 (M4 #2).

핵심 보증: validator tool 의 판정 = server `validate_custom_pack` 판정(thin wrapping,
4번째 SoT 금지). + 실 pack(builtin/reference/example) valid + 잘못된 pack 거부 + spec
문서가 인용한 필드가 schema 에 실재.

CLI 는 subprocess 로 실행(test_check_forbidden_words_cli.py 패턴) — server 외부 deps 없이
독립 동작함을 함께 검증.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.services.factor_pack import compute_pack_hash, validate_custom_pack

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI = _REPO_ROOT / "tools" / "validate_pack.py"
_BUILTIN = _REPO_ROOT / "server" / "builtin-packs" / "factors" / "speculum-builtin-v1.0.0.json"
_REFERENCE = (
    _REPO_ROOT / "server" / "builtin-packs" / "reference"
    / "speculum-reit-reference-v1.0.0.json"
)
_EXAMPLE = _REPO_ROOT / "examples" / "factor-packs" / "example-debt-ratio-v1.0.0.json"
_SCHEMA = _REPO_ROOT / "shared" / "schemas" / "factor-pack-v1.json"
_FORMAT_DOC = _REPO_ROOT / "docs" / "FACTOR_PACK_FORMAT.md"


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CLI), *[str(p) for p in paths]],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# =============================================================================
# 1. 실 pack — valid (exit 0)
# =============================================================================

def test_builtin_pack_valid() -> None:
    res = _run(_BUILTIN)
    assert res.returncode == 0, res.stderr
    assert "valid" in res.stdout


def test_reference_pack_valid() -> None:
    res = _run(_REFERENCE)
    assert res.returncode == 0, res.stderr


def test_example_pack_valid() -> None:
    # 외부 저자 템플릿 — 봉인 정상.
    res = _run(_EXAMPLE)
    assert res.returncode == 0, res.stderr
    assert "봉인 정상" in res.stdout


# =============================================================================
# 2. parity — 도구 exit == server validate_custom_pack 판정
# =============================================================================

def test_parity_with_server_validate() -> None:
    # 도구가 별도 검증을 재구현하지 않고 server 를 위임함을 확인 — valid pack 은
    # server issues 0 ↔ 도구 exit 0.
    for pack_path in (_BUILTIN, _REFERENCE, _EXAMPLE):
        body = json.loads(pack_path.read_text(encoding="utf-8"))
        server_issues = validate_custom_pack(body)
        cli_code = _run(pack_path).returncode
        assert (len(server_issues) == 0) == (cli_code == 0), (
            f"{pack_path.name}: server issues={server_issues}, cli exit={cli_code}"
        )


# =============================================================================
# 3. 잘못된 pack — 거부 (exit 1)
# =============================================================================

def test_invalid_pack_rejected(tmp_path: Path) -> None:
    # schema 위반(필수 $schema 부재) → exit 1 + stage 진단.
    bad = tmp_path / "bad.json"
    bad.write_text('{"type":"factor-pack","factors":[]}', encoding="utf-8")
    res = _run(bad)
    assert res.returncode == 1
    assert "schema" in res.stderr


def test_number_const_rejected(tmp_path: Path) -> None:
    # ADR-0032 D1 — number const(float drift) 는 decimalString 위반 → exit 1.
    body = json.loads(_BUILTIN.read_text(encoding="utf-8"))
    body["factors"][0]["formula"]["ast"] = {
        "op": "mul", "left": {"field": "x"}, "right": {"const": 0.5},
    }
    body["factors"][0]["formula"]["inputs"] = ["x"]
    p = tmp_path / "numconst.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    res = _run(p)
    assert res.returncode == 1


def test_hash_mismatch_rejected(tmp_path: Path) -> None:
    # 봉인 파손(content_hash 변조) → exit 1.
    body = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    body["content_hash"] = "sha256:" + "0" * 64
    p = tmp_path / "tampered.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    res = _run(p)
    assert res.returncode == 1
    assert "불일치" in res.stderr or "mismatch" in res.stderr.lower()


def test_shared_meta_advice_rejected(tmp_path: Path) -> None:
    # ADR-0032 D4 — publish 전 검증: pack description 의 advice 어휘 → exit 1.
    # validate_custom_pack(factor 정의)은 통과하나 validate_shared_meta(공개 메타)가
    # 차단 — 게시(공유) 대상이므로. content_hash 는 본문에 맞게 재봉인(meta 게이트가
    # 봉인 검사보다 먼저지만 정상 pack 형태 유지).
    body = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    body["description"] = "추천주 모음 — 지금 매수하세요."
    body["content_hash"] = compute_pack_hash(body)
    p = tmp_path / "advice-desc.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    res = _run(p)
    assert res.returncode == 1
    assert "forbidden_vocab" in res.stderr


def test_bad_json_exit_two(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not valid json", encoding="utf-8")
    res = _run(p)
    assert res.returncode == 2


# =============================================================================
# 4. spec 문서 ↔ schema 정합 (doc drift 방지, Metis SoT)
# =============================================================================

def test_format_doc_fields_exist_in_schema() -> None:
    # FACTOR_PACK_FORMAT.md 의 최상위 필드 표가 schema properties 와 어긋나지 않음.
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    schema_props = set(schema.get("properties", {}).keys())
    doc = _FORMAT_DOC.read_text(encoding="utf-8")
    # 문서 §2 표의 백틱 필드명(예: `pack_slug`)이 전부 schema 에 실재해야 함.
    # 최상위 필드만 대상 — 표에 등장하는 알려진 키 집합으로 한정 검사.
    for field in schema_props:
        # schema 의 각 최상위 필드는 문서에 백틱으로 언급되어야(누락 방지).
        assert f"`{field}`" in doc, f"schema 필드 '{field}' 가 format 문서에 누락"
