"""setup_logging() 단위 테스트 — 파일 핸들러 부착·멱등·디렉토리 생성.

테스트 격리: `log_dir=tmp_path` 주입으로 실 `server/logs/` 를 건드리지 않는다.
또한 핸들러 카운트는 **이 테스트의 tmp_path 로그 경로에 한정**해 센다 — 전체
스위트 실행 시 다른 테스트가 `create_app()`(→ `setup_logging()` 실 logs/) 을
호출해 root 에 실-logs file 핸들러를 미리 붙였을 수 있으므로(전역 logging 상태
공유), 경로-무관 카운트는 부정확하다. 각 테스트는 부착한 핸들러를 finally 에서
제거(전역 상태 복원 — caplog/다른 테스트 오염 방지).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.logging_config import (
    _SPECULUM_FILE_HANDLER_MARKER,
    _UVICORN_LOGGER_NAMES,
    setup_logging,
)


def _speculum_handlers(logger: logging.Logger) -> list[logging.Handler]:
    """본 모듈 marker 가 붙은 핸들러만 추출."""
    return [
        h
        for h in logger.handlers
        if getattr(h, _SPECULUM_FILE_HANDLER_MARKER, False)
    ]


def _file_handlers_for(
    logger: logging.Logger, log_path: Path,
) -> list[logging.Handler]:
    """본 모듈이 부착한 file 핸들러 중 **해당 log_path** 를 가리키는 것만 추출.

    경로 한정 — 전역 root logger 에 다른 경로(실 logs/) file 핸들러가 공존해도
    이 테스트의 tmp 경로 핸들러만 정확히 센다(전체 스위트 격리).
    """
    target = os.path.abspath(str(log_path))
    out = []
    for h in _speculum_handlers(logger):
        base = getattr(h, "baseFilename", None)
        if base is not None and os.path.abspath(base) == target:
            out.append(h)
    return out


@pytest.fixture
def _restore_logging() -> Iterator[None]:
    """테스트 후 본 모듈이 부착한 핸들러 제거 — 전역 logging 상태 복원."""
    loggers = [
        logging.getLogger(),
        *(logging.getLogger(n) for n in _UVICORN_LOGGER_NAMES),
    ]
    before = {id(lg): list(lg.handlers) for lg in loggers}
    try:
        yield
    finally:
        for logger in loggers:
            original = before[id(logger)]
            for handler in list(logger.handlers):
                if handler not in original:
                    logger.removeHandler(handler)
                    handler.close()


def test_creates_log_dir_and_file_handler(
    tmp_path: Path, _restore_logging: None,
) -> None:
    """logs/ 디렉토리 생성 + root 에 file 핸들러 부착 + 실제 파일 기록."""
    log_dir = tmp_path / "logs"
    assert not log_dir.exists()

    log_path = setup_logging(log_dir=log_dir)

    assert log_dir.is_dir()
    assert log_path == log_dir / "speculum.log"

    root = logging.getLogger()
    assert len(_file_handlers_for(root, log_path)) == 1

    logging.getLogger("test.logging").info("hello-from-test")
    for handler in _file_handlers_for(root, log_path):
        handler.flush()
    assert log_path.is_file()
    assert "hello-from-test" in log_path.read_text(encoding="utf-8")


def test_idempotent_no_duplicate_handlers(
    tmp_path: Path, _restore_logging: None,
) -> None:
    """2회 호출해도 이 경로의 root file 핸들러 1개·uvicorn file 핸들러 각 1개."""
    log_dir = tmp_path / "logs"

    log_path = setup_logging(log_dir=log_dir)
    root_file_after_first = len(_file_handlers_for(logging.getLogger(), log_path))

    setup_logging(log_dir=log_dir)
    root_file_after_second = len(_file_handlers_for(logging.getLogger(), log_path))

    assert root_file_after_first == 1
    assert root_file_after_second == 1

    for name in _UVICORN_LOGGER_NAMES:
        uv_logger = logging.getLogger(name)
        assert len(_file_handlers_for(uv_logger, log_path)) == 1, name


def test_uvicorn_loggers_get_file_handler(
    tmp_path: Path, _restore_logging: None,
) -> None:
    """uvicorn 3개 로거 모두에 file 핸들러 부착(propagate=False 대비 명시 부착)."""
    log_path = setup_logging(log_dir=tmp_path / "logs")

    for name in _UVICORN_LOGGER_NAMES:
        uv_logger = logging.getLogger(name)
        assert len(_file_handlers_for(uv_logger, log_path)) == 1, name


def test_root_has_console_handler(
    tmp_path: Path, _restore_logging: None,
) -> None:
    """root 에 file + console(StreamHandler) 둘 다 — 기존 stdout 동작 보존.

    console StreamHandler 는 경로별로 구분되지 않으므로(공유), 이 호출이 새로
    console 핸들러 1개를 추가했는지를 before/after 차이로 확인한다.
    """
    root = logging.getLogger()
    before_stream = [
        h
        for h in _speculum_handlers(root)
        if getattr(h, "baseFilename", None) is None
    ]

    log_path = setup_logging(log_dir=tmp_path / "logs")

    after_stream = [
        h
        for h in _speculum_handlers(root)
        if getattr(h, "baseFilename", None) is None
    ]
    # 이 경로의 file 핸들러 정확히 1개.
    assert len(_file_handlers_for(root, log_path)) == 1
    # 새 console(StreamHandler) 1개 추가됨(기존 stdout 동작 보존).
    new_stream = [h for h in after_stream if h not in before_stream]
    assert len(new_stream) == 1
