"""금지 어휘 가드 미들웨어 — ADR-0007 D4.4 의 런타임 implementation.

본 미들웨어는 모든 JSON API 응답을 가로채 `forbidden_words.scan_api_response` 로
검사한다. 검출 시 환경별 정책 (WARN_ONLY / REDACT / BLOCK) 에 따라 응답을 변형.

관련 ADR / 리뷰:
- ADR-0007 D4.4 — middleware 강제 메커니즘 약속
- ADR-0007 D4.5 — Scope (exclude_keys 로 종목명 등 제외)
- ADR-0006 D2, D8 — 자본시장법 회피의 시스템 디자인 implementation
- oracle 리뷰 v1 G1 (Critical) — 모듈 호출처 부재 → 본 middleware 가 해소
- oracle 리뷰 v1 G5 (Medium) — 감사 로그 → AuditSink interface 로 추상화
- oracle 리뷰 v1 B4 (Critical) — ValueError 응답 본문 echo 방지 → BLOCK 모드 generic 500
- oracle 리뷰 v2 (2026-05-22) C1~C5, H1~H3 — BackgroundTask 보존, multi-value
  header, json.loads RecursionError 보호, body size cap, REDACT 정확화,
  audit safe emit, exclude_keys rename, WARN_ONLY 의 opt-in 격상

설계 핵심:
- **응답 본문 검사**: `application/json` Content-Type 만.
- **응답 본문 echo 차단** (oracle v1 B4): BLOCK 모드는 generic message.
  REDACT 모드는 검출 어휘를 `***` 치환 (어휘 자체 노출 X).
- **exclude_keys** (이전 exclude_paths, oracle v2 H2): dict key 이름 set.
  같은 key 가 nested 어디서든 등장하면 그 값 검사 제외 — 종목명·회사명·DART
  공시 제목 등 EXTERNAL_QUOTE scope path.
- **AuditSink**: protocol 추상화. emit 의 예외는 middleware 가 swallow
  (oracle v2 H1).
- **BackgroundTask 보존**: `_replace_body` 가 `background` 속성 전달
  (oracle v2 C1).
- **Multi-value headers**: `Set-Cookie` 등 중복 키 보존. REDACT 시 무결성
  헤더 (`ETag`, `Content-MD5`, `Content-Digest`, `Repr-Digest`) 자동 제거
  (oracle v2 C2).
- **JSON parse 보호**: `RecursionError` 까지 catch (oracle v2 C3).
- **Body size cap**: 기본 1 MiB. 초과 시 검사 skip + audit (oracle v2 C4).
- **REDACT 정확화**: alternation regex 가 아닌 `scan_text` 를 각 문자열에
  재실행해 정확한 span 으로 치환 (oracle v2 C5).

성능 노트:
- JSON body 를 한 번 더 파싱·재직렬화 → 큰 응답에서 latency 영향.
- max_body_bytes 로 OOM 위험 차단.
- M1+ 큰 응답은 라우터에서 명시적 streaming + middleware bypass 권장.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import Environment, get_environment
from app.services.forbidden_words import Match, scan_api_response, scan_text

_logger = logging.getLogger("speculum.forbidden_words_guard")


class ForbiddenWordsPolicy(Enum):
    """검출 시 응답 변형 정책.

    WARN_ONLY: 응답 통과 + 로그만. dev 환경 default.
    REDACT: 검출 어휘를 `***` 로 치환 후 응답. staging 환경 default.
        - 사용자 화면에 표시되더라도 어휘 자체는 노출 X.
        - 응답 schema 는 유지 (필드 길이 변경만).
    BLOCK: 응답을 generic 500 으로 swap. prod 환경 default.
        - oracle B4 — 응답 본문에 어휘가 echo 되지 않도록 가장 안전.
        - 클라이언트는 "내부 오류" 만 알게 됨.
    """

    WARN_ONLY = "warn-only"
    REDACT = "redact"
    BLOCK = "block"


_POLICY_ENV_VAR = "SPECULUM_FORBIDDEN_POLICY"


def default_policy_for(env: Environment) -> ForbiddenWordsPolicy:
    """환경별 default policy 매핑.

    안전 측면 default (oracle v2 H3): WARN_ONLY 는 어휘를 응답 본문에 echo 하므로
    ADR-0006 D2 의 자본시장법 회피 의도와 정면 충돌. WARN_ONLY 는 모든 환경에서
    명시적 opt-in 이 필요 — 환경변수 `SPECULUM_FORBIDDEN_POLICY=warn-only`.

    - DEV: REDACT (어휘를 `***` 치환)
    - STAGING: REDACT
    - PROD: BLOCK (응답 자체를 generic 500 으로 swap)

    환경변수가 set 되어 있으면 그 값이 우선.
    """
    override = os.environ.get(_POLICY_ENV_VAR, "").lower().strip()
    if override:
        try:
            return ForbiddenWordsPolicy(override)
        except ValueError:
            # 알 수 없는 값 — 환경 기반 default 로 fallback.
            _logger.warning(
                "Unknown %s=%r, falling back to env-based default",
                _POLICY_ENV_VAR, override,
            )
    if env is Environment.PROD:
        return ForbiddenWordsPolicy.BLOCK
    # DEV, STAGING: REDACT (oracle v2 H3 보수적 default)
    return ForbiddenWordsPolicy.REDACT


# =============================================================================
# AuditSink — 감사 로그 abstraction (oracle G5)
# =============================================================================

@dataclass(frozen=True, slots=True)
class AuditEvent:
    """검출 사건의 record.

    Attributes:
        path: 요청 path (예: "/api/screen").
        method: HTTP method.
        policy: 적용된 정책.
        match_words: 검출 어휘 list (canonical 형식).
        match_count: 총 검출 수.
        status_code: 최종 응답 status code.
    """

    path: str
    method: str
    policy: ForbiddenWordsPolicy
    match_words: tuple[str, ...]
    match_count: int
    status_code: int


class AuditSink(Protocol):
    """감사 로그 수신자 interface.

    M0 default = LoggingAuditSink (logging.warning).
    운영 환경에서는 Sentry / audit table 구현으로 교체.

    개인정보 보호: AuditEvent 에는 사용자 입력 텍스트 자체를 포함하지 않음.
    어휘 canonical 만 — ADR-0006 D6 의 개인정보 분리 원칙 일관.
    """

    def emit(self, event: AuditEvent) -> None: ...


class LoggingAuditSink:
    """기본 sink — Python logging 으로 WARN 출력."""

    def emit(self, event: AuditEvent) -> None:
        _logger.warning(
            "forbidden_words detected: path=%s method=%s policy=%s "
            "count=%d words=%s status=%d",
            event.path,
            event.method,
            event.policy.value,
            event.match_count,
            ",".join(event.match_words),
            event.status_code,
        )


# =============================================================================
# Middleware
# =============================================================================

# Content-Type prefix 가 일치하면 JSON 으로 간주. `application/json; charset=utf-8`
# 같은 변형 cover.
_JSON_CONTENT_TYPE_PREFIXES = (
    "application/json",
    "application/vnd.api+json",
)

# Block 모드의 응답 — 어휘 echo 없는 generic message.
_BLOCK_RESPONSE_BODY: dict[str, object] = {
    "detail": "Internal response policy violation",
    "code": "FORBIDDEN_WORDS_GUARD_BLOCK",
}

# 기본 body size cap — 1 MiB. 큰 응답은 검사 skip + audit (oracle v2 C4).
_DEFAULT_MAX_BODY_BYTES = 1 * 1024 * 1024

# REDACT 시 무효화되는 무결성 헤더 — 응답 body 변형으로 잘못된 값이 됨 (oracle v2 C2).
_INTEGRITY_HEADERS_TO_STRIP = frozenset({
    "etag",
    "content-md5",
    "content-digest",
    "repr-digest",
})


class ForbiddenWordsGuardMiddleware(BaseHTTPMiddleware):
    """모든 JSON 응답을 forbidden_words 로 검사하는 미들웨어.

    Args:
        app: ASGI app.
        policy: override policy. None 이면 환경 / 환경변수에서 결정.
        exclude_keys: dict key 이름 — 검사 제외 (예: 종목명).
            ADR-0007 D4.5 EXTERNAL_QUOTE scope 의 key 수준 implementation.
            (oracle v2 H2: 이전 이름 `exclude_paths` 를 의미 명확화로 rename.)
        audit_sink: 검출 사건 수신자. None 이면 LoggingAuditSink.
        skip_routes: 검사 자체를 skip 할 URL prefix.
        max_body_bytes: body 검사 상한. 초과 시 검사 skip + audit.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        policy: ForbiddenWordsPolicy | None = None,
        exclude_keys: frozenset[str] | None = None,
        audit_sink: AuditSink | None = None,
        skip_routes: tuple[str, ...] = ("/healthz", "/metrics"),
        max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        super().__init__(app)
        self._policy: ForbiddenWordsPolicy = policy or default_policy_for(get_environment())
        self._exclude_keys: frozenset[str] = exclude_keys or frozenset()
        self._audit: AuditSink = audit_sink or LoggingAuditSink()
        self._skip_routes: tuple[str, ...] = skip_routes
        self._max_body_bytes: int = max_body_bytes

    def _safe_emit(self, event: AuditEvent) -> None:
        """AuditSink 의 emit 예외를 swallow (oracle v2 H1).

        감사 로그 실패가 응답 차단을 막아선 안 됨.
        """
        try:
            self._audit.emit(event)
        except Exception:  # noqa: BLE001 — 모든 예외 swallow 가 정책
            _logger.exception("AuditSink.emit failed (swallowed)")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Skip 경로는 즉시 통과 (healthcheck 등의 latency 최소화).
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._skip_routes):
            return await call_next(request)

        response = await call_next(request)

        # JSON 응답만 검사. 다른 Content-Type 은 통과.
        content_type = response.headers.get("content-type", "").lower()
        if not any(content_type.startswith(p) for p in _JSON_CONTENT_TYPE_PREFIXES):
            return response

        # gzip/br 등으로 압축된 응답은 검사 path 의 의미가 사라짐 — bypass
        # (oracle v2 C2 의 silent bypass 정책 결정: 압축 응답은 그대로 통과).
        content_encoding = response.headers.get("content-encoding", "").lower().strip()
        if content_encoding and content_encoding != "identity":
            return response

        # starlette BaseHTTPMiddleware 가 반환하는 응답은 `_StreamingResponse` —
        # `body` 속성이 비어 있고 `body_iterator` 로만 body 접근 가능.
        # iterator 를 소비해 bytes 로 모은 뒤 검사 + 새 응답 생성.
        raw_body = await _collect_body(response, max_bytes=self._max_body_bytes)

        # max_body_bytes 초과 (oracle v2 C4) — 검사 skip + audit.
        if raw_body is None:
            self._safe_emit(AuditEvent(
                path=path, method=request.method, policy=self._policy,
                match_words=("__body_too_large__",), match_count=0,
                status_code=response.status_code,
            ))
            return _replace_body_passthrough(response, b"<body too large for inspection>")

        if not raw_body:
            return _replace_body(response, b"")

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, RecursionError, ValueError):
            # JSON 이라 명시되었으나 파싱 실패 또는 깊이 폭발 (oracle v2 C3) — 통과.
            _logger.warning(
                "forbidden_words_guard: JSON parse failed or too deep (path=%s)", path
            )
            return _replace_body(response, raw_body)

        try:
            matches = scan_api_response(payload, exclude_paths=self._exclude_keys)
        except ValueError:
            # scan_api_response 의 max_depth 초과. 통과 + audit.
            _logger.warning(
                "forbidden_words_guard: payload depth exceeded (path=%s)", path
            )
            return _replace_body(response, raw_body)

        if not matches:
            return _replace_body(response, raw_body)

        # 검출 발생 — 정책 적용.
        return self._apply_policy(
            request=request,
            response=response,
            payload=payload,
            raw_body=raw_body,
            matches=matches,
        )

    def _apply_policy(
        self,
        *,
        request: Request,
        response: Response,
        payload: object,
        raw_body: bytes,
        matches: list[Match],
    ) -> Response:
        path = request.url.path
        method = request.method
        words = tuple(sorted({m.canonical for m in matches}))

        if self._policy is ForbiddenWordsPolicy.WARN_ONLY:
            self._safe_emit(AuditEvent(
                path=path, method=method, policy=self._policy,
                match_words=words, match_count=len(matches),
                status_code=response.status_code,
            ))
            return _replace_body(response, raw_body)

        if self._policy is ForbiddenWordsPolicy.REDACT:
            redacted_payload = _redact_payload(payload)
            new_body = json.dumps(redacted_payload, ensure_ascii=False).encode("utf-8")
            self._safe_emit(AuditEvent(
                path=path, method=method, policy=self._policy,
                match_words=words, match_count=len(matches),
                status_code=response.status_code,
            ))
            # REDACT 는 body 길이 변경 → 무결성 헤더 제거 (oracle v2 C2).
            return _replace_body(response, new_body, strip_integrity_headers=True)

        # BLOCK
        self._safe_emit(AuditEvent(
            path=path, method=method, policy=self._policy,
            match_words=words, match_count=len(matches),
            status_code=500,
        ))
        return JSONResponse(_BLOCK_RESPONSE_BODY, status_code=500)


# =============================================================================
# Helpers
# =============================================================================

async def _collect_body(response: Response, *, max_bytes: int) -> bytes | None:
    """starlette BaseHTTPMiddleware 응답의 body 를 async iterator 로 수집.

    max_bytes 초과 시 None 반환 — 검사 skip + audit 의 signal (oracle v2 C4).
    """
    body_chunks: list[bytes] = []
    total = 0
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        body = getattr(response, "body", b"")
        return body if isinstance(body, bytes) else bytes(body)
    async for chunk in body_iterator:
        bytes_chunk: bytes = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
        total += len(bytes_chunk)
        if total > max_bytes:
            # 더 이상 모으지 않음 — iterator 를 마저 소비해서 starlette 의
            # 후속 상태를 정상화하고 None 반환.
            async for _ in body_iterator:
                pass
            return None
        body_chunks.append(bytes_chunk)
    return b"".join(body_chunks)


def _extract_raw_headers(original: Response) -> list[tuple[bytes, bytes]]:
    """Multi-value 헤더를 보존하는 raw header 추출 (oracle v2 C2).

    `MutableHeaders.items()` 는 같은 키의 중복 값을 잃음 — `Set-Cookie` 처럼
    여러 번 등장하는 헤더가 정상 동작하려면 raw 순회가 필요.
    """
    raw = getattr(original.headers, "raw", None)
    if raw is not None:
        return [(name, value) for name, value in raw]
    # Fallback — items() 사용 (중복 손실 가능, 그러나 starlette 0.20+ 는 raw 보유).
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in original.headers.items()]


def _replace_body(
    original: Response,
    new_body: bytes,
    *,
    strip_integrity_headers: bool = False,
) -> Response:
    """원본 응답의 status code / headers / content-type / background 보존 + body 교체.

    - Content-Length 는 starlette 가 자동 재계산하도록 헤더에서 제거.
    - Multi-value 헤더 (Set-Cookie 등) raw 순회로 보존 (oracle v2 C2).
    - BackgroundTask 전달 (oracle v2 C1).
    - strip_integrity_headers=True 면 ETag 등 무효화 헤더 제거 (REDACT 시).
    """
    response = Response(
        content=new_body,
        status_code=original.status_code,
        media_type=original.media_type,
        background=getattr(original, "background", None),
    )
    # 기본 헤더 초기화 후 원본의 raw 헤더를 그대로 복원.
    response.raw_headers = []
    for name_b, value_b in _extract_raw_headers(original):
        name_lower = name_b.decode("latin-1").lower()
        if name_lower == "content-length":
            continue
        if strip_integrity_headers and name_lower in _INTEGRITY_HEADERS_TO_STRIP:
            continue
        response.raw_headers.append((name_b, value_b))
    # Content-Length 재계산.
    response.raw_headers.append((b"content-length", str(len(new_body)).encode("latin-1")))
    return response


def _replace_body_passthrough(original: Response, marker: bytes) -> Response:
    """body 가 너무 커서 검사 skip 한 경우 — marker 로 대체.

    원본 body 는 이미 소비되어 복원 불가. middleware 가 응답을 깨뜨리지 않도록
    명시적 marker 반환.
    """
    return _replace_body(original, marker)


# =============================================================================
# Redaction (oracle v2 C5)
# =============================================================================

def _redact_payload(payload: object) -> object:
    """JSON payload 의 모든 문자열 값을 정확한 span 단위로 redact.

    이전 구현 (alternation regex) 은 다음 문제:
      1. 어휘 사전이 커지면 NFA backtracking 폭발 가능 (oracle v2 C5).
      2. payload 의 raw 문자열 vs scan_text 의 NFKC normalize 후 match 위치
         불일치 — 잘못된 span 치환 또는 missed match.

    새 구현: 각 문자열에 대해 `scan_text` 를 호출 → Match 들의 (index, len)
    으로 정확 치환. scan_text 가 normalize 후 작동하므로 결과 텍스트도 normalize
    된 형태. (대부분의 ASCII / 한글에서 길이 불변)
    """
    return _walk_and_redact(payload)


def _walk_and_redact(node: object) -> object:
    """재귀적으로 모든 문자열을 redact. dict / list / tuple 구조 유지."""
    if isinstance(node, str):
        return _redact_string(node)
    if isinstance(node, dict):
        return {k: _walk_and_redact(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk_and_redact(v) for v in node]
    if isinstance(node, tuple):
        return tuple(_walk_and_redact(v) for v in node)
    return node


def _redact_string(text: str) -> str:
    """단일 문자열의 검출 어휘 span 을 별표 치환.

    scan_text 가 NFKC normalize 후 검사 → 반환 index 도 normalize 기준.
    원본이 normalize 와 다르면 (e.g. fullwidth `Ｂｕｙ`), 안전 측면에서 normalize
    된 결과를 반환. 결과 문자열의 visible 형태는 동등 (fullwidth → ASCII 변환만).
    """
    matches = scan_text(text)
    if not matches:
        return text

    # scan_text 가 NFKC normalize 후 진행 → normalize 된 텍스트를 base 로 사용.
    import unicodedata
    normalized = unicodedata.normalize("NFKC", text)

    # 끝에서부터 치환해야 index shift 가 발생하지 않음.
    sorted_matches = sorted(matches, key=lambda m: m.index, reverse=True)
    result = normalized
    for match in sorted_matches:
        start = match.index
        end = start + len(match.word)
        if 0 <= start < len(result) and end <= len(result):
            result = result[:start] + ("*" * (end - start)) + result[end:]
    return result
