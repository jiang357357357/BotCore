"""
NapCat API 接口
通过 NoneBot2 的 OneBot V11 Bot 实例与 NapCat 交互
"""

import time
from typing import Optional, Dict, Any, List
from nonebot.adapters.onebot.v11 import Bot

from src.System.Logs import get_logger

logger = get_logger(__name__)

class NapCatAPI:
    """NapCat API 接口类（通过 NoneBot2 Bot 实例操作）"""
    
    def __init__(self):
        self.bot: Optional[Bot] = None
        self._login_info: Optional[Dict[str, Any]] = None
        self._group_member_name_cache: Dict[tuple[str, str], tuple[float, str]] = {}
        self._group_member_alias_cache: Dict[str, tuple[float, Dict[str, str]]] = {}
    
    def set_bot(self, bot: Bot):
        """设置机器人实例"""
        self.bot = bot
        logger.info("NapCat API 机器人实例已设置")

    def get_cached_bot_display_name(self, user_id: str) -> Optional[str]:
        """从已缓存的登录信息中获取机器人显示名。"""
        user_id = str(user_id or "")
        if not user_id:
            return None

        login_info = self._login_info or {}
        login_user_id = str(login_info.get("user_id") or "")
        if login_user_id == user_id:
            return login_info.get("nickname") or login_user_id

        bot_self_id = str(getattr(self.bot, "self_id", "") or "") if self.bot else ""
        if bot_self_id == user_id:
            return login_info.get("nickname") or bot_self_id

        return None

    @staticmethod
    def _read_mapping_or_attr(value: Any, key: str, default: Any = None) -> Any:
        """兼容 NapCat 返回的 dict、Pydantic 模型或普通对象。"""
        if isinstance(value, dict):
            return value.get(key, default)
        if hasattr(value, "dict"):
            try:
                return value.dict().get(key, default)
            except Exception:
                pass
        return getattr(value, key, default)

    async def get_group_member_display_name(
        self,
        group_id: str,
        user_id: str,
        cache_ttl: float = 3600.0,
    ) -> Optional[str]:
        """获取群成员显示名，优先群名片，其次昵称。"""
        group_id = str(group_id or "")
        user_id = str(user_id or "")
        if not group_id or not user_id or user_id.lower() == "all":
            return None

        bot_display_name = self.get_cached_bot_display_name(user_id)
        if bot_display_name:
            return bot_display_name

        cache_key = (group_id, user_id)
        cached = self._group_member_name_cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] <= cache_ttl:
            return cached[1]

        if not self.bot:
            logger.debug(f"机器人实例未设置，无法获取群成员信息: group_id={group_id}, user_id={user_id}")
            return None

        try:
            member_info = await self.bot.get_group_member_info(
                group_id=int(group_id),
                user_id=int(user_id),
                no_cache=False,
            )
            card = self._read_mapping_or_attr(member_info, "card", "") or ""
            nickname = self._read_mapping_or_attr(member_info, "nickname", "") or ""
            display_name = str(card or nickname or "").strip()
            if display_name:
                self._group_member_name_cache[cache_key] = (now, display_name)
                return display_name
        except Exception as e:
            logger.debug(f"获取群成员显示名失败: group_id={group_id}, user_id={user_id}, error={e}")

        return None

    @staticmethod
    def _add_group_member_alias_candidate(candidates: Dict[str, set[str]], alias: Any, user_id: Any) -> None:
        alias_text = str(alias or "").strip().lstrip("@")
        user_id_text = str(user_id or "").strip()
        if not alias_text or not user_id_text or user_id_text.lower() == "all":
            return
        candidates.setdefault(alias_text, set()).add(user_id_text)

    async def get_group_member_aliases(
        self,
        group_id: str,
        cache_ttl: float = 600.0,
    ) -> Dict[str, str]:
        """获取群成员显示名映射，只返回未冲突的别名。"""
        group_id = str(group_id or "")
        if not group_id:
            return {}

        cached = self._group_member_alias_cache.get(group_id)
        now = time.time()
        if cached and now - cached[0] <= cache_ttl:
            return cached[1]

        if not self.bot:
            logger.debug(f"机器人实例未设置，无法获取群成员列表: group_id={group_id}")
            return {}

        try:
            get_member_list = getattr(self.bot, "get_group_member_list", None)
            if callable(get_member_list):
                member_list = await get_member_list(
                    group_id=int(group_id),
                    no_cache=False,
                )
            else:
                member_list = await self.bot.call_api(
                    "get_group_member_list",
                    group_id=int(group_id),
                    no_cache=False,
                )
        except Exception as e:
            logger.debug(f"获取群成员列表失败: group_id={group_id}, error={e}")
            return {}

        candidates: Dict[str, set[str]] = {}
        for member in member_list or []:
            user_id = self._read_mapping_or_attr(member, "user_id", "")
            card = self._read_mapping_or_attr(member, "card", "") or ""
            nickname = self._read_mapping_or_attr(member, "nickname", "") or ""
            self._add_group_member_alias_candidate(candidates, user_id, user_id)
            self._add_group_member_alias_candidate(candidates, card, user_id)
            self._add_group_member_alias_candidate(candidates, nickname, user_id)

        aliases = {
            alias: next(iter(user_ids))
            for alias, user_ids in candidates.items()
            if len(user_ids) == 1
        }
        self._group_member_alias_cache[group_id] = (now, aliases)
        return aliases
    
    def get_user_avatar_url(self, user_id: str, size: int = 100) -> str:
        """
        获取用户头像 URL（腾讯官方头像服务）
        
        Args:
            user_id: QQ 号
            size: 头像尺寸（40, 100, 140, 640）
        """
        return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s={size}"
    
    def get_group_avatar_url(self, group_id: str, size: int = 100) -> str:
        """
        获取群头像 URL（腾讯官方头像服务）
        
        Args:
            group_id: 群号
            size: 头像尺寸（40, 100, 140, 640）
        """
        return f"https://p.qlogo.cn/gh/{group_id}/{group_id}/{size}"
    
    async def get_role_info(self) -> Optional[str]:
        """
        从后端获取角色信息
            
        Returns:
            角色信息文本，如果获取失败则返回 None
        """
        try:
            logger.info("获取角色信息（未实现）")
            return None
            
        except Exception as e:
            logger.error(f"获取角色信息失败: {e}")
            return None
    
    async def get_friend_list(self) -> List[Dict[str, Any]]:
        """
        获取好友列表
        
        Returns:
            好友列表，如果获取失败则返回空列表
        """
        try:
            if not self.bot:
                logger.error("机器人实例未设置")
                return []
            
            logger.info("获取好友列表")
            friend_list = await self.bot.get_friend_list()
            logger.info(f"成功获取 {len(friend_list)} 个好友")
            
            # 转换为字典列表，确保格式正确
            # 提取字段：user_id（字符串）、nickname 和 avatar_url
            # NapCat 返回的是字典类型，包含 user_id (整数) 和 nickname (字符串)
            result = []
            for friend in friend_list:
                # 优先使用字典访问（NapCat 返回的是字典）
                if isinstance(friend, dict):
                    user_id = friend.get('user_id')
                    nickname = friend.get('nickname')
                # 兼容 Pydantic 模型
                elif hasattr(friend, 'dict'):
                    try:
                        friend_dict = friend.dict()
                        user_id = friend_dict.get('user_id')
                        nickname = friend_dict.get('nickname')
                    except Exception:
                        user_id = getattr(friend, 'user_id', None)
                        nickname = getattr(friend, 'nickname', None)
                # 兼容对象属性访问
                else:
                    user_id = getattr(friend, 'user_id', None)
                    nickname = getattr(friend, 'nickname', None)
                
                # 确保 user_id 存在，如果为 None 则跳过
                if user_id is None:
                    logger.warning(f"好友信息缺少 user_id，跳过: {nickname}")
                    continue
                
                # 转换为字符串
                user_id_str = str(user_id)
                
                # 构建好友信息，包含头像URL
                friend_info = {
                    "user_id": user_id_str,
                    "nickname": nickname or "",  # 如果 nickname 为 None，使用空字符串
                    "avatar_url": self.get_user_avatar_url(user_id_str, size=100)
                }
                
                result.append(friend_info)
            
            return result
            
        except Exception as e:
            logger.error(f"获取好友列表失败: {e}")
            return []
    
    async def get_group_list(self) -> List[Dict[str, Any]]:
        """
        获取群列表
        
        Returns:
            群列表，如果获取失败则返回空列表
        """
        try:
            if not self.bot:
                logger.error("机器人实例未设置")
                return []
            
            logger.info("获取群列表")
            group_list = await self.bot.get_group_list()
            logger.info(f"成功获取 {len(group_list)} 个群")
            
            # 转换为字典列表，确保格式正确
            # 提取字段：group_id（字符串）、group_name 和 avatar_url
            # NapCat 返回的是字典类型，包含 group_id (整数) 和 group_name (字符串)
            result = []
            for group in group_list:
                # 优先使用字典访问（NapCat 返回的是字典）
                if isinstance(group, dict):
                    group_id = group.get('group_id')
                    group_name = group.get('group_name')
                # 兼容 Pydantic 模型
                elif hasattr(group, 'dict'):
                    try:
                        group_dict = group.dict()
                        group_id = group_dict.get('group_id')
                        group_name = group_dict.get('group_name')
                    except Exception:
                        group_id = getattr(group, 'group_id', None)
                        group_name = getattr(group, 'group_name', None)
                # 兼容对象属性访问
                else:
                    group_id = getattr(group, 'group_id', None)
                    group_name = getattr(group, 'group_name', None)
                
                # 确保 group_id 存在，如果为 None 则跳过
                if group_id is None:
                    logger.warning(f"群信息缺少 group_id，跳过: {group_name}")
                    continue
                
                # 转换为字符串
                group_id_str = str(group_id)
                
                # 构建群信息，包含头像URL
                group_info = {
                    "group_id": group_id_str,
                    "group_name": group_name or "",  # 如果 group_name 为 None，使用空字符串
                    "avatar_url": self.get_group_avatar_url(group_id_str, size=100)
                }
                
                result.append(group_info)
            
            return result
            
        except Exception as e:
            logger.error(f"获取群列表失败: {e}")
            return []
    
    async def get_bot_login_info(self) -> Optional[Dict[str, Any]]:
        """
        获取机器人登录信息（包括昵称）
        
        Returns:
            登录信息字典，包含 user_id 和 nickname，如果获取失败则返回 None
        """
        try:
            if not self.bot:
                logger.error("机器人实例未设置")
                return None
            
            logger.info("获取机器人登录信息")
            login_info = await self.bot.get_login_info()
            
            if login_info:
                # 转换为字典格式
                if isinstance(login_info, dict):
                    result = {
                        "user_id": str(login_info.get('user_id', '')),
                        "nickname": login_info.get('nickname', '')
                    }
                elif hasattr(login_info, 'dict'):
                    login_dict = login_info.dict()
                    result = {
                        "user_id": str(login_dict.get('user_id', '')),
                        "nickname": login_dict.get('nickname', '')
                    }
                else:
                    result = {
                        "user_id": str(getattr(login_info, 'user_id', '')),
                        "nickname": getattr(login_info, 'nickname', '')
                    }

                self._login_info = result
                logger.info(f"成功获取机器人登录信息: {result}")
                return result
            else:
                logger.warning("获取机器人登录信息为空")
                return None
                
        except Exception as e:
            logger.error(f"获取机器人登录信息失败: {e}", exc_info=True)
            return None

    async def get_bot_signature(self, user_id: Optional[str] = None) -> Optional[str]:
        """
        获取机器人 QQ 个性签名。

        NapCat 的 get_login_info 只返回基础登录信息，个性签名在 get_stranger_info
        的 long_nick / longNick 字段中。
        """
        try:
            if not self.bot:
                logger.error("机器人实例未设置")
                return None

            target_user_id = str(user_id or "").strip()
            if not target_user_id:
                login_info = self._login_info or await self.get_bot_login_info() or {}
                target_user_id = str(login_info.get("user_id") or "").strip()
            if not target_user_id:
                logger.warning("缺少机器人 QQ 号，无法获取个性签名")
                return None

            stranger_info = await self.bot.get_stranger_info(
                user_id=int(target_user_id),
                no_cache=True,
            )
            signature = (
                self._read_mapping_or_attr(stranger_info, "long_nick", None)
                or self._read_mapping_or_attr(stranger_info, "longNick", None)
                or ""
            )
            signature_text = str(signature).strip()
            logger.info("成功获取机器人个性签名" if signature_text else "机器人个性签名为空")
            return signature_text

        except Exception as e:
            logger.warning(f"获取机器人个性签名失败: {e}")
            return None
