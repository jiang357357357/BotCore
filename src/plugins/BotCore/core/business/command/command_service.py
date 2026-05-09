"""
命令业务服务
处理各种命令的业务逻辑
"""

from src.System.Logs import get_logger
from typing import List, Set, Optional
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.matcher import matchers
from nonebot.rule import CommandRule

from ....config import BotConfig
from ....external.napcat import NapCatAPI

logger = get_logger(__name__)


class CommandService:
    """命令业务服务类"""
    
    def __init__(self, config: BotConfig, napcat_api: Optional[NapCatAPI] = None):
        """
        初始化命令服务
        
        Args:
            config: 机器人配置
            napcat_api: NapCat API 服务（用于调用后端）
        """
        self.config = config
        self.napcat_api = napcat_api
    
    @staticmethod
    def _get_registered_commands() -> Set[str]:
        """
        从 NoneBot 的 matchers 中提取所有已注册的命令
        
        Returns:
            命令名称集合
        """
        commands: Set[str] = set()
        
        try:
            # 遍历所有优先级的 matchers
            for priority, matcher_list in matchers.items():
                for matcher in matcher_list:
                    # 检查 matcher 的 rule 中是否包含 CommandRule
                    for checker in matcher.rule.checkers:
                        # checker 是 Dependent[bool]，检查其 call 属性
                        if hasattr(checker, 'call'):
                            checker_call = checker.call
                            # 检查是否是 CommandRule 实例
                            if isinstance(checker_call, CommandRule):
                                # 提取命令名称
                                for cmd_tuple in checker_call.cmds:
                                    if cmd_tuple and len(cmd_tuple) > 0:
                                        # 取命令的第一部分作为命令名
                                        commands.add(cmd_tuple[0])
            
            logger.debug(f"从 NoneBot matchers 中提取到 {len(commands)} 个命令: {commands}")
            
        except Exception as e:
            logger.error(f"提取注册命令时出错: {e}")
        
        return commands
    
    def get_available_commands(self) -> List[str]:
        """
        获取可用命令列表（从 NoneBot 中动态获取）
        
        Returns:
            命令名称列表（已排序）
        """
        commands = self._get_registered_commands()
        return sorted(list(commands))
    
    async def get_help_text(self, event: MessageEvent) -> str:
        """
        获取帮助信息（使用 NoneBot 动态获取的命令列表）
        
        Args:
            event: 消息事件
            
        Returns:
            帮助文本
        """
        # 获取QQ机器人本身的昵称
        bot_nickname = self.config.bot_name  # 默认使用配置中的名字
        if self.napcat_api and self.napcat_api.bot:
            try:
                login_info = await self.napcat_api.get_bot_login_info()
                if login_info and login_info.get('nickname'):
                    bot_nickname = login_info.get('nickname')
            except Exception as e:
                logger.debug(f"获取机器人昵称失败，使用默认名字: {e}")
        
        # 从 NoneBot 动态获取已注册的命令
        available_commands = self.get_available_commands()
        commands_text = "\n".join([f"{self.config.command_prefix}{cmd}" for cmd in available_commands])
        
        help_text = f"""
（温柔地微笑）{bot_nickname}的可用命令：

{commands_text}

*轻轻整理了一下头发*

如果有什么需要帮助的，随时告诉我哦~
        """
        return help_text.strip()
    
    async def get_rule_text(self, event: MessageEvent) -> str:
        """
        获取角色信息（通过后端获取）
        
        Args:
            event: 消息事件
            
        Returns:
            角色信息文本
        """
        try:
            # 调用后端获取角色信息
            if self.napcat_api:
                role_info = await self.napcat_api.get_role_info()
                if role_info:
                    # 格式化角色信息
                    role_text = f"""
（温柔地微笑）我的角色设定：

{role_info}

*轻轻整理了一下头发*

这就是我的设定呢，希望你能喜欢~
                    """
                    return role_text.strip()
            
            # 如果无法获取，返回默认提示
            return "（有些困扰）抱歉...暂时无法获取角色信息呢，可能是后端服务还没准备好..."
            
        except Exception as e:
            logger.error(f"获取角色信息时出错: {e}")
            return "（歉意地）获取角色信息时出错了...对不起，我会努力修复的..."
    
    def get_voice_mode_text(self) -> str:
        """
        获取语音模式状态文本
        
        Returns:
            语音模式状态文本
        """
        try:
            from ....app import get_voice_mode
            is_enabled = get_voice_mode()
            status_text = "已启用" if is_enabled else "已禁用"
            emoji = "🔊" if is_enabled else "🔇"
            
            return f"""
（温柔地微笑）当前语音模式状态：

{emoji} {status_text}

*轻轻整理了一下头发*

{"我现在会发送语音消息哦~" if is_enabled else "我现在只发送文本消息呢~"}
            """.strip()
        except Exception as e:
            logger.error(f"获取语音模式状态时出错: {e}")
            return "（歉意地）获取语音模式状态时出错了...对不起..."
    
    def set_voice_mode(self, enabled: Optional[bool] = None, args: str = "") -> str:
        """
        设置语音模式状态
        
        Args:
            enabled: 是否启用（如果为 None，则根据 args 判断）
            args: 命令参数（必须是"开启"或"关闭"）
            
        Returns:
            设置结果文本
        """
        try:
            from ....app import get_voice_mode, set_voice_mode
            
            # 只接受明确的参数：开启 或 关闭
            args_stripped = args.strip()
            if args_stripped == "开启":
                enabled = True
            elif args_stripped == "关闭":
                enabled = False
            else:
                # 参数不正确，返回提示
                return "（有些困扰）抱歉...请使用「/语音 开启」或「/语音 关闭」来设置语音模式哦~"
            
            # 设置语音模式
            set_voice_mode(enabled)
            
            status_text = "已启用" if enabled else "已禁用"
            emoji = "🔊" if enabled else "🔇"
            
            return f"""
（温柔地微笑）语音模式{status_text}了！

{emoji} 当前状态：{status_text}

*轻轻整理了一下头发*

{"我现在会发送语音消息哦~" if enabled else "我现在只发送文本消息呢~"}
            """.strip()
        except Exception as e:
            logger.error(f"设置语音模式状态时出错: {e}")
            return "（歉意地）设置语音模式状态时出错了...对不起，我会努力修复的..."

