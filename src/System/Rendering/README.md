# MonBot 渲染系统

MonBot 的终端渲染模块，基于 Rich 库提供彩色终端输出功能。

## 特性

- 🎨 彩色终端输出
- 📊 表格渲染
- 📦 面板渲染
- 🎯 统一主题管理
- 🔧 简单易用的 API

## 快速开始

### 渲染表格

```python
from src.plugins.System.Rendering import render_table

headers = ["用户", "消息", "状态"]
rows = [
    ["Alice", "你好", "✓"],
    ["Bob", "早上好", "✓"],
    ["Charlie", "晚安", "✓"],
]

render_table(headers, rows, title="消息列表")
```

### 渲染面板

```python
from src.plugins.System.Rendering import render_panel

render_panel(
    "机器人已成功连接到 NapCat",
    title="MonBot 状态",
    subtitle="v1.0.0"
)
```

### 自定义样式

```python
from src.plugins.System.Rendering import render_table, get_style

# 使用预定义样式
header_style = get_style("table.header")

# 自定义样式字典
styles = {
    "header_style": "bold cyan",
    "title_style": "bold white",
    "border_style": "dim blue",
}

render_table(headers, rows, title="自定义表格", styles=styles)
```

## API 文档

### render_table()

渲染表格到终端。

**参数：**
- `headers` (List[str]): 列标题列表
- `rows` (List[List[Any]]): 数据行列表
- `title` (Optional[str]): 表格标题
- `box_style` (Any): 边框样式（默认 `box.SQUARE`）
- `styles` (Optional[Dict[str, str]]): 样式字典
- `width` (Optional[int]): 渲染宽度
- `show_lines` (bool): 是否显示行分隔线

### render_panel()

渲染面板到终端。

**参数：**
- `content` (Any): 面板内容
- `title` (Optional[str]): 标题
- `subtitle` (Optional[str]): 副标题
- `border_style` (str): 边框样式（默认 "panel.border"）
- `box_style` (Any): 边框形状（默认 `box.ROUNDED`）
- `width` (Optional[int]): 固定宽度
- `expand` (bool): 是否扩展到终端宽度

### get_style()

获取预定义样式字符串。

**参数：**
- `name` (str): 样式名称

**返回：**
- `str`: 样式字符串

## 可用样式

### 基础消息样式
- `info` - 绿色
- `warning` - 黄色
- `error` - 红色
- `critical` - 白字红底

### 模块标签样式
- `tag.botcore` - 粗体蓝色
- `tag.napcat` - 粗体青色
- `tag.moncore` - 粗体紫色
- `tag.system` - 粗体绿色

### 表格样式
- `table.header` - 粗体青色
- `table.title` - 粗体白色
- `table.border` - 暗淡

### 面板样式
- `panel.border` - 暗淡蓝色
- `panel.title` - 粗体

### 状态样式
- `status.ok` - 绿色
- `status.warn` - 黄色
- `status.fail` - 红色
- `status.dim` - 暗淡白色

## 边框样式

Rich 提供多种边框样式：

```python
from rich import box

# 方形边框（默认）
box.SQUARE

# 圆角边框
box.ROUNDED

# 双线边框
box.DOUBLE

# 粗边框
box.HEAVY

# 简单边框
box.SIMPLE

# 最小边框
box.MINIMAL
```

## 架构设计

```
Rendering/
├── __init__.py      # 导出接口
├── themes.py        # 主题和样式配置
├── printer.py       # 核心输出逻辑
├── table.py         # 表格渲染
├── panel.py         # 面板渲染
└── README.md        # 文档
```

## 使用示例

### 消息统计表格

```python
from src.plugins.System.Rendering import render_table

headers = ["时间", "用户", "消息类型", "状态"]
rows = [
    ["14:30:25", "Alice", "文本", "✓"],
    ["14:30:28", "Bob", "图片", "✓"],
    ["14:30:32", "Charlie", "语音", "⏳"],
]

render_table(headers, rows, title="消息处理统计", show_lines=True)
```

### 状态面板

```python
from src.plugins.System.Rendering import render_panel
from rich import box

status_text = """
✓ NapCat 连接正常
✓ MonCore 连接正常
✓ 消息处理器运行中
⏳ 语音模块初始化中
"""

render_panel(
    status_text,
    title="MonBot 系统状态",
    subtitle="2024-03-17 14:30:00",
    box_style=box.DOUBLE
)
```

### 配置信息表格

```python
from src.plugins.System.Rendering import render_table

headers = ["配置项", "值"]
rows = [
    ["机器人名称", "星野唯"],
    ["命令前缀", "/"],
    ["语音模式", "已启用"],
    ["日志级别", "INFO"],
]

render_table(headers, rows, title="当前配置")
```

## 依赖

- `rich>=13.0.0` - 终端渲染库

## 注意事项

1. 确保终端支持 ANSI 颜色码（Windows 10+ 默认支持）
2. 表格宽度会自动适应终端大小
3. 可以通过 `width` 参数手动指定渲染宽度
4. 使用 `show_lines=True` 可以显示行分隔线，适合数据较多的表格
