"""ADR-0024 D4(c) result_codes 불변식 — CI 정적 게이트.

`small_sample`/`sample_size`/`SMALL_SAMPLE_THRESHOLD` 는 **표시 전용**이다. screener
조건 평가 (pass/fail)·`_apply_universe_relative`·`result_codes` 산출 경로에 **절대
진입 금지** (ADR-0024 D4 c). 만약 구현이 "n<30 → percentile N/A 처리"(Alternative A
의 유혹) 로 미끄러지면 즉시 result_codes 가 변동해 재현성 (§2.10) 이 붕괴한다.

본 게이트는 ADR-0007 D8.2 의 검색-게이트 *정신*을 AST-grep (정적 소스 검증) 로 강화한
신설 게이트 — 디스클로저가 값 생성 *뒤* 표시층에만 부착됨을 컴파일 시점이 아닌 테스트
시점에 강제한다. snapshot_versions 의 ast-walk CI 선례와 동형 (소스를 ast.parse 후
이름 참조를 walk).

검증 대상:
1. 분포 op 반환 경로 (`percentile`/`zscore`/`min_max_scale` of
   DbUniverseDistributionProvider, `_apply_universe_relative` of factor_evaluator)
   가 `SMALL_SAMPLE_THRESHOLD` 를 **참조하지 않음** — 임계값이 값·result_codes 에
   개입하면 D4(b)(c) 붕괴.
2. screener (`screen.py` 모듈 — `screen_active_codes` 포함) 가
   `SMALL_SAMPLE_THRESHOLD`/`small_sample`/`sample_size` 를 **import·참조하지
   않음** — 소표본이 screener pass/fail 로 새지 않음 (result_codes 불변).
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import app.api.routes.screen as screen_module
import app.services.db_universe_distribution as dist_module
import app.services.factor_evaluator as evaluator_module

# 분포 op 반환 경로가 참조하면 안 되는 표시-게이트 상수 (ADR-0024 D4 b/c).
_FORBIDDEN_NAME = "SMALL_SAMPLE_THRESHOLD"
# screener 가 import·참조하면 안 되는 표시 전용 식별자 (ADR-0024 D4 c).
_DISPLAY_ONLY_NAMES = frozenset(
    {"SMALL_SAMPLE_THRESHOLD", "small_sample", "sample_size"}
)


def _referenced_names(func: object) -> set[str]:
    """함수 소스를 ast.parse 후 참조되는 모든 Name/Attribute 식별자 집합.

    `bisect.bisect_right` 같은 attribute 도 attr 명까지 수집해 `sample_size`
    등 메서드명 누출도 잡는다 (Name.id + Attribute.attr).
    """
    source = inspect.getsource(func)  # type: ignore[arg-type]
    # 메서드 소스는 클래스 본문 들여쓰기를 가질 수 있어 dedent.
    tree = ast.parse(textwrap.dedent(source))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


# =============================================================================
# 1. 분포 op 반환 경로 — SMALL_SAMPLE_THRESHOLD 미참조 (ADR-0024 D4 b/c)
# =============================================================================

def test_percentile_does_not_reference_threshold() -> None:
    """percentile 반환 경로가 임계값 미참조 — 값이 임계에 의존하면 result_hash 붕괴."""
    names = _referenced_names(dist_module.DbUniverseDistributionProvider.percentile)
    assert _FORBIDDEN_NAME not in names


def test_zscore_does_not_reference_threshold() -> None:
    names = _referenced_names(dist_module.DbUniverseDistributionProvider.zscore)
    assert _FORBIDDEN_NAME not in names


def test_min_max_scale_does_not_reference_threshold() -> None:
    names = _referenced_names(dist_module.DbUniverseDistributionProvider.min_max_scale)
    assert _FORBIDDEN_NAME not in names


def test_apply_universe_relative_does_not_reference_threshold() -> None:
    """evaluator 의 universe-상대 dispatch 가 임계값 미참조 (값 생성 경로 격리)."""
    names = _referenced_names(evaluator_module._apply_universe_relative)
    assert _FORBIDDEN_NAME not in names


def test_visit_universe_unary_does_not_reference_threshold() -> None:
    """evaluator 의 universe-상대 op visit (N/A 결정 포함) 도 임계값 미참조.

    `_visit_universe_unary` 가 N/A 분기 (universe_distribution_required /
    missing_input / universe_distribution_na) 를 결정 — 이 경로가 임계를 보면
    result_codes 가 소표본에 의존하게 되어 D4(c) 붕괴.
    """
    names = _referenced_names(
        evaluator_module.FactorEvaluator._visit_universe_unary
    )
    assert _FORBIDDEN_NAME not in names


# =============================================================================
# 2. screener — 표시 전용 식별자 미참조 (ADR-0024 D4 c result_codes 불변식)
# =============================================================================

def test_screen_module_does_not_reference_display_only_names() -> None:
    """screen.py 모듈 전체가 SMALL_SAMPLE_THRESHOLD/small_sample/sample_size 미참조.

    screener pass/fail (result_codes) 경로가 소표본 디스클로저를 import·참조하지
    않음을 모듈 소스 정적 검증으로 강제 (ADR-0024 D4 c). 디스클로저는 값 생성 뒤
    표시층 (evaluate route) 에만 부착되며 screener 결과를 바꾸지 않는다.
    """
    source = inspect.getsource(screen_module)
    tree = ast.parse(source)
    referenced: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    leaked = (referenced | imported) & _DISPLAY_ONLY_NAMES
    assert not leaked, (
        f"screen.py 가 표시 전용 식별자 {sorted(leaked)} 참조 — "
        f"소표본 디스클로저가 result_codes 경로로 누출 (ADR-0024 D4 c 위반)."
    )


def test_screen_active_codes_does_not_reference_display_only_names() -> None:
    """screen_active_codes (단일 조건 매칭 경로) 함수 본문 정밀 검증.

    POST /api/screen 과 POST /api/runs 의 공통 result_codes 산출 함수가 소표본
    식별자를 참조하지 않음 — 저장된 run 과 라이브 screen 의 byte-동일 재현
    (§2.10) 이 소표본 표시 정책 변경에 영향받지 않음을 보장.
    """
    names = _referenced_names(screen_module.screen_active_codes)
    leaked = names & _DISPLAY_ONLY_NAMES
    assert not leaked, (
        f"screen_active_codes 가 {sorted(leaked)} 참조 — result_codes 가 소표본에 "
        f"의존 (ADR-0024 D4 c 위반)."
    )
