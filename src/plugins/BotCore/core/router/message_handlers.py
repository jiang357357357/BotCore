"""
消息处理器（路由层）
使用 on_message() 处理关键词触发和普通消息，负责消息分发
"""

from nonebot import on_message
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, GroupMessageEvent, PrivateMessageEvent

from ..business.message import PrivateMessageService, GroupMessageService
from .backend_policy import is_allowed_by_backend
from src.System.Logs import get_logger

# 从 app 导入全局单例，避免重复实例化
from ...app import bot_config, storage, napcat_api

# 初始化业务服务（分别处理私聊和群聊）
private_message_service = PrivateMessageService(bot_config, storage, napcat_api)
group_message_service = GroupMessageService(bot_config, storage, napcat_api)

logger = get_logger(__name__)

# 创建消息监听器（优先级较低，命令优先处理）
# 注意：on_message() 默认 block=True，会阻断后续响应器
# 这里我们想要处理所有非命令消息（关键词和普通消息），所以保持 block=True
message_matcher = on_message(priority=10, block=True)


async def _finish_reply(bot: Bot, event: MessageEvent, reply: Message, reason: str) -> None:
    """发送回复；无论正文是否包含换行，都只发送一次 QQ 消息。"""
    await message_matcher.finish(reply)


def _get_supported_keywords():
    """延迟获取后端支持的关键词列表"""
    from ...app import get_supported_keywords
    return get_supported_keywords()


def _is_keyword_trigger(event: MessageEvent) -> bool:
    """
    判断是否为关键词触发
    
    触发条件：
    - 群聊：@机器人 或 提到机器人名字 或 消息中包含后端维护的关键词
    - 私聊：提到机器人名字 或 消息中包含后端维护的关键词
    
    Args:
        event: 消息事件
        
    Returns:
        是否为关键词触发
    """
    try:
        plain_text = event.get_message().extract_plain_text()
        
        # 群消息：检查@机器人、名字和后端关键词
        if isinstance(event, GroupMessageEvent):
            # 检查是否@了机器人
            if bot_config.enable_mention_reply and event.is_tome():
                logger.debug(f"关键词触发: @机器人 - 群聊: {event.group_id}, 用户: {event.user_id}")
                return True
            
            # 检查消息中是否包含机器人名字
            if bot_config.enable_name_mention and bot_config.contains_bot_name(plain_text):
                logger.debug(f"关键词触发: 提到机器人名字 - 群聊: {event.group_id}, 用户: {event.user_id}")
                return True
            
            # 检查消息中是否包含后端维护的关键词
            supported_keywords = _get_supported_keywords()
            if supported_keywords and plain_text:
                for keyword in supported_keywords:
                    if keyword and keyword in plain_text:
                        logger.debug(f"关键词触发: 包含后端关键词 '{keyword}' - 群聊: {event.group_id}, 用户: {event.user_id}")
                        return True
        
        # 私聊消息：检查名字和后端关键词（私聊没有@的概念）
        elif isinstance(event, PrivateMessageEvent):
            # 检查消息中是否包含机器人名字
            if bot_config.enable_name_mention and bot_config.contains_bot_name(plain_text):
                logger.debug(f"关键词触发: 提到机器人名字 - 私聊: 用户: {event.user_id}")
                return True
            
            # 检查消息中是否包含后端维护的关键词
            supported_keywords = _get_supported_keywords()
            if supported_keywords and plain_text:
                for keyword in supported_keywords:
                    if keyword and keyword in plain_text:
                        logger.debug(f"关键词触发: 包含后端关键词 '{keyword}' - 私聊: 用户: {event.user_id}")
                        return True
        
        return False
        
    except Exception as e:
        logger.error(f"判断关键词触发时出错: {e}")
        return False


@message_matcher.handle()
async def handle_message(bot: Bot, event: MessageEvent):
    """处理所有非命令消息（路由分发）"""
    try:
        from .commands import is_command_message
        if is_command_message(event):
            return
        # 记录消息接收信息
        message_text = event.get_message().extract_plain_text()
        if isinstance(event, GroupMessageEvent):
            logger.info(f"收到消息 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}, 内容: {message_text[:50]}")
        else:
            logger.info(f"收到消息 - 私聊: 用户: {event.user_id}, 内容: {message_text[:50]}")

        if not await is_allowed_by_backend(event):
            logger.info("Core 未批准本次消息或服务不可用，请检查 Web QQBot 权限")
            return

        from ..business.message.napcat_input import send_input_feedback
        await send_input_feedback(event)
        
        # 判断是否为关键词触发
        if _is_keyword_trigger(event):
            logger.info(f"检测到关键词触发 - 用户: {event.user_id}")
            # 根据消息类型路由到不同的业务服务
            if isinstance(event, GroupMessageEvent):
                reply = await group_message_service.handle_keyword_message(event)
            elif isinstance(event, PrivateMessageEvent):
                reply = await private_message_service.handle_keyword_message(event)
            else:
                logger.warning(f"未知消息类型，无法处理关键词触发")
                return
            
            if reply:
                logger.info(f"已生成回复 - 用户: {event.user_id}")
                await _finish_reply(bot, event, reply, "关键词回复")
            else:
                logger.debug(f"未生成回复 - 用户: {event.user_id}")
        else:
            logger.debug(f"普通消息处理 - 用户: {event.user_id}")
            # 根据消息类型路由到不同的业务服务
            if isinstance(event, GroupMessageEvent):
                # 群聊普通消息：只存储，不回复
                await group_message_service.handle_normal_message(event)
            elif isinstance(event, PrivateMessageEvent):
                # 私聊普通消息：存储并回复（私聊每次都要回复）
                reply = await private_message_service.handle_normal_message(event)
                if reply:
                    logger.info(f"已生成回复（私聊普通消息） - 用户: {event.user_id}")
                    await _finish_reply(bot, event, reply, "私聊普通回复")
            else:
                logger.warning(f"未知消息类型，无法处理普通消息")
            
    except FinishedException:
        # FinishedException 是 NoneBot 的正常机制，finish() 会抛出此异常来结束 matcher
        # 不需要记录为错误，直接重新抛出让框架处理
        raise
    except Exception as e:
        logger.error(f"处理消息时出错: {e}", exc_info=True)
