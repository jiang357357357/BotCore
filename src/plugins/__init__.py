# MonBot 插件包
# BotCore 是 NoneBot 插件，启动时必须导入以注册事件处理器
# System 下的工具库（Logs/Rendering/LogCleaner）由各模块按需导入

from . import BotCore

__all__ = ["BotCore"]
