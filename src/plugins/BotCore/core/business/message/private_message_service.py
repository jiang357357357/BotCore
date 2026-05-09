"""
私聊消息业务服务
处理私聊消息的存储、AI回复请求等业务逻辑
"""

from src.System.Logs import get_logger
from typing import Optional
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, Message

from .base_message_service import BaseMessageService

logger = get_logger(__name__)


class PrivateMessageService(BaseMessageService):
    """私聊消息业务服务类"""

    async def handle_keyword_message(self, event: PrivateMessageEvent) -> Optional[Message]:
        """
        处理关键词触发消息（提到机器人名字 或 包含后端关键词）
        存储消息到后端 → 请求回复 → 返回回复消息
        """
        try:
            context = f"私聊 {event.user_id}"
            return await self._store_and_request_reply(event, context)
        except Exception as e:
            logger.error(f"处理私聊关键词消息时出错: {e}", exc_info=True)
            return None

    async def handle_normal_message(self, event: PrivateMessageEvent) -> Optional[Message]:
        """
        处理普通私聊消息
        存储消息到后端 → 请求回复 → 返回回复消息（私聊每次都要回复）
        """
        try:
            context = f"私聊 {event.user_id}"
            return await self._store_and_request_reply(event, context)
        except Exception as e:
            logger.error(f"处理普通私聊消息时出错: {e}", exc_info=True)
            return None
