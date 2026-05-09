"""
消息业务服务（统一接口，保持向后兼容）
处理消息存储、AI回复请求等业务逻辑
"""

from src.System.Logs import get_logger
from typing import Optional
from nonebot.adapters.onebot.v11 import MessageEvent, Message

from .base_message_service import BaseMessageService

logger = get_logger(__name__)


class MessageService(BaseMessageService):
    """消息业务服务类（统一接口）"""

    async def handle_keyword_message(self, event: MessageEvent) -> Optional[Message]:
        """
        处理关键词触发消息（@机器人 或 提到机器人名字）
        存储消息到后端 → 请求回复 → 返回回复消息
        """
        try:
            return await self._store_and_request_reply(event, "消息")
        except Exception as e:
            logger.error(f"处理关键词消息时出错: {e}", exc_info=True)
            return None

    async def handle_normal_message(self, event: MessageEvent) -> None:
        """处理普通消息（只存储，不回复）"""
        try:
            success = await self._store_message(event, "消息")
            if success:
                logger.debug(f"普通消息已存储: {event.get_message().extract_plain_text()[:50]}")
            else:
                logger.warning("普通消息存储失败")
        except Exception as e:
            logger.error(f"处理普通消息时出错: {e}", exc_info=True)
