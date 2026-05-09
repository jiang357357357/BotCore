"""
日志清理器核心实现
"""

import os
import time
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass, field

from .config import CleanerConfig, CleanStrategy


@dataclass
class CleanResult:
    """清理结果"""
    total_files: int = 0
    deleted_files: int = 0
    freed_space_mb: float = 0.0
    errors: List[str] = field(default_factory=list)


class LogCleaner:
    """日志清理器"""

    def __init__(self, config: CleanerConfig):
        self.config = config
        self.result = CleanResult()

    def clean(self, strategy: CleanStrategy = CleanStrategy.BY_AGE) -> CleanResult:
        """
        执行清理

        Args:
            strategy: 清理策略

        Returns:
            CleanResult 清理结果
        """
        self.result = CleanResult()

        if not self.config.log_root.exists():
            self.result.errors.append(f"日志目录不存在: {self.config.log_root}")
            return self.result

        if strategy == CleanStrategy.BY_AGE:
            self._clean_by_age()
        elif strategy == CleanStrategy.BY_SIZE:
            self._clean_by_size()
        elif strategy == CleanStrategy.BY_COUNT:
            self._clean_by_count()
        elif strategy == CleanStrategy.ALL:
            self._clean_all()

        return self.result

    # ──────────────────────────────────────────────
    # 清理策略实现
    # ──────────────────────────────────────────────

    def _clean_by_age(self) -> None:
        """按时间清理：删除超过 max_age_days 天的文件"""
        if self.config.max_age_days is None:
            return

        cutoff = time.time() - self.config.max_age_days * 24 * 3600

        for f in self._scan_log_files():
            try:
                if self.config.keep_latest and self._is_latest_log(f):
                    continue
                if f.stat().st_mtime < cutoff:
                    self._delete_file(f)
            except Exception as e:
                self.result.errors.append(f"处理文件 {f} 失败: {e}")

    def _clean_by_size(self) -> None:
        """按大小清理：单文件超限 + 总大小超限"""
        if self.config.max_file_size_mb:
            max_bytes = self.config.max_file_size_mb * 1024 * 1024
            for f in self._scan_log_files():
                try:
                    if f.stat().st_size > max_bytes:
                        if not (self.config.keep_latest and self._is_latest_log(f)):
                            self._delete_file(f)
                except Exception as e:
                    self.result.errors.append(f"处理文件 {f} 失败: {e}")

        if self.config.max_total_size_mb:
            self._clean_by_total_size()

    def _clean_by_total_size(self) -> None:
        """总大小超限时，从最旧的文件开始删除"""
        max_total = self.config.max_total_size_mb * 1024 * 1024

        files_info = []
        for f in self._scan_log_files():
            try:
                stat = f.stat()
                files_info.append((f, stat.st_mtime, stat.st_size))
            except Exception as e:
                self.result.errors.append(f"读取文件信息失败 {f}: {e}")

        files_info.sort(key=lambda x: x[1])  # 旧的在前
        current_total = sum(s for _, _, s in files_info)

        for f, _, size in files_info:
            if current_total <= max_total:
                break
            if self.config.keep_latest and self._is_latest_log(f):
                continue
            if self._delete_file(f):
                current_total -= size

    def _clean_by_count(self) -> None:
        """按备份数量清理：每个日志保留最新的 max_backup_count 个"""
        if self.config.max_backup_count is None:
            return

        # 按基础日志名分组
        groups: Dict[str, List[Path]] = {}
        for f in self._scan_log_files():
            base = self._get_base_log_name(f)
            groups.setdefault(base, []).append(f)

        for files in groups.values():
            files_with_time = []
            for f in files:
                try:
                    files_with_time.append((f, f.stat().st_mtime))
                except Exception as e:
                    self.result.errors.append(f"读取文件信息失败 {f}: {e}")

            # 新的在前，超出数量的删除
            files_with_time.sort(key=lambda x: x[1], reverse=True)
            for i, (f, _) in enumerate(files_with_time):
                if i >= self.config.max_backup_count:
                    self._delete_file(f)

    def _clean_all(self) -> None:
        """清理所有日志文件（keep_latest 时保留最新）"""
        for f in self._scan_log_files():
            if self.config.keep_latest and self._is_latest_log(f):
                continue
            self._delete_file(f)

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _scan_log_files(self) -> List[Path]:
        """扫描所有符合条件的日志文件"""
        log_files = []

        for root, _, files in os.walk(self.config.log_root):
            root_path = Path(root)
            for name in files:
                f = root_path / name
                if not self._should_clean_file(f):
                    continue

                # 模块过滤（基于相对路径第一层目录名）
                try:
                    rel = f.relative_to(self.config.log_root)
                    module = rel.parts[0] if len(rel.parts) > 1 else None
                except ValueError:
                    module = None

                if self.config.include_modules and module not in self.config.include_modules:
                    continue
                if self.config.exclude_modules and module in self.config.exclude_modules:
                    continue

                log_files.append(f)
                self.result.total_files += 1

        return log_files

    def _should_clean_file(self, f: Path) -> bool:
        """判断文件是否应该被清理"""
        name = f.name

        # 必须是日志文件
        if not (name.endswith('.log') or '.log.' in name):
            return False

        # 彩色日志（不含 _plain）
        if not self.config.clean_colored and '_plain' not in name:
            return False

        # 纯文本日志（含 _plain）
        if not self.config.clean_plain and '_plain' in name:
            return False

        # 备份文件（含 .log.N）
        if not self.config.clean_backups and '.log.' in name:
            return False

        return True

    def _is_latest_log(self, f: Path) -> bool:
        """是否是最新日志（非备份）"""
        return f.name.endswith('.log')

    def _get_base_log_name(self, f: Path) -> str:
        """获取基础日志名（去除 .1/.2 等备份后缀）"""
        name = f.name
        if '.log.' in name:
            return name.split('.log.')[0] + '.log'
        return name

    def _delete_file(self, f: Path) -> bool:
        """删除单个文件"""
        try:
            size = f.stat().st_size

            if self.config.dry_run:
                print(f"[演习] 将删除: {f} ({size / 1024 / 1024:.2f} MB)")
                self.result.deleted_files += 1
                self.result.freed_space_mb += size / 1024 / 1024
                return True

            if self.config.confirm_before_delete:
                resp = input(f"确认删除 {f}? (y/n): ")
                if resp.lower() != 'y':
                    return False

            f.unlink()
            self.result.deleted_files += 1
            self.result.freed_space_mb += size / 1024 / 1024
            return True

        except Exception as e:
            self.result.errors.append(f"删除文件失败 {f}: {e}")
            return False

    def get_statistics(self) -> dict:
        """获取日志目录统计信息"""
        stats: dict = {
            'total_files': 0,
            'total_size_mb': 0.0,
            'by_module': {},
            'by_type': {'colored': 0, 'plain': 0, 'backup': 0},
        }

        for f in self._scan_log_files():
            try:
                size = f.stat().st_size
                stats['total_files'] += 1
                stats['total_size_mb'] += size / 1024 / 1024

                module = f.parent.name
                if module not in stats['by_module']:
                    stats['by_module'][module] = {'count': 0, 'size_mb': 0.0}
                stats['by_module'][module]['count'] += 1
                stats['by_module'][module]['size_mb'] += size / 1024 / 1024

                name = f.name
                if '_plain' in name:
                    stats['by_type']['plain'] += 1
                elif '.log.' in name:
                    stats['by_type']['backup'] += 1
                else:
                    stats['by_type']['colored'] += 1

            except Exception:
                pass

        return stats
