"""
MonCore API 接口
提供与 MonCore 后端交互的高级 API 接口
封装业务逻辑，使用 WebSocketClient 进行底层通信
"""

import asyncio
import time
import random
from typing import Optional, Dict, Any, Callable
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, PrivateMessageEvent

from ..client import WebSocketClient
from src.System.Logs import get_logger

logger = get_logger(__name__)


class MonCoreAPI:
    """MonCore API 接口类"""
    
    def __init__(self, ws_client: WebSocketClient, server_ip: Optional[str] = None, http_port: Optional[int] = None, http_host: Optional[str] = None):
        """
        初始化 MonCore API
        
        Args:
            ws_client: WebSocket 客户端实例
            server_ip: 服务器IP地址（用于WebSocket连接）
            http_port: HTTP端口（用于拼接audio_url的完整URL）
            http_host: HTTP访问地址（用于拼接audio_url的完整URL，本地访问使用localhost）
        """
        self.ws_client = ws_client
        self.server_ip = server_ip
        self.http_port = http_port
        self.http_host = http_host or "localhost"  # 默认使用 localhost
        self.pending_requests: Dict[str, asyncio.Future] = {}  # 等待响应的请求（key: request_id）
        self.pending_store_requests: Dict[str, asyncio.Future] = {}  # 等待存储响应的请求（key: store_request_id）
        self.reply_callbacks: list[Callable] = []  # 回复回调函数列表
        
        # 注册消息处理器
        self.ws_client.register_handler("reply", self._handle_reply)
        self.ws_client.register_handler("store", self._handle_store_response)
        self.ws_client.register_handler("favorability", self._handle_favorability_response)
        self.ws_client.register_handler("memory", self._handle_memory_response)

    @staticmethod
    def _get_event_message(event: MessageEvent):
        return getattr(event, "original_message", None) or event.get_message()

    @staticmethod
    async def _resolve_at_display(event: MessageEvent, segment) -> str:
        qq = str(segment.data.get("qq", "") or "")
        if not qq:
            return "[@]"
        if qq.lower() == "all":
            return "@全体成员"

        for key in ("name", "nickname", "card", "display"):
            value = segment.data.get(key)
            if value:
                return f"@{value}"

        try:
            from src.plugins.BotCore.app import napcat_api
            display_name = None
            if napcat_api:
                if isinstance(event, GroupMessageEvent):
                    display_name = await napcat_api.get_group_member_display_name(
                        group_id=str(event.group_id),
                        user_id=qq,
                    )
                if not display_name:
                    display_name = napcat_api.get_cached_bot_display_name(qq)
            if display_name:
                return f"@{display_name}"
        except Exception as e:
            logger.debug(f"解析 at 显示名失败: qq={qq}, error={e}")

        return f"@用户{qq}"

    @staticmethod
    async def _extract_mentions(event: MessageEvent) -> list[Dict[str, str]]:
        mentions: list[Dict[str, str]] = []
        try:
            message = MonCoreAPI._get_event_message(event)
            for segment in message:
                if segment.type != "at":
                    continue
                qq = str(segment.data.get("qq", "") or "")
                display = await MonCoreAPI._resolve_at_display(event, segment)
                mentions.append({"qq": qq, "display": display.lstrip("@")})
        except Exception as e:
            logger.debug(f"提取 at 元数据失败: {e}")
        return mentions

    @staticmethod
    async def _extract_event_content(event: MessageEvent) -> str:
        """提取可发送给 MonCore 的消息内容，保证 store/chat 使用同一套规则。"""
        message = MonCoreAPI._get_event_message(event)
        try:
            content_parts = []
            for segment in message:
                segment_type = segment.type
                if segment_type == "text":
                    text = segment.data.get("text", "").strip()
                    if text:
                        content_parts.append(text)
                elif segment_type == "image":
                    content_parts.append("[图片]")
                elif segment_type == "face":
                    content_parts.append("[表情]")
                elif segment_type == "record":
                    content_parts.append("[语音]")
                elif segment_type == "video":
                    content_parts.append("[视频]")
                elif segment_type == "at":
                    content_parts.append(await MonCoreAPI._resolve_at_display(event, segment))
                else:
                    content_parts.append(f"[{segment_type}]")

            if content_parts:
                return " ".join(content_parts)

            content = message.extract_plain_text()
            return content.strip() if content.strip() else "[空消息]"
        except Exception as e:
            logger.warning(f"提取非文本消息内容时出错: {e}")
            return "[消息解析错误]"

    @staticmethod
    async def _build_event_metadata(event: MessageEvent, content: str) -> Dict[str, Any]:
        """构建消息元数据，用于 MonCore 保留群内真实发送者。"""
        is_group = isinstance(event, GroupMessageEvent)
        sender = getattr(event, "sender", None)
        sender_nickname = getattr(sender, "card", None) or getattr(sender, "nickname", None) or ""
        sender_user_id = str(getattr(event, "user_id", "") or "")
        group_id = str(getattr(event, "group_id", "") or "") if is_group else ""
        display_name = sender_nickname or sender_user_id or "用户"

        metadata = {
            "sender_user_id": sender_user_id,
            "sender_nickname": sender_nickname,
            "display_name": f"{display_name}({sender_user_id})" if sender_user_id and sender_nickname else display_name,
            "is_group": is_group,
            "raw_content": content,
            "mentions": await MonCoreAPI._extract_mentions(event),
        }
        if group_id:
            metadata["group_id"] = group_id
        return metadata
    
    async def store_message(self, event: MessageEvent, timeout: float = 10.0) -> bool:
        """
        存储消息到后端，并等待存储成功的响应
        
        生成唯一的 store_request_id：时间戳 + is_group + qq_number + random
        格式：store_{timestamp_ms}_{is_group}_{qq_number}_{random}
        
        协议格式（发送）：
        {
            "command": "store",
            "data": {
                "content": "用户消息内容",
                "is_group": false,
                "qq_number": "123456789",
                "store_request_id": "store_1234567890123_0_123456789_12345"
            }
        }
        
        协议格式（接收）：
        {
            "command": "store",
            "subCommand": "success",
            "data": {
                "store_request_id": "store_1234567890123_0_123456789_12345"
            }
        }
        
        Args:
            event: 消息事件
            timeout: 超时时间（秒）
            
        Returns:
            存储是否成功
        """
        store_request_id = None
        try:
            # 判断消息类型
            is_group = isinstance(event, GroupMessageEvent)
            # qq_number 是用户ID或群ID（根据 is_group 判断）
            qq_number = str(event.group_id if is_group else event.user_id)
            
            content = await self._extract_event_content(event)
            metadata = await self._build_event_metadata(event, content)
            
            # 生成唯一的 store_request_id
            timestamp_ms = int(time.time() * 1000)
            is_group_flag = 1 if is_group else 0
            random_suffix = random.randint(10000, 99999)
            store_request_id = f"store_{timestamp_ms}_{is_group_flag}_{qq_number}_{random_suffix}"
            
            # 创建等待响应的 Future
            future = asyncio.Future()
            self.pending_store_requests[store_request_id] = future
            
            logger.debug(f"准备发送存储请求: store_request_id={store_request_id}, qq_number={qq_number}, is_group={is_group}, content={content[:50]}")
            
            # 发送存储消息（包含 store_request_id）
            success = await self.ws_client.send_store_message(
                qq_number=qq_number,
                content=content,
                is_group=is_group,
                store_request_id=store_request_id,
                metadata=metadata,
            )
            
            if not success:
                # 发送失败，清理 Future
                if store_request_id in self.pending_store_requests:
                    self.pending_store_requests.pop(store_request_id, None)
                logger.error(f"发送存储请求失败: store_request_id={store_request_id}, qq_number={qq_number}")
                return False
            
            logger.info(f"已发送存储请求: store_request_id={store_request_id}, qq_number={qq_number}, 等待存储响应...")
            
            # 等待存储响应（带超时）
            try:
                store_result = await asyncio.wait_for(future, timeout=timeout)
                if store_result:
                    logger.info(f"消息存储成功: store_request_id={store_request_id}, qq_number={qq_number}, content={content[:50]}")
                else:
                    logger.warning(f"消息存储失败: store_request_id={store_request_id}, qq_number={qq_number}")
                return store_result
            except asyncio.TimeoutError:
                logger.warning(f"等待存储响应超时: store_request_id={store_request_id}, qq_number={qq_number}")
                if store_request_id in self.pending_store_requests:
                    self.pending_store_requests.pop(store_request_id, None)
                return False
                
        except Exception as e:
            logger.error(f"存储消息时出错: {e}")
            # 清理 Future
            if store_request_id and store_request_id in self.pending_store_requests:
                self.pending_store_requests.pop(store_request_id, None)
            return False
    
    async def request_reply(
        self,
        event: MessageEvent,
        timeout: float = 30.0,
        need_voice: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        """
        向后端请求回复
        
        生成唯一的 request_id：时间戳 + is_group + qq_number
        格式：{timestamp_ms}_{is_group}_{qq_number}
        
        协议格式：
        {
            "command": "chat",
            "data": {
                "content": "用户消息内容",
                "is_group": false,
                "qq_number": "123456789",
                "request_id": "1234567890123_0_123456789",
                "need_voice": true  // 可选，是否需要语音回复
            }
        }
        
        后端返回 reply，格式：
        {
            "command": "reply",
            "data": {
                "content": "AI回复的文本内容",              // 必需：文本回复（包含动作描述）
                "request_id": "请求ID（可选，用于并发匹配）", // 可选：请求ID（用于匹配）
                "audio_url": "音频URL（可选，如果有TTS音频）" // 可选：音频URL（相对路径，需要拼接服务器地址）
            }
        }
        
        Args:
            event: 消息事件
            timeout: 超时时间（秒）
            need_voice: 是否需要语音回复（如果为 None，则根据当前语音模式状态决定）
            
        Returns:
            回复内容字典，包含 "content" 和可选的 "audio_url"
            如果超时或失败则返回 None
        """
        request_id = None
        try:
            # 判断消息类型
            is_group = isinstance(event, GroupMessageEvent)
            # qq_number 是用户ID或群ID（根据 is_group 判断）
            qq_number = str(event.group_id if is_group else event.user_id)
            
            content = await self._extract_event_content(event)
            metadata = await self._build_event_metadata(event, content)
            
            # 如果 need_voice 未指定，根据当前语音模式状态决定
            if need_voice is None:
                try:
                    from src.plugins.BotCore.app import get_voice_mode
                    need_voice = get_voice_mode()
                except Exception as e:
                    logger.debug(f"获取语音模式状态失败，默认不请求语音: {e}")
                    need_voice = False
            
            # 生成唯一的 request_id：时间戳（毫秒）+ is_group + qq_number
            timestamp_ms = int(time.time() * 1000)
            is_group_flag = 1 if is_group else 0
            request_id = f"{timestamp_ms}_{is_group_flag}_{qq_number}"
            
            # 创建等待响应的 Future
            # 使用 request_id 作为 key，支持并发请求
            future = asyncio.Future()
            self.pending_requests[request_id] = future
            
            logger.debug(f"准备发送聊天请求: request_id={request_id}, qq_number={qq_number}, is_group={is_group}, need_voice={need_voice}, content={content[:50]}")
            
            # 发送聊天请求（包含 request_id 和 need_voice）
            success = await self.ws_client.send_chat_request(
                qq_number=qq_number,
                content=content,
                is_group=is_group,
                request_id=request_id,
                need_voice=need_voice,
                metadata=metadata,
            )
            
            if not success:
                # 发送失败，清理 Future
                if request_id in self.pending_requests:
                    self.pending_requests.pop(request_id, None)
                logger.error(f"发送聊天请求失败: request_id={request_id}, qq_number={qq_number}")
                return None
            
            logger.info(f"已发送聊天请求: request_id={request_id}, qq_number={qq_number}, 等待回复...")
            
            # 等待响应（带超时）
            # 后端返回的 reply 包含 request_id，用于精确匹配
            try:
                reply_data = await asyncio.wait_for(future, timeout=timeout)
                logger.info(f"收到回复: request_id={request_id}, qq_number={qq_number}, has_content={bool(reply_data.get('content'))}, has_audio={bool(reply_data.get('audio_url'))}")
                return reply_data
            except asyncio.TimeoutError:
                logger.warning(f"等待回复超时: request_id={request_id}, qq_number={qq_number}")
                if request_id in self.pending_requests:
                    self.pending_requests.pop(request_id, None)
                return None
                
        except Exception as e:
            logger.error(f"请求回复时出错: {e}")
            # 清理 Future
            if request_id and request_id in self.pending_requests:
                self.pending_requests.pop(request_id, None)
            return None
    
    async def get_role_info(self, timeout: float = 10.0) -> Optional[str]:
        """
        获取角色信息
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            角色信息文本，如果获取失败则返回 None
        """
        request_id = None
        try:
            # 创建一个唯一的请求ID
            request_id = f"role_info_{asyncio.get_running_loop().time()}"
            
            # 创建等待响应的 Future
            future = asyncio.Future()
            self.pending_requests[request_id] = future
            
            # 发送角色信息请求
            # 注意：这里需要根据后端的实际协议来发送请求
            # 假设后端支持 role 命令
            message = {
                "command": "role",
                "subCommand": "get",
                "data": {
                    "request_id": request_id
                }
            }
            
            success = await self.ws_client.send(message)
            
            if not success:
                if request_id:
                    self.pending_requests.pop(request_id, None)
                logger.error("发送角色信息请求失败")
                return None
            
            logger.info("已发送角色信息请求，等待回复...")
            
            # 等待响应（带超时）
            try:
                role_info = await asyncio.wait_for(future, timeout=timeout)
                logger.info("收到角色信息")
                return role_info
            except asyncio.TimeoutError:
                logger.warning("等待角色信息超时")
                if request_id:
                    self.pending_requests.pop(request_id, None)
                return None
                
        except Exception as e:
            logger.error(f"获取角色信息时出错: {e}")
            if request_id:
                self.pending_requests.pop(request_id, None)
            return None

    async def get_favorability(
        self,
        event: MessageEvent,
        user_qq_number: Optional[str] = None,
        timeout: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """查询指定 QQ 用户与当前 Bot 绑定角色的好感状态；默认查当前发送者。"""
        request_id = None
        try:
            is_group = isinstance(event, GroupMessageEvent)
            session_qq_number = str(event.group_id if is_group else event.user_id)
            target_qq_number = str(user_qq_number or event.user_id)
            request_id = f"favorability_{int(time.time() * 1000)}_{target_qq_number}"

            future = asyncio.Future()
            self.pending_requests[request_id] = future

            success = await self.ws_client.send(
                {
                    "command": "favorability",
                    "subCommand": "get",
                    "data": {
                        "request_id": request_id,
                        "action": "self",
                        "session_qq_number": session_qq_number,
                        "is_group": is_group,
                        "user_qq_number": target_qq_number,
                    },
                }
            )
            if not success:
                self.pending_requests.pop(request_id, None)
                return None

            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("等待好感状态响应超时")
            if request_id:
                self.pending_requests.pop(request_id, None)
            return None
        except Exception as e:
            logger.error(f"查询好感状态时出错: {e}")
            if request_id:
                self.pending_requests.pop(request_id, None)
            return None

    async def get_favorability_ranking(self, event: MessageEvent, limit: int = 10, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """查询当前群/当前 Bot 的用户好感总值排行。"""
        request_id = None
        try:
            is_group = isinstance(event, GroupMessageEvent)
            session_qq_number = str(event.group_id) if is_group else ""
            request_id = f"favorability_ranking_{int(time.time() * 1000)}_{event.user_id}"

            future = asyncio.Future()
            self.pending_requests[request_id] = future

            data = {
                "request_id": request_id,
                "action": "ranking",
                "limit": limit,
            }
            if is_group:
                data["session_qq_number"] = session_qq_number
                data["is_group"] = True

            success = await self.ws_client.send(
                {
                    "command": "favorability",
                    "subCommand": "ranking",
                    "data": data,
                }
            )
            if not success:
                self.pending_requests.pop(request_id, None)
                return None

            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("等待好感排行响应超时")
            if request_id:
                self.pending_requests.pop(request_id, None)
            return None
        except Exception as e:
            logger.error(f"查询好感排行时出错: {e}")
            if request_id:
                self.pending_requests.pop(request_id, None)
            return None

    async def get_memories(
        self,
        event: MessageEvent,
        user_qq_number: Optional[str] = None,
        limit: int = 10,
        timeout: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """查询指定 QQ 用户在当前 Bot 会话中的最近记忆；默认查当前发送者。"""
        request_id = None
        try:
            is_group = isinstance(event, GroupMessageEvent)
            session_qq_number = str(event.group_id if is_group else event.user_id)
            target_qq_number = str(user_qq_number or event.user_id)
            request_id = f"memory_{int(time.time() * 1000)}_{target_qq_number}"

            future = asyncio.Future()
            self.pending_requests[request_id] = future

            success = await self.ws_client.send(
                {
                    "command": "memory",
                    "subCommand": "list",
                    "data": {
                        "request_id": request_id,
                        "session_qq_number": session_qq_number,
                        "is_group": is_group,
                        "user_qq_number": target_qq_number,
                        "limit": limit,
                    },
                }
            )
            if not success:
                self.pending_requests.pop(request_id, None)
                return None

            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("等待记忆列表响应超时")
            if request_id:
                self.pending_requests.pop(request_id, None)
            return None
        except Exception as e:
            logger.error(f"查询记忆列表时出错: {e}")
            if request_id:
                self.pending_requests.pop(request_id, None)
            return None
    
    def register_reply_callback(self, callback: Callable):
        """
        注册回复回调函数
        
        Args:
            callback: 回调函数，接收 (message_id, reply_content) 作为参数
        """
        self.reply_callbacks.append(callback)
        logger.debug(f"已注册回复回调函数: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
    
    async def _handle_reply(self, message: Dict[str, Any]):
        """
        处理回复消息
        
        根据后端协议，回复消息格式：
        {
            "command": "reply",
            "data": {
                "content": "AI回复的文本内容",              // 必需：文本回复（包含动作描述）
                "request_id": "请求ID（可选，用于并发匹配）", // 可选：请求ID（用于匹配）
                "audio_url": "音频URL（可选，如果有TTS音频）" // 可选：音频URL（相对路径或完整URL）
            }
        }
        
        匹配逻辑：
        - 如果提供了 request_id，使用 request_id 精确匹配
        - 如果找不到对应的 request_id，记录警告并调用回调函数（用于推送的回复）
        
        audio_url 处理：
        - 如果 audio_url 是相对路径（以 / 开头），则拼接服务器地址和端口
        - 如果 audio_url 是完整URL（以 http:// 或 https:// 开头），则直接使用
        
        Args:
            message: 回复消息字典
        """
        try:
            data = message.get("data", {})
            content = data.get("content")  # 文本回复（必需）
            request_id = data.get("request_id")  # 请求ID（可选，用于并发匹配）
            audio_url = data.get("audio_url")  # 可选的音频URL（相对路径或完整URL）
            
            if not content:
                logger.warning("收到回复但缺少 content")
                return
            
            # 处理 audio_url：如果是相对路径，拼接服务器地址
            if audio_url:
                # 如果是相对路径（以 / 开头），拼接服务器地址
                # 使用 http_host 而不是 server_ip，因为本地访问应该使用 localhost
                if audio_url.startswith("/") and self.http_host and self.http_port:
                    audio_url = f"http://{self.http_host}:{self.http_port}{audio_url}"
                    logger.debug(f"已将相对路径转换为完整URL: {audio_url}")
                # 如果已经是完整URL（以 http:// 或 https:// 开头），直接使用
                elif audio_url.startswith(("http://", "https://")):
                    pass  # 已经是完整URL，无需处理
                else:
                    logger.warning(f"audio_url 格式异常，既不是相对路径也不是完整URL: {audio_url}")
            
            # 构造回复数据
            reply_data = {
                "content": content,
                "audio_url": audio_url  # 可能为 None 或完整URL
            }
            
            # 如果提供了 request_id，使用 request_id 精确匹配
            if request_id:
                request_id = str(request_id)  # 确保是字符串
                if request_id in self.pending_requests:
                    future = self.pending_requests.pop(request_id)
                    if not future.done():
                        future.set_result(reply_data)
                    logger.debug(f"已处理回复（通过 request_id 匹配）: request_id={request_id}, has_audio={bool(audio_url)}")
                    return
                else:
                    logger.warning(
                        f"收到回复包含 request_id={request_id}，但未找到对应的待处理请求。"
                        f"待处理请求数={len(self.pending_requests)}, "
                        f"待处理请求keys={list(self.pending_requests.keys())[:5]}..."  # 只显示前5个
                    )
            
            # 如果没有 request_id 或找不到匹配的请求，调用所有注册的回调函数（用于推送的回复）
            for callback in self.reply_callbacks:
                try:
                    await callback(reply_data)
                except Exception as e:
                    logger.error(f"调用回复回调函数时出错: {e}")
                
        except Exception as e:
            logger.error(f"处理回复消息时出错: {e}")
    
    async def _handle_store_response(self, message: Dict[str, Any]):
        """
        处理存储响应消息
        
        根据后端协议，存储响应消息格式：
        {
            "command": "store",
            "subCommand": "success",  // 或 "error"
            "data": {
                "store_request_id": "store_1234567890123_0_123456789_12345"  // 必需：存储请求ID（用于匹配）
            }
        }
        
        匹配逻辑：
        - 必须使用 store_request_id 匹配
        - 如果找不到对应的 store_request_id，记录警告
        
        Args:
            message: 存储响应消息字典
        """
        try:
            sub_command = message.get("subCommand")
            data = message.get("data", {})
            store_request_id = data.get("store_request_id")  # 存储请求ID（必需）
            
            if not store_request_id:
                logger.warning("收到存储响应但缺少 store_request_id，无法匹配")
                return
            
            # 判断存储是否成功
            is_success = (sub_command == "success")
            
            # 使用 store_request_id 精确匹配
            store_request_id = str(store_request_id)  # 确保是字符串
            if store_request_id in self.pending_store_requests:
                future = self.pending_store_requests.pop(store_request_id)
                if not future.done():
                    future.set_result(is_success)
                logger.debug(f"已处理存储响应（通过 store_request_id 匹配）: store_request_id={store_request_id}, success={is_success}")
                return
            else:
                logger.warning(
                    f"收到存储响应包含 store_request_id={store_request_id}，但未找到对应的待处理请求。"
                    f"待处理存储请求数={len(self.pending_store_requests)}, "
                    f"待处理请求keys={list(self.pending_store_requests.keys())[:5]}..."  # 只显示前5个
                )
                
        except Exception as e:
            logger.error(f"处理存储响应消息时出错: {e}")

    async def _handle_favorability_response(self, message: Dict[str, Any]):
        """处理好感状态/排行响应。"""
        try:
            data = message.get("data", {})
            request_id = str(data.get("request_id") or "")
            if not request_id:
                logger.warning("收到好感响应但缺少 request_id")
                return

            if request_id not in self.pending_requests:
                logger.warning(f"收到好感响应但未找到待处理请求: request_id={request_id}")
                return

            future = self.pending_requests.pop(request_id)
            if future.done():
                return

            if message.get("subCommand") == "error":
                future.set_result({"success": False, "message": data.get("message", "查询失败")})
                return

            future.set_result({"success": True, **(data.get("result") or {})})
        except Exception as e:
            logger.error(f"处理好感响应时出错: {e}")

    async def _handle_memory_response(self, message: Dict[str, Any]):
        """处理记忆列表响应。"""
        try:
            data = message.get("data", {})
            request_id = str(data.get("request_id") or "")
            if not request_id:
                logger.warning("收到记忆响应但缺少 request_id")
                return

            if request_id not in self.pending_requests:
                logger.warning(f"收到记忆响应但未找到待处理请求: request_id={request_id}")
                return

            future = self.pending_requests.pop(request_id)
            if future.done():
                return

            if message.get("subCommand") == "error":
                future.set_result({"success": False, "message": data.get("message", "查询失败")})
                return

            future.set_result({"success": True, **(data.get("result") or {})})
        except Exception as e:
            logger.error(f"处理记忆响应时出错: {e}")
