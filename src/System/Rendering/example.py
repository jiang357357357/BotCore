"""
渲染系统使用示例
"""

from table import render_table
from panel import render_panel
from rich import box


def example_table():
    """表格渲染示例"""
    print("\n=== 表格渲染示例 ===\n")
    
    # 基础表格
    headers = ["用户", "消息", "状态"]
    rows = [
        ["Alice", "你好", "✓"],
        ["Bob", "早上好", "✓"],
        ["Charlie", "晚安", "✓"],
    ]
    
    render_table(headers, rows, title="消息列表")
    
    # 带行分隔线的表格
    print("\n")
    headers2 = ["时间", "模块", "级别", "消息"]
    rows2 = [
        ["14:30:25", "BotCore", "INFO", "收到消息"],
        ["14:30:26", "NapCat", "INFO", "发送回复"],
        ["14:30:27", "MonCore", "INFO", "存储成功"],
    ]
    
    render_table(headers2, rows2, title="日志记录", show_lines=True)


def example_panel():
    """面板渲染示例"""
    print("\n=== 面板渲染示例 ===\n")
    
    # 基础面板
    render_panel(
        "机器人已成功连接到 NapCat",
        title="MonBot 状态"
    )
    
    # 带副标题的面板
    print("\n")
    status_text = """
✓ NapCat 连接正常
✓ MonCore 连接正常
✓ 消息处理器运行中
⏳ 语音模块初始化中
    """
    
    render_panel(
        status_text,
        title="系统状态",
        subtitle="2024-03-17 14:30:00",
        box_style=box.DOUBLE
    )


def example_custom_styles():
    """自定义样式示例"""
    print("\n=== 自定义样式示例 ===\n")
    
    headers = ["配置项", "值"]
    rows = [
        ["机器人名称", "星野唯"],
        ["命令前缀", "/"],
        ["语音模式", "已启用"],
        ["日志级别", "INFO"],
    ]
    
    styles = {
        "header_style": "bold magenta",
        "title_style": "bold yellow",
        "border_style": "cyan",
    }
    
    render_table(headers, rows, title="当前配置", styles=styles)


if __name__ == "__main__":
    example_table()
    example_panel()
    example_custom_styles()
    
    print("\n✅ 示例运行完成！\n")
