"""
日志清理配置模块
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List
from pathlib import Path


class CleanStrategy(Enum):
    """清理策略"""
    BY_AGE   = "by_age"    # 按时间清理
    BY_SIZE  = "by_size"   # 按大小清理
    BY_COUNT = "by_count"  # 按文件数量清理
    ALL      = "all"       # 清理所有


@dataclass
class CleanerConfig:
    """清理器配置"""

    # 基础配置
    log_root: Path                                      # 日志根目录
    dry_run: bool = False                               # 演习模式（不实际删除）

    # 按时间清理
    max_age_days: Optional[int] = 7                    # 保留最近N天的日志

    # 按大小清理
    max_total_size_mb: Optional[int] = 100             # 总大小限制（MB）
    max_file_size_mb: Optional[int] = 10               # 单文件大小限制（MB）

    # 按数量清理
    max_backup_count: Optional[int] = 5                # 每个日志最多保留N个备份

    # 模块过滤
    include_modules: Optional[List[str]] = None        # 只清理指定模块（None=全部）
    exclude_modules: Optional[List[str]] = None        # 排除指定模块

    # 文件类型过滤
    clean_colored: bool = True                         # 是否清理彩色日志
    clean_plain: bool = True                           # 是否清理纯文本日志
    clean_backups: bool = True                         # 是否清理备份文件（.1/.2等）

    # 安全配置
    keep_latest: bool = True                           # 始终保留最新的日志文件
    confirm_before_delete: bool = False                # 删除前需要确认
