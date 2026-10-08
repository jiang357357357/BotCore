"""
MonCore API 接口
提供与 MonCore 后端交互的高级 API 接口
封装业务逻辑，使用 WebSocketClient 进行底层通信
"""

import asyncio
import base64
import hashlib
import time
import random
import secrets
from typing import Optional, Dict, Any, Callable
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, PrivateMessageEvent

from ..client import WebSocketClient
from .qzone_api import QzonePublishMixin
from .napcat_actions_api import NapCatActionsMixin
from .commands_api import CommandsMixin
from src.System.Logs import get_logger

logger = get_logger(__name__)


class MonCoreAPI(QzonePublishMixin, NapCatActionsMixin, CommandsMixin):
    """MonCore API 接口类"""

    MODE_DETECTION_GRACE = 10.0
    AGENT_REPLY_TIMEOUT = 1830.0
    
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
        self.pending_chat_modes: Dict[str, asyncio.Future] = {}
        self.pending_card_requests: Dict[str, asyncio.Future] = {}
        self.pending_access_requests: Dict[str, asyncio.Future] = {}
        self.pending_file_sends: Dict[str, Dict[str, Any]] = {}
        self.pending_store_requests: Dict[str, asyncio.Future] = {}  # 等待存储响应的请求（key: store_request_id）
        self.chat_send_locks: Dict[str, asyncio.Lock] = {}
        self.reply_callbacks: list[Callable] = []  # 回复回调函数列表
        self._host_message_tasks: set[asyncio.Task] = set()
        self._init_qzone()
        self._init_napcat_actions()
        self._init_commands()
        
        # 注册消息处理器
        self.register_ws_handlers()

    def register_ws_handlers(self):
        """注册 MonCore 下发消息处理器。重连替换 ws_client 后需要再次调用。"""
        self.ws_client.register_handler("reply", self._handle_reply)
        self.ws_client.register_handler("chat", self._handle_chat_processing)
        self.ws_client.register_handler("error", self._handle_error)
        self.ws_client.register_handler("store", self._handle_store_response)
        self.ws_client.register_handler("sendMessageHost", self._schedule_send_message_host)
        self.ws_client.register_handler("historyHost", self._handle_history_host)
        self.ws_client.register_handler("sync_bot_info", self._handle_sync_bot_info)
        self.ws_client.register_handler("renderCard", self._handle_render_card)
        self.ws_client.register_handler("accessCheck", self._handle_access_check)
        self.ws_client.register_handler("qqCommand", self._handle_command_response)
        self.ws_client.register_handler("qzonePublishHost", self._schedule_qzone_publish_host)
        self.ws_client.register_handler("napcatActionHost", self._schedule_napcat_action_host)
        self.ws_client.register_handler("napcatEventAck", self._handle_napcat_event_ack)

    async def check_access(self, event: MessageEvent, *, capability="chat", target_qq="", timeout=8.0):
        request_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.pending_access_requests[request_id] = future
        denied = {"approved": False, "code": "BACKEND_UNAVAILABLE"}
        try:
            sent = await self.ws_client.send({"command": "accessCheck", "data": {
                "request_id": request_id, "user_id": str(event.user_id),
                "group_id": str(event.group_id) if isinstance(event, GroupMessageEvent) else "",
                "capability": capability, "target_qq": str(target_qq or ""),
            }})
            if not sent:
                return denied
            return await asyncio.wait_for(future, timeout=timeout)
        except (asyncio.TimeoutError, ConnectionError):
            return denied
        finally:
            self.pending_access_requests.pop(request_id, None)

    async def _handle_access_check(self, message):
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        future = self.pending_access_requests.get(str(data.get("request_id") or ""))
        if future and not future.done():
            future.set_result({"approved": message.get("subCommand") == "success" and data.get("approved") is True,
                               "code": str(data.get("code") or "FORBIDDEN")})

    async def render_help_card(self, content: str, timeout: float = 8.0) -> list[str]:
        """Ask the authenticated Core renderer for bounded help PNG pages."""
        text = str(content or "").strip()
        if not text or len(text) > 4000:
            return []
        request_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.pending_card_requests[request_id] = future
        try:
            if not await self.ws_client.send({"command": "renderCard", "data": {
                "request_id": request_id, "kind": "help", "content": text,
            }}):
                return []
            return await asyncio.wait_for(future, timeout=timeout)
        except Exception as error:
            logger.warning("QQ 帮助卡片不可用，改用文字: %s", error)
            return []
        finally:
            self.pending_card_requests.pop(request_id, None)

    async def _handle_render_card(self, message: Dict[str, Any]):
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        request_id = str(data.get("request_id") or "")
        future = self.pending_card_requests.get(request_id)
        if not future or future.done():
            return
        pages = data.get("images_base64")
        if message.get("subCommand") != "success" or not isinstance(pages, list) or not 1 <= len(pages) <= 8 or any(
            not isinstance(page, str) or len(page) > 2_000_000 for page in pages
        ):
            future.set_result([])
        else:
            future.set_result(pages)

    async def _handle_sync_bot_info(self, message: Dict[str, Any]):
        """响应 Mon Web 手动刷新，立即从 NapCat 拉取好友和群聊。"""
        try:
            from src.plugins.BotCore.app import sync_bot_info_once

            synced = await sync_bot_info_once("manual_refresh")
            logger.info(f"手动 Bot 信息同步完成: success={synced}")
        except Exception as exc:
            logger.error(f"手动 Bot 信息同步失败: {exc}", exc_info=True)

    @staticmethod
    def _get_event_message(event: MessageEvent):
        return getattr(event, "original_message", None) or event.get_message()

    @staticmethod
    def _get_message_id(event: MessageEvent) -> str:
        message_id = getattr(event, "message_id", None)
        return str(message_id) if message_id is not None else ""

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
            from ....core.business.message.napcat_input import enrich_input
            enriched = await enrich_input(event, message)
            supplement_count = max(1, sum(segment.type in {"record", "forward"} for segment in message))
            supplement_budget = max(0, 3400 - len(message.extract_plain_text())) // supplement_count

            def excerpt(text):
                if not text or supplement_budget < 100:
                    return ""
                return text if len(text) <= supplement_budget else text[:supplement_budget - 30] + "\n[内容已截断，仅为预览]"

            content_parts = []
            image_index = 0
            for segment in message:
                segment_type = segment.type
                if segment_type == "text":
                    text = segment.data.get("text", "").strip()
                    if text:
                        content_parts.append(text)
                elif segment_type == "image":
                    image_index += 1
                    summary = str(segment.data.get("summary") or "").strip()
                    if summary and summary != "[图片]":
                        content_parts.append(f"[图片{image_index}: {summary}]")
                    else:
                        content_parts.append(f"[图片{image_index}]")
                elif segment_type == "face":
                    content_parts.append("[表情]")
                elif segment_type == "record":
                    transcript = excerpt(enriched.get("voice"))
                    content_parts.append(f"[用户语音转写]\n{transcript}\n[/用户语音转写]" if transcript else "[语音：暂未能转写]")
                elif segment_type == "forward":
                    forward_id = str(segment.data.get("id") or segment.data.get("message_id") or "")
                    forwarded = excerpt(enriched.get("forwards", {}).get(forward_id))
                    content_parts.append(f"[用户提供的合并转发，以下为引用内容]\n{forwarded}\n[/合并转发]" if forwarded else "[合并转发：暂未能展开]")
                elif segment_type == "video":
                    content_parts.append("[视频]")
                elif segment_type == "reply":
                    continue
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
    def _extract_message_image_metadata(message) -> list[Dict[str, Any]]:
        """提取 OneBot 图片段元数据，供 MonCore 进行视觉理解。"""
        images: list[Dict[str, Any]] = []
        try:
            for index, segment in enumerate((seg for seg in message if seg.type == "image"), 1):
                data = dict(getattr(segment, "data", {}) or {})
                image: Dict[str, Any] = {"index": index, "type": "image"}
                for key in ("url", "file", "summary", "sub_type", "file_size"):
                    value = data.get(key)
                    if value is None:
                        continue
                    text = str(value).strip()
                    if text:
                        image[key] = text
                if image.get("url") or image.get("file") or image.get("summary"):
                    images.append(image)
        except Exception as e:
            logger.debug(f"提取图片元数据失败: {e}")
        return images

    @staticmethod
    def _extract_image_metadata(event: MessageEvent) -> list[Dict[str, Any]]:
        return MonCoreAPI._extract_message_image_metadata(MonCoreAPI._get_event_message(event))

    @staticmethod
    def _extract_file_metadata(message) -> list[Dict[str, Any]]:
        files = []
        for segment in message:
            if segment.type != "file":
                continue
            data = dict(getattr(segment, "data", {}) or {})
            files.append({"file_id": str(data.get("file_id") or ""),
                          "filename": str(data.get("file") or ""),
                          "file_size": data.get("file_size")})
        return files

    @staticmethod
    def _extract_resource_ids(message) -> Dict[str, list[str]]:
        """Bind NapCat media identifiers to the authenticated original message."""
        resources = {}
        for segment in message:
            kind = segment.type
            if kind not in {"record", "image", "file", "video"}:
                continue
            data = getattr(segment, "data", {}) or {}
            keys = ("file_id", "id") if kind == "file" else ("file_id", "file", "id")
            for key in keys:
                value = data.get(key)
                if not isinstance(value, (str, int)) or isinstance(value, bool):
                    continue
                identifier = str(value)
                if not identifier or len(identifier) > 1024:
                    continue
                values = resources.setdefault(kind, [])
                if len(values) < 8 and identifier not in values:
                    values.append(identifier)
        return resources

    @staticmethod
    def _extract_message_content_for_metadata(message) -> str:
        content_parts = []
        image_index = 0
        for segment in message:
            segment_type = segment.type
            if segment_type == "reply":
                continue
            if segment_type == "text":
                text = str(segment.data.get("text") or "").strip()
                if text:
                    content_parts.append(text)
            elif segment_type == "image":
                image_index += 1
                summary = str(segment.data.get("summary") or "").strip()
                if summary and summary != "[图片]":
                    content_parts.append(f"[图片{image_index}: {summary}]")
                else:
                    content_parts.append(f"[图片{image_index}]")
            elif segment_type == "at":
                qq = str(segment.data.get("qq", "") or "")
                content_parts.append("@全体成员" if qq.lower() == "all" else f"@用户{qq}")
            elif segment_type == "face":
                content_parts.append("[表情]")
            elif segment_type == "record":
                content_parts.append("[语音]")
            elif segment_type == "video":
                content_parts.append("[视频]")
            else:
                content_parts.append(f"[{segment_type}]")

        if content_parts:
            return " ".join(content_parts)
        plain_text = message.extract_plain_text()
        return plain_text.strip() if plain_text and plain_text.strip() else ""

    @staticmethod
    def _extract_reply_metadata(event: MessageEvent) -> Dict[str, Any]:
        reply_metadata: Dict[str, Any] = {}
        try:
            reply = getattr(event, "reply", None)
            if reply:
                message = getattr(reply, "message", None)
                sender = getattr(reply, "sender", None)
                message_id = getattr(reply, "message_id", None)
                real_id = getattr(reply, "real_id", None)
                reply_metadata = {
                    "message_id": str(message_id) if message_id is not None else "",
                    "real_id": str(real_id) if real_id is not None else "",
                    "message_type": str(getattr(reply, "message_type", "") or ""),
                    "sender_user_id": str(getattr(sender, "user_id", "") or ""),
                    "sender_nickname": str(getattr(sender, "card", None) or getattr(sender, "nickname", None) or ""),
                }
                if message is not None:
                    content = MonCoreAPI._extract_message_content_for_metadata(message)
                    if content:
                        reply_metadata["content"] = content
                    images = MonCoreAPI._extract_message_image_metadata(message)
                    if images:
                        reply_metadata["images"] = images

            if not reply_metadata:
                message = MonCoreAPI._get_event_message(event)
                for segment in message:
                    if segment.type != "reply":
                        continue
                    message_id = segment.data.get("id") or segment.data.get("message_id")
                    if message_id is not None:
                        reply_metadata["message_id"] = str(message_id)
                    break
        except Exception as e:
            logger.debug(f"提取回复引用元数据失败: {e}")

        return {key: value for key, value in reply_metadata.items() if value not in (None, "", [])}

    @staticmethod
    async def _build_event_metadata(event: MessageEvent, content: str) -> Dict[str, Any]:
        """构建消息元数据，用于 MonCore 保留群内真实发送者。"""
        is_group = isinstance(event, GroupMessageEvent)
        message = MonCoreAPI._get_event_message(event)
        sender = getattr(event, "sender", None)
        sender_nickname = getattr(sender, "card", None) or getattr(sender, "nickname", None) or ""
        sender_user_id = str(getattr(event, "user_id", "") or "")
        group_id = str(getattr(event, "group_id", "") or "") if is_group else ""
        display_name = sender_nickname or sender_user_id or "用户"

        metadata = {
            "onebot_message_id": MonCoreAPI._get_message_id(event),
            "sender_user_id": sender_user_id,
            "sender_nickname": sender_nickname,
            "display_name": f"{display_name}({sender_user_id})" if sender_user_id and sender_nickname else display_name,
            "is_group": is_group,
            "raw_content": content,
            "has_text": any(segment.type == "text" and str(segment.data.get("text") or "").strip()
                            for segment in message),
            "has_non_text": any(segment.type not in {"text", "reply"} for segment in message),
            "segment_types": sorted({segment.type for segment in message if segment.type != "reply"}),
            "to_me": bool(event.is_tome()) if hasattr(event, "is_tome") else False,
            "mentions": await MonCoreAPI._extract_mentions(event),
        }
        images = MonCoreAPI._extract_image_metadata(event)
        if images:
            metadata["images"] = images
        resource_ids = MonCoreAPI._extract_resource_ids(message)
        if resource_ids:
            metadata["resource_ids"] = resource_ids
        forward_ids = [str(segment.data.get("id") or segment.data.get("message_id") or "")
                       for segment in message if segment.type == "forward"]
        if forward_ids:
            metadata["forward_ids"] = [identifier for identifier in forward_ids[:3] if 0 < len(identifier) <= 256]
        if event.__dict__.get("_mon_napcat_input"):
            metadata["enriched_input"] = True
        files = MonCoreAPI._extract_file_metadata(message)
        if files:
            metadata["files"] = files
        reply_to = MonCoreAPI._extract_reply_metadata(event)
        if reply_to:
            metadata["reply_to"] = reply_to
        if group_id:
            metadata["group_id"] = group_id
            from ....core.router.message_handlers import _is_keyword_trigger
            metadata["group_agent_trigger"] = bool(_is_keyword_trigger(event))
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
    
    async def _wait_for_chat_reply(self, future: asyncio.Future, mode_future: asyncio.Future, timeout: float) -> Dict[str, Any]:
        """Only an explicit normal-mode acknowledgement may use the short deadline."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.MODE_DETECTION_GRACE
        detecting_mode = True
        watch_mode = True
        while True:
            waiting = {future, mode_future} if watch_mode else {future}
            done, _ = await asyncio.wait(
                waiting, timeout=max(0.0, deadline - loop.time()), return_when=asyncio.FIRST_COMPLETED,
            )
            if future in done:
                return future.result()
            if watch_mode and mode_future in done:
                watch_mode = False
                wait_seconds = timeout if mode_future.result() == "normal" else self.AGENT_REPLY_TIMEOUT
                next_deadline = loop.time() + wait_seconds
                deadline = next_deadline if detecting_mode else min(deadline, next_deadline)
                detecting_mode = False
                continue
            if detecting_mode:
                # Missing/legacy processing notices do not establish ordinary chat.
                deadline = loop.time() + self.AGENT_REPLY_TIMEOUT
                detecting_mode = False
                logger.warning("回复模式尚未确认，保留长任务等待，上限=%ss", self.AGENT_REPLY_TIMEOUT)
                continue
            raise asyncio.TimeoutError

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
                "request_id": "1234567890123_0_123456789_a1b2c3d4",
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
            超时返回可发送的文字提示；其他失败返回 None
        """
        request_id = None
        future = None
        mode_future = None
        send_lock = None
        owns_send_lock = False
        try:
            # 判断消息类型
            is_group = isinstance(event, GroupMessageEvent)
            # qq_number 是用户ID或群ID（根据 is_group 判断）
            qq_number = str(event.group_id if is_group else event.user_id)
            
            content = await self._extract_event_content(event)
            metadata = await self._build_event_metadata(event, content)
            send_lock = self.chat_send_locks.setdefault(
                f"{'group' if is_group else 'private'}:{qq_number}", asyncio.Lock())
            await send_lock.acquire()
            owns_send_lock = True
            
            # 如果 need_voice 未指定，根据当前语音模式状态决定
            if need_voice is None:
                try:
                    from src.plugins.BotCore.app import get_voice_mode
                    need_voice = get_voice_mode()
                except Exception as e:
                    logger.debug(f"获取语音模式状态失败，默认不请求语音: {e}")
                    need_voice = False
            
            # 同一联系人在同一毫秒内也可能连续发消息，随机后缀避免覆盖等待中的 Future。
            timestamp_ms = int(time.time() * 1000)
            is_group_flag = 1 if is_group else 0
            request_id = f"{timestamp_ms}_{is_group_flag}_{qq_number}_{secrets.token_hex(4)}"

            if not is_group and (metadata.get("files") or metadata.get("images")):
                try:
                    files = metadata.get("files") or []
                    images = metadata.get("images") or []
                    if len(files) + len(images) > 4:
                        raise ValueError("一次最多发送 4 个文件或图片")
                    from src.plugins.BotCore.app import napcat_api
                    if not napcat_api:
                        raise RuntimeError("NapCat 未连接")
                    uploaded = []
                    for kind, index, item in [*(('file', index, item) for index, item in enumerate(files, 1)),
                                              *(('image', index, item) for index, item in enumerate(images, 1))]:
                        size = int(item.get("file_size") or 0)
                        if kind == "file":
                            filename = str(item.get("filename") or "")
                            file_id = str(item.get("file_id") or "")
                            if not filename or not file_id or size < 0 or size > 8 * 1024 * 1024:
                                raise ValueError("文件信息无效或单文件超过 8 MiB")
                            blob = await napcat_api.read_private_file(file_id, size)
                        else:
                            image_file = str(item.get("file") or "")
                            if not image_file or size < 0 or size > 8 * 1024 * 1024:
                                raise ValueError("图片信息无效或单张图片超过 8 MiB")
                            blob, extension = await napcat_api.read_private_image(image_file, size)
                            filename = f"qq-image-{index}.{extension}"
                        upload_id = secrets.token_hex(16)
                        sha256 = hashlib.sha256(blob).hexdigest()
                        head = {"command": "fileUpload", "data": {"phase": "start", "upload_id": upload_id,
                                "request_id": request_id, "qq_number": qq_number,
                                "message_id": metadata.get("onebot_message_id"),
                                "filename": filename, "size": len(blob), "sha256": sha256}}
                        if not await self.ws_client.send(head):
                            raise RuntimeError("QQ 文件上传通道已断开")
                        for offset in range(0, len(blob), 96 * 1024):
                            chunk = base64.b64encode(blob[offset:offset + 96 * 1024]).decode("ascii")
                            if not await self.ws_client.send({"command": "fileUpload", "data": {
                                    "phase": "chunk", "upload_id": upload_id, "content_base64": chunk}}):
                                raise RuntimeError("QQ 文件上传中断")
                        if not await self.ws_client.send({"command": "fileUpload", "data": {
                                "phase": "finish", "upload_id": upload_id}}):
                            raise RuntimeError("QQ 文件上传中断")
                        uploaded.append(upload_id)
                    metadata["file_upload_ids"] = uploaded
                except Exception as error:
                    metadata["file_upload_error"] = str(error)[:180]
            
            # 创建等待响应的 Future
            # 使用 request_id 作为 key，支持并发请求
            future = asyncio.Future()
            self.pending_requests[request_id] = future
            mode_future = asyncio.Future()
            self.pending_chat_modes[request_id] = mode_future
            
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
            # A media-only message must finish staging before this contact sends the next message.
            if not (metadata.get("files") or metadata.get("images")) or metadata.get("has_text") or is_group:
                send_lock.release()
                owns_send_lock = False
            
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
                reply_data = await self._wait_for_chat_reply(future, mode_future, timeout)
                logger.info(f"收到回复: request_id={request_id}, qq_number={qq_number}, has_content={bool(reply_data.get('content'))}, has_audio={bool(reply_data.get('audio_url'))}")
                return reply_data
            except asyncio.TimeoutError:
                logger.warning(f"等待回复超时: request_id={request_id}, qq_number={qq_number}")
                if request_id in self.pending_requests:
                    self.pending_requests.pop(request_id, None)
                return {"content": "等待回复已超时。如已启动智能体任务，请先在 Agent 会话中查看执行结果。",
                        "audio_url": None, "images_base64": [], "error_code": "REPLY_TIMEOUT"}
                
        except Exception as e:
            logger.error(f"请求回复时出错: {e}")
            # 清理 Future
            if request_id and request_id in self.pending_requests:
                self.pending_requests.pop(request_id, None)
            return None
        finally:
            if owns_send_lock and send_lock is not None:
                send_lock.release()
            if request_id:
                self.pending_requests.pop(request_id, None)
                self.pending_chat_modes.pop(request_id, None)
            for pending in (future, mode_future):
                if pending is not None and not pending.done():
                    pending.cancel()
    


    
    def register_reply_callback(self, callback: Callable):
        """
        注册回复回调函数
        
        Args:
            callback: 回调函数，接收 (message_id, reply_content) 作为参数
        """
        self.reply_callbacks.append(callback)
        logger.debug(f"已注册回复回调函数: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
    
    async def _handle_chat_processing(self, message: Dict[str, Any]):
        """Core 告知本轮走普通聊天还是 Agent，以便选用对应等待时间。"""
        if message.get("subCommand") != "processing":
            return
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        request_id = str(data.get("request_id") or "")
        mode = data.get("mode")
        if mode not in ("normal", "agent"):
            return
        future = self.pending_chat_modes.get(request_id)
        if future and not future.done():
            future.set_result(mode)

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
        - 如果指定的 request_id 已失效，记录警告并丢弃，避免误送到其他请求
        - 未指定 request_id 的推送沿用独立回调
        
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
            images_base64 = data.get("images_base64")
            if not isinstance(images_base64, list) or len(images_base64) > 8 or any(
                not isinstance(item, str) or len(item) > 2_000_000 for item in images_base64
            ):
                images_base64 = []
            
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
                "audio_url": audio_url,  # 可能为 None 或完整URL
                "images_base64": images_base64,
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
                    # 带请求 ID 的回复只能交给原请求；超时后的迟到回复不能进入主动发送回调。
                    return
            
            # 只有无请求 ID 的旧式主动回复才进入通用回调。
            for callback in self.reply_callbacks:
                try:
                    await callback(reply_data)
                except Exception as e:
                    logger.error(f"调用回复回调函数时出错: {e}")
                
        except Exception as e:
            logger.error(f"处理回复消息时出错: {e}")

    async def _handle_error(self, message: Dict[str, Any]):
        """Resolve the matching request immediately when MonCore rejects it."""
        data = message.get("data", {}) if isinstance(message.get("data"), dict) else {}
        request_id = str(data.get("request_id") or "").strip()
        if not request_id:
            return

        future = self.pending_requests.pop(request_id, None)
        if not future:
            logger.debug(f"错误响应没有匹配的待处理请求: request_id={request_id}")
            return

        if not future.done():
            future.set_result(
                {
                    "content": str(data.get("user_message") or "").strip(),
                    "audio_url": None,
                    "error_code": str(data.get("code") or "UNKNOWN"),
                    "error_message": str(data.get("message") or "未知错误"),
                }
            )

    async def _send_message_host_ack(self, sub_command: str, data: Dict[str, Any]):
        await self.ws_client.send(
            {
                "command": "sendMessageBot",
                "subCommand": sub_command,
                "data": data,
            }
        )

    async def _handle_history_host(self, message: Dict[str, Any]):
        data = message.get("data", {}) if isinstance(message.get("data"), dict) else {}
        request_id = str(data.get("request_id") or "")
        try:
            from src.plugins.BotCore.app import napcat_api

            messages = await napcat_api.get_message_history(
                str(data.get("target_type") or ""),
                str(data.get("target_qq_number") or ""),
                int(data.get("limit") or 100),
                int(data.get("message_seq") or 0),
            )
            await self.ws_client.send(
                {
                    "command": "historyBot",
                    "subCommand": "success",
                    "data": {"request_id": request_id, "messages": messages},
                }
            )
        except Exception as error:
            logger.error(f"读取 QQ 原生历史失败: request_id={request_id} error={error}", exc_info=True)
            await self.ws_client.send(
                {
                    "command": "historyBot",
                    "subCommand": "error",
                    "data": {"request_id": request_id, "message": str(error)},
                }
            )

    async def _schedule_send_message_host(self, message: Dict[str, Any]):
        """Send progress cards without holding the socket's only receive loop."""
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        if data.get("file_phase") is not None:
            # File chunks are ordered protocol frames; retain their existing handling.
            await self._handle_send_message_host(message)
            return
        if len(self._host_message_tasks) >= 16:
            await self._send_message_host_ack("error", {
                "request_id": str(data.get("request_id") or ""), "status": "failed",
                "message": "QQ 消息发送繁忙，本条进度消息未发送",
            })
            return
        task = asyncio.create_task(self._handle_send_message_host(message))
        self._host_message_tasks.add(task)

        def finished(completed):
            self._host_message_tasks.discard(completed)
            if not completed.cancelled() and completed.exception() is not None:
                logger.warning("QQ 主动消息处理未能完成回执")

        task.add_done_callback(finished)

    async def _handle_send_message_host(self, message: Dict[str, Any]):
        """处理 MonCore 主动下发的 QQ 发送命令。"""
        data = message.get("data", {}) if isinstance(message.get("data"), dict) else {}
        request_id = str(data.get("request_id") or "")
        target_type = str(data.get("target_type") or "")
        target_qq_number = str(data.get("target_qq_number") or "")
        content = str(data.get("content") or "")
        images = data.get("images_base64")
        if data.get("file_phase") is not None:
            await self._handle_file_send(data)
            return
        try:
            from src.plugins.BotCore.app import napcat_api

            if not napcat_api:
                raise RuntimeError("NapCat API 未初始化")
            if images is not None:
                if target_type not in {"user", "group"} or not isinstance(images, list) or not 1 <= len(images) <= 8:
                    raise ValueError("主动 QQ 图片只支持 1 至 8 张好友或群聊卡片")
                try:
                    sender = napcat_api.send_group_images if target_type == "group" else napcat_api.send_private_images
                    result = await sender(target_qq_number, images)
                    if not result.get("message_id"):
                        raise RuntimeError("QQ 图片发送后未返回消息 ID")
                    result["api"] = "send_group_msg" if target_type == "group" else "send_private_msg"
                except Exception as image_error:
                    logger.warning("主动 QQ 卡片发送失败，改发文字: request_id=%s error=%s", request_id, image_error)
                    result = await napcat_api.send_text_message(
                        target_type=target_type, target_id=target_qq_number, content=content,
                    )
            else:
                result = await napcat_api.send_text_message(
                    target_type=target_type, target_id=target_qq_number, content=content,
                )
            payload = {
                "request_id": request_id,
                "target_type": target_type,
                "target_qq_number": target_qq_number,
                "message_id": result.get("message_id") or "",
                "api": result.get("api") or "",
                "status": "sent",
            }
            await self._send_message_host_ack("success", payload)
        except Exception as e:
            logger.error(
                f"处理主动 QQ 发信失败: request_id={request_id} "
                f"target={target_type}:{target_qq_number} error={e}",
                exc_info=True,
            )
            await self._send_message_host_ack(
                "error",
                {
                    "request_id": request_id,
                    "target_type": target_type,
                    "target_qq_number": target_qq_number,
                    "message": str(e),
                    "status": "failed",
                },
            )

    async def _handle_file_send(self, data: Dict[str, Any]):
        request_id = str(data.get("request_id") or "")
        phase = data.get("file_phase")
        target = str(data.get("target_qq_number") or "")
        if not request_id.startswith("qq_file_") or not target.isdigit() or data.get("target_type") != "user":
            return
        if phase == "start":
            self.pending_file_sends = {key: item for key, item in self.pending_file_sends.items()
                                       if time.monotonic() - item["created"] <= 120}
            filename = str(data.get("filename") or "")
            size = data.get("size")
            if (not isinstance(size, int) or size < 0 or size > 8 * 1024 * 1024 or
                    not filename or len(filename) > 255 or any(c in filename for c in "/\\\x00\r\n") or
                    len(self.pending_file_sends) >= 4 or request_id in self.pending_file_sends):
                return
            self.pending_file_sends[request_id] = {"target": target, "filename": filename,
                "size": size, "sha256": str(data.get("sha256") or ""),
                "length": 0, "chunks": [], "created": time.monotonic()}
            return
        item = self.pending_file_sends.get(request_id)
        if phase == "chunk":
            if not item or item["target"] != target or time.monotonic() - item["created"] > 120:
                self.pending_file_sends.pop(request_id, None)
                return
            encoded = data.get("content_base64")
            try:
                if not isinstance(encoded, str) or len(encoded) > 131072:
                    raise ValueError("QQ 文件分块无效")
                chunk = base64.b64decode(encoded, validate=True)
            except ValueError:
                self.pending_file_sends.pop(request_id, None)
                return
            item["length"] += len(chunk)
            if item["length"] > item["size"]:
                self.pending_file_sends.pop(request_id, None)
                return
            item["chunks"].append(chunk)
            return
        if phase != "finish":
            return
        item = self.pending_file_sends.pop(request_id, None)
        try:
            if not item or item["target"] != target:
                raise RuntimeError("QQ 文件传输不完整")
            content = b"".join(item["chunks"])
            if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise RuntimeError("QQ 文件完整性校验失败")
            from src.plugins.BotCore.app import napcat_api
            if not napcat_api:
                raise RuntimeError("NapCat 未连接")
            result = await napcat_api.send_private_file(target, item["filename"], content)
            await self._send_message_host_ack("success", {"request_id": request_id,
                "target_type": "user", "target_qq_number": target, "status": "sent",
                "api": "upload_private_file", "file_id": result.get("file_id") or ""})
        except Exception as error:
            logger.error("QQ 文件发送失败: request_id=%s error=%s", request_id, error)
            await self._send_message_host_ack("error", {"request_id": request_id,
                "target_type": "user", "target_qq_number": target,
                "status": "failed", "message": str(error)[:180]})
    
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
