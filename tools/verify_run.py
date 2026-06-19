"""Screen Run export 무결성 검증 CLI — ADR-0033 / M5 T-M5-03c.

운영 배포된 self-identifying Screen Run export(`GET /api/runs/{id}/export` 결과,
`speculum-screen-run-export-v1`)를 **오프라인에서 검증**하는 standalone CLI. export
JSON 의 선언된 `result_hash` 가 그 안에 동봉된 입력(query/as_of/result_codes/
data_versions)으로부터 **재유도한 값과 일치**하는지 확인한다.

재유도는 server `app.services.screen_run.ScreenRunBuilder.build` 를 **그대로 재사용**
한다 — hash recipe 를 재구현하지 않는다(단일 SoT, ADR-0032 D5 와 동일 원칙).
ScreenRunBuilder 가 conditions/result_codes 를 canonical 정규화(정렬·dedup·zero-pad)
후 `JCS-SHA256(query, as_of, result_codes, data_versions)` 로 result_hash 를 계산하므로,
export 의 입력을 build 에 재투입하면 같은 입력이면 같은 hash 가 나온다(정규화는 멱등).

검증 의미와 **한계**(과대 주장 금지 — ADR-0033 #3c):
    - 탐지 가능: result_codes / conditions / as_of / data_versions 중 **일부만**
      변조되었거나 직렬화 drift 가 생긴 경우 → 선언 hash 와 재유도 hash 불일치.
    - 탐지 불가: 입력과 result_hash 를 **함께(일관되게) 재계산**해 위조한 경우는
      내부 정합이 맞아 통과한다. 즉 본 도구는 artifact 의 **내부 정합성**(hash ↔ 입력)
      만 보증하며, result_codes 가 실제 스크리닝 결과로 **옳은지**는 보증하지 않는다
      (그건 frozen batch_id 로 재실행하는 `POST /api/runs/reproduce` = `matches` 의 몫).

사용:
    python tools/verify_run.py run-export.json [export2.json ...]

Exit code:
    0 — 모든 export 의 result_hash 가 동봉 입력과 일치(내부 정합 정상).
    1 — 하나 이상 불일치(부분 변조 / 직렬화 drift).
    2 — 입력/IO/JSON/형식 오류(검증 자체 불가).

설계(validate_pack.py / check_forbidden_words.py 패턴 일관):
    - CLI script 단독 — server 디렉토리를 sys.path 에 추가 후 lazy import.
    - hash 재계산은 screen_run(→ _jcs)에 위임. screen_run 은 import 시 snapshot_versions
      를 끌어오나, 본 도구는 export 의 data_versions 를 항상 주입하므로
      `collect_active_policy_versions()`(DB 접근) 는 호출되지 않는다(import 만 로드).

관련: ADR-0033(reproducibility-trust-verification), M5_PLAN #3 T-M5-03c, ADR-0021 §4.1
(self-identifying export), `server/app/api/routes/runs.py`(export/reproduce route).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

# 재유도 시 hash 입력에 영향 없는 식별자(run_id/user_id)는 결정적 더미로 고정.
# result_hash 입력 = {query, as_of, result_codes, data_versions} 뿐이므로(screen_run.py:
# 350-357) id/user 는 hash 와 무관 — env(_resolve_system_user_id) 의존도 회피.
_DUMMY_ID = UUID(int=0)


def _import_server_deps() -> tuple[Any, str]:
    """server `screen_run`(hash SoT) + export 마커(형식 SoT) lazy import.

    validate_pack.py 와 동일 패턴 — CLI 가 repo 루트에서 실행되도록 server 디렉토리를
    sys.path 에 추가 후 import. 마커를 하드코딩하지 않고 `app.schemas.screen` 에서
    가져와 server 와의 drift 를 차단(schemas.screen 은 screen_run 을 거쳐 이미 로드되는
    의존이라 추가 비용 0). screen_run 은 result_hash recipe 의 단일 SoT.
    """
    repo_root = Path(__file__).resolve().parent.parent
    server_dir = repo_root / "server"
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    from app.schemas.screen import EXPORT_FORMAT_MARKER  # noqa: PLC0415
    from app.services import screen_run  # noqa: PLC0415

    return screen_run, EXPORT_FORMAT_MARKER


def _extract_run(body: Any, path: Path, marker: str) -> dict[str, Any] | int:
    """입력 JSON 에서 검증 대상 run dict 추출 → run dict 또는 exit code(2).

    두 형식 수용(runs.py reproduce 의 lax 입력과 동일 관용):
        - export wrapper: `{"export_format": "...-v1", "run": {...}}` → run 추출.
        - run 직접: `{"result_hash": ..., ...}` (export 의 `run` 부분만 paste).
    """
    if not isinstance(body, dict):
        print(f"{path}: error: 최상위가 JSON object 가 아님", file=sys.stderr)
        return 2

    # export wrapper — 마커 검증 후 run 추출.
    if "export_format" in body:
        declared_marker = body.get("export_format")
        if declared_marker != marker:
            print(
                f"{path}: error: 지원하지 않는 export 형식 — "
                f"export_format={declared_marker!r} (기대: {marker!r})",
                file=sys.stderr,
            )
            return 2
        run = body.get("run")
        if not isinstance(run, dict):
            print(f"{path}: error: export 에 'run' object 가 없음", file=sys.stderr)
            return 2
        return run

    # run 직접 입력 — result_hash 존재로 식별.
    if "result_hash" in body:
        return body

    print(
        f"{path}: error: Screen Run export 가 아님 "
        f"('export_format' 또는 'result_hash' 키 없음)",
        file=sys.stderr,
    )
    return 2


def _verify_one(path: Path, sr: Any, marker: str) -> int:
    """단일 export 파일 검증 → exit code(0 일치 / 1 불일치 / 2 IO·형식).

    출력은 사람이 읽는 file: message 형식. 불일치 시 선언/재유도 hash 를 모두 표시.
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

    run = _extract_run(body, path, marker)
    if isinstance(run, int):  # 추출 실패 → exit code.
        return run

    # 필수 입력 추출 — 하나라도 없으면 재유도 불가(검증 자체 불가 → exit 2).
    declared_hash = run.get("result_hash")
    if not isinstance(declared_hash, str) or not declared_hash:
        print(f"{path}: error: 선언된 result_hash 없음 — 검증 불가", file=sys.stderr)
        return 2

    missing = [
        key
        for key in ("as_of", "result_codes", "conditions", "selected_factors", "data_versions")
        if key not in run
    ]
    if missing:
        print(
            f"{path}: error: 재유도 입력 누락 {missing} — 검증 불가",
            file=sys.stderr,
        )
        return 2

    # 구조 타입 검증 — 키 존재만으로는 부족(키는 있으나 타입이 손상된 artifact).
    # 예: result_codes 가 배열이 아닌 JSON object({"0":"005930"})면 키-존재 검사를
    # 통과하고 normalize_stock_codes 가 dict 의 *키*('0')를 순회해 엉뚱한 코드로
    # 재유도 → 형식오류(2)가 아니라 hash 불일치(1='변조')로 오분류된다. 무결성
    # 도구로서 "구조 손상"과 "변조"를 구분하려면 재유도 전에 타입을 확정해야 한다.
    # (배열 → list, JSON object → dict 로 파싱됨.)
    _list_fields = ("result_codes", "conditions", "selected_factors")
    bad_type = [key for key in _list_fields if not isinstance(run[key], list)]
    if not isinstance(run["data_versions"], dict):
        bad_type.append("data_versions")
    # security_types 는 optional — 있으면 list 여야 함(None 은 builder default 허용).
    if run.get("security_types") is not None and not isinstance(
        run["security_types"], list
    ):
        bad_type.append("security_types")
    if bad_type:
        print(
            f"{path}: error: 재유도 입력 타입 손상 {bad_type} "
            f"(list/object 기대) — 검증 불가",
            file=sys.stderr,
        )
        return 2

    # as_of 파싱 — date 형식 오류는 재유도 불가(검증 자체 불가).
    try:
        as_of = date.fromisoformat(str(run["as_of"]))
    except ValueError as exc:
        print(f"{path}: error: as_of 형식 오류 — {exc}", file=sys.stderr)
        return 2

    # security_types 는 구 export 에 부재 가능 → None 이면 builder 가 default
    # ("common",) 적용(원 run 도 동일하게 build 됐으므로 hash 일치). (ADR-0023 D7)
    security_types = run.get("security_types")

    # result_hash 재유도 — ScreenRunBuilder 단일 SoT 재사용. 입력 정규화(정렬·dedup·
    # zero-pad)는 멱등이라 이미 canonical 한 export 입력을 재투입해도 동일 hash.
    # data_versions 의 non-str 값 등 malformed artifact 는 build 가 ValueError →
    # 검증 자체 불가(exit 2)로 분류(불일치[1]는 "재유도 성공 후 hash 다름"에 한정).
    try:
        rebuilt = sr.ScreenRunBuilder.build(
            run_id=_DUMMY_ID,
            user_id=_DUMMY_ID,
            conditions=run["conditions"],
            selected_factors=run["selected_factors"],
            security_types=security_types,
            as_of=as_of,
            result_codes=run["result_codes"],
            data_versions=run["data_versions"],
        )
    except (ValueError, TypeError, AttributeError) as exc:
        print(
            f"{path}: error: 입력으로 result_hash 재유도 실패(artifact 손상 가능) — {exc}",
            file=sys.stderr,
        )
        return 2

    if rebuilt.result_hash != declared_hash:
        print(
            f"{path}: 불일치 — result_hash 가 동봉 입력과 다릅니다(부분 변조/직렬화 drift).\n"
            f"  선언={declared_hash}\n"
            f"  재유도={rebuilt.result_hash}",
            file=sys.stderr,
        )
        return 1

    print(f"{path}: 정합 정상 — result_hash 가 동봉 입력과 일치 ({declared_hash}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Screen Run export 무결성 검증(ADR-0033 / M5) — 선언 result_hash 가 동봉 "
            "입력(query/as_of/result_codes/data_versions)으로부터 재유도한 값과 일치하는지 "
            "확인(부분 변조·직렬화 drift 탐지). reproduce(재실행) 와 달리 DB·데이터 불요."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="검증할 Screen Run export JSON 파일(들).",
    )
    args = parser.parse_args(argv)

    sr, marker = _import_server_deps()

    worst = 0
    for raw in args.paths:
        code = _verify_one(Path(raw), sr, marker)
        # 가장 나쁜 exit code 채택(2 > 1 > 0 — IO·형식 > 불일치 > 정상).
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    # Windows CMD 의 CP949 codec 이 한글 출력 시 UnicodeEncodeError → UTF-8 강제
    # (validate_pack.py / check_forbidden_words.py 와 동일).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
