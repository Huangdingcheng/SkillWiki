"""日志工具 - 生产级别（无循环依赖）"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# 延迟导入 config_manager，避免循环依赖
# logger 模块在 config 初始化之前就可能被调用，因此使用懒加载

_DEFAULT_LEVEL = "INFO"
_DEFAULT_FORMAT = "json"
_DEFAULT_FILE = "logs/skillos.log"
_DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB
_DEFAULT_BACKUP_COUNT = 10

_loggers: dict[str, logging.Logger] = {}
_initialized = False


def _get_logging_config():
    """懒加载日志配置，避免循环依赖。"""
    try:
        from .config_manager import get_config_manager  # noqa: PLC0415
        mgr = get_config_manager()
        return mgr.get_logging_config()
    except Exception:
        return None


def _make_formatter(fmt: str) -> logging.Formatter:
    if fmt == "json":
        try:
            from pythonjsonlogger import jsonlogger  # noqa: PLC0415
            return jsonlogger.JsonFormatter(
                "%(asctime)s %(name)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        except ImportError:
            pass
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _build_logger(name: str) -> logging.Logger:
    cfg = _get_logging_config()

    level_str = cfg.level if cfg else _DEFAULT_LEVEL
    fmt_str = cfg.format if cfg else _DEFAULT_FORMAT
    log_file = cfg.file if cfg else _DEFAULT_FILE
    max_bytes = cfg.max_bytes if cfg else _DEFAULT_MAX_BYTES
    backup_count = cfg.backup_count if cfg else _DEFAULT_BACKUP_COUNT
    console = cfg.console if cfg else True

    level = getattr(logging, level_str.upper(), logging.INFO)
    formatter = _make_formatter(fmt_str)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # 防止日志向 root logger 传播导致重复输出

    # 文件处理器（带轮转）
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 控制台处理器
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    获取命名日志记录器。

    首次调用时构建并缓存；后续调用直接返回缓存实例。
    在 ConfigManager 初始化之前调用也是安全的（使用默认配置）。
    """
    if name not in _loggers:
        _loggers[name] = _build_logger(name)
    return _loggers[name]


def set_level(level: str, name: Optional[str] = None) -> None:
    """动态调整日志级别。name=None 时调整所有已注册的 logger。"""
    lvl = getattr(logging, level.upper(), None)
    if lvl is None:
        raise ValueError(f"无效的日志级别: {level!r}")
    targets = [_loggers[name]] if name and name in _loggers else list(_loggers.values())
    for logger in targets:
        logger.setLevel(lvl)
        for handler in logger.handlers:
            handler.setLevel(lvl)


def clear_loggers() -> None:
    """清除所有缓存的 logger（主要用于测试）。"""
    for logger in _loggers.values():
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
    _loggers.clear()
