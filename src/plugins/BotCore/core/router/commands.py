"""
命令处理器（路由层）
使用 on_command() 注册所有命令，每个命令独立注册，负责命令分发
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message, GroupMessageEvent
from nonebot.params import CommandArg

from ..business import CommandService
from src.System.Logs import get_logger

# 从 app 导入全局单例
from ...app import bot_config, napcat_api

# 初始化业务服务（传入 napcat_api）
command_service = CommandService(bot_config, napcat_api)

logger = get_logger(__name__)


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
        result_text = command_service.set_voice_mode(args=args_text)
    else:
        result_text = command_service.get_voice_mode_text()
    
    await voice_cmd.finish(Message(result_text))

