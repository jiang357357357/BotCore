import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%m-%d %H:%M:%S"
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_LOG_ROOT = Path(os.environ.get("MON_LOG_ROOT", _PROJECT_ROOT / "Logs"))
_TEXT_CATEGORY = "Text"
_LOG_NAME = "monbot.log"
_MAX_FILE_SIZE = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _latest_start_dir() -> Path | None:
    current_file = _LOG_ROOT / "current_start.txt"
    try:
        current = current_file.read_text(encoding="utf-8").strip()
    except OSError:
        current = ""

    if current:
        candidate = Path(current)
        if not candidate.is_absolute():
            candidate = _LOG_ROOT / candidate
        if candidate.is_dir():
            return candidate

    if not _LOG_ROOT.is_dir():
        return None

    dirs = [
        path
        for path in _LOG_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("start_")
    ]
    return sorted(dirs)[-1] if dirs else None


def _resolve_log_file() -> Path:
    env_start_dir = os.environ.get("MON_LOG_START_DIR")
    if env_start_dir:
        return Path(env_start_dir) / _TEXT_CATEGORY / _LOG_NAME

    start_dir = _latest_start_dir()
    if start_dir is not None:
        return start_dir / _TEXT_CATEGORY / _LOG_NAME

    return _LOG_ROOT / _TEXT_CATEGORY / _LOG_NAME


def _get_level() -> int:
    try:
        from src.System.MonConfig import MonConfig
        cfg = MonConfig()
        level_name = cfg.get("log", "LEVEL", default="INFO")
    except Exception:
        level_name = "INFO"
    return _LEVEL_MAP.get(level_name.upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = _get_level()
    logger.setLevel(level)
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    try:
        from logging import StreamHandler
        console.setFormatter(_ColoredFormatter(_LOG_FORMAT, _DATE_FORMAT))
    except Exception:
        console.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    logger.addHandler(console)

    try:
        log_file = _resolve_log_file()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=_MAX_FILE_SIZE, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        logger.addHandler(file_handler)
    except Exception:
        pass

    return logger


class _ColoredFormatter(logging.Formatter):
    _COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        level_name = record.levelname
        color = self._COLORS.get(level_name, "")
        reset = self._COLORS["RESET"]
        original = record.levelname
        record.levelname = f"{color}{level_name}{reset}"
        result = super().format(record)
        record.levelname = original
        return result


__all__ = ["get_logger"]
