"""
连接管理器
管理 MonCore 后端的连接流程：MonHub 查询 → 手动 IP → 注册 → 建立专用连接
只负责连接管理，不处理回调逻辑
"""

import asyncio
import os
from typing import Optional
from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot

from .client import WebSocketClient, ConnectionCallbackHandler
from .client.hub_discovery import discover_via_hub, ServerInfo
from src.System.Logs import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """连接管理器类"""

    def __init__(
        self,
        qq_number: Optional[str] = None,
        manual_ip: Optional[str] = None,
        discovery_port: int = 8888,
        ws_port: int = 8000,
        http_port: Optional[int] = None,
        http_host: Optional[str] = None,
        enable_discovery: bool = True,
        hub_address: Optional[str] = None,
        hub_timeout: float = 5.0,
    ):
        """
        Args:
            qq_number: QQ 号（None 则从 NoneBot 获取）
            manual_ip: 手动配置的服务器 IP（MonHub 失败时使用）
            discovery_port: 已废弃，保留参数仅用于兼容旧调用
            ws_port: WebSocket 端口默认值
            http_port: HTTP 端口（默认与 ws_port 相同）
            http_host: HTTP 访问地址
            enable_discovery: 已废弃，MonBot 现在通过 MonHub 获取 MonCore 地址
            hub_address: MonHub ZMQ 地址（如 tcp://127.0.0.1:40051），None 则跳过 MonHub 查询
            hub_timeout: MonHub 查询超时秒数
        """
        self.qq_number = qq_number
        self.manual_ip = manual_ip or os.getenv("MONCORE_IP")
        self.discovery_port = discovery_port
        self.ws_port = ws_port
        self.http_port = http_port if http_port is not None else ws_port
        self.http_host = http_host or os.getenv("MONCORE_HTTP_HOST", "localhost")
        self.enable_discovery = enable_discovery
        self.hub_address = hub_address
        self.hub_timeout = hub_timeout

        self.ws_client: Optional[WebSocketClient] = None
        self.server_ip: Optional[str] = None
        self.bot_url: Optional[str] = None

        self._connected_event = asyncio.Event()
        self._registered_event = asyncio.Event()

        self.callback_handler = ConnectionCallbackHandler(self)
    
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
    
    async def _get_qq_number(self) -> Optional[str]:
        """
        获取 QQ 号
        
        Returns:
            QQ 号字符串，如果获取失败则返回 None
        """
        if self.qq_number:
            return self.qq_number
        
        try:
            # 尝试从 NoneBot 获取 Bot 实例
            bot = get_bot()
            if isinstance(bot, Bot):
                self_id = str(bot.self_id)
                logger.info(f"从 NoneBot 获取到 QQ 号: {self_id}")
                return self_id
        except Exception as e:
            logger.warning(f"无法从 NoneBot 获取 QQ 号: {e}")
        
        return None
    
    async def discover_server(self) -> Optional[str]:
        """
        发现服务器 IP 地址
        优先级：MonHub 查询 > 手动配置 IP
        """
        # 1) 通过 MonHub 查询
        if self.hub_address:
            logger.info(f"尝试通过 MonHub 查询 MonCore 地址 ({self.hub_address})...")
            server_info = await asyncio.to_thread(
                discover_via_hub, self.hub_address, self.hub_timeout
            )
            if server_info:
                self._apply_server_info(server_info)
                return server_info.ip
            logger.warning("MonHub 查询失败，尝试使用手动配置的 IP")
        else:
            logger.warning("未配置 MonHub 地址，尝试使用手动配置的 IP")

        # 2) 手动配置的 IP
        if self.manual_ip:
            logger.info(f"使用手动配置的服务器 IP: {self.manual_ip}")
            self.server_ip = self.manual_ip
            return self.manual_ip

        logger.warning("未发现 MonCore 服务器：MonHub/手动IP 均失败，将跳过后端连接")
        return None

    def _apply_server_info(self, info: ServerInfo):
        """应用服务器信息"""
        self.server_ip = info.ip
        if info.ws_port:
            self.ws_port = info.ws_port
            if not info.http_port:
                self.http_port = info.ws_port
        if info.http_port:
            self.http_port = info.http_port
        logger.info(f"MonCore 连接参数: ip={self.server_ip}, ws_port={self.ws_port}, http_port={self.http_port}")
    
    async def connect_login_endpoint(self) -> bool:
        """
        连接登录端点
        
        Returns:
            连接是否成功
        """
        if not self.server_ip:
            logger.warning("未设置 MonCore 服务器 IP，无法连接登录端点")
            return False
        
        # 构建登录 URL
        login_url = f"ws://{self.server_ip}:{self.ws_port}/ws/qq_devices/login/"
        logger.info(f"正在连接登录端点: {login_url}")
        
        # 创建 WebSocket 客户端
        self.ws_client = WebSocketClient(login_url)
        
        # 注册注册响应处理器（使用回调处理器）
        self.ws_client.register_handler("register", self.callback_handler.handle_register_response)
        
        # 连接
        success = await self.ws_client.connect()
        
        if success:
            self.is_connected = True
            # 调用连接回调（通过回调处理器）
            await self.callback_handler.handle_connected()
        
        return success
    
    async def register_bot(self) -> bool:
        """
        注册机器人
        
        Returns:
            注册是否成功
        """
        if not self.ws_client or not self.ws_client.is_connected:
            logger.error("WebSocket 未连接，无法注册")
            return False
        
        # 获取 QQ 号
        qq_number = await self._get_qq_number()
        if not qq_number:
            logger.error("无法获取 QQ 号，无法注册")
            return False
        
        # 生成机器人头像URL（使用腾讯官方头像服务）
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq_number}&s=100"
        
        # 获取机器人昵称、联系人列表和群聊列表
        bot_nickname = None
        contacts_list = None
        groups_list = None
        try:
            from src.plugins.BotCore.app import napcat_api
            if not napcat_api:
                logger.warning("napcat_api 未初始化，无法获取机器人信息")
            elif not napcat_api.bot:
                logger.warning("napcat_api.bot 未设置，无法获取联系人和群聊列表（可能 bot 尚未连接）")
            else:
                # 获取机器人昵称
                try:
                    login_info = await napcat_api.get_bot_login_info()
                    if login_info:
                        bot_nickname = login_info.get('nickname')
                        logger.info(f"获取到机器人昵称: {bot_nickname}")
                    else:
                        logger.warning("获取机器人登录信息为空")
                except Exception as e:
                    logger.warning(f"获取机器人昵称失败: {e}")
                
                # 获取联系人列表（好友列表）
                try:
                    contacts_list = await napcat_api.get_friend_list()
                    if contacts_list:
                        logger.info(f"获取到联系人列表: {len(contacts_list)} 个联系人")
                    else:
                        logger.info("联系人列表为空")
                except Exception as e:
                    logger.warning(f"获取联系人列表失败: {e}", exc_info=True)
                
                # 获取群聊列表
                try:
                    groups_list = await napcat_api.get_group_list()
                    if groups_list:
                        logger.info(f"获取到群聊列表: {len(groups_list)} 个群聊")
                    else:
                        logger.info("群聊列表为空")
                except Exception as e:
                    logger.warning(f"获取群聊列表失败: {e}", exc_info=True)
        except Exception as e:
            logger.warning(f"获取机器人信息失败: {e}，将使用默认值", exc_info=True)
        
        # 发送注册请求（包含QQ号、头像URL、昵称、联系人列表和群聊列表）
        logger.info(f"正在注册机器人: QQ号 {qq_number}, 昵称 {bot_nickname}, 头像URL {avatar_url}, contacts={len(contacts_list) if contacts_list else 0}, groups={len(groups_list) if groups_list else 0}")
        success = await self.ws_client.register(
            qq_number, 
            avatar_url=avatar_url, 
            nickname=bot_nickname,
            contacts=contacts_list,
            groups=groups_list
        )
        
        if success:
            # 等待注册响应（最多等待 10 秒）
            # 注册响应会在 _handle_register_response 中处理
            await self._wait_for_registration()
            return self.is_registered
        
        return False
    
    async def _wait_for_registration(self):
        """等待注册完成（使用 asyncio.Event 替代忙等待轮询）"""
        try:
            await asyncio.wait_for(self._registered_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("等待注册超时")
    
    
    async def _connect_bot_channel(self, bot_url: str) -> bool:
        """
        连接机器人专用通道
        
        Args:
            bot_url: 机器人专用 WebSocket URL
            
        Returns:
            连接是否成功
        """
        logger.info(f"正在连接机器人专用通道: {bot_url}")
        
        # 保存 bot_url，用于重连
        self.bot_url = bot_url
        
        # 创建新的 WebSocket 客户端
        self.ws_client = WebSocketClient(bot_url)
        
        # 注册映射更新处理器
        self.ws_client.register_handler("mappingHost", self.callback_handler.handle_mapping_update)
        
        # 注册关键词更新处理器
        self.ws_client.register_handler("keywordsHost", self.callback_handler.handle_keywords_update)
        
        # 注册重连回调：重连后重新注册消息处理器
        self.ws_client.register_reconnect_callback(self._on_reconnect)
        
        # 连接
        success = await self.ws_client.connect()
        
        if success:
            logger.info("机器人专用通道连接成功")
            self.is_connected = True
            # 标记为已注册（专用通道连接成功即表示已注册）
            self.ws_client.is_registered = True
            self.ws_client.bot_url = bot_url
        else:
            logger.error("机器人专用通道连接失败")
            self.is_connected = False
        
        return success
    
    async def _on_reconnect(self):
        """
        重连成功后的回调
        重新注册消息处理器
        """
        logger.info("重连成功，重新注册消息处理器...")
        
        if self.ws_client:
            # 重新注册映射更新处理器
            self.ws_client.register_handler("mappingHost", self.callback_handler.handle_mapping_update)
            
            # 重新注册关键词更新处理器
            self.ws_client.register_handler("keywordsHost", self.callback_handler.handle_keywords_update)
            
            # 更新连接状态
            self.is_connected = True
            self.is_registered = True
            
            # 通知 MonCoreAPI 重连成功（如果已初始化）
            try:
                from src.plugins.BotCore.app import moncore_api
                if moncore_api:
                    # 更新 MonCoreAPI 的 ws_client 引用（如果 ws_client 被替换了）
                    if moncore_api.ws_client != self.ws_client:
                        moncore_api.ws_client = self.ws_client
                        # 重新注册 MonCoreAPI 的消息处理器
                        moncore_api.ws_client.register_handler("reply", moncore_api._handle_reply)
                        moncore_api.ws_client.register_handler("store", moncore_api._handle_store_response)
                        logger.info("已更新 MonCoreAPI 的 WebSocket 客户端引用并重新注册处理器")
            except Exception as e:
                logger.warning(f"通知 MonCoreAPI 重连成功时出错: {e}")
            
            logger.info("重连后消息处理器已重新注册")
    
    async def start(self) -> bool:
        """
        启动完整的连接流程
        
        Returns:
            连接和注册是否成功
        """
        try:
            logger.info("开始 MonCore 连接流程...")
            
            # 阶段一：发现服务器
            server_ip = await self.discover_server()
            if not server_ip:
                logger.warning("未发现 MonCore 服务器，连接流程结束（机器人将继续运行，但后端功能不可用）")
                return False
            
            # 阶段二：连接登录端点并注册
            if not await self.connect_login_endpoint():
                logger.error("连接登录端点失败")
                return False
            
            if not await self.register_bot():
                logger.error("注册机器人失败")
                return False
            
            logger.info("MonCore 连接流程完成")
            return True
            
        except Exception as e:
            logger.error(f"连接流程出错: {e}")
            return False
    
    async def stop(self):
        """停止连接"""
        try:
            if self.ws_client:
                await self.ws_client.disconnect()
            
            self.is_connected = False
            self.is_registered = False
            
            # 调用断开连接回调（通过回调处理器）
            await self.callback_handler.handle_disconnected()
            
            logger.info("连接管理器已停止")
            
        except Exception as e:
            logger.error(f"停止连接时出错: {e}")
    
    def register_on_connected(self, callback):
        """注册连接成功回调"""
        self.callback_handler.register_on_connected(callback)
    
    def register_on_registered(self, callback):
        """注册注册成功回调"""
        self.callback_handler.register_on_registered(callback)
    
    def register_on_disconnected(self, callback):
        """注册断开连接回调"""
        self.callback_handler.register_on_disconnected(callback)
    
    def get_ws_client(self) -> Optional[WebSocketClient]:
        """获取 WebSocket 客户端实例"""
        return self.ws_client
