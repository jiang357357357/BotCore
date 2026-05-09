"""
MonBot 日志清理模块

提供智能的日志文件清理功能，支持按时间、大小、数量等多种策略。
"""

from .cleaner import LogCleaner, CleanResult
from .config import CleanerConfig, CleanStrategy

__all__ = ["LogCleaner", "CleanResult", "CleanerConfig", "CleanStrategy"]
