"""
表格渲染模块
"""

from rich.table import Table
from rich import box
from typing import List, Optional, Dict, Any

from ..core.printer import print_renderable


def render_table(
    headers: List[str],
    rows: List[List[Any]],
    title: Optional[str] = None,
    box_style: Any = box.SQUARE,
    styles: Optional[Dict[str, str]] = None,
    width: Optional[int] = None,
    show_lines: bool = False,
) -> None:
    """
    渲染表格到终端
    
    Args:
        headers: 列标题列表
        rows: 数据行列表
        title: 表格标题
        box_style: 边框样式（默认 box.SQUARE）
        styles: 样式字典（header_style, title_style, border_style）
        width: 渲染宽度
        show_lines: 是否显示行分隔线
        
    Example:
        ```python
        from src.plugins.System.Rendering import render_table
        
        headers = ["用户", "消息", "状态"]
        rows = [
            ["Alice", "你好", "✓"],
            ["Bob", "早上好", "✓"],
        ]
        
        render_table(headers, rows, title="消息列表")
        ```
    """
    table = _create_table(headers, rows, title, box_style, styles, show_lines)
    print_renderable(table, title=title, width=width)


def _create_table(
    headers: List[str],
    rows: List[List[Any]],
    title: Optional[str] = None,
    box_style: Any = box.SQUARE,
    styles: Optional[Dict[str, str]] = None,
    show_lines: bool = False,
) -> Table:
    """
    创建 Rich Table 对象
    
    Args:
        headers: 列标题列表
        rows: 数据行列表
        title: 表格标题
        box_style: 边框样式
        styles: 样式字典
        show_lines: 是否显示行分隔线
        
    Returns:
        Rich Table 对象
    """
    styles = styles or {}
    
    table = Table(
        title=title,
        box=box_style,
        show_header=True,
        header_style=styles.get("header_style", "table.header"),
        title_style=styles.get("title_style", "table.title"),
        border_style=styles.get("border_style", "table.border"),
        expand=False,
        show_lines=show_lines,
    )
    
    # 添加列
    for header in headers:
        table.add_column(header, style=styles.get(header, ""), overflow="fold")
    
    # 添加行
    for row in rows:
        str_row = [str(item) for item in row]
        table.add_row(*str_row)
    
    return table
