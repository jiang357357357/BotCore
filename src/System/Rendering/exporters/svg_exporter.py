"""
SVG 导出器
将 Rich 可渲染对象导出为 SVG 文件
"""

import sys
import shutil
from io import StringIO
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from rich.console import Console

from ..config.themes import monbot_theme
from ..core.canvas import create_svg


def export_svg(
    renderable: Any,
    output_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    title: Optional[str] = None,
    width: Optional[int] = None,
) -> Optional[Path]:
    """
    将 Rich 可渲染对象导出为 SVG 文件

    Args:
        renderable: Rich 可渲染对象（Table、Panel 等）
        output_path: 指定输出文件路径（优先级高于 output_dir）
        output_dir: 输出目录（自动生成文件名）
        title: SVG 标题
        width: 渲染宽度

    Returns:
        保存的 SVG 文件路径，失败时返回 None

    Example:
        ```python
        from src.plugins.System.Rendering.exporters import export_svg
        from src.plugins.System.Rendering.renderers.table import _create_table

        table = _create_table(["用户", "消息"], [["Alice", "你好"]], title="消息列表")
        path = export_svg(table, output_dir=Path("logs/svg"), title="消息列表")
        ```
    """
    # 计算渲染宽度
    terminal_width = 100
    try:
        terminal_size = shutil.get_terminal_size()
        if terminal_size.columns > 40:
            terminal_width = terminal_size.columns
    except OSError:
        pass

    final_width = width or min(terminal_width, 120)

    # 捕获带 ANSI 颜色码的文本
    try:
        buffer = StringIO()
        console = Console(
            theme=monbot_theme,
            width=final_width,
            file=buffer,
            force_terminal=True,
            legacy_windows=False,
            no_color=False,
        )
        console.print(renderable)
        ansi_text = buffer.getvalue()
    except Exception as e:
        print(f"❌ [Rendering] 捕获渲染内容失败: {e}", file=sys.stderr)
        return None

    # 生成 SVG
    try:
        svg_content = create_svg(ansi_text, title=title, width=final_width)
    except Exception as e:
        print(f"❌ [Rendering] SVG 生成失败: {e}", file=sys.stderr)
        return None

    # 确定输出路径
    if output_path:
        svg_file = Path(output_path)
    elif output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"render_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.svg"
        svg_file = output_dir / filename
    else:
        print("❌ [Rendering] 必须指定 output_path 或 output_dir", file=sys.stderr)
        return None

    # 写入文件
    try:
        svg_file.parent.mkdir(parents=True, exist_ok=True)
        svg_file.write_text(svg_content, encoding="utf-8")
        return svg_file
    except Exception as e:
        print(f"❌ [Rendering] SVG 写入失败: {svg_file} - {e}", file=sys.stderr)
        return None
