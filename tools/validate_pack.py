"""Factor pack open-format validator — ADR-0032 D5 (M4 #2).

외부 저자(블로거·연구자)가 factor pack 을 git 에 올리기 전에 **로컬에서 검증**하는
standalone CLI. server 의 `app.services.factor_pack` 검증을 **thin wrapping** 한다 —
재구현 0 (4번째 SoT 금지, ADR-0032 D5). 따라서 본 도구의 판정 = server validate 판정.

검증 단계(server `validate_custom_pack` 위임):
    1. schema   — `shared/schemas/factor-pack-v1.json` 구조(+ ADR-0032 D1 decimalString)
    2. identity — canonical_id / uuid 중복
    3. acyclic  — factor 의존 그래프 정적 순환
    4. citation — pack / factor-level citation 의무
    5. forbidden_vocab — factor name / description 금지 어휘(§2.2 No Advice)

추가로 **content_hash 재계산**(ADR-0032 D1 JCS recipe) 후:
    - 선언된 content_hash 없음 → 계산값을 "이 값으로 봉인하세요" 안내 (exit 0).
    - 선언값 == 계산값 → 정상 봉인 (exit 0).
    - 선언값 != 계산값 → **봉인 파손**(body 변조/손상) → exit 1.

사용:
    python tools/validate_pack.py my-pack.json [pack2.json ...]

Exit code:
    0 — 모든 pack valid + (봉인 정상 또는 미봉인).
    1 — 검증 이슈 또는 content_hash 불일치(봉인 파손).
    2 — 입력/IO/JSON 파싱 오류.

설계(check_forbidden_words.py 패턴 일관):
    - CLI script 단독 — server 의 sqlalchemy 등 무거운 deps 불필요. `app.services.
      factor_pack`(+ `_jcs`/`forbidden_words`/`factor_pack_identity`) 만 import.
    - sys.path 에 server 추가 후 lazy import.

관련: ADR-0032 D1/D5, `docs/FACTOR_PACK_FORMAT.md`(사람용 사양), ADR-0022/0028.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _import_factor_pack() -> Any:
    """server `app.services.factor_pack` lazy import (sys.path 셋업).

    check_forbidden_words.py 와 동일 — CLI 가 server deps 와 분리되도록 server
    디렉토리를 sys.path 에 추가 후 import. factor_pack 은 _jcs / forbidden_words /
    factor_pack_identity 만 의존(sqlalchemy 등 무관).
    """
    repo_root = Path(__file__).resolve().parent.parent
    server_dir = repo_root / "server"
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    from app.services import factor_pack  # noqa: PLC0415

    return factor_pack


def _validate_one(path: Path, fp: Any) -> int:
    """단일 pack 파일 검증 → exit code(0 valid / 1 invalid / 2 IO·parse).

    출력은 사람이 읽는 file:stage:message 형식 + content_hash 진단.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{path}: error: 파일 읽기 실패 — {exc}", file=sys.stderr)
        return 2
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"{path}: error: JSON 파싱 실패 — {exc}", file=sys.stderr)
        return 2
    if not isinstance(body, dict):
        print(f"{path}: error: pack 최상위가 JSON object 가 아님", file=sys.stderr)
        return 2

    # 1~5단계 server 검증 위임(thin wrapping).
    issues = fp.validate_custom_pack(body)
    if issues:
        for issue in issues:
            print(f"{path}: {issue.stage}: {issue.message}", file=sys.stderr)
        return 1

    # 공유 메타 게이트(ADR-0032 D4) — 게시(공유) 대상 pack 이므로 citation.title /
    # description 의 advice 어휘를 publish 전에 차단(import 게이트와 동일 기준).
    try:
        fp.validate_shared_meta(body)
    except fp.ForbiddenVocabInPack:
        # 어휘 echo 0(forbidden_words B4) — 일반 메시지.
        print(
            f"{path}: forbidden_vocab: 공유 메타(제목/설명)에 금지 어휘가 있습니다.",
            file=sys.stderr,
        )
        return 1

    # content_hash 재계산(ADR-0032 D1 JCS recipe) — 봉인 진단.
    computed = fp.compute_pack_hash(body)
    declared = body.get("content_hash")
    if declared is None:
        print(f"{path}: valid — 미봉인. content_hash 를 다음 값으로 설정하세요:")
        print(f"  {computed}")
        return 0
    if declared != computed:
        print(
            f"{path}: error: content_hash 불일치(봉인 파손) — "
            f"선언={declared} 계산={computed}",
            file=sys.stderr,
        )
        return 1
    print(f"{path}: valid — 봉인 정상 ({computed}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Factor pack open-format validator (ADR-0032) — schema/identity/"
            "acyclic/citation/forbidden-vocab + content_hash 재계산."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="검증할 factor pack JSON 파일(들).",
    )
    args = parser.parse_args(argv)

    fp = _import_factor_pack()

    worst = 0
    for raw in args.paths:
        code = _validate_one(Path(raw), fp)
        # 가장 나쁜 exit code 채택(2 > 1 > 0 — 우선순위는 IO > invalid > valid).
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    # Windows CMD 의 CP949 codec 이 한글 출력 시 UnicodeEncodeError → UTF-8 강제
    # (check_forbidden_words.py 와 동일).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
