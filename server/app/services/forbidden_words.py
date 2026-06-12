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
    """금지 어휘의 분류 — ADR-0007 D4.1 / D4.2 + ADR-0031 D2(sentiment)."""

    KO_ABSOLUTE = "ko-absolute"
    EN_ABSOLUTE = "en-absolute"
    # sentiment(가치판단) 어휘 — LLM 출력 게이트 전용(opt-in). absolute 와 분리해
    # conformance CLI/미들웨어 기본 검사에서 제외(ADR-0031 D2 scope 분리).
    KO_SENTIMENT = "ko-sentiment"
    EN_SENTIMENT = "en-sentiment"


class ForbiddenWordsAssertError(ValueError):
    """`assert_clean` 의 raise 전용 sub-class — Momus M0 review V12/W6.

    기존 ValueError catch 와 backward compat (ValueError 상속) + FastAPI
    exception handler 가 본 sub-class 만 명시 catch → generic 500 응답 + 어휘
    echo 차단. ValueError 의 message attr 는 server-side 로그용 어휘 echo
    유지, `.matches` / `.scope` / `.context` attribute 가 audit 용.

    호출자 직접 catch 시 `except ValueError` 또는 `except
    ForbiddenWordsAssertError` 모두 작동.
    """

    def __init__(
        self,
        message: str,
        *,
        matches: tuple[Match, ...],
        scope: CheckScope,
        context: str = "",
    ) -> None:
        super().__init__(message)
        # server-side audit 용 — handler 가 본 attribute 로 generic 500 응답
        # 생성 + 별도 log line 으로 detail 기록.
        self.matches: Final[tuple[Match, ...]] = matches
        self.scope: Final[CheckScope] = scope
        self.context: Final[str] = context


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


def _load_vocab() -> tuple[
    tuple[str, ...], tuple[str, ...], tuple[str, ...],
    tuple[str, ...], tuple[str, ...],
]:
    """`shared/forbidden-words.json` 에서 (ko_abs, en_abs, allowed, ko_sent, en_sent) 로드.

    SoT 가 단일 JSON 이므로 양 언어 구현이 분기 불가. 빌드 시 1 회 로드.
    sentiment 키는 ADR-0031 D2 추가분 — 구버전 JSON 호환 위해 `.get` 기본 빈 list.
    """
    with _VOCAB_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return (
        tuple(data["ko_absolute"]),
        tuple(data["en_absolute"]),
        tuple(data["allowed_phrases"]),
        tuple(data.get("ko_sentiment", ())),
        tuple(data.get("en_sentiment", ())),
    )


(
    _KO_FORBIDDEN_RAW,
    _EN_FORBIDDEN_RAW,
    _ALLOWED_PHRASES,
    _KO_SENTIMENT_RAW,
    _EN_SENTIMENT_RAW,
) = _load_vocab()


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


def _build_optional_korean_pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    """빈 list 면 None — 빈 패턴(`""`)이 every-position 빈 매치를 내는 것을 방지."""
    return _build_korean_pattern(words) if words else None


def _build_optional_english_pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    """빈 list 면 None — 빈 패턴(`\\b(?:)\\b`)의 부작용 방지."""
    return _build_english_pattern(words) if words else None


# sentiment 패턴 — LLM 출력 게이트 전용(opt-in). 빈 list(en_sentiment 기본)는 None.
_KO_SENTIMENT_PATTERN: Final[re.Pattern[str] | None] = (
    _build_optional_korean_pattern(_KO_SENTIMENT_RAW)
)
_EN_SENTIMENT_PATTERN: Final[re.Pattern[str] | None] = (
    _build_optional_english_pattern(_EN_SENTIMENT_RAW)
)


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


def scan_text(text: str, *, include_sentiment: bool = False) -> list[Match]:
    """주어진 텍스트에서 금지 어휘 모두 검출 (등장 순서).

    내부적으로 NFKC normalize 후 검사. 반환되는 Match.index 는 normalize 된
    텍스트 기준. NFKC 가 일반적으로 길이를 보존 (fullwidth → halfwidth 는 같은
    code unit count) 하므로 대부분의 경우 원본 index 와 일치.

    Args:
        text: 검사 대상.
        include_sentiment: **기본 False** — absolute 어휘만 검사(기존 동작 그대로).
            conformance CLI·미들웨어·일반 SYSTEM 검사는 이 기본을 쓰므로, sentiment
            어휘("호재/악재/긍정적/부정적")가 server 주석/docstring·일반 응답에
            정당하게 등장해도 검출하지 않는다(ADR-0031 D2 scope 분리, 회귀 0).
            True 면 sentiment 어휘도 검출 — **LLM 추출 텍스트(생성 텍스트) 게이트
            전용**(`disclosure_fact_extraction`). 가치판단·hallucination 2차 차단.
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

    # sentiment 매치 — opt-in(LLM 출력 게이트 전용). allowed_spans 동일 적용.
    if include_sentiment:
        if _KO_SENTIMENT_PATTERN is not None:
            for m in _KO_SENTIMENT_PATTERN.finditer(text):
                if _is_inside_any(m.start(), m.end(), allowed_spans):
                    continue
                results.append(
                    Match(
                        word=m.group(0),
                        canonical=m.group(0),
                        kind=ForbiddenKind.KO_SENTIMENT,
                        index=m.start(),
                    )
                )
        if _EN_SENTIMENT_PATTERN is not None:
            for m in _EN_SENTIMENT_PATTERN.finditer(text):
                if _is_inside_any(m.start(), m.end(), allowed_spans):
                    continue
                canonical = next(
                    (w for w in _EN_SENTIMENT_RAW if w.lower() == m.group(0).lower()),
                    m.group(0),
                )
                results.append(
                    Match(
                        word=m.group(0),
                        canonical=canonical,
                        kind=ForbiddenKind.EN_SENTIMENT,
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
    include_sentiment: bool = False,
) -> None:
    """텍스트가 깨끗하지 않으면 ValueError.

    Args:
        text: 검사할 텍스트.
        scope: 검사 정책 (CheckScope 참조). USER_PRIVATE / EXTERNAL_QUOTE 는 검사 skip.
        context: 에러 메시지의 디버깅용 컨텍스트 (예: "stock_detail.card.title").
            **호출자는 사용자 입력을 그대로 context 로 넘기지 말 것** (oracle B4).
        include_sentiment: 기본 False(absolute 만). True 면 sentiment 어휘도 차단 —
            **LLM 추출 텍스트 게이트 전용**(ADR-0031 D2). `disclosure_fact_extraction`
            이 SYSTEM scope + include_sentiment=True 로 호출해 "호재/악재" 등 가치판단
            을 2차 차단. 미들웨어·일반 SYSTEM 검사는 기본(False)이라 회귀 0.

    Raises:
        ValueError: 금지 어휘 검출 시.

    Note (oracle B4):
        본 함수의 ValueError 메시지는 **server-side 로그·alert 용**. FastAPI
        exception handler 에서 generic 500 응답으로 squash 해야 함 —
        클라이언트 응답 본문에 직접 노출 금지 (응답 자체가 금지 어휘를 echo).
    """
    if scope in (CheckScope.USER_PRIVATE, CheckScope.EXTERNAL_QUOTE):
        return

    matches = scan_text(text, include_sentiment=include_sentiment)
    if matches:
        details = ", ".join(f"{m.word!r}@{m.index}" for m in matches)
        # context 의 control character 제거 (log injection 방어).
        safe_context = re.sub(r"[\x00-\x1f]", "?", context) if context else ""
        suffix = f" [{safe_context}]" if safe_context else ""
        # ForbiddenWordsAssertError (ValueError sub-class) raise — backward
        # compat 보존 + FastAPI handler 가 본 type 만 명시 catch (Momus W6 fix).
        # message 는 server-side log/alert 용 어휘 echo 보존, response body
        # 차단은 handler 책임.
        raise ForbiddenWordsAssertError(
            f"Forbidden words detected{suffix} (scope={scope.value}): {details}",
            matches=tuple(matches),
            scope=scope,
            context=safe_context,
        )
