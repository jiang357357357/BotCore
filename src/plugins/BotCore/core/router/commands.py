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
from .local_policy import is_allowed_by_local_policy
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
help_cmd = on_command("帮助", rule=is_allowed_by_local_policy, aliases={"help"}, block=True)

@help_cmd.handle()
async def handle_help(event: MessageEvent, args: Message = CommandArg()):
    """处理帮助命令（路由到业务层）"""
    # 记录命令触发信息
    if isinstance(event, GroupMessageEvent):
        logger.info(f"收到帮助命令 - 群聊: {event.group_id}, 用户: {event.user_id}, 昵称: {event.sender.nickname}")
    else:
        logger.info(f"收到帮助命令 - 私聊: 用户: {event.user_id}")
    
    help_text = await command_service.get_help_text(event)
    if isinstance(event, PrivateMessageEvent):
        from ...app import get_moncore_api

        api = get_moncore_api()
        if api and napcat_api:
            pages = await api.render_help_card(help_text)
            if pages:
                try:
                    await napcat_api.send_private_images(str(event.user_id), pages)
                except Exception as error:
                    logger.warning("QQ 帮助卡片发送失败，回退文字: %s", error)
                else:
                    await help_cmd.finish()
    await help_cmd.finish(Message(help_text))


# ==================== /角色 命令 ====================

rule_cmd = on_command("角色", rule=is_allowed_by_local_policy, aliases={"设定"}, block=True)

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


# ==================== /状态 命令 ====================

status_cmd = on_command("状态", rule=is_allowed_by_local_policy, block=True)

@status_cmd.handle()
async def handle_status(event: MessageEvent, args: Message = CommandArg()):
    """Show one private QQ status card assembled by MonCore."""
    if not isinstance(event, PrivateMessageEvent):
        await status_cmd.finish(Message("请在与机器人的私聊中使用 /状态。"))
        return
    if not await _ensure_command_backend_ready(event):
        await status_cmd.finish(Message("MonCore 当前不可用，无法读取总状态。"))
        return
    from ...app import get_moncore_api, napcat_api as active_napcat_api
    api = get_moncore_api()
    if not api:
        await status_cmd.finish(Message("MonCore API 未就绪，无法读取总状态。"))
        return
    result = await api.request_reply(event, timeout=15.0, need_voice=False)
    content = str((result or {}).get("content") or "总状态暂时无法读取，请稍后重试。")
    images = (result or {}).get("images_base64")
    if isinstance(images, list) and images:
        try:
            await active_napcat_api.send_private_images(str(event.user_id), images)
            await status_cmd.finish()
            return
        except FinishedException:
            raise
        except Exception as error:
            logger.warning("QQ 总状态卡片发送失败，改发文字: %s", error)
    await status_cmd.finish(Message(content))


# ==================== /模式 命令 ====================

mode_cmd = on_command("模式", rule=is_allowed_by_local_policy, block=True)

@mode_cmd.handle()
async def handle_mode(event: MessageEvent, args: Message = CommandArg()):
    """由 Core 按 Bot 超级管理员规则管理私聊的回复模式。"""
    if not isinstance(event, PrivateMessageEvent):
        await mode_cmd.finish(Message("请在与机器人的私聊中使用 /模式。"))
        return
    if not await _ensure_command_backend_ready(event):
        await mode_cmd.finish(Message("MonCore 当前不可用，暂时无法切换模式。"))
        return
    from ...app import get_moncore_api
    api = get_moncore_api()
    if not api:
        await mode_cmd.finish(Message("MonCore API 未就绪。"))
        return
    result = await api.request_reply(event, timeout=15.0, need_voice=False)
    await mode_cmd.finish(Message(str((result or {}).get("content") or "模式操作失败，请稍后再试。")))


# ==================== /权限 命令 ====================

permission_mode_cmd = on_command("权限", rule=is_allowed_by_local_policy, block=True)

@permission_mode_cmd.handle()
async def handle_permission_mode(event: MessageEvent, args: Message = CommandArg()):
    """由 MonCore 验证 Bot 超级管理员并设置本 QQ 私聊的智能体权限。"""
    if not isinstance(event, PrivateMessageEvent):
        await permission_mode_cmd.finish(Message("请在与机器人的私聊中使用 /权限。"))
        return
    if not await _ensure_command_backend_ready(event):
        await permission_mode_cmd.finish(Message("MonCore 当前不可用，权限设置未生效。"))
        return
    from ...app import get_moncore_api
    api = get_moncore_api()
    if not api:
        await permission_mode_cmd.finish(Message("MonCore API 未就绪，权限设置未生效。"))
        return
    result = await api.request_reply(event, timeout=15.0, need_voice=False)
    await permission_mode_cmd.finish(Message(str((result or {}).get("content") or "权限设置未生效，请稍后重试。")))


# ==================== /审批 命令 ====================

approval_cmd = on_command("审批", rule=is_allowed_by_local_policy, block=True)

@approval_cmd.handle()
async def handle_approval(event: MessageEvent, args: Message = CommandArg()):
    """将 QQ 超级管理员的单次工具审批交给 MonCore 校验。"""
    if not isinstance(event, PrivateMessageEvent):
        await approval_cmd.finish(Message("请在与机器人的私聊中审批工具调用。"))
        return
    if not await _ensure_command_backend_ready(event):
        await approval_cmd.finish(Message("MonCore 当前不可用，审批未生效。"))
        return
    from ...app import get_moncore_api
    api = get_moncore_api()
    if not api:
        await approval_cmd.finish(Message("MonCore API 未就绪，审批未生效。"))
        return
    result = await api.request_reply(event, timeout=15.0, need_voice=False)
    content = str((result or {}).get("content") or "审批未生效，请稍后重试。")
    images = (result or {}).get("images_base64")
    if isinstance(images, list) and images:
        try:
            from ...app import napcat_api as active_napcat_api
            await active_napcat_api.send_private_images(str(event.user_id), images)
            await approval_cmd.finish()
            return
        except FinishedException:
            raise
        except Exception as error:
            logger.warning("QQ 审批结果卡片发送失败，改发文字: %s", error)
    await approval_cmd.finish(Message(content))


# ==================== /语音 命令 ====================

voice_cmd = on_command("语音", rule=is_allowed_by_local_policy, block=True)

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

favorability_cmd = on_command("好感", rule=is_allowed_by_local_policy, block=True)

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

favorability_ranking_cmd = on_command("好感排行", rule=is_allowed_by_local_policy, aliases={"好感榜"}, block=True)

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

memory_cmd = on_command("记忆", rule=is_allowed_by_local_policy, aliases={"记忆列表"}, block=True)

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
