"""loguru 初始化：控制台 + 按天轮转文件（UTF-8）。"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(data_dir: Path, level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        logs_dir / "hub_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {name}:{line} - {message}",
    )
    return logger


def get_logger(name: str):
    return logger.bind(name=name)
