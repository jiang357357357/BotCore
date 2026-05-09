"""
回调处理器
处理 WebSocket 消息回调，与连接管理分离
"""

import asyncio
from typing import Dict, Any, Callable, Optional
from src.System.Logs import get_logger

logger = get_logger(__name__)


class ConnectionCallbackHandler:
    """连接回调处理器类"""
    
    def __init__(self, connection_manager):
        """
        初始化回调处理器
        
        Args:
            connection_manager: 连接管理器实例
        """
        self.connection_manager = connection_manager
        
        # 回调函数列表
        self.on_connected_callbacks: list[Callable] = []
        self.on_registered_callbacks: list[Callable] = []
        self.on_disconnected_callbacks: list[Callable] = []
    
    async def handle_register_response(self, message: Dict[str, Any]):
        """
        处理注册响应消息
        
        后端返回格式：
        {
            "command": "register",
            "subCommand": "success",
            "data": {
                "bot_id": "3977489248",
                "bot_url": "ws://...",
                "contacts": ["123456789", "987654321"],  // 可选
                "groups": ["888888888"],                  // 可选
                "keywords": ["关键词1", "关键词2"]        // 可选
            }
        }
        
        Args:
            message: 注册响应消息字典
        """
        try:
            sub_command = message.get("subCommand")
            if sub_command == "success":
                data = message.get("data", {})
                bot_id = data.get("bot_id")
                bot_url = data.get("bot_url")
                
                # 处理映射配置（contacts 和 groups）
                # 注意：即使 contacts 和 groups 都是空列表，也应该更新（空列表表示没有配置）
                contacts = data.get("contacts", [])
                groups = data.get("groups", [])
                # 检查 data 中是否包含 contacts 或 groups 字段（即使值为空列表）
                if "contacts" in data or "groups" in data:
                    try:
                        from src.plugins.BotCore.app import update_supported_contacts_and_groups
                        update_supported_contacts_and_groups(contacts, groups)
                        logger.info(f"注册时更新映射配置: contacts={len(contacts)}, groups={len(groups)}")
                    except ImportError as e:
                        logger.warning(f"无法导入 update_supported_contacts_and_groups: {e}")
                    except Exception as e:
                        logger.error(f"更新映射配置时出错: {e}", exc_info=True)
                
                # 处理关键词配置
                keywords = data.get("keywords", [])
                if keywords:
                    try:
                        from src.plugins.BotCore.app import update_supported_keywords
                        update_supported_keywords(keywords)
                        logger.info(f"注册时更新关键词配置: keywords={len(keywords)}")
                    except ImportError as e:
                        logger.warning(f"无法导入 update_supported_keywords: {e}")
                
                if bot_id and bot_url:
                    logger.info(f"机器人注册成功: bot_id={bot_id}, bot_url={bot_url}")
                    
                    # 断开登录端点连接
                    if self.connection_manager.ws_client:
                        await self.connection_manager.ws_client.disconnect()
                    
                    # 连接专用通道（通过连接管理器的方法）
                    # 注意：这里需要调用连接管理器的内部方法，但为了解耦，我们通过一个公共方法
                    success = await self.connection_manager._connect_bot_channel(bot_url)
                    
                    if success:
                        self.connection_manager.is_registered = True
                        # 调用注册回调
                        await self._invoke_callbacks(self.on_registered_callbacks)
                    else:
                        logger.error("连接专用通道失败")
                else:
                    logger.error("注册响应中缺少 bot_id 或 bot_url")
            else:
                logger.error(f"注册失败: {sub_command}")
                
        except Exception as e:
            logger.error(f"处理注册响应时出错: {e}")
    
    async def handle_connected(self):
        """处理连接成功事件"""
        await self._invoke_callbacks(self.on_connected_callbacks)
    
    async def handle_disconnected(self):
        """处理断开连接事件"""
        await self._invoke_callbacks(self.on_disconnected_callbacks)
    
    async def _invoke_callbacks(self, callbacks: list[Callable]):
        """
        调用回调函数列表
        
        Args:
            callbacks: 回调函数列表
        """
        for callback in callbacks:
            try:
                if callable(callback):
                    # 检查是否是协程函数
                    if asyncio.iscoroutinefunction(callback):
                        await callback()
                    else:
                        callback()
            except Exception as e:
                logger.error(f"调用回调函数时出错: {e}")
    
    def register_on_connected(self, callback: Callable):
        """注册连接成功回调"""
        self.on_connected_callbacks.append(callback)
        logger.debug("已注册连接成功回调")
    
    def register_on_registered(self, callback: Callable):
        """注册注册成功回调"""
        self.on_registered_callbacks.append(callback)
        logger.debug("已注册注册成功回调")
    
    def register_on_disconnected(self, callback: Callable):
        """注册断开连接回调"""
        self.on_disconnected_callbacks.append(callback)
        logger.debug("已注册断开连接回调")
    
    async def handle_mapping_update(self, message: Dict[str, Any]):
        """
        处理映射更新消息
        
        后端发送格式：
        {
            "command": "mappingHost",
            "subCommand": "update",
            "data": {
                "contacts": ["123456789", "987654321"],
                "groups": ["888888888"]
            }
        }
        
        Args:
            message: 映射更新消息字典
        """
        try:
            sub_command = message.get("subCommand")
            if sub_command == "update":
                data = message.get("data", {})
                contacts = data.get("contacts", [])
                groups = data.get("groups", [])
                
                # 完全替换本地配置（不是增量更新）
                try:
                    from src.plugins.BotCore.app import update_supported_contacts_and_groups
                    update_supported_contacts_and_groups(contacts, groups)
                    logger.info(f"映射配置已更新: contacts={len(contacts)}, groups={len(groups)}")
                    
                    # 发送确认响应（可选）
                    if self.connection_manager.ws_client:
                        await self.connection_manager.ws_client.send_mapping_update_confirm()
                except ImportError as e:
                    logger.warning(f"无法导入 update_supported_contacts_and_groups: {e}")
            else:
                logger.warning(f"未知的映射更新子命令: {sub_command}")
                
        except Exception as e:
            logger.error(f"处理映射更新消息时出错: {e}", exc_info=True)
    
    async def handle_keywords_update(self, message: Dict[str, Any]):
        """
        处理关键词更新消息
        
        后端发送格式：
        {
            "command": "keywordsHost",
            "subCommand": "update",
            "data": {
                "keywords": ["关键词1", "关键词2", ...]
            }
        }
        
        Args:
            message: 关键词更新消息字典
        """
        try:
            sub_command = message.get("subCommand")
            if sub_command == "update":
                data = message.get("data", {})
                keywords = data.get("keywords", [])
                
                # 完全替换本地配置（不是增量更新）
                try:
                    from src.plugins.BotCore.app import update_supported_keywords
                    update_supported_keywords(keywords)
                    logger.info(f"关键词配置已更新: keywords={len(keywords)}")
                    
                    # 发送确认响应（可选）
                    if self.connection_manager.ws_client:
                        await self.connection_manager.ws_client.send_keywords_update_confirm(keywords)
                except ImportError as e:
                    logger.warning(f"无法导入 update_supported_keywords: {e}")
            else:
                logger.warning(f"未知的关键词更新子命令: {sub_command}")
                
        except Exception as e:
            logger.error(f"处理关键词更新消息时出错: {e}", exc_info=True)

