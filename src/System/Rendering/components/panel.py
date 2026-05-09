"""
面板渲染模块
"""

from rich.panel import Panel
from rich import box
from typing import Any, Optional

from ..core.printer import print_renderable


def render_panel(
    content: Any,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    border_style: str = "panel.border",
    box_style: Any = box.ROUNDED,
    width: Optional[int] = None,
    expand: bool = False,
) -> None:
    """
    渲染面板到终端
    
    Args:
        content: 面板内容（字符串或 Rich 对象）
        title: 标题
        subtitle: 副标题
        border_style: 边框样式
        box_style: 边框形状（默认 box.ROUNDED）
        width: 固定宽度
        expand: 是否扩展到终端宽度
        
    Example:
        ```python
        from src.plugins.System.Rendering import render_panel
        
        render_panel("机器人已启动", title="MonBot 状态")
        ```
    """
    panel = _create_panel(
        content, title, subtitle, border_style, box_style, width, expand
    )
    print_renderable(panel, title=title, width=width)


def _create_panel(
    content: Any,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    border_style: str = "panel.border",
    box_style: Any = box.ROUNDED,
    width: Optional[int] = None,
    expand: bool = False,
) -> Panel:
    """
    创建 Rich Panel 对象
    
    Args:
        content: 面板内容
        title: 标题
        subtitle: 副标题
        border_style: 边框样式
        box_style: 边框形状
        width: 固定宽度
        expand: 是否扩展
        
    Returns:
        Rich Panel 对象
    """
    return Panel(
        content,
        title=title,
        subtitle=subtitle,
        border_style=border_style,
        box=box_style,
        width=width,
        padding=(0, 1),
        expand=expand,
    )
