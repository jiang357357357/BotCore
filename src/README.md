# MonBot 源码目录

MonBot 是一个基于 NoneBot2 的 QQ 机器人，通过 NapCat 协议与 QQ 通信，通过 WebSocket 与 MonCore 后端交互。

## 目录结构

```
src/
├── plugins/          # NoneBot 插件目录
│   └── BotCore/     # 核心业务插件（唯一的 NoneBot 插件）
└── System/          # 系统工具库（与 NoneBot 无关）
    ├── Logs/        # 日志系统
    ├── Rendering/   # 渲染系统（终端 → SVG）
    └── LogCleaner/  # 日志清理工具
```

## 架构说明

### plugins/ - NoneBot 插件目录

NoneBot 会扫描此目录下的所有子包，将其作为插件加载。目前只有一个插件：`BotCore`。

- 由 `bot.py` 中的 `nonebot.load_plugins("src/plugins")` 触发加载
- 插件必须有 `__init__.py` 才会被识别
- 插件加载时会执行 `__init__.py` 中的代码，注册事件处理器

### System/ - 系统工具库

独立的工具模块，不依赖 NoneBot，可以被任何 Python 代码导入使用。

- `Logs/`：彩色日志系统，基于 Python logging
- `Rendering/`：终端渲染系统，支持 Rich 对象 → ANSI → SVG 导出
- `LogCleaner/`：日志文件清理工具，支持按大小/时间清理

## 依赖关系

```
plugins/BotCore/  →  System/Logs
                  →  System/Rendering (未使用)

System/Logs       →  无依赖
System/Rendering  →  无依赖
System/LogCleaner →  无依赖
```

## 启动流程

```
bot.py
  → nonebot.load_plugins("src/plugins")
  → 加载 plugins/BotCore/
      → 执行 BotCore/__init__.py
          → 导入 BotCore/app.py
              → 初始化全局单例
              → 注册生命周期钩子
              → 导入路由模块（commands / message_handlers）
                  → 注册 NoneBot 事件处理器
```

## 开发指南

### 添加新的工具模块

在 `System/` 下创建新目录，例如 `System/NewTool/`：

```python
# System/NewTool/__init__.py
from .main import some_function

__all__ = ["some_function"]
```

在其他地方使用：

```python
from src.System.NewTool import some_function
```

### 添加新的 NoneBot 插件

在 `plugins/` 下创建新目录，例如 `plugins/NewPlugin/`：

```python
# plugins/NewPlugin/__init__.py
from nonebot import on_command

test_cmd = on_command("test")

@test_cmd.handle()
async def handle_test():
    await test_cmd.finish("测试成功")
```

NoneBot 会自动加载并注册。

## 注意事项

1. `plugins/` 下的代码会被 NoneBot 扫描，不要放非插件代码
2. `System/` 下的代码是纯工具库，不应该依赖 NoneBot
3. 所有路径导入都使用 `src.` 开头的绝对导入
4. 日志统一使用 `from src.System.Logs import get_logger`
