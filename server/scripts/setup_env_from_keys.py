"""keys.txt(repo 루트) → server/.env 생성 — 실데이터 적재용 API 키 셋업.

사용자가 받은 발급 키를 `keys.txt`(repo 루트)에 출처별로 적어두면, 본 스크립트가
`server/.env`(gitignore 됨)에 표준 환경변수로 기록한다. 서버/배치/스크립트가
`app.core.config._load_dotenv_files` 로 `.env` 를 자동 로드하므로, 이후
`python -m batch.scheduler` 가 DART/ECOS/KOSIS/FSC 키를 인식한다.

keys.txt 형식(출처 URL 라벨 + 다음 줄에 키):
    opendart.fss.or.kr        ← DART
    <DART 인증키>
    ecos.bok.or.kr            ← ECOS
    <ECOS 인증키>
    kosis.kr/openapi          ← KOSIS
    <KOSIS 인증키>
    data.go.kr                ← FSC(금융위)
    <설명/URL ...>
    <FSC 서비스키>

설계:
- **키 값 미출력** — 마스킹된 확인만 stdout(로그/CI 에 키 누출 방지).
- 라벨 매칭(라인 번호 비의존) — 각 출처 라벨 다음의 "키스러운" 줄(URL/한글/라벨
  아닌 영숫자)을 키로 채택.
- 누락 키는 .env 에서 생략(없는 키는 적재 시 해당 factor N/A). 전부 누락이면 실패.
- 기존 server/.env 의 비-API-KEY 라인(SPECULUM_DATABASE_URL 등)은 보존하고 키만 갱신.

실행: `cd server && python -m scripts.setup_env_from_keys`
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ -> server/ -> repo 루트.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_KEYS_TXT = _REPO_ROOT / "keys.txt"
_ENV_PATH = _REPO_ROOT / "server" / ".env"

# 출처 라벨 → 환경변수명. 라벨 substring 으로 매칭.
_LABEL_TO_VAR: tuple[tuple[str, str], ...] = (
    ("opendart", "DART_API_KEY"),
    ("ecos.bok", "ECOS_API_KEY"),
    ("kosis", "KOSIS_API_KEY"),
    ("data.go.kr", "FSC_API_KEY"),
)


def _is_korean(s: str) -> bool:
    """한글 포함 여부 — 라벨/설명 줄을 키 후보에서 제외."""
    return any(
        0xAC00 <= ord(c) <= 0xD7A3 or 0x3130 <= ord(c) <= 0x318F for c in s
    )


def _looks_like_key(s: str) -> bool:
    """키 후보 판정 — URL/도메인/한글/빈 줄이 아닌 영숫자 토큰."""
    if not s or s.lower().startswith("http"):
        return False
    if ".kr" in s.lower() or ".go.kr" in s.lower() or _is_korean(s):
        return False
    # 키는 보통 길고 영숫자(+/=) 위주.
    return len(s) >= 8


def _key_after(lines: list[str], label_substr: str) -> str:
    """label_substr 를 포함한 줄 다음의 첫 키 후보를 반환(없으면 '')."""
    low = label_substr.lower()
    for i, line in enumerate(lines):
        if low in line.lower():
            for j in range(i + 1, len(lines)):
                if _looks_like_key(lines[j]):
                    return lines[j]
    return ""


def _mask(value: str) -> str:
    """키 값 마스킹(앞4·뒤2·길이) — 누출 방지."""
    if not value:
        return "(없음)"
    return f"{value[:4]}…{value[-2:]}(len={len(value)})" if len(value) > 8 else value


def main() -> int:
    if not _KEYS_TXT.is_file():
        print(f"[setup-env] keys.txt 없음: {_KEYS_TXT}", file=sys.stderr)
        print(
            "  repo 루트에 keys.txt 를 만들고 출처 라벨 + 키를 적으세요 "
            "(opendart.fss.or.kr / ecos.bok.or.kr / kosis.kr/openapi / data.go.kr).",
            file=sys.stderr,
        )
        return 1

    lines = [
        ln.strip()
        for ln in _KEYS_TXT.read_text(encoding="utf-8", errors="replace").splitlines()
    ]

    resolved: dict[str, str] = {}
    for label, var in _LABEL_TO_VAR:
        key = _key_after(lines, label)
        if key:
            resolved[var] = key
        print(f"  {var:16s}: {_mask(key)}")

    if not resolved:
        print(
            "[setup-env] keys.txt 에서 키를 하나도 찾지 못함 — 형식 확인 필요.",
            file=sys.stderr,
        )
        return 1

    # 기존 .env 의 비-키 라인(SPECULUM_DATABASE_URL 등) 보존, API 키만 갱신.
    preserved: list[str] = []
    if _ENV_PATH.is_file():
        for ln in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = ln.strip()
            if "=" not in stripped or stripped.startswith("#"):
                continue
            name = stripped.split("=", 1)[0].strip()
            if name not in {v for _, v in _LABEL_TO_VAR}:
                preserved.append(ln)

    body_lines = [
        "# server/.env — scripts.setup_env_from_keys 가 keys.txt 에서 생성.",
        "# gitignore 됨. 절대 커밋 금지.",
    ]
    body_lines += [f"{var}={val}" for var, val in resolved.items()]
    body_lines += preserved
    _ENV_PATH.write_text("\n".join(body_lines) + "\n", encoding="utf-8")

    print(f"[setup-env] {_ENV_PATH} 생성/갱신 완료 (키 {len(resolved)}종).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
