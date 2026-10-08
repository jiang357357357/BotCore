"""Core is the sole authority for QQ business permissions; no local grants."""

import asyncio

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent

from src.System.Logs import get_logger

logger = get_logger(__name__)


async def check_backend_permission(event: MessageEvent, capability="chat", target_qq="") -> bool:
    from ...app import ensure_moncore_ready, get_moncore_api

    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return False
    try:
        if not await ensure_moncore_ready("QQ 权限检查"):
            return False
        api = get_moncore_api()
        if not api:
            return False
        result = await api.check_access(event, capability=capability, target_qq=target_qq)
        return result.get("approved") is True
    except Exception:
        logger.warning("Core 权限暂不可用，本次消息不放行", exc_info=True)
        return False


async def is_allowed_by_backend(event: MessageEvent) -> bool:
    # NoneBot evaluates command matchers concurrently. Share only this event's
    # in-flight query; the next QQ event must obtain a new Core decision.
    task = event.__dict__.get("_moncore_access_task")
    if task is None:
        task = asyncio.create_task(check_backend_permission(event))
        event.__dict__["_moncore_access_task"] = task
    return await asyncio.shield(task)
