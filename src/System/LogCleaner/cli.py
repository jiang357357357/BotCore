"""
日志清理命令行工具
"""

import sys
from pathlib import Path
from argparse import ArgumentParser

from .cleaner import LogCleaner, CleanResult
from .config import CleanerConfig, CleanStrategy


def _print_banner() -> None:
    print("=" * 60)
    print("🧹 MonBot 日志清理工具")
    print("=" * 60)
    print()


def _print_statistics(stats: dict) -> None:
    print("\n📊 日志目录统计:")
    print(f"  总文件数: {stats['total_files']}")
    print(f"  总大小:   {stats['total_size_mb']:.2f} MB")
    print(f"\n  按类型:")
    print(f"    彩色日志:   {stats['by_type']['colored']} 个")
    print(f"    纯文本日志: {stats['by_type']['plain']} 个")
    print(f"    备份文件:   {stats['by_type']['backup']} 个")
    if stats['by_module']:
        print(f"\n  按模块:")
        for module, info in stats['by_module'].items():
            print(f"    {module}: {info['count']} 个文件, {info['size_mb']:.2f} MB")


def _print_result(result: CleanResult) -> None:
    print("\n" + "=" * 60)
    print("✅ 清理完成!")
    print("=" * 60)
    print(f"  扫描文件: {result.total_files} 个")
    print(f"  删除文件: {result.deleted_files} 个")
    print(f"  释放空间: {result.freed_space_mb:.2f} MB")

    if result.errors:
        print(f"\n⚠️  遇到 {len(result.errors)} 个错误:")
        for err in result.errors[:5]:
            print(f"  - {err}")
        if len(result.errors) > 5:
            print(f"  ... 还有 {len(result.errors) - 5} 个错误")


def main() -> None:
    parser = ArgumentParser(description="MonBot 日志清理工具")

    # 日志目录
    parser.add_argument(
        '--log-dir', '-l',
        type=str,
        required=True,
        help='日志根目录路径'
    )

    # 清理策略
    parser.add_argument(
        '--strategy', '-s',
        choices=['age', 'size', 'count', 'all'],
        default='age',
        help='清理策略 (默认: age)'
    )

    # 时间相关
    parser.add_argument('--days', '-d', type=int, default=7,
                        help='保留最近N天的日志 (默认: 7)')

    # 大小相关
    parser.add_argument('--max-size', type=int, help='总大小限制 (MB)')
    parser.add_argument('--max-file-size', type=int, help='单文件大小限制 (MB)')

    # 数量相关
    parser.add_argument('--max-backups', type=int, default=5,
                        help='每个日志最多保留N个备份 (默认: 5)')

    # 模块过滤
    parser.add_argument('--modules', nargs='+', help='只清理指定模块')
    parser.add_argument('--exclude', nargs='+', help='排除指定模块')

    # 文件类型
    parser.add_argument('--no-colored', action='store_true', help='不清理彩色日志')
    parser.add_argument('--no-plain', action='store_true', help='不清理纯文本日志')
    parser.add_argument('--no-backups', action='store_true', help='不清理备份文件')

    # 安全选项
    parser.add_argument('--dry-run', action='store_true', help='演习模式，不实际删除')
    parser.add_argument('--confirm', action='store_true', help='删除前需要确认')
    parser.add_argument('--no-keep-latest', action='store_true', help='不保留最新日志')

    # 统计
    parser.add_argument('--stats', action='store_true', help='只显示统计信息')

    args = parser.parse_args()

    _print_banner()

    config = CleanerConfig(
        log_root=Path(args.log_dir),
        dry_run=args.dry_run,
        max_age_days=args.days,
        max_total_size_mb=args.max_size,
        max_file_size_mb=args.max_file_size,
        max_backup_count=args.max_backups,
        include_modules=args.modules,
        exclude_modules=args.exclude,
        clean_colored=not args.no_colored,
        clean_plain=not args.no_plain,
        clean_backups=not args.no_backups,
        keep_latest=not args.no_keep_latest,
        confirm_before_delete=args.confirm,
    )

    cleaner = LogCleaner(config)

    if args.stats:
        _print_statistics(cleaner.get_statistics())
        return

    # 显示配置摘要
    print("📋 清理配置:")
    print(f"  日志目录: {config.log_root}")
    print(f"  清理策略: {args.strategy}")
    if args.strategy == 'age':
        print(f"  保留天数: {config.max_age_days} 天")
    elif args.strategy == 'size':
        if config.max_total_size_mb:
            print(f"  总大小限制: {config.max_total_size_mb} MB")
        if config.max_file_size_mb:
            print(f"  单文件限制: {config.max_file_size_mb} MB")
    elif args.strategy == 'count':
        print(f"  最大备份数: {config.max_backup_count}")
    if config.include_modules:
        print(f"  包含模块: {', '.join(config.include_modules)}")
    if config.exclude_modules:
        print(f"  排除模块: {', '.join(config.exclude_modules)}")
    print(f"  演习模式: {'是' if config.dry_run else '否'}")
    print(f"  保留最新: {'是' if config.keep_latest else '否'}")
    print()

    strategy_map = {
        'age':   CleanStrategy.BY_AGE,
        'size':  CleanStrategy.BY_SIZE,
        'count': CleanStrategy.BY_COUNT,
        'all':   CleanStrategy.ALL,
    }

    print("🚀 开始清理...")
    result = cleaner.clean(strategy_map[args.strategy])
    _print_result(result)


if __name__ == "__main__":
    main()
