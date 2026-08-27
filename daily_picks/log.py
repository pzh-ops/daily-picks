"""日志初始化：控制台 + 轮转文件（开发文档 §4.2）。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DEFAULT_LOG_FILE = "logs/daily_picks.log"


def setup_logging(level: str = "INFO", log_file: str | None = None,
                  max_bytes: int = 1_048_576, backup_count: int = 3) -> None:
    """初始化全局 logger `daily_picks`（控制台 + RotatingFileHandler）。

    log_file 为 None 时默认 `logs/daily_picks.log`；重复调用幂等（先移除旧 handler 再重建）。
    """
    logger = logging.getLogger("daily_picks")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(level.upper())
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(level.upper())
    console.setFormatter(formatter)
    logger.addHandler(console)

    target = Path(log_file or DEFAULT_LOG_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        target, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(level.upper())
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
