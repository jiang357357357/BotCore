"""
消息业务服务基类
提供群聊和私聊消息服务的公共逻辑
"""

import os
import re
from src.System.Logs import get_logger
from typing import Dict, Optional
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment

from ....config import BotConfig
from ....external import Storage
from ....external.napcat import NapCatAPI
from ....app import get_voice_mode
from ...utils.audio_utils import download_audio, cleanup_audio_file

logger = get_logger(__name__)

CQ_AT_PATTERN = re.compile(r"\[CQ:at,qq=([^\],]+)(?:,[^\]]*)?\]")


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
        try:
            from ....app import ensure_moncore_ready, is_moncore_ready

            moncore_api = self._get_moncore_api()
            if moncore_api and is_moncore_ready():
                return moncore_api

            if moncore_api:
                logger.warning(f"MonCoreAPI 存在但连接不可用，开始按需恢复: {context_label}")

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

    @staticmethod
    def _append_text_segment(message: Message, text: str) -> None:
        if text:
            message += MessageSegment.text(text)

    @staticmethod
    def _add_mention_alias(aliases: Dict[str, str], alias: object, qq: object) -> None:
        alias_text = str(alias or "").strip().lstrip("@")
        qq_text = str(qq or "").strip()
        if not alias_text or not qq_text or qq_text.lower() == "all":
            return
        aliases.setdefault(alias_text, qq_text)
        aliases.setdefault(qq_text, qq_text)
        aliases.setdefault(f"用户{qq_text}", qq_text)

    async def _build_mention_aliases(self, event: MessageEvent) -> Dict[str, str]:
        """构建出站 @ 文本到 QQ 号的映射。"""
        aliases: Dict[str, str] = {}
        if not isinstance(event, GroupMessageEvent):
            return aliases

        sender = getattr(event, "sender", None)
        sender_qq = str(getattr(event, "user_id", "") or "")
        sender_card = getattr(sender, "card", "") or ""
        sender_nickname = getattr(sender, "nickname", "") or ""
        self._add_mention_alias(aliases, sender_card, sender_qq)
        self._add_mention_alias(aliases, sender_nickname, sender_qq)

        try:
            message = getattr(event, "original_message", None) or event.get_message()
            for segment in message:
                if segment.type != "at":
                    continue
                qq = str(segment.data.get("qq", "") or "")
                if not qq or qq.lower() == "all":
                    continue
                for key in ("name", "nickname", "card", "display"):
                    self._add_mention_alias(aliases, segment.data.get(key), qq)

                display_name = None
                if self.napcat_api:
                    display_name = await self.napcat_api.get_group_member_display_name(
                        group_id=str(event.group_id),
                        user_id=qq,
                    )
                self._add_mention_alias(aliases, display_name, qq)

            if self.napcat_api:
                group_aliases = await self.napcat_api.get_group_member_aliases(str(event.group_id))
                for alias, qq in group_aliases.items():
                    self._add_mention_alias(aliases, alias, qq)
        except Exception as e:
            logger.debug(f"构建出站 @ 映射失败: {e}")

        return aliases

    @staticmethod
    def _find_mention_alias(content: str, start: int, aliases: Dict[str, str]) -> tuple[str, str] | None:
        if start >= len(content) or content[start] != "@":
            return None

        for alias, qq in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if alias and content.startswith(alias, start + 1):
                return alias, qq

        match = re.match(r"@(\d{5,12})", content[start:])
        if match:
            qq = match.group(1)
            return qq, qq
        return None

    async def _build_text_message(self, content: str, event: MessageEvent) -> Message:
        """把回复正文中的 @ 文本转换成真实 OneBot at 消息段。"""
        text = str(content or "")
        aliases = await self._build_mention_aliases(event)
        message = Message()
        cursor = 0
        index = 0

        while index < len(text):
            cq_match = CQ_AT_PATTERN.match(text, index)
            if cq_match:
                self._append_text_segment(message, text[cursor:index])
                message += MessageSegment.at(cq_match.group(1).strip())
                index = cq_match.end()
                cursor = index
                continue

            alias_match = self._find_mention_alias(text, index, aliases)
            if alias_match:
                alias, qq = alias_match
                self._append_text_segment(message, text[cursor:index])
                message += MessageSegment.at(qq)
                index += len(alias) + 1
                cursor = index
                continue

            index += 1

        self._append_text_segment(message, text[cursor:])
        return message or Message(MessageSegment.text(text))

    async def _try_send_voice_or_fallback(
        self,
        content: str,
        audio_url: str,
        context_label: str,
        event: MessageEvent,
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
        return await self._build_text_message(content, event)

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
            return await self._try_send_voice_or_fallback(content, audio_url, context_label, event)
        else:
            preview = content[:50] + "..." if len(content) > 50 else content
            logger.info(f"已生成AI回复（{context_label}，文本）: {preview}")
            return await self._build_text_message(content, event)

    async def _store_message(self, event, context_label: str) -> bool:
        """存储消息到后端，不请求回复"""
        moncore_api = await self._ensure_moncore_api(context_label)
        if not moncore_api:
            logger.warning(f"MonCoreAPI 未初始化，无法存储{context_label}消息")
            return False
        return await moncore_api.store_message(event)
