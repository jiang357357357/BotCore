# System 工具库

独立的系统工具模块，不依赖 NoneBot，可以被任何 Python 代码导入使用。

## 目录结构

```
System/
├── Logs/           # 彩色日志系统
├── Rendering/      # 终端渲染系统（Rich → ANSI → SVG）
└── LogCleaner/     # 日志文件清理工具
```

## Logs - 彩色日志系统

基于 Python `logging` 模块的彩色日志系统，支持终端彩色输出和文件日志。

### 使用方法

```python
from src.System.Logs import get_logger

logger = get_logger(__name__)

logger.debug("调试信息")
logger.info("普通信息")
logger.warning("警告信息")
logger.error("错误信息")
logger.critical("严重错误")
```

### 特性

- 自动彩色输出（终端）
- 支持文件日志
- 自定义日志级别
- 自定义日志格式
- 线程安全

### 配置

日志配置在 `Logs/config/config.py` 中：

```python
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = "logs/monbot.log"
```

## Rendering - 终端渲染系统

将 Rich 可渲染对象（Table、Panel 等）渲染到终端，并支持导出为 SVG 文件。

### 使用方法

#### 渲染面板

```python
from src.System.Rendering import render_panel

render_panel("机器人已启动", title="MonBot 状态")
```

#### 渲染表格

```python
from src.System.Rendering import render_table

headers = ["用户", "消息", "状态"]
rows = [
    ["Alice", "你好", "已发送"],
    ["Bob", "再见", "已发送"]
]

render_table(headers, rows, title="消息列表")
```

#### 导出 SVG

```python
from src.System.Rendering.exporters import export_svg
from src.System.Rendering.renderers.table import _create_table
from pathlib import Path

table = _create_table(headers, rows, title="消息列表")
svg_path = export_svg(table, output_dir=Path("logs/svg"), title="消息列表")
```

### 特性

- 支持 Rich 所有可渲染对象
- 自动适配终端宽度
- ANSI 颜色码 → SVG 转换
- 支持中文字符（全角/半角自动识别）
- 支持表格线字符（圆角、直角等）

### 渲染流程

```
Rich 对象
  → Rich Console 渲染
  → ANSI 颜色码文本
  → 解析 ANSI 码
  → 生成 SVG 元素
  → 输出 SVG 文件
```

## LogCleaner - 日志清理工具

自动清理过期或过大的日志文件。

### 使用方法

#### 命令行

```bash
python -m src.System.LogCleaner.cli --dir logs --max-size 100 --max-age 30
```

#### 代码调用

```python
from src.System.LogCleaner import LogCleaner

cleaner = LogCleaner(
    log_dir="logs",
    max_size_mb=100,
    max_age_days=30
)

cleaner.clean()
```

### 配置

在 `LogCleaner/config.py` 中配置：

```python
DEFAULT_LOG_DIR = "logs"
DEFAULT_MAX_SIZE_MB = 100
DEFAULT_MAX_AGE_DAYS = 30
```

### 特性

- 按文件大小清理
- 按文件时间清理
- 支持通配符匹配
- 安全删除（先检查后删除）
- 详细日志输出

## 依赖关系

```
Logs/         → 无依赖
Rendering/    → 无依赖（依赖 rich 库）
LogCleaner/   → 无依赖
```

所有模块都是独立的，互不依赖。

## 开发指南

### 添加新的工具模块

1. 在 `System/` 下创建新目录，例如 `System/NewTool/`
2. 创建 `__init__.py` 导出公共接口
3. 在其他地方使用：

```python
from src.System.NewTool import some_function
```

### 设计原则

- 无状态：工具函数应该是无状态的，不依赖全局变量
- 独立：不依赖其他 System 模块
- 通用：不包含业务逻辑，只提供通用功能
- 文档：每个模块都应该有清晰的文档和示例

## 注意事项

1. System 模块不应该依赖 NoneBot
2. System 模块不应该依赖 BotCore
3. System 模块之间不应该相互依赖
4. 所有导入都使用 `src.System.` 开头的绝对导入
