"""群聊和私聊共用的本地许可规则。"""

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent

from src.System.Logs import get_logger

from ...app import bot_config

logger = get_logger(__name__)


def is_allowed_by_local_policy(event: MessageEvent) -> bool:
    """先检查拒绝列表，再按默认许可开关检查允许列表。"""
    try:
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            if group_id in (bot_config.group_deny_list or []):
                logger.info(f"群聊 {group_id} 命中本地拒绝列表，跳过处理")
                return False
            if not bot_config.group_default_permit:
                allowed = group_id in (bot_config.group_allow_list or [])
                if not allowed:
                    logger.info(f"群聊 {group_id} 未在本地认可列表中，跳过处理")
                return allowed
            return True

        if isinstance(event, PrivateMessageEvent):
            user_id = str(event.user_id)
            if user_id in (bot_config.private_deny_list or []):
                logger.info(f"私聊 {user_id} 命中本地拒绝列表，跳过处理")
                return False
            if not bot_config.private_default_permit:
                allowed = user_id in (bot_config.private_allow_list or [])
                if not allowed:
                    logger.info(f"私聊 {user_id} 未在本地认可列表中，跳过处理")
                return allowed
            return True

        logger.debug("未知消息类型，本地策略拒绝处理")
        return False
    except Exception as e:
        logger.error(f"本地许可/白黑名单判断出错: {e}", exc_info=True)
        return False
