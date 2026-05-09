"""
群聊消息业务服务
处理群聊消息的存储、AI回复请求等业务逻辑
"""

from src.System.Logs import get_logger
from typing import Optional
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from .base_message_service import BaseMessageService

logger = get_logger(__name__)


class GroupMessageService(BaseMessageService):
    """群聊消息业务服务类"""

    async def handle_keyword_message(self, event: GroupMessageEvent) -> Optional[Message]:
        """
        处理关键词触发消息（@机器人 或 提到机器人名字 或 包含后端关键词）
        存储消息到后端 → 请求回复 → 返回回复消息
        """
        try:
            context = f"群聊 {event.group_id}"
            return await self._store_and_request_reply(event, context)
        except Exception as e:
            logger.error(f"处理群聊关键词消息时出错: {e}", exc_info=True)
            return None

    async def handle_normal_message(self, event: GroupMessageEvent) -> None:
        """
        处理普通群聊消息（只存储，不回复）
        """
        try:
            context = f"群聊 {event.group_id}"
            success = await self._store_message(event, context)
            if success:
                logger.debug(f"普通群聊消息已存储: 群 {event.group_id}, 用户 {event.user_id}")
            else:
                logger.warning(f"普通群聊消息存储失败: 群 {event.group_id}, 用户 {event.user_id}")
        except Exception as e:
            logger.error(f"处理普通群聊消息时出错: {e}", exc_info=True)
