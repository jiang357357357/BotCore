"""
NapCat API 接口
通过 NoneBot2 的 OneBot V11 Bot 实例与 NapCat 交互
"""

import base64
import hashlib
import os
from pathlib import Path
import time
from typing import Optional, Dict, Any, List
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

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

    async def get_online_status(self) -> Optional[bool]:
        """读取 OneBot 报告的 QQ 登录态；查询失败时不猜测状态。"""
        if not self.bot:
            return None
        result = await self.bot.call_api("get_status")
        online = self._read_mapping_or_attr(result, "online", None)
        return online if isinstance(online, bool) else None

    async def send_text_message(self, target_type: str, target_id: str, content: str) -> Dict[str, Any]:
        """向 QQ 好友或群聊发送纯文本消息。"""
        if not self.bot:
            raise RuntimeError("机器人实例未设置")

        normalized_type = str(target_type or "").strip().lower()
        normalized_target = str(target_id or "").strip()
        text = str(content or "").strip()
        if normalized_type not in {"user", "group"}:
            raise ValueError("target_type 必须是 user 或 group")
        if not normalized_target:
            raise ValueError("缺少 QQ 号/群号")
        if not text:
            raise ValueError("消息内容不能为空")

        message = Message(MessageSegment.text(text))
        if normalized_type == "group":
            api_name = "send_group_msg"
            result = await self.bot.call_api(api_name, group_id=int(normalized_target), message=message)
        else:
            api_name = "send_private_msg"
            result = await self.bot.call_api(api_name, user_id=int(normalized_target), message=message)

        message_id = self._read_mapping_or_attr(result, "message_id", "")
        logger.info(f"QQ 消息发送成功: type={normalized_type} target={normalized_target} message_id={message_id}")
        return {
            "target_type": normalized_type,
            "target_id": normalized_target,
            "message_id": str(message_id or ""),
            "api": api_name,
        }

    async def send_private_images(self, user_id: str, images_base64: List[str]) -> Dict[str, Any]:
        """Send locally rendered PNG pages to one private QQ contact."""
        if not self.bot:
            raise RuntimeError("机器人实例未设置")
        if not str(user_id).isdigit() or not 1 <= len(images_base64) <= 8:
            raise ValueError("QQ 图片目标或页数无效")
        message = Message()
        for encoded in images_base64:
            if not isinstance(encoded, str) or len(encoded) > 2_000_000:
                raise ValueError("QQ 图片大小无效")
            binary = base64.b64decode(encoded, validate=True)
            if len(binary) > 1_500_000 or not binary.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("QQ 图片必须是受限 PNG")
            message += MessageSegment.image(f"base64://{encoded}")
        result = await self.bot.call_api("send_private_msg", user_id=int(user_id), message=message)
        return {"message_id": str(self._read_mapping_or_attr(result, "message_id", "") or "")}

    async def read_private_file(self, file_id: str, expected_size: int) -> bytes:
        """Fetch a received QQ file from NapCat's local cache with a strict size bound."""
        if not self.bot or not file_id or expected_size < 0 or expected_size > 8 * 1024 * 1024:
            raise ValueError("QQ 文件不可用或超过 8 MiB")
        result = await self.bot.call_api("get_file", file_id=file_id)
        location = self._read_mapping_or_attr(result, "file", "")
        if not isinstance(location, str) or not location:
            raise RuntimeError("NapCat 未返回文件路径")
        path = Path(location)
        stat = path.lstat()
        if not path.is_file() or path.is_symlink() or stat.st_size > 8 * 1024 * 1024:
            raise ValueError("QQ 文件不是受限的普通文件")
        with path.open("rb") as source:
            data = source.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024 or len(data) != expected_size:
            raise ValueError("QQ 文件大小与消息记录不符")
        return data

    async def read_private_image(self, image_file: str, expected_size: int = 0) -> tuple[bytes, str]:
        """Resolve a received image through NapCat and read its bounded local cache copy."""
        if not self.bot or not image_file or expected_size < 0 or expected_size > 8 * 1024 * 1024:
            raise ValueError("QQ 图片不可用或超过 8 MiB")
        result = await self.bot.call_api("get_image", file=image_file)
        location = self._read_mapping_or_attr(result, "file", "")
        if not isinstance(location, str) or not location:
            raise RuntimeError("NapCat 未返回图片路径")
        path = Path(location)
        stat = path.lstat()
        if not path.is_file() or path.is_symlink() or stat.st_size > 8 * 1024 * 1024:
            raise ValueError("QQ 图片不是受限的普通文件")
        with path.open("rb") as source:
            data = source.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024 or (expected_size and len(data) != expected_size):
            raise ValueError("QQ 图片大小与消息记录不符")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = "png"
        elif data.startswith(b"\xff\xd8\xff"):
            extension = "jpg"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            extension = "gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            extension = "webp"
        else:
            raise ValueError("暂不支持这张图片的格式")
        return data, extension

    async def send_private_file(self, user_id: str, filename: str, content: bytes) -> Dict[str, Any]:
        """Upload a bounded local file through NapCat's file API."""
        if not self.bot or not str(user_id).isdigit() or len(content) > 8 * 1024 * 1024:
            raise ValueError("QQ 文件目标或大小无效")
        if not filename or len(filename) > 255 or any(c in filename for c in "/\\\x00\r\n"):
            raise ValueError("QQ 文件名无效")
        import tempfile
        with tempfile.NamedTemporaryFile(prefix="mon-qq-file-", delete=False) as temp:
            os.chmod(temp.name, 0o600)
            temp.write(content)
            local_path = temp.name
        try:
            result = await self.bot.call_api("upload_private_file", user_id=str(user_id), file=local_path, name=filename)
            return {"file_id": str(self._read_mapping_or_attr(result, "file_id", "") or ""),
                    "sha256": hashlib.sha256(content).hexdigest()}
        finally:
            os.unlink(local_path)

    async def get_message_history(
        self,
        target_type: str,
        target_id: str,
        count: int = 100,
        message_seq: int = 0,
    ) -> List[Dict[str, Any]]:
        """Read recent native QQ history from NapCat."""
        if not self.bot:
            raise RuntimeError("机器人实例未设置")
        normalized_type = str(target_type or "").strip().lower()
        normalized_target = str(target_id or "").strip()
        limit = max(1, min(int(count or 100), 100))
        if normalized_type == "group":
            result = await self.bot.call_api(
                "get_group_msg_history",
                group_id=int(normalized_target),
                message_seq=int(message_seq or 0),
                count=limit,
            )
        elif normalized_type == "user":
            result = await self.bot.call_api(
                "get_friend_msg_history",
                user_id=int(normalized_target),
                message_seq=int(message_seq or 0),
                count=limit,
            )
        else:
            raise ValueError("target_type 必须是 user 或 group")
        data = result if isinstance(result, dict) else self._read_mapping_or_attr(result, "data", result)
        messages = data.get("messages") if isinstance(data, dict) else []
        return messages if isinstance(messages, list) else []

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

                # get_login_info 可能保留当前 NTQQ 登录会话的旧昵称。对机器人自身再做
                # 一次明确禁用缓存的资料查询，以手机端修改后的公开资料为准。
                user_id = str(result.get("user_id") or "").strip()
                login_nickname = str(result.get("nickname") or "").strip()
                if user_id:
                    try:
                        stranger_info = await self.bot.get_stranger_info(
                            user_id=int(user_id),
                            no_cache=True,
                        )
                        fresh_nickname = str(
                            self._read_mapping_or_attr(stranger_info, "nickname", "")
                            or self._read_mapping_or_attr(stranger_info, "nick", "")
                            or ""
                        ).strip()
                        logger.info(
                            "机器人昵称资料核对: "
                            f"get_login_info={login_nickname!r}, "
                            f"get_stranger_info(no_cache=true)={fresh_nickname!r}"
                        )
                        if fresh_nickname:
                            result["nickname"] = fresh_nickname
                    except Exception as profile_error:
                        logger.warning(f"无缓存查询机器人资料失败，沿用登录昵称: {profile_error}")

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
