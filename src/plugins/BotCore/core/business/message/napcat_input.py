"""Bounded enrichment of received voice/forward messages, treated as user content."""

import asyncio
import os

from nonebot.adapters.onebot.v11 import PrivateMessageEvent

from src.System.Logs import get_logger


logger = get_logger(__name__)


def _enabled(name):
    return os.getenv(name, "true").lower() not in {"0", "false", "off", "no"}


def summarize_forward(result, limit=8000):
    """Flatten only text and media labels; forward contents are never executable commands."""
    messages = result.get("messages", []) if isinstance(result, dict) else []
    if not isinstance(messages, list):
        return ""
    lines = []
    for item in messages[:30]:
        if not isinstance(item, dict):
            continue
        sender = item.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        name = str(sender.get("nickname") or sender.get("user_id") or "转发者")[:80]
        content = item.get("content", item.get("message", ""))
        if isinstance(content, str):
            text = content[:1500]
        elif isinstance(content, list):
            parts = []
            for segment in content[:30]:
                if not isinstance(segment, dict):
                    continue
                kind = segment.get("type")
                data = segment.get("data")
                data = data if isinstance(data, dict) else {}
                parts.append(str(data.get("text", ""))[:1500] if kind == "text" else
                             {"image": "[图片]", "record": "[语音]", "video": "[视频]", "forward": "[嵌套转发]"}.get(kind, "[附件]"))
            text = "".join(parts)[:1500]
        else:
            continue
        lines.append(f"{name}: {text}")
    return "\n".join(lines)[:limit]


async def enrich_input(event, message):
    """Cache on this event so storing and asking for a reply do not repeat NapCat reads."""
    cached = event.__dict__.get("_mon_napcat_input")
    if isinstance(cached, dict):
        return cached
    result = {"voice": "", "forwards": {}}
    event.__dict__["_mon_napcat_input"] = result
    from ....app import napcat_api

    if not napcat_api or not napcat_api.bot:
        return result
    jobs = []
    if _enabled("MON_QQBOT_TRANSCRIBE_VOICE") and any(segment.type == "record" for segment in message):
        message_id = getattr(event, "message_id", None)
        if message_id is not None:
            jobs.append(("voice", "fetch_ptt_text", {"message_id": str(message_id)}))
    if _enabled("MON_QQBOT_EXPAND_FORWARD"):
        for segment in message:
            if segment.type == "forward":
                identifier = str(segment.data.get("id") or segment.data.get("message_id") or "")
                if identifier and len(identifier) <= 256 and not any(job[0] == identifier for job in jobs):
                    jobs.append((identifier, "get_forward_msg", {"message_id": identifier}))
                if len(jobs) >= 3:
                    break

    async def read(key, action, params):
        try:
            value = await asyncio.wait_for(napcat_api.execute_action(action, params), timeout=12.0)
            if key == "voice":
                text = value.get("text") if isinstance(value, dict) else None
                if isinstance(text, str):
                    result["voice"] = text[:8000]
            else:
                result["forwards"][key] = summarize_forward(value)
        except Exception:
            logger.debug("QQ 输入解析暂不可用: action=%s", action)

    await asyncio.gather(*(read(*job) for job in jobs))
    return result


async def send_input_feedback(event):
    """A short private typing hint; failure must never suppress the final reply."""
    if not isinstance(event, PrivateMessageEvent) or not _enabled("MON_QQBOT_INPUT_FEEDBACK"):
        return
    from ....app import napcat_api
    if not napcat_api or not napcat_api.bot:
        return
    try:
        await asyncio.wait_for(napcat_api.execute_action(
            "set_input_status", {"user_id": str(event.user_id), "event_type": 1}), timeout=2.0)
    except Exception:
        logger.debug("QQ 输入状态提示暂不可用")
