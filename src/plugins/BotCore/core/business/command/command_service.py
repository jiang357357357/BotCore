"""
命令业务服务
处理各种命令的业务逻辑
"""

from src.System.Logs import get_logger
from typing import Any, Dict, List, Set, Optional
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
        bot_nickname = self.config.bot_name or "QQBot"
        if self.napcat_api and self.napcat_api.bot:
            try:
                login_info = await self.napcat_api.get_bot_login_info()
                if login_info and login_info.get('nickname'):
                    bot_nickname = login_info.get('nickname')
            except Exception as e:
                logger.debug(f"获取机器人昵称失败，使用默认名字: {e}")
        
        prefix = self.config.command_prefix or "/"
        
        help_text = f"""
{bot_nickname} 可用命令

前缀：{prefix}
群聊中可以直接发送命令，也可以先 @我 再发送命令。

常用
{prefix}帮助
  查看这份说明。

{prefix}角色
{prefix}设定
  查看当前 QQBot 绑定的角色设定。
  权限：管理员，或当前群/发言人已被后端支持。

状态
{prefix}语音
  查看当前是否启用语音回复。
  权限：管理员，或当前群/发言人已被后端支持。

{prefix}好感
{prefix}好感 @某人
  查看你与当前角色的好感数值；管理员可 @某人 查询别人。
  权限：普通用户只能查自己；管理员可查别人。

{prefix}好感排行
{prefix}好感榜
  查看好感总值排行榜；群聊中统计当前群，私聊中统计当前 Bot。
  权限：管理员，或当前群/发言人已被后端支持。

{prefix}记忆
{prefix}记忆列表
{prefix}记忆 @某人
  查看你与当前角色在当前会话中的最近记忆；管理员可 @某人 查询别人。
  权限：普通用户只能查自己；管理员可查别人。

管理
{prefix}语音 开启
{prefix}语音 关闭
  开启或关闭全局语音回复。
  权限：仅管理员。
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
            role_info = None
            try:
                from ....app import get_moncore_api
                moncore_api = get_moncore_api()
                if moncore_api:
                    role_info = await moncore_api.get_role_info()
            except Exception as e:
                logger.debug(f"通过 MonCore 获取角色信息失败，尝试旧通道: {e}")

            if not role_info and self.napcat_api:
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
            saved = set_voice_mode(enabled)
            if not saved:
                return "（有些困扰）语音模式已在当前运行中切换，但写入配置文件失败了，重启后可能不会保留。"
            
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

    @staticmethod
    def _format_score(value: Any) -> str:
        try:
            return f"{float(value):.2f}"
        except Exception:
            return "0.00"

    @staticmethod
    def _truncate_text(value: Any, max_length: int = 12) -> str:
        text = str(value or "").strip()
        if len(text) <= max_length:
            return text
        return f"{text[:max_length - 1]}…"

    @classmethod
    def _format_user_label(cls, item: Dict[str, Any]) -> str:
        qq_number = str(item.get("qq_number") or "-")
        display_name = str(item.get("display_name") or "").strip()
        if display_name and display_name != qq_number:
            return f"{cls._truncate_text(display_name, 10)}({qq_number})"
        return qq_number

    def format_favorability_text(self, data: Optional[Dict[str, Any]]) -> str:
        """格式化单个用户的 BOT 好感状态。"""
        if not data or not data.get("success"):
            return data.get("message", "（有些困扰）暂时查不到你的好感状态。") if data else "（有些困扰）暂时查不到你的好感状态。"

        profile = data.get("profile") or {}
        character_name = data.get("character_name") or "当前角色"
        user_qq_number = str(data.get("user_qq_number") or profile.get("qq_number") or "").strip()
        display_name = str(profile.get("display_name") or "").strip()
        if display_name and user_qq_number and display_name != user_qq_number:
            user_label = f"{display_name}({user_qq_number})"
        else:
            user_label = user_qq_number or "该用户"
        total = self._format_score(profile.get("total", data.get("total", 0)))
        return "\n".join([
            f"{character_name} 对 {user_label} 的好感状态：",
            "维度 | 数值",
            "总值 | " + total,
            "喜爱 | " + self._format_score(profile.get("affection")),
            "信任 | " + self._format_score(profile.get("trust")),
            "依恋 | " + self._format_score(profile.get("attachment")),
            "占有 | " + self._format_score(profile.get("possessive")),
            "互动 | " + str(profile.get("total_interactions", 0)),
        ])

    def format_favorability_ranking_text(self, data: Optional[Dict[str, Any]]) -> str:
        """格式化 BOT 好感排行。"""
        if not data or not data.get("success"):
            return data.get("message", "（有些困扰）暂时查不到好感排行。") if data else "（有些困扰）暂时查不到好感排行。"

        character_name = data.get("character_name") or "当前角色"
        items = data.get("items") or []
        if not items:
            return f"{character_name} 还没有可排行的好感记录。"

        lines = [
            f"{character_name} 的好感总值排行：",
            "排名 | 用户 | 总值 | 互动",
        ]
        for item in items[:10]:
            lines.append(
                f"{item.get('rank', '-')} | "
                f"{self._format_user_label(item)} | "
                f"{self._format_score(item.get('total'))} | "
                f"{item.get('total_interactions', 0)}"
            )
        return "\n".join(lines)

    def format_memories_text(self, data: Optional[Dict[str, Any]]) -> str:
        """格式化当前用户的 BOT 记忆列表。"""
        if not data or not data.get("success"):
            return data.get("message", "（有些困扰）暂时查不到你的记忆。") if data else "（有些困扰）暂时查不到你的记忆。"

        character_name = data.get("character_name") or "当前角色"
        user_qq_number = str(data.get("user_qq_number") or "").strip()
        user_label = user_qq_number or "该用户"
        items = data.get("items") or []
        if not items:
            return f"{character_name} 暂时还没有记录与 {user_label} 相关的记忆。"

        lines = [
            f"{character_name} 记录的 {user_label} 最近记忆：",
            "序号 | 时间 | 内容",
        ]
        for index, item in enumerate(items[:10], start=1):
            created_at = str(item.get("created_at") or "-")
            if "T" in created_at:
                created_at = created_at.replace("T", " ")[:16]
            content = str(item.get("content") or "").replace("\n", " ").strip()
            lines.append(
                f"{index} | "
                f"{created_at} | "
                f"{self._truncate_text(content, 36)}"
            )
        return "\n".join(lines)
