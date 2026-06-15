"""
消息业务服务基类
提供群聊和私聊消息服务的公共逻辑
"""

import os
from src.System.Logs import get_logger
from typing import Optional
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from ....config import BotConfig
from ....external import Storage
from ....external.napcat import NapCatAPI
from ....app import get_voice_mode
from ...utils.audio_utils import download_audio, cleanup_audio_file

logger = get_logger(__name__)


class BaseMessageService:
    """消息业务服务基类"""

    def __init__(self, config: BotConfig, storage: Storage, napcat_api: NapCatAPI):
        self.config = config
        self.storage = storage
        self.napcat_api = napcat_api

    def _get_moncore_api(self):
        """延迟获取 MonCoreAPI 实例"""
        from ....app import get_moncore_api
        return get_moncore_api()

    async def _ensure_moncore_api(self, context_label: str):
        """按需恢复 MonCore 连接并返回可用 API。"""
        moncore_api = self._get_moncore_api()
        if moncore_api:
            return moncore_api

        try:
            from ....app import ensure_moncore_ready
            if await ensure_moncore_ready(f"{context_label}业务调用"):
                return self._get_moncore_api()
        except Exception as e:
            logger.error(f"按需恢复 MonCore 连接失败: {e}", exc_info=True)

        return None

    @staticmethod
    def _build_audio_message(audio_path: str) -> Message:
        """构建语音消息"""
        abs_path = os.path.abspath(audio_path).replace('\\', '/')
        return Message(MessageSegment.record(f"file:///{abs_path}"))

    async def _try_send_voice_or_fallback(
        self,
        content: str,
        audio_url: str,
        context_label: str
    ) -> Message:
        """
        尝试发送语音，失败则回退到文本
        
        Args:
            content: 文本内容
            audio_url: 音频下载 URL
            context_label: 上下文标签（如 "群聊 12345"）
        """
        audio_path = await download_audio(audio_url)
        if audio_path:
            try:
                logger.info(f"已生成AI回复（{context_label}，语音）: {content[:50]}...")
                return self._build_audio_message(audio_path)
            except Exception as e:
                logger.error(f"发送语音消息失败: {e}，回退到文本消息")
                cleanup_audio_file(audio_path)
        else:
            logger.warning(f"音频下载失败，发送文本消息: {audio_url}")

        preview = content[:50] + "..." if len(content) > 50 else content
        logger.info(f"已生成AI回复（{context_label}，文本）: {preview}")
        return Message(content)

    async def _store_and_request_reply(self, event, context_label: str) -> Optional[Message]:
        """
        存储消息并向后端请求回复（关键词触发和私聊普通消息共用）
        
        Args:
            event: 消息事件
            context_label: 上下文标签
        """
        moncore_api = await self._ensure_moncore_api(context_label)
        if not moncore_api:
            logger.warning(f"MonCoreAPI 未初始化，无法处理{context_label}消息")
            return None

        store_success = await moncore_api.store_message(event)
        if not store_success:
            logger.warning(f"{context_label}消息存储失败，跳过回复请求")
            return None

        reply_data = await moncore_api.request_reply(event)
        if not reply_data or not reply_data.get("content"):
            logger.debug(f"未收到AI回复（{context_label}）")
            return None

        content = reply_data.get("content", "")
        audio_url = reply_data.get("audio_url")

        if get_voice_mode() and audio_url:
            return await self._try_send_voice_or_fallback(content, audio_url, context_label)
        else:
            preview = content[:50] + "..." if len(content) > 50 else content
            logger.info(f"已生成AI回复（{context_label}，文本）: {preview}")
            return Message(content)

    async def _store_message(self, event, context_label: str) -> bool:
        """存储消息到后端，不请求回复"""
        moncore_api = await self._ensure_moncore_api(context_label)
        if not moncore_api:
            logger.warning(f"MonCoreAPI 未初始化，无法存储{context_label}消息")
            return False
        return await moncore_api.store_message(event)
