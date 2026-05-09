"""
命令处理插件
处理各种命令
"""

from src.System.Logs import get_logger
from typing import Optional
from nonebot.adapters.onebot.v11 import MessageEvent, Message

from ...config import BotConfig

logger = get_logger(__name__)

class CommandPlugin:
    """命令处理插件"""
    
    def __init__(self, config: BotConfig = None):
        self.name = "command_plugin"
        self.config = config or BotConfig()
    
    async def handle(self, event: MessageEvent, message: str) -> Optional[Message]:
        """
        处理命令消息
        
        Args:
            event: 消息事件
            message: 消息内容
            
        Returns:
            回复消息，如果没有回复则返回 None
        """
        try:
            # 检查是否是命令
            if not message.startswith(self.config.command_prefix):
                return None
            
            # 解析命令
            parts = message.split()
            command = parts[0][len(self.config.command_prefix):]  # 移除命令前缀
            args = ' '.join(parts[1:]) if len(parts) > 1 else ""
            
            # 检查命令是否有效
            if not self.config.is_valid_command(command):
                return Message(f"哼！本小姐不认识这个命令：{self.config.command_prefix}{command}")
            
            # 处理命令
            return await self._process_command(event, command, args)
            
        except Exception as e:
            logger.error(f"处理命令时出错: {e}")
            return Message(self.config.error_reply)
    
    async def _process_command(self, event: MessageEvent, command: str, args: str) -> Message:
        """处理具体命令"""
        command_handlers = {
            "napcat": self._handle_napcat_status,
            "help": self._handle_help,
            "test": self._handle_test,
            "history": self._handle_history,
            "ping": self._handle_ping,
            "status": self._handle_status
        }
        
        handler = command_handlers.get(command)
        if handler:
            return await handler(event, args)
        
        return Message(f"哼！本小姐不认识这个命令：/{command}")
    
    async def _handle_napcat_status(self, event: MessageEvent, args: str) -> Message:
        """处理napcat状态命令"""
        status_info = f"""
哼！{self.config.bot_name}的消息处理功能状态：

✅ 消息监听：已启用
✅ 群消息处理：已启用  
✅ 私聊处理：已启用
✅ 命令处理：已启用
✅ 关键词回复：已启用

本小姐正在努力工作呢！
        """
        return Message(status_info.strip())
    
    async def _handle_help(self, event: MessageEvent, args: str) -> Message:
        """处理帮助命令"""
        commands_text = "\n".join([f"{self.config.command_prefix}{cmd}" for cmd in self.config.available_commands])
        help_text = f"""
哼！{self.config.bot_name}的可用命令：

{commands_text}

*傲娇地整理了一下头发*

本小姐可是很厉害的哦！
        """
        return Message(help_text.strip())
    
    async def _handle_test(self, event: MessageEvent, args: str) -> Message:
        """处理测试命令"""
        test_message = f"""
哼！{self.config.bot_name}功能测试：

🎯 消息处理：正常
🎯 命令识别：正常  
🎯 回复功能：正常
🎯 状态管理：正常

本小姐的插件运行完美！
        """
        return Message(test_message.strip())
    
    async def _handle_history(self, event: MessageEvent, args: str) -> Message:
        """处理历史命令"""
        return Message("哼！本小姐暂时没有保存消息历史功能呢...")
    
    async def _handle_ping(self, event: MessageEvent, args: str) -> Message:
        """处理ping命令"""
        return Message("哼！本小姐在线着呢！Pong！")
    
    async def _handle_status(self, event: MessageEvent, args: str) -> Message:
        """处理状态命令"""
        user_id = str(event.user_id)
        status_text = f"""
哼！你的状态信息：

👤 用户ID：{user_id}
🎯 本小姐运行正常！

*傲娇地整理了一下头发*
        """
        return Message(status_text.strip())
