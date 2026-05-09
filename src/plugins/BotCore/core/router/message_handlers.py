"""
消息处理器（路由层）
使用 on_message() 处理关键词触发和普通消息，负责消息分发
"""

from nonebot import on_message
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import MessageEvent, Message, GroupMessageEvent, PrivateMessageEvent

from ..business.message import PrivateMessageService, GroupMessageService
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


def _get_supported_contacts():
    """延迟获取后端支持的联系人列表"""
    from ...app import get_supported_contacts
    return get_supported_contacts()


def _get_supported_groups():
    """延迟获取后端支持的群聊列表"""
    from ...app import get_supported_groups
    return get_supported_groups()


def _is_allowed_by_local_policy(event: MessageEvent) -> bool:
    """
    本地许可/白黑名单过滤（在后端 supported_contacts/groups 过滤前执行）

    规则：
    - 群聊：
      - group_default_permit=True：默认允许；若群号在 group_deny_list 则拒绝
      - group_default_permit=False：默认拒绝；仅群号在 group_allow_list 才允许
    - 私聊：
      - private_default_permit=True：默认允许；若QQ号在 private_deny_list 则拒绝
      - private_default_permit=False：默认拒绝；仅QQ号在 private_allow_list 才允许
    """
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


def _is_supported_by_backend(event: MessageEvent) -> bool:
    """
    检查消息是否在后端支持的列表中
    
    后端支持的QQ号列表用于过滤消息：
    - 私聊消息：检查发送者QQ号是否在 contacts 中
    - 群聊消息：检查群聊QQ号是否在 groups 中
    
    重要：只有在映射数组中的消息才会被处理，空数组表示没有配置任何映射
    
    Args:
        event: 消息事件
        
    Returns:
        如果消息在后端支持列表中，返回 True；否则返回 False
    """
    try:
        if isinstance(event, GroupMessageEvent):
            # 群聊消息：检查群ID是否在支持的群聊列表中
            group_id = str(event.group_id)
            supported_groups = _get_supported_groups()
            
            # 检查群ID是否在支持列表中
            is_supported = group_id in supported_groups
            if not is_supported:
                logger.info(f"群聊 {group_id} 不在后端支持的列表中（当前支持列表: {supported_groups}），跳过处理")
            else:
                logger.debug(f"群聊 {group_id} 在后端支持的列表中，允许处理")
            return is_supported
            
        elif isinstance(event, PrivateMessageEvent):
            # 私聊消息：检查发送者QQ号是否在支持的联系人列表中
            user_id = str(event.user_id)
            supported_contacts = _get_supported_contacts()
            
            # 检查用户ID是否在支持列表中
            is_supported = user_id in supported_contacts
            if not is_supported:
                logger.info(f"联系人 {user_id} 不在后端支持的列表中（当前支持列表: {supported_contacts}），跳过处理")
            else:
                logger.debug(f"联系人 {user_id} 在后端支持的列表中，允许处理")
            return is_supported
        
        # 其他类型消息，默认拒绝（只处理私聊和群聊）
        logger.debug(f"未知消息类型，拒绝处理")
        return False
        
    except Exception as e:
        logger.error(f"检查消息是否在后端支持列表中时出错: {e}", exc_info=True)
        # 出错时默认拒绝，避免发送不应该发送的消息
        return False


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
async def handle_message(event: MessageEvent):
    """处理所有非命令消息（路由分发）"""
    try:
        # 记录消息接收信息
        message_text = event.get_message().extract_plain_text()
        if isinstance(event, GroupMessageEvent):
            logger.info(f"收到消息 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}, 内容: {message_text[:50]}")
        else:
            logger.info(f"收到消息 - 私聊: 用户: {event.user_id}, 内容: {message_text[:50]}")

        # 本地许可/白黑名单过滤（先过滤，避免不必要的后端交互）
        if not _is_allowed_by_local_policy(event):
            logger.debug("消息被本地策略过滤，跳过处理")
            return

        # 检查消息是否在后端支持的列表中（必须先检查，只有支持的才处理）
        if not _is_supported_by_backend(event):
            logger.debug(f"消息不在后端支持列表中，跳过处理")
            return
        
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
                await message_matcher.finish(reply)
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
                    await message_matcher.finish(reply)
            else:
                logger.warning(f"未知消息类型，无法处理普通消息")
            
    except FinishedException:
        # FinishedException 是 NoneBot 的正常机制，finish() 会抛出此异常来结束 matcher
        # 不需要记录为错误，直接重新抛出让框架处理
        raise
    except Exception as e:
        logger.error(f"处理消息时出错: {e}", exc_info=True)
