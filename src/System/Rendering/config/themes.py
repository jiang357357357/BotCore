"""
主题和样式配置
"""

from rich.theme import Theme


# MonBot 主题配置
MONBOT_THEME = {
    # 基础消息样式
    "info": "green",
    "warning": "yellow",
    "error": "red",
    "critical": "bold white on red",
    
    # 模块标签样式
    "tag.botcore": "bold blue",
    "tag.napcat": "bold cyan",
    "tag.moncore": "bold magenta",
    "tag.system": "bold green",
    
    # 表格样式
    "table.header": "bold cyan",
    "table.title": "bold white",
    "table.border": "dim",
    
    # 面板样式
    "panel.border": "dim blue",
    "panel.title": "bold",
    
    # 状态样式
    "status.ok": "green",
    "status.warn": "yellow",
    "status.fail": "red",
    "status.dim": "dim white",
    
    # 特殊样式
    "highlight": "bold yellow",
    "dim": "dim",
}


# 创建 Rich 主题对象
monbot_theme = Theme(MONBOT_THEME)


def get_style(name: str) -> str:
    """
    获取样式字符串
    
    Args:
        name: 样式名称
        
    Returns:
        样式字符串
    """
    return MONBOT_THEME.get(name, "")
