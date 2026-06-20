"""
命令处理器（路由层）
使用 on_command() 注册所有命令，每个命令独立注册，负责命令分发
"""

from nonebot import on_command
from nonebot import get_driver
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import MessageEvent, Message, GroupMessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg

from ..business import CommandService
from src.System.Logs import get_logger

# 从 app 导入全局单例
from ...app import bot_config, napcat_api

# 初始化业务服务（传入 napcat_api）
command_service = CommandService(bot_config, napcat_api)

logger = get_logger(__name__)


def _is_superuser(event: MessageEvent) -> bool:
    try:
        return str(event.user_id) in get_driver().config.superusers
    except Exception as e:
        logger.warning(f"命令权限检查 superuser 失败: {e}")
        return False


def _is_supported_scope(event: MessageEvent) -> bool:
    """读类命令：superuser 或后端支持的群/联系人可用。"""
    if _is_superuser(event):
        return True
    try:
        from ...app import get_supported_contacts, get_supported_groups

        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            user_id = str(event.user_id)
            return group_id in get_supported_groups() or user_id in get_supported_contacts()
        if isinstance(event, PrivateMessageEvent):
            return str(event.user_id) in get_supported_contacts()
    except Exception as e:
        logger.warning(f"命令作用域检查失败: {e}")
    return False


async def _ensure_command_backend_ready(event: MessageEvent) -> bool:
    try:
        from ...app import ensure_moncore_ready
        return await ensure_moncore_ready(f"收到命令: user={event.user_id}")
    except Exception as e:
        logger.error(f"命令触发时恢复 MonCore 失败: {e}", exc_info=True)
        return False


async def _require_read_permission(matcher, event: MessageEvent) -> bool:
    if not _is_supported_scope(event):
        await matcher.finish(Message("当前群/联系人未在 Bot 后端支持列表中，无法使用这个命令。"))
        return False
    return True


async def _require_admin_permission(matcher, event: MessageEvent) -> bool:
    if not _is_superuser(event):
        await matcher.finish(Message("这个命令需要管理员权限。"))
        return False
    return True


def _extract_target_qq(event: MessageEvent, args: Message) -> str:
    """从命令参数中提取目标 QQ；默认当前发送者。"""
    try:
        for segment in args:
            if segment.type != "at":
                continue
            qq = str(segment.data.get("qq") or "").strip()
            if qq and qq.lower() != "all":
                return qq
    except Exception as e:
        logger.debug(f"解析命令 @ 参数失败: {e}")

    text = args.extract_plain_text().strip() if args else ""
    for item in text.replace("，", " ").replace(",", " ").split():
        if item.isdigit() and 5 <= len(item) <= 12:
            return item
    return str(event.user_id)


async def _require_target_permission(matcher, event: MessageEvent, target_qq: str) -> bool:
    """普通用户只能查自己；superuser 可以查指定 QQ。"""
    if str(target_qq) == str(event.user_id):
        return True
    if _is_superuser(event):
        return True
    await matcher.finish(Message("只能查询自己的信息；查询别人需要管理员权限。"))
    return False


# ==================== /帮助 命令 ====================

# 注意：on_command() 默认 block=False，必须显式设置 block=True
# 这样命令处理完后会阻断事件传递，避免消息处理器重复处理
help_cmd = on_command("帮助", block=True)

@help_cmd.handle()
async def handle_help(event: MessageEvent, args: Message = CommandArg()):
    """处理帮助命令（路由到业务层）"""
    # 记录命令触发信息
    if isinstance(event, GroupMessageEvent):
        logger.info(f"收到帮助命令 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}")
    else:
        logger.info(f"收到帮助命令 - 私聊: 用户: {event.user_id}")
    
    help_text = await command_service.get_help_text(event)
    await help_cmd.finish(Message(help_text))


# ==================== /角色 命令 ====================

rule_cmd = on_command("角色", aliases={"设定"}, block=True)

@rule_cmd.handle()
async def handle_rule(event: MessageEvent, args: Message = CommandArg()):
    """处理角色信息命令（路由到业务层，从后端获取）"""
    # 记录命令触发信息
    if isinstance(event, GroupMessageEvent):
        logger.info(f"收到角色信息命令 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}")
    else:
        logger.info(f"收到角色信息命令 - 私聊: 用户: {event.user_id}")
    
    if not await _ensure_command_backend_ready(event):
        await rule_cmd.finish(Message("MonCore 当前不可用，暂时无法获取角色信息。"))
        return
    if not await _require_read_permission(rule_cmd, event):
        return

    rule_text = await command_service.get_rule_text(event)
    await rule_cmd.finish(Message(rule_text))


# ==================== /语音 命令 ====================

voice_cmd = on_command("语音", block=True)

@voice_cmd.handle()
async def handle_voice(event: MessageEvent, args: Message = CommandArg()):
    """处理语音模式命令（查询或设置语音模式状态）"""
    # 记录命令触发信息
    if isinstance(event, GroupMessageEvent):
        logger.info(f"收到语音模式命令 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}")
    else:
        logger.info(f"收到语音模式命令 - 私聊: 用户: {event.user_id}")
    
    # 获取命令参数
    args_text = args.extract_plain_text().strip() if args else ""
    
    # 如果有参数，则设置语音模式；否则查询状态
    if args_text:
        if not await _require_admin_permission(voice_cmd, event):
            return
        result_text = command_service.set_voice_mode(args=args_text)
    else:
        if not await _require_read_permission(voice_cmd, event):
            return
        result_text = command_service.get_voice_mode_text()
    
    await voice_cmd.finish(Message(result_text))


# ==================== /好感 命令 ====================

favorability_cmd = on_command("好感", block=True)

@favorability_cmd.handle()
async def handle_favorability(event: MessageEvent, args: Message = CommandArg()):
    """查看指定用户和 Bot 绑定角色之间的好感状态；默认查自己。"""
    if isinstance(event, GroupMessageEvent):
        logger.info(f"收到好感查询命令 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}")
    else:
        logger.info(f"收到好感查询命令 - 私聊: 用户: {event.user_id}")

    if not await _ensure_command_backend_ready(event):
        await favorability_cmd.finish(Message("MonCore 当前不可用，暂时无法查询好感状态。"))
        return
    if not await _require_read_permission(favorability_cmd, event):
        return
    target_qq = _extract_target_qq(event, args)
    if not await _require_target_permission(favorability_cmd, event, target_qq):
        return

    try:
        from ...app import get_moncore_api
        moncore_api = get_moncore_api()
        if not moncore_api:
            await favorability_cmd.finish(Message("MonCore API 未就绪，暂时无法查询好感状态。"))
            return

        data = await moncore_api.get_favorability(event, user_qq_number=target_qq)
        await favorability_cmd.finish(Message(command_service.format_favorability_text(data)))
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"好感查询命令失败: {e}", exc_info=True)
        await favorability_cmd.finish(Message("查询好感状态时出错了。"))


# ==================== /好感排行 命令 ====================

favorability_ranking_cmd = on_command("好感排行", aliases={"好感榜"}, block=True)

@favorability_ranking_cmd.handle()
async def handle_favorability_ranking(event: MessageEvent, args: Message = CommandArg()):
    """查看当前群/当前 Bot 的用户好感总值排行。"""
    if isinstance(event, GroupMessageEvent):
        logger.info(f"收到好感排行命令 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}")
    else:
        logger.info(f"收到好感排行命令 - 私聊: 用户: {event.user_id}")

    if not await _ensure_command_backend_ready(event):
        await favorability_ranking_cmd.finish(Message("MonCore 当前不可用，暂时无法查询好感排行。"))
        return
    if not await _require_read_permission(favorability_ranking_cmd, event):
        return

    try:
        from ...app import get_moncore_api
        moncore_api = get_moncore_api()
        if not moncore_api:
            await favorability_ranking_cmd.finish(Message("MonCore API 未就绪，暂时无法查询好感排行。"))
            return

        data = await moncore_api.get_favorability_ranking(event)
        await favorability_ranking_cmd.finish(Message(command_service.format_favorability_ranking_text(data)))
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"好感排行命令失败: {e}", exc_info=True)
        await favorability_ranking_cmd.finish(Message("查询好感排行时出错了。"))


# ==================== /记忆 命令 ====================

memory_cmd = on_command("记忆", aliases={"记忆列表"}, block=True)

@memory_cmd.handle()
async def handle_memory(event: MessageEvent, args: Message = CommandArg()):
    """查看指定用户和 Bot 绑定角色在当前会话中的最近记忆；默认查自己。"""
    if isinstance(event, GroupMessageEvent):
        logger.info(f"收到记忆查询命令 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}")
    else:
        logger.info(f"收到记忆查询命令 - 私聊: 用户: {event.user_id}")

    if not await _ensure_command_backend_ready(event):
        await memory_cmd.finish(Message("MonCore 当前不可用，暂时无法查询记忆。"))
        return
    if not await _require_read_permission(memory_cmd, event):
        return
    target_qq = _extract_target_qq(event, args)
    if not await _require_target_permission(memory_cmd, event, target_qq):
        return

    try:
        from ...app import get_moncore_api
        moncore_api = get_moncore_api()
        if not moncore_api:
            await memory_cmd.finish(Message("MonCore API 未就绪，暂时无法查询记忆。"))
            return

        data = await moncore_api.get_memories(event, user_qq_number=target_qq)
        await memory_cmd.finish(Message(command_service.format_memories_text(data)))
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"记忆查询命令失败: {e}", exc_info=True)
        await memory_cmd.finish(Message("查询记忆时出错了。"))
