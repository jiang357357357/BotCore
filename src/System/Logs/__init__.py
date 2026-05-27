import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%m-%d %H:%M:%S"
_LOG_DIR = Path(__file__).resolve().parents[3] / "data" / "Logs"
_LOG_FILE = _LOG_DIR / "monbot.log"
_MAX_FILE_SIZE = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


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
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=_MAX_FILE_SIZE, backupCount=_BACKUP_COUNT, encoding="utf-8"
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
