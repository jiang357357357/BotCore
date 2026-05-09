"""
文本处理插件
处理文本消息
"""

from src.System.Logs import get_logger
from typing import Optional
from nonebot.adapters.onebot.v11 import MessageEvent, Message

from ...config import BotConfig

logger = get_logger(__name__)

class TextPlugin:
    """文本处理插件"""
    
    def __init__(self, config: BotConfig = None):
        self.name = "text_plugin"
        self.config = config or BotConfig()
    
    async def handle(self, event: MessageEvent, message: str) -> Optional[Message]:
        """
        处理文本消息
        
        Args:
            event: 消息事件
            message: 消息内容
            
        Returns:
            回复消息，如果没有回复则返回 None
        """
        try:
            # 检查是否是@机器人的消息
            if self.config.enable_mention_reply and hasattr(event, 'is_tome') and event.is_tome():
                return await self._handle_at_message(event, message)
            
            # 检查消息中是否包含机器人名字
            if self.config.enable_name_mention and self.config.contains_bot_name(message):
                return await self._handle_mention_message(event, message)
            
            # 检查是否是私聊消息
            if hasattr(event, 'message_type') and event.message_type == 'private':
                return await self._handle_private_message(event, message)
            
            # 群聊中如果没有@或提到机器人名字，则不回复
            return None
            
        except Exception as e:
            logger.error(f"处理文本消息时出错: {e}")
            return Message(self.config.error_reply)
    
    async def _handle_at_message(self, event: MessageEvent, message: str) -> Message:
        """处理@机器人的消息"""
        # 移除@标记
        clean_message = message.replace(f"[CQ:at,qq={event.self_id}]", "").strip()
        
        if not clean_message:
            return Message(f"哼！你@{self.config.bot_name}了！有什么事情吗？")
        
        # 检查关键词回复
        response = self.config.get_keyword_response(clean_message)
        if response:
            return Message(response)
        
        return Message(f"哼！你@{self.config.bot_name}说：{clean_message}\n本小姐听到了！")
    
    async def _handle_mention_message(self, event: MessageEvent, message: str) -> Message:
        """处理提到机器人名字的消息"""
        # 检查关键词回复
        response = self.config.get_keyword_response(message)
        if response:
            return Message(response)
        
        return Message(f"哼！你提到{self.config.bot_name}了！有什么事情吗？")
    
    async def _handle_private_message(self, event: MessageEvent, message: str) -> Message:
        """处理私聊消息"""
        if message.strip():
            return Message(self.config.private_reply.format(message=message))
        return Message("哼！你发送了空消息！")
