"""금지 어휘 검사 — 8 기둥 §2.2 No Advice 의 코드 수준 implementation.

본 모듈은 사용자에게 노출되는 모든 텍스트가 "추천·매수·매도·유망" 등 매매
시그널로 해석될 수 있는 어휘를 포함하지 않음을 보증한다.

관련 ADR:
- ADR-0006 §D2 — 자본시장법 유사투자자문업 회색지대 회피의 legal-side 근거
- ADR-0007 §D4 — 금지 어휘 정책 + CI 게이트
- ADR-0007 §D4.5 — CheckScope 별 검사 정책 (system/user-private/user-shared/external)
- ADR-0007 §D9 — 어휘 변경 절차

어휘 list 의 single source of truth: `shared/forbidden-words.json` (oracle 리뷰 D1).
Python 과 TypeScript 가 빌드 시 동일 JSON 을 로드 — divergence 차단.

사용 예:
    from app.services.forbidden_words import scan_text, assert_clean, CheckScope

    # 1. 시스템 생성 텍스트 (가장 엄격)
    assert_clean(card_title, scope=CheckScope.SYSTEM)

    # 2. 사용자 본인만 보는 메모 (검사 X — by policy)
    assert_clean(user_note, scope=CheckScope.USER_PRIVATE)

    # 3. 공유되는 사용자 콘텐츠 (검사 함)
    assert_clean(shared_factor_name, scope=CheckScope.USER_SHARED)

    # 4. 외부 인용 (회사명·DART 공시 제목) — 검사 X
    assert_clean(stock_name, scope=CheckScope.EXTERNAL_QUOTE)

    # API response — 검사 제외 path 명시 가능 (회사명 등)
    scan_api_response(payload, exclude_paths={"stock_name", "company_name"})

설계 결정 (oracle 리뷰 반영):
- **SoT** (D1): 어휘는 JSON 단일 정의. 빌드 시 import.
- **Scope** (C3): 4 종 CheckScope. 호출자가 의도 명시.
- **Exclude paths** (C1): `scan_api_response` 가 종목명 등 회사 식별자 path 제외.
- **NFKC normalize** (G4): fullwidth `Ｂｕｙ` 같은 회피 차단.
- **Iterative walker** (B2): 무한 자기참조 JSON 의 RecursionError 방지.
- **Error message in ValueError**: server-side only — FastAPI exception handler 에서
  generic 500 으로 squash 필요. **클라이언트 응답 본문에 노출 금지**.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final


class ForbiddenKind(Enum):
    """금지 어휘의 분류 — ADR-0007 D4.1 / D4.2."""

    KO_ABSOLUTE = "ko-absolute"
    EN_ABSOLUTE = "en-absolute"


class CheckScope(Enum):
    """검사 scope — ADR-0007 D4.5 (oracle 리뷰 C3).

    SYSTEM: 시스템 생성 텍스트 (UI 라벨, 카드 제목, 자동 메시지 등). 가장 엄격.
    USER_PRIVATE: 사용자 본인만 보는 텍스트 (Watchlist 메모 M0). 검사 X (정책).
    USER_SHARED: 다른 사용자에게 공유되는 텍스트 (Factor Lab pack M2). 검사 함.
    EXTERNAL_QUOTE: 외부 인용 (회사명, DART 공시 제목, ETF 이름). 검사 X — 종목
        이름이 "이베스트투자증권" 처럼 금지 어휘 substring 을 포함해도 사실 표시.
    """

    SYSTEM = "system"
    USER_PRIVATE = "user-private"
    USER_SHARED = "user-shared"
    EXTERNAL_QUOTE = "external-quote"


# =============================================================================
# Vocabulary loading — single source of truth
# =============================================================================

_VOCAB_PATH: Final[Path] = Path(__file__).resolve().parents[3] / "shared" / "forbidden-words.json"


def _load_vocab() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """`shared/forbidden-words.json` 에서 (ko, en, allowed) 어휘 로드.

    SoT 가 단일 JSON 이므로 양 언어 구현이 분기 불가. 빌드 시 1 회 로드.
    """
    with _VOCAB_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return (
        tuple(data["ko_absolute"]),
        tuple(data["en_absolute"]),
        tuple(data["allowed_phrases"]),
    )


_KO_FORBIDDEN_RAW, _EN_FORBIDDEN_RAW, _ALLOWED_PHRASES = _load_vocab()


@dataclass(frozen=True, slots=True)
class Match:
    """금지 어휘 발견 결과.

    Attributes:
        word: 검출된 원문 어휘 (정규화 후 — NFKC).
        canonical: 등록된 표준형 (예: "Buy" — 사용자 입력은 "BUY" / "Ｂｕｙ" 였더라도).
        kind: 분류 (한국어 / 영문).
        index: 정규화된 텍스트에서의 시작 인덱스 (0-based, 문자 단위).
    """

    word: str
    canonical: str
    kind: ForbiddenKind
    index: int


# =============================================================================
# Pattern building
# =============================================================================

def _build_korean_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """한국어 패턴 — 단어 경계 없이 substring 매치.

    한국어는 \\b 가 작동하지 않으므로 substring + 화이트리스트로 false positive 차단.
    길이 내림차순 + 같은 길이는 codepoint 순으로 안정 정렬.
    """
    sorted_words = sorted(set(words), key=lambda w: (-len(w), w))
    escaped = [re.escape(w) for w in sorted_words]
    return re.compile("|".join(escaped))


def _build_english_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """영문 패턴 — \\b 단어 경계 + 대소문자 무시.

    "buy" 가 "buyer", "buyout" 같은 정상 단어를 잡지 않도록.
    """
    sorted_words = sorted(set(words), key=lambda w: (-len(w), w))
    escaped = [re.escape(w) for w in sorted_words]
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


_KO_PATTERN: Final[re.Pattern[str]] = _build_korean_pattern(_KO_FORBIDDEN_RAW)
_EN_PATTERN: Final[re.Pattern[str]] = _build_english_pattern(_EN_FORBIDDEN_RAW)


# =============================================================================
# Allowed-phrase span detection (false positive 차단)
# =============================================================================

def _build_allowed_spans(text: str) -> list[tuple[int, int]]:
    """텍스트 내 화이트리스트 표현이 차지하는 [start, end) span 리스트.

    같은 위치의 매치가 이 span 안에 있으면 무시. 화이트리스트는 대소문자 구분 — JSON
    에 입력된 그대로 (예: "Buy-side" 와 "buy-side" 는 다름).
    """
    spans: list[tuple[int, int]] = []
    for phrase in _ALLOWED_PHRASES:
        start = 0
        while (idx := text.find(phrase, start)) != -1:
            spans.append((idx, idx + len(phrase)))
            start = idx + 1
    return spans


def _is_inside_any(index: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= index and end <= e for s, e in spans)


# =============================================================================
# Public API
# =============================================================================

def normalize(text: str) -> str:
    """NFKC 정규화 — fullwidth `Ｂｕｙ` → `Buy`, 합성 문자 변환 등 (oracle G4).

    호출자가 직접 사용할 일은 거의 없음 — scan_text 가 내부적으로 적용.
    """
    return unicodedata.normalize("NFKC", text)


def scan_text(text: str) -> list[Match]:
    """주어진 텍스트에서 금지 어휘 모두 검출 (등장 순서).

    내부적으로 NFKC normalize 후 검사. 반환되는 Match.index 는 normalize 된
    텍스트 기준. NFKC 가 일반적으로 길이를 보존 (fullwidth → halfwidth 는 같은
    code unit count) 하므로 대부분의 경우 원본 index 와 일치.
    """
    if not text:
        return []

    text = normalize(text)
    allowed_spans = _build_allowed_spans(text)
    results: list[Match] = []

    # 한국어 매치 — substring
    for m in _KO_PATTERN.finditer(text):
        if _is_inside_any(m.start(), m.end(), allowed_spans):
            continue
        results.append(
            Match(
                word=m.group(0),
                canonical=m.group(0),
                kind=ForbiddenKind.KO_ABSOLUTE,
                index=m.start(),
            )
        )

    # 영문 매치 — 단어 경계, case-insensitive
    for m in _EN_PATTERN.finditer(text):
        if _is_inside_any(m.start(), m.end(), allowed_spans):
            continue
        canonical = next(
            (w for w in _EN_FORBIDDEN_RAW if w.lower() == m.group(0).lower()),
            m.group(0),
        )
        results.append(
            Match(
                word=m.group(0),
                canonical=canonical,
                kind=ForbiddenKind.EN_ABSOLUTE,
                index=m.start(),
            )
        )

    results.sort(key=lambda r: r.index)
    return results


# =============================================================================
# Recursive JSON walker (iterative — oracle B2)
# =============================================================================

_DEFAULT_MAX_DEPTH: Final[int] = 64


def scan_api_response(
    payload: object,
    *,
    exclude_paths: frozenset[str] | None = None,
    max_depth: int = _DEFAULT_MAX_DEPTH,
) -> list[Match]:
    """API response 의 모든 문자열 값을 검사.

    Args:
        payload: dict / list / nested 자료구조.
        exclude_paths: 검사 제외 dict key 이름 set (oracle 리뷰 C1).
            예: {"stock_name", "company_name", "name_en"} — 종목 식별자는 검사 X.
            본 set 의 key 가 등장하는 dict 의 직접 자식은 검사 안 함.
        max_depth: 재귀 깊이 한계 (oracle B2). 초과 시 ValueError.

    Returns:
        Match list (모든 발견 등장 순서).

    Raises:
        ValueError: max_depth 초과 시. 무한 자기참조 JSON 방어.
    """
    if exclude_paths is None:
        exclude_paths = frozenset()

    matches: list[Match] = []
    # Stack-based iterative walker — 깊은 nesting 의 RecursionError 방지.
    # Element = (node, current_depth)
    stack: list[tuple[object, int]] = [(payload, 0)]

    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(
                f"Max recursion depth {max_depth} exceeded — possible self-referential JSON"
            )

        if isinstance(node, str):
            matches.extend(scan_text(node))
        elif isinstance(node, dict):
            for key, value in node.items():
                # 제외 path 의 직접 자식은 push 안 함.
                if key in exclude_paths:
                    continue
                stack.append((value, depth + 1))
        elif isinstance(node, (list, tuple, set, frozenset)):
            for item in node:
                stack.append((item, depth + 1))
        # 그 외 primitives 는 무시

    return matches


def assert_clean(
    text: str,
    *,
    scope: CheckScope = CheckScope.SYSTEM,
    context: str = "",
) -> None:
    """텍스트가 깨끗하지 않으면 ValueError.

    Args:
        text: 검사할 텍스트.
        scope: 검사 정책 (CheckScope 참조). USER_PRIVATE / EXTERNAL_QUOTE 는 검사 skip.
        context: 에러 메시지의 디버깅용 컨텍스트 (예: "stock_detail.card.title").
            **호출자는 사용자 입력을 그대로 context 로 넘기지 말 것** (oracle B4).

    Raises:
        ValueError: 금지 어휘 검출 시.

    Note (oracle B4):
        본 함수의 ValueError 메시지는 **server-side 로그·alert 용**. FastAPI
        exception handler 에서 generic 500 응답으로 squash 해야 함 —
        클라이언트 응답 본문에 직접 노출 금지 (응답 자체가 금지 어휘를 echo).
    """
    if scope in (CheckScope.USER_PRIVATE, CheckScope.EXTERNAL_QUOTE):
        return

    matches = scan_text(text)
    if matches:
        details = ", ".join(f"{m.word!r}@{m.index}" for m in matches)
        # context 의 control character 제거 (log injection 방어).
        safe_context = re.sub(r"[\x00-\x1f]", "?", context) if context else ""
        suffix = f" [{safe_context}]" if safe_context else ""
        raise ValueError(
            f"Forbidden words detected{suffix} (scope={scope.value}): {details}"
        )
