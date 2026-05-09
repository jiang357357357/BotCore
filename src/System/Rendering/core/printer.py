"""
核心输出模块
负责将 Rich 对象输出到终端
"""

import sys
import shutil
from typing import Any, Optional
from rich.console import Console

from ..config.themes import monbot_theme


def print_renderable(
    renderable: Any,
    title: Optional[str] = None,
    width: Optional[int] = None,
) -> None:
    """
    输出 Rich 可渲染对象到终端
    
    Args:
        renderable: Rich 可渲染对象（Table、Panel等）
        title: 标题（可选）
        width: 渲染宽度（可选）
    """
    # 自动计算宽度
    terminal_width = 100
    max_width = 120
    
    try:
        terminal_size = shutil.get_terminal_size()
        if terminal_size.columns > 40:
            terminal_width = terminal_size.columns
    except OSError:
        pass
    
    final_width = width or min(terminal_width, max_width)
    
    # 创建控制台并输出
    console = Console(
        theme=monbot_theme,
        force_terminal=True,
        width=final_width,
        file=sys.stdout
    )
    
    sys.stdout.flush()
    console.print(renderable)
    sys.stdout.flush()
