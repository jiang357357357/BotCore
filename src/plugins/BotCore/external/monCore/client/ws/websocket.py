"""
WebSocket 客户端
管理与 MonCore 后端的 WebSocket 连接
"""

import asyncio
import json
import os
from typing import Optional, Callable, Dict, Any
from websockets.client import connect
from websockets.exceptions import ConnectionClosed

from src.System.Logs import get_logger

logger = get_logger(__name__)


class WebSocketClient:
    """WebSocket 客户端类"""
    
    def __init__(self, server_url: str):
        """
        初始化 WebSocket 客户端
        
        Args:
            server_url: 服务器 WebSocket URL
        """
        self.server_url = server_url
        self.websocket = None
        self._connected_event = asyncio.Event()
        self._registered_event = asyncio.Event()
        self.bot_id: Optional[str] = None
        self.bot_url: Optional[str] = None
        self.message_handlers: Dict[str, Callable] = {}
        self.reconnect_interval = 5
        self.reconnect_task: Optional[asyncio.Task] = None
        self.enable_reconnect = True
        self.reconnect_callbacks: list[Callable] = []
        self.reconnect_failed_callbacks: list[Callable[[int], Any]] = []
        self._receive_task: Optional[asyncio.Task] = None
    
    @property
    def is_connected(self) -> bool:
        return self._connected_event.is_set()
    
    @is_connected.setter
    def is_connected(self, value: bool):
        if value:
            self._connected_event.set()
        else:
            self._connected_event.clear()
    
    @property
    def is_registered(self) -> bool:
        return self._registered_event.is_set()
    
    @is_registered.setter
    def is_registered(self, value: bool):
        if value:
            self._registered_event.set()
        else:
            self._registered_event.clear()
        
    async def connect(self) -> bool:
        """
        连接到服务器
        
        Returns:
            连接是否成功
        """
        try:
            logger.info(f"正在连接到 WebSocket 服务器: {self.server_url}")
            self.websocket = await connect(self.server_url)
            self.is_connected = True
            logger.info("WebSocket 连接成功")
            
            # 启动消息接收任务
            self._receive_task = asyncio.create_task(self._receive_messages())
            
            return True
            
        except Exception as e:
            logger.error(f"WebSocket 连接失败: {e}")
            self.is_connected = False
            return False
    
    async def disconnect(self, disable_reconnect: bool = False):
        """
        断开连接
        
        Args:
            disable_reconnect: 是否禁用重连（用于主动断开连接）
        """
        try:
            if disable_reconnect:
                self.enable_reconnect = False
            
            if self.websocket:
                await self.websocket.close()
            self.is_connected = False
            if disable_reconnect:
                self.is_registered = False
            logger.info("WebSocket 连接已断开")
        except Exception as e:
            logger.error(f"断开连接时出错: {e}")
    
    async def register(
        self, 
        qq_number: str, 
        avatar_url: Optional[str] = None, 
        nickname: Optional[str] = None,
        signature: Optional[str] = None,
        contacts: Optional[list] = None,
        groups: Optional[list] = None
    ) -> bool:
        """
        注册机器人
        
        协议格式：
        {
            "command": "QQBot",
            "subCommand": "register",
            "data": {
                "qq_number": "123456789",
                "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=123456789&s=100",
                "nickname": "机器人昵称",
                "signature": "QQ 个性签名",
                "contacts": [{"user_id": "123456789", "nickname": "好友昵称", "avatar_url": "..."}],
                "groups": [{"group_id": "888888888", "group_name": "群名称", "avatar_url": "..."}]
            }
        }
        
        Args:
            qq_number: QQ 号
            avatar_url: 机器人头像URL（可选）
            nickname: 机器人昵称（可选）
            signature: QQ 个性签名（可选）
            contacts: 联系人列表（可选，从NapCat获取）
            groups: 群聊列表（可选，从NapCat获取）
            
        Returns:
            注册是否成功
        """
        try:
            if not self.is_connected:
                logger.error("WebSocket 未连接，无法注册")
                return False
            
            # 构建注册数据
            register_data = {
                "qq_number": qq_number
            }
            
            # 如果提供了头像URL，添加到数据中
            if avatar_url:
                register_data["avatar_url"] = avatar_url
            
            # 如果提供了昵称，添加到数据中
            if nickname:
                register_data["nickname"] = nickname

            if signature is not None:
                register_data["signature"] = signature
            
            # 如果提供了联系人列表，添加到数据中
            if contacts is not None:
                register_data["contacts"] = contacts
            
            # 如果提供了群聊列表，添加到数据中
            if groups is not None:
                register_data["groups"] = groups
            
            # 发送注册请求
            register_message = {
                "command": "QQBot",
                "subCommand": "register",
                "data": register_data
            }
            
            await self.send(register_message)
            logger.info(f"已发送注册请求: QQ号 {qq_number}, 昵称 {nickname}, contacts={len(contacts) if contacts else 0}, groups={len(groups) if groups else 0}")
            
            # 等待注册响应（在消息处理器中处理）
            # 这里只是发送请求，实际响应会在 _receive_messages 中处理
            
            return True
            
        except Exception as e:
            logger.error(f"注册机器人失败: {e}")
            return False
    
    async def send(self, message: Dict[str, Any]) -> bool:
        """
        发送消息
        
        Args:
            message: 消息字典
            
        Returns:
            发送是否成功
        """
        try:
            if not self.is_connected or not self.websocket:
                logger.error("WebSocket 未连接，无法发送消息")
                await self._handle_send_disconnected("发送前发现 WebSocket 未连接")
                return False
            
            message_str = json.dumps(message, ensure_ascii=False)
            await self.websocket.send(message_str)
            logger.debug(f"已发送消息: {message.get('command', 'unknown')}")
            return True
            
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            await self._handle_send_disconnected("发送消息失败后标记断开")
            return False

    async def _handle_send_disconnected(self, reason: str):
        """发送路径发现连接不可用时，也要触发恢复流程。"""
        self.is_connected = False
        if not self.is_registered or not self.enable_reconnect:
            return

        logger.warning(f"{reason}，启动自动重连")
        await self._start_reconnect()
    
    async def send_store_message(
        self,
        qq_number: str,
        content: str,
        is_group: bool,
        store_request_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        发送存储消息命令
        
        协议格式：
        {
            "command": "store",
            "data": {
                "content": "用户消息内容",
                "is_group": false,
                "qq_number": "123456789",
                "store_request_id": "store_1234567890123_0_123456789_12345"  // 可选：用于匹配响应
            }
        }
        
        Args:
            qq_number: 用户ID或群ID（字符串格式）
            content: 消息内容
            is_group: 是否为群消息
            store_request_id: 存储请求ID（可选，用于匹配响应）
            
        Returns:
            发送是否成功
        """
        data = {
            "content": content,
            "is_group": is_group,
            "qq_number": qq_number
        }
        
        # 如果提供了 store_request_id，添加到 data 中
        if store_request_id:
            data["store_request_id"] = store_request_id

        if metadata:
            data["metadata"] = metadata
        
        message = {
            "command": "store",
            "data": data
        }
        
        return await self.send(message)
    
    async def send_chat_request(
        self,
        qq_number: str,
        content: str,
        is_group: bool,
        request_id: Optional[str] = None,
        need_voice: Optional[bool] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        发送聊天请求命令
        
        协议格式：
        {
            "command": "chat",
            "data": {
                "content": "用户消息内容",
                "is_group": false,
                "qq_number": "123456789",
                "request_id": "1234567890123_0_123456789",  // 可选，用于匹配回复
                "need_voice": true  // 可选，是否需要语音回复
            }
        }
        
        Args:
            qq_number: 用户ID或群ID（字符串格式）
            content: 消息内容
            is_group: 是否为群消息
            request_id: 请求ID（格式：时间戳_is_group_qq_number），用于匹配回复
            need_voice: 是否需要语音回复（如果为 None，则不包含此字段）
            
        Returns:
            发送是否成功
        """
        data = {
            "content": content,
            "is_group": is_group,
            "qq_number": qq_number
        }
        
        # 如果提供了 request_id，添加到数据中
        if request_id:
            data["request_id"] = request_id
        
        # 如果指定了 need_voice，添加到数据中
        if need_voice is not None:
            data["need_voice"] = need_voice

        if metadata:
            data["metadata"] = metadata
        
        message = {
            "command": "chat",
            "data": data
        }
        
        return await self.send(message)
    
    async def send_bot_info(
        self,
        contacts: Optional[list[dict]] = None,
        groups: Optional[list[dict]] = None,
    ) -> bool:
        """
        发送机器人好友和群聊列表到后端
        
        协议格式：
        {
            "command": "bot_info",
            "data": {
                "contacts": [
                    {
                        "user_id": "123456789",
                        "nickname": "好友昵称",
                        "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=123456789&s=100"
                    },
                    ...
                ],
                "groups": [
                    {
                        "group_id": "888888888",
                        "group_name": "群名称",
                        "avatar_url": "https://p.qlogo.cn/gh/888888888/888888888/100"
                    },
                    ...
                ]
            }
        }
        
        Args:
            contacts: 好友列表，每个元素包含 user_id、nickname 和 avatar_url
            groups: 群聊列表，每个元素包含 group_id、group_name 和 avatar_url
            
        Returns:
            发送是否成功
        """
        data: Dict[str, Any] = {}
        if contacts is not None:
            data["contacts"] = contacts
        if groups is not None:
            data["groups"] = groups

        if not data:
            logger.debug("跳过发送机器人信息到后端: contacts/groups 均为空")
            return False

        message = {
            "command": "bot_info",
            "data": data,
        }
        
        logger.info(
            "发送机器人信息到后端: "
            f"contacts={len(contacts) if contacts is not None else 'skip'}, "
            f"groups={len(groups) if groups is not None else 'skip'}"
        )
        return await self.send(message)
    
    async def send_mapping_update_confirm(self) -> bool:
        """
        发送映射更新确认响应
        
        协议格式：
        {
            "command": "mappingBot",
            "subCommand": "success",
            "data": {
                "message": "映射更新已接收并处理"
            }
        }
        
        Returns:
            发送是否成功
        """
        message = {
            "command": "mappingBot",
            "subCommand": "success",
            "data": {
                "message": "映射更新已接收并处理"
            }
        }
        
        logger.debug("发送映射更新确认响应")
        return await self.send(message)
    
    async def send_keywords_update_confirm(self, keywords: list[str]) -> bool:
        """
        发送关键词更新确认响应
        
        协议格式：
        {
            "command": "keywordsBot",
            "subCommand": "success",
            "data": {
                "message": "关键词更新通知已接收",
                "keywords": ["关键词1", "关键词2", ...]
            }
        }
        
        Args:
            keywords: 关键词列表
            
        Returns:
            发送是否成功
        """
        message = {
            "command": "keywordsBot",
            "subCommand": "success",
            "data": {
                "message": "关键词更新通知已接收",
                "keywords": keywords
            }
        }
        
        logger.debug(f"发送关键词更新确认响应: keywords={len(keywords)}")
        return await self.send(message)
    
    def register_handler(self, command: str, handler: Callable):
        """
        注册消息处理器
        
        Args:
            command: 命令名称
            handler: 处理函数
        """
        self.message_handlers[command] = handler
        logger.debug(f"已注册消息处理器: {command}")
    
    def register_reconnect_callback(self, callback: Callable):
        """
        注册重连成功回调函数
        
        Args:
            callback: 回调函数（无参数）
        """
        self.reconnect_callbacks.append(callback)
        logger.debug(f"已注册重连回调函数: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")

    def register_reconnect_failed_callback(self, callback: Callable[[int], Any]):
        """
        注册重连失败回调函数。

        专用通道使用后端返回的 bot_url；当服务 IP 变化时，这个 URL 可能失效。
        失败回调用于通知上层放弃旧 URL，重新走发现和注册流程。
        """
        self.reconnect_failed_callbacks.append(callback)
        logger.debug(f"已注册重连失败回调函数: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
    
    async def _receive_messages(self):
        """接收消息循环"""
        try:
            while self.is_connected:
                if not self.websocket:
                    break
                
                try:
                    message_str = await self.websocket.recv()
                    message = json.loads(message_str)
                    
                    # 处理消息
                    await self._handle_message(message)
                    
                except ConnectionClosed:
                    logger.warning("WebSocket 连接已关闭")
                    self.is_connected = False
                    break
                except json.JSONDecodeError as e:
                    logger.error(f"解析消息失败: {e}")
                except Exception as e:
                    logger.error(f"接收消息时出错: {e}")
                    
        except Exception as e:
            logger.error(f"消息接收循环出错: {e}")
            self.is_connected = False
        
        # 连接断开后尝试重连（仅对已注册的专用通道进行重连）
        if self.is_registered and self.enable_reconnect:
            logger.info("检测到连接断开，启动自动重连...")
            await self._start_reconnect()
        else:
            if not self.is_registered:
                logger.debug("连接断开，但未注册，不进行重连（这是登录端点连接）")
            elif not self.enable_reconnect:
                logger.debug("连接断开，但自动重连已禁用")
    
    async def _handle_message(self, message: Dict[str, Any]):
        """
        处理接收到的消息
        
        根据后端协议，可能的消息类型：
        - register: 注册响应
        - store: 存储响应（fire-and-forget，不需要匹配）
        - chat: 聊天处理确认（processing 状态）
        - reply: 聊天回复（需要 request_id 匹配）
        - error: 错误响应
        
        Args:
            message: 消息字典
        """
        command = message.get("command")
        
        # 处理注册响应（通过 handler 通知外部，不自动处理）
        if command == "register":
            if "register" in self.message_handlers:
                await self.message_handlers["register"](message)
            else:
                # 如果没有注册 handler，使用默认处理逻辑
                sub_command = message.get("subCommand")
                if sub_command == "success":
                    data = message.get("data", {})
                    self.bot_id = data.get("bot_id")
                    self.bot_url = data.get("bot_url")
                    self.is_registered = True
                    logger.info(f"机器人注册成功: bot_id={self.bot_id}, bot_url={self.bot_url}")
        
        # 处理连接确认消息（可能包含 contacts、groups 和 keywords）
        elif command == "connection":
            sub_command = message.get("subCommand")
            if sub_command == "confirm":
                data = message.get("data", {})
                contacts = data.get("contacts", [])
                groups = data.get("groups", [])
                keywords = data.get("keywords", [])
                
                # 更新映射配置（contacts 和 groups）
                # 注意：即使 contacts 和 groups 都是空列表，也应该更新（空列表表示没有配置）
                if "contacts" in data or "groups" in data:
                    try:
                        from src.plugins.BotCore.app import update_supported_contacts_and_groups
                        update_supported_contacts_and_groups(contacts, groups)
                        logger.debug(f"连接确认时更新QQ号列表: contacts={len(contacts)}, groups={len(groups)}")
                    except ImportError as e:
                        logger.warning(f"无法导入 update_supported_contacts_and_groups: {e}")
                    except Exception as e:
                        logger.error(f"连接确认时更新映射配置出错: {e}", exc_info=True)
                
                # 更新关键词配置
                if keywords:
                    try:
                        from src.plugins.BotCore.app import update_supported_keywords
                        update_supported_keywords(keywords)
                        logger.debug(f"连接确认时更新关键词列表: keywords={len(keywords)}")
                    except ImportError as e:
                        logger.warning(f"无法导入 update_supported_keywords: {e}")
                
                if not (contacts or groups or keywords):
                    logger.debug("连接确认消息（无配置数据）")
        
        # 处理映射更新消息
        elif command == "mappingHost":
            if "mappingHost" in self.message_handlers:
                await self.message_handlers["mappingHost"](message)
            else:
                logger.debug(f"收到映射更新消息，但未注册处理器: {message}")
        
        # 处理关键词更新消息
        elif command == "keywordsHost":
            if "keywordsHost" in self.message_handlers:
                await self.message_handlers["keywordsHost"](message)
            else:
                logger.debug(f"收到关键词更新消息，但未注册处理器: {message}")
        
        # 处理存储响应（通过注册的处理器处理，支持 request_id 匹配）
        elif command == "store":
            if "store" in self.message_handlers:
                await self.message_handlers["store"](message)
            else:
                # 如果没有注册处理器，记录日志（向后兼容）
                sub_command = message.get("subCommand")
                if sub_command == "success":
                    logger.debug("消息存储成功（未注册处理器）")
                else:
                    logger.warning(f"消息存储失败（未注册处理器）: {message}")
        
        # 处理聊天处理确认（processing 状态，只记录日志）
        elif command == "chat":
            sub_command = message.get("subCommand")
            if sub_command == "processing":
                logger.debug("聊天请求已接收，正在处理中...")
            else:
                logger.debug(f"收到聊天响应: {sub_command}")
        
        # 处理回复消息（需要 request_id 匹配）
        elif command == "reply":
            if "reply" in self.message_handlers:
                await self.message_handlers["reply"](message)
            else:
                logger.debug(f"收到回复消息，但未注册处理器: {message}")
        
        # 处理错误响应
        elif command == "error":
            data = message.get("data", {})
            error_message = data.get("message", "未知错误")
            error_code = data.get("code", "UNKNOWN")
            logger.error(f"收到错误响应: code={error_code}, message={error_message}")
        
        # 处理其他消息（通过注册的 handler）
        elif command in self.message_handlers:
            await self.message_handlers[command](message)
        else:
            logger.debug(f"未处理的消息: {command}")
    
    async def _start_reconnect(self):
        """启动重连任务"""
        if self.reconnect_task and not self.reconnect_task.done():
            return
        
        self.reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    async def _reconnect_loop(self):
        """重连循环"""
        max_retries = int(os.getenv("MONCORE_WS_RECONNECT_MAX_RETRIES", "6"))
        retry_count = 0
        
        while not self.is_connected:
            logger.info(f"等待 {self.reconnect_interval} 秒后尝试重连... (尝试次数: {retry_count + 1})")
            await asyncio.sleep(self.reconnect_interval)
            
            try:
                # 重连前清理旧连接
                if self.websocket:
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    self.websocket = None
                
                # 尝试重连
                if await self.connect():
                    logger.info("WebSocket 重连成功")
                    # 重连成功后，消息处理器会自动保留（因为 message_handlers 字典没有被清空）
                    # 调用重连成功回调
                    for callback in self.reconnect_callbacks:
                        try:
                            await callback()
                        except Exception as e:
                            logger.error(f"调用重连回调函数时出错: {e}")
                    break
                else:
                    retry_count += 1
                    if max_retries > 0 and retry_count >= max_retries:
                        logger.error(f"达到最大重试次数 ({max_retries})，停止重连")
                        await self._notify_reconnect_failed(retry_count)
                        break
            except Exception as e:
                retry_count += 1
                logger.error(f"重连失败: {e} (尝试次数: {retry_count})")
                if max_retries > 0 and retry_count >= max_retries:
                    logger.error(f"达到最大重试次数 ({max_retries})，停止重连")
                    await self._notify_reconnect_failed(retry_count)
                    break

    async def _notify_reconnect_failed(self, retry_count: int):
        """通知上层重连已失败到阈值。"""
        if not self.reconnect_failed_callbacks:
            return

        self.enable_reconnect = False
        for callback in self.reconnect_failed_callbacks:
            try:
                await callback(retry_count)
            except Exception as e:
                logger.error(f"调用重连失败回调函数时出错: {e}")
