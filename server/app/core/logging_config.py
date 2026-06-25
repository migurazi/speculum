"""파일 로깅 설정 — 운영/디버깅용 RotatingFileHandler.

현재 앱은 uvicorn 의 stdout 콘솔 로깅만 사용해 영속 로그 파일이 없어 운영 중
발생한 문제(예: 스크리너 "0건" 의 원인이 as_of 인지 na_excluded 인지)를 사후에
추적하기 어렵다. 본 모듈의 `setup_logging()` 은:

- `server/logs/speculum.log` 에 RotatingFileHandler(10MB × 5 backup) 부착.
- root logger 에 file + console(stdout) 핸들러를 등록(콘솔 기존 동작 보존).
- uvicorn 로거(`uvicorn` / `uvicorn.access` / `uvicorn.error`)는 `propagate=False`
  라 root 핸들러를 안 타므로 **file 핸들러를 명시 부착**(access 로그의 method/path
  ?query/status 가 파일에 남도록 — query 의 as_of 가 보임). 콘솔은 uvicorn 기본
  StreamHandler 가 이미 출력하므로 file 만 추가(중복 방지).
- **멱등** — 중복 호출 시 동일 파일 핸들러를 재추가하지 않는다(marker attribute).

pytest 의 caplog 는 root logger 에 자체 핸들러를 부착해 캡처하므로 root 에
핸들러를 추가해도 공존한다(caplog 는 propagate 경로의 레코드를 캡처). 테스트
격리를 위해 `log_dir` 파라미터로 임시 디렉토리 주입이 가능하다(실 `logs/` 미오염).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 파일 핸들러 식별용 marker — 멱등 보장. setup_logging 이 부착한 핸들러에만
# True 로 표시해, 재호출 시 기존 핸들러를 (타입+경로 비교 대신) marker 로 탐지.
_SPECULUM_FILE_HANDLER_MARKER = "_speculum_file_handler"

# 로그 포맷 — asctime/level/name/message. uvicorn access 의 message 에 method
# path?query status 가 들어오므로 그대로 파일에 기록된다.
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# file 핸들러를 부착할 uvicorn 로거 — propagate=False 라 root 를 안 타므로 명시 필요.
_UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.access", "uvicorn.error")

# RotatingFileHandler 회전 설정 — 10MB 당 회전, backup 5개.
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5


def _resolve_log_dir(log_dir: str | os.PathLike[str] | None) -> Path:
    """로그 디렉토리 경로 해소 — 명시 인자 우선, 미지정 시 `server/logs`.

    이 파일은 `server/app/core/logging_config.py` 이므로 `parents[2]` 가 `server`.
    테스트는 `log_dir=tmp_path` 로 실 `logs/` 격리.
    """
    if log_dir is not None:
        return Path(log_dir)
    return Path(__file__).resolve().parents[2] / "logs"


def _has_speculum_file_handler(logger: logging.Logger, log_path: Path) -> bool:
    """주어진 logger 에 본 모듈이 부착한 file 핸들러(동일 경로)가 이미 있는지.

    marker attribute 1차 + (방어적으로) baseFilename 경로 일치 2차로 멱등 판정.
    """
    target = os.path.abspath(str(log_path))
    for handler in logger.handlers:
        if not getattr(handler, _SPECULUM_FILE_HANDLER_MARKER, False):
            continue
        # marker 가 있고 baseFilename(file 핸들러 한정)이 같은 파일이면 중복.
        # StreamHandler 는 baseFilename 이 없어 file 판정에서 제외(stream 마커는
        # 무시) — root 의 file 핸들러 유무만 정확히 본다.
        base = getattr(handler, "baseFilename", None)
        if base is not None and os.path.abspath(base) == target:
            return True
    return False


def _build_file_handler(log_path: Path, level: int) -> RotatingFileHandler:
    """경로/레벨로 RotatingFileHandler 1개 생성(marker 부착)."""
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.setLevel(level)
    # 멱등 판정용 marker — 재호출 시 본 핸들러를 식별해 중복 부착 회피.
    setattr(handler, _SPECULUM_FILE_HANDLER_MARKER, True)
    return handler


def setup_logging(
    *,
    log_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """파일 로깅 활성화 — root + uvicorn 로거에 RotatingFileHandler 부착(멱등).

    Args:
        log_dir: 로그 디렉토리. None 이면 `server/logs`. 테스트는 `tmp_path` 주입으로
            실 `logs/` 미오염.

    Returns:
        실제 로그 파일 경로(`<log_dir>/speculum.log`).

    동작:
        - `LOG_LEVEL` env(기본 INFO)로 레벨 결정. root logger 레벨을 그 값으로 설정.
        - root 에 file + console(StreamHandler→stdout) 핸들러 부착. console 은
          기존 stdout 출력 동작 보존용(테스트/dev 가 root 로 찍는 로그).
        - uvicorn 3개 로거에 file 핸들러만 부착(console 은 uvicorn 기본이 출력 —
          중복 방지). 이들은 propagate=False 라 root file 핸들러를 안 타므로 명시 필요.
        - **멱등**: 이미 본 모듈의 file 핸들러가 있으면 재부착 skip.
    """
    log_dir_path = _resolve_log_dir(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_path = log_dir_path / "speculum.log"

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    # root 레벨 — 핸들러가 INFO 레코드를 처리하도록 최소 보장(pytest 가 WARNING 으로
    # 올려둔 경우 INFO 파일 로그가 누락되지 않게). 명시 LOG_LEVEL 을 존중.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    # root file 핸들러 — 멱등.
    if not _has_speculum_file_handler(root, log_path):
        root.addHandler(_build_file_handler(log_path, level))
        # console(stdout) 핸들러도 본 모듈이 root 에 1개 보장 — 기존 stdout 동작
        # 보존. 동일 marker 로 멱등(file 과 별도 marker 속성으로 구분 불필요 —
        # _has_speculum_file_handler 가 file 부재일 때만 여기 진입하므로 stream 도
        # 미부착 상태). StreamHandler 는 baseFilename 이 없어 file 판정에서 제외됨.
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        stream_handler.setLevel(level)
        setattr(stream_handler, _SPECULUM_FILE_HANDLER_MARKER, True)
        root.addHandler(stream_handler)

    # uvicorn 로거 — file 핸들러만(console 은 uvicorn 기본 StreamHandler 가 출력).
    for name in _UVICORN_LOGGER_NAMES:
        uv_logger = logging.getLogger(name)
        if not _has_speculum_file_handler(uv_logger, log_path):
            uv_logger.addHandler(_build_file_handler(log_path, level))

    return log_path
