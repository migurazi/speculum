"""forbidden_words 단위 테스트 — oracle 리뷰 v1 반영.

테스트 분류:
1. 한국어 절대 금지 매트릭스 (보강된 어휘 포함)
2. 영문 절대 금지 매트릭스 (보강된 어휘 포함)
3. 화이트리스트 (false positive 회피)
4. NFKC 정규화 (fullwidth 회피 차단)
5. CheckScope 별 정책
6. exclude_paths (종목명 등 외부 인용)
7. iterative walker — 깊은 nesting / max_depth
8. assert_clean 동작
9. 우선순위 (긴 표현 우선)
10. Edge case
"""

from __future__ import annotations

import pytest

from app.services.forbidden_words import (
    CheckScope,
    ForbiddenKind,
    Match,
    assert_clean,
    normalize,
    scan_api_response,
    scan_text,
)


# =============================================================================
# 1. 한국어 절대 금지 매트릭스
# =============================================================================

@pytest.mark.parametrize(
    "text,expected_word",
    [
        # 기존
        ("이번주 추천 종목입니다", "추천"),
        ("유망주 TOP 10", "유망주"),
        ("기대주 분석", "기대주"),
        ("주목할 만한 종목", "주목할"),
        ("강력 매수 시그널", "강력 매수"),
        ("강력매수", "강력매수"),
        ("매수 추천", "매수"),
        ("매도 타이밍", "매도"),
        ("탑픽 발표", "탑픽"),
        ("종목 픽 공개", "종목 픽"),
        ("강추 종목", "강추"),
        ("비중 확대 권고", "비중 확대"),
        ("지금이 적기", "적기"),
        # oracle A1 보강
        ("고평가 종목", "고평가"),
        ("저평가 상태", "저평가"),
        ("매집 시기", "매집"),
        ("손절 라인", "손절"),
        ("익절 권고", "익절"),
        ("투자 의견 매수", "투자 의견"),
        ("목표가 10만원", "목표가"),
        ("진입 시점", "진입 시점"),
        ("비중 축소 권고", "비중 축소"),
        ("강력 매도 의견", "강력 매도"),
        ("유망종목 발표", "유망종목"),
    ],
)
def test_korean_forbidden_words_are_detected(text: str, expected_word: str) -> None:
    matches = scan_text(text)
    assert matches, f"{expected_word!r} 가 {text!r} 에서 검출되어야 함"
    assert any(m.word == expected_word for m in matches)
    assert matches[0].kind == ForbiddenKind.KO_ABSOLUTE


# =============================================================================
# 2. 영문 절대 금지 매트릭스
# =============================================================================

@pytest.mark.parametrize(
    "text,expected_canonical",
    [
        # 기존
        ("Buy this stock", "Buy"),
        ("BUY signal detected", "Buy"),
        ("buy now", "Buy"),
        ("Strong Buy rating", "Strong Buy"),
        ("Sell recommendation", "Sell"),
        ("Top Pick of the week", "Top Pick"),
        ("Bullish outlook", "Bullish"),
        ("Bearish trend", "Bearish"),
        ("Outperform expected", "Outperform"),
        ("This is the Best stock", "Best"),
        # oracle A2 보강
        ("Go Long on this", "Long"),
        ("Go Short", "Short"),
        ("Overweight rating", "Overweight"),
        ("Underweight position", "Underweight"),
        ("Hold this stock", "Hold"),
        ("Accumulate slowly", "Accumulate"),
        ("Upgrade to outperform", "Upgrade"),
        ("Downgrade announcement", "Downgrade"),
        ("Target Price raised", "Target Price"),
        ("Price Target updated", "Price Target"),
    ],
)
def test_english_forbidden_words_detect_with_case_insensitivity(
    text: str, expected_canonical: str
) -> None:
    matches = scan_text(text)
    assert matches, f"{expected_canonical!r} 가 {text!r} 에서 검출되어야 함"
    assert any(m.canonical == expected_canonical for m in matches)


@pytest.mark.parametrize(
    "text",
    [
        "buyer satisfied",
        "buyout completed",
        "sellable goods",
        "Bestseller list",
        "holdings overview",
        "shortage report",   # Short 의 변형이지만 \b 로 막힘
        "longitude data",
    ],
)
def test_english_word_boundary_avoids_false_positives_in_compound(text: str) -> None:
    matches = scan_text(text)
    bad = {"buy", "sell", "best", "hold", "short", "long"}
    detected = {m.word.lower() for m in matches}
    assert bad.isdisjoint(detected), (
        f"{text!r} 가 false positive: {detected & bad}"
    )


# =============================================================================
# 3. 화이트리스트
# =============================================================================

@pytest.mark.parametrize(
    "text",
    [
        # 기존
        "관심 종목 목록",
        "관심종목 추가",
        "조건에 부합하는 종목입니다",
        "필터 통과 항목",
        "선택한 종목 정보",
        "비교 대상 종목",
        "Speculum 은 종목을 추천하지 않습니다",
        "본 도구는 추천이 아닙니다",
        "추천 안 함 정책",
        "추천 위젯 없음",
        "Buy-side analyst",
        "Buy Side institution",
        # oracle A5 보강
        "Best Practice 사례",
        "Best Effort delivery",
        "베스트셀러 리스트",
        "이베스트투자증권",  # 종목명 false positive 회피
        "기대수명 분석",
        "기대치 평가",
        "적시 공시 의무",
        "주목받는 시장 동향",
        "매수자 보호 규정",
        "장기 보유 전략",
        "Long-term outlook",
        "Short-term volatility",
    ],
)
def test_allowed_phrases_do_not_trigger_false_positive(text: str) -> None:
    matches = scan_text(text)
    assert matches == [], f"{text!r} 는 허용 표현인데 {matches} 가 검출됨"


def test_disclaimer_self_text_is_safe() -> None:
    disclaimer = (
        "Speculum 은 한국 주식 시장의 정량 데이터 탐색 도구이며 "
        "투자 권유 또는 투자자문이 아닙니다. 종목을 추천하지 않습니다."
    )
    assert scan_text(disclaimer) == []


# =============================================================================
# 4. NFKC 정규화 — fullwidth 회피 차단 (oracle G4)
# =============================================================================

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ｂｕｙ", "Buy"),       # fullwidth → halfwidth
        ("ＳＥＬＬ", "Sell"),
        ("Ｂｅｓｔ Pick", "Best"),
    ],
)
def test_nfkc_normalize_blocks_fullwidth_evasion(text: str, expected: str) -> None:
    matches = scan_text(text)
    assert matches, f"{text!r} 의 fullwidth 회피가 차단되어야 함"
    assert any(m.canonical == expected for m in matches)


def test_normalize_function_explicit() -> None:
    """normalize() 의 직접 동작 확인."""
    assert normalize("Ｂｕｙ") == "Buy"
    assert normalize("ＳＥＬＬ") == "SELL"
    assert normalize("일반 텍스트") == "일반 텍스트"


# =============================================================================
# 5. CheckScope 별 정책 (oracle C3)
# =============================================================================

def test_assert_clean_system_scope_strict() -> None:
    """SYSTEM scope — 가장 엄격."""
    with pytest.raises(ValueError):
        assert_clean("매수 추천 종목", scope=CheckScope.SYSTEM)


def test_assert_clean_user_private_skips_check() -> None:
    """USER_PRIVATE — 본인만 보는 메모. 검사 X."""
    # 정책상 통과 — 사용자 본인의 노트는 검사 안 함.
    assert_clean("매수 추천 종목", scope=CheckScope.USER_PRIVATE)


def test_assert_clean_external_quote_skips_check() -> None:
    """EXTERNAL_QUOTE — 회사명·DART 공시 제목 등. 검사 X."""
    # 종목명에 금지 substring 이 들어가도 통과
    assert_clean("이베스트투자증권", scope=CheckScope.EXTERNAL_QUOTE)
    assert_clean("매수의 정석 (책 제목)", scope=CheckScope.EXTERNAL_QUOTE)


def test_assert_clean_user_shared_checks() -> None:
    """USER_SHARED — 공유 콘텐츠 (Factor Lab M2). 검사 함."""
    with pytest.raises(ValueError):
        assert_clean("추천 팩터", scope=CheckScope.USER_SHARED)


def test_assert_clean_default_scope_is_system() -> None:
    """scope 미지정 시 SYSTEM (가장 엄격) — 안전한 default."""
    with pytest.raises(ValueError):
        assert_clean("매수 추천")


# =============================================================================
# 6. exclude_paths — 종목명 등 외부 인용 (oracle C1)
# =============================================================================

def test_scan_api_response_excludes_specified_keys() -> None:
    """`stock_name` 등 식별자 key 는 검사 제외."""
    payload = {
        "title": "Stock Detail",
        "stock_name": "이베스트투자증권",   # 금지 substring 포함, 검사 제외
        "company_name": "기대산업",
        "value": 12.3,
    }
    matches = scan_api_response(
        payload, exclude_paths=frozenset({"stock_name", "company_name"})
    )
    assert matches == []


def test_scan_api_response_excludes_in_nested_lists() -> None:
    """exclude 된 key 의 list 내부도 검사 제외."""
    payload = {
        "stocks": [
            {"stock_name": "이베스트투자증권", "code": "078020"},
            {"stock_name": "기대산업", "code": "999999"},
        ],
        "title": "검색 결과",   # 정상
    }
    # exclude_paths 가 dict 의 key 단위로 적용되므로, 안의 nested dict 의
    # stock_name 도 같은 key 면 제외.
    matches = scan_api_response(payload, exclude_paths=frozenset({"stock_name"}))
    assert matches == []


def test_scan_api_response_without_exclude_catches_stock_name() -> None:
    """exclude_paths 안 주면 종목명에서 false positive 발생."""
    payload = {"stock_name": "이베스트투자증권"}
    matches = scan_api_response(payload)
    # 이베스트 는 화이트리스트에 있으므로 통과
    # 다른 가상 종목명으로 검증:
    payload2 = {"some_field": "추천 종목"}
    matches2 = scan_api_response(payload2)
    assert any(m.word == "추천" for m in matches2)


# =============================================================================
# 7. Iterative walker — depth / 무한 자기참조 방어 (oracle B2)
# =============================================================================

def test_deeply_nested_dict_processed_without_recursion_error() -> None:
    """깊이 100 의 nested dict — 기존 재귀 구현이라면 RecursionError 발생.

    Iterative walker 는 안전. max_depth=200 으로 명시.
    """
    node: object = "추천 종목"
    for _ in range(100):
        node = {"child": node}
    matches = scan_api_response(node, max_depth=200)
    assert any(m.word == "추천" for m in matches)


def test_max_depth_exceeded_raises() -> None:
    """max_depth 초과 시 ValueError — 자기참조 JSON 방어."""
    node: object = "text"
    for _ in range(100):
        node = [node]
    with pytest.raises(ValueError, match="depth"):
        scan_api_response(node, max_depth=10)


def test_scan_api_response_ignores_non_string_values() -> None:
    payload = {"count": 42, "ratio": 1.23, "flag": True, "empty": None}
    assert scan_api_response(payload) == []


# =============================================================================
# 8. assert_clean — 메시지 + 컨텍스트
# =============================================================================

def test_assert_clean_passes_for_safe_text() -> None:
    assert_clean("관심 종목에 추가되었습니다")


def test_assert_clean_raises_with_context_in_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        assert_clean("매수 추천 종목입니다", context="api.screen.title")
    assert "api.screen.title" in str(exc_info.value)


def test_assert_clean_sanitizes_control_chars_in_context() -> None:
    """log injection 방어 — context 의 control character 는 '?' 로 변환."""
    with pytest.raises(ValueError) as exc_info:
        assert_clean("매수", context="path\nwith\rnewline")
    msg = str(exc_info.value)
    assert "\n" not in msg
    assert "\r" not in msg
    assert "?" in msg


# =============================================================================
# 9. 우선순위
# =============================================================================

def test_longer_korean_phrase_takes_precedence_over_substring() -> None:
    matches = scan_text("강력 매수 알림")
    assert any(m.word == "강력 매수" for m in matches)
    matched_words = [m.word for m in matches]
    assert matched_words.count("매수") == 0


# =============================================================================
# 10. Edge case
# =============================================================================

def test_empty_text_returns_empty() -> None:
    assert scan_text("") == []


def test_pure_numbers_and_symbols_are_safe() -> None:
    assert scan_text("12.3% (TTM 연결)") == []


def test_korean_punctuation_does_not_break_matching() -> None:
    matches = scan_text("추천!!! 종목.")
    assert any(m.word == "추천" for m in matches)


def test_multiple_matches_returned_in_index_order() -> None:
    text = "매수 추천 + Sell signal"
    matches = scan_text(text)
    indices = [m.index for m in matches]
    assert indices == sorted(indices)


def test_match_records_index_correctly() -> None:
    text = "오늘의 추천 종목"
    matches = scan_text(text)
    assert len(matches) == 1
    assert matches[0].index == text.index("추천")


def test_korean_english_mixed_text() -> None:
    """한영 혼용 텍스트에서 양쪽 모두 검출."""
    text = "Buy 를 추천합니다"
    matches = scan_text(text)
    found_words = {m.word for m in matches}
    assert "Buy" in found_words
    assert "추천" in found_words
