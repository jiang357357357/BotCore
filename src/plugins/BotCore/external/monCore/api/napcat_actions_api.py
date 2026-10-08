"""Authenticated, bounded NapCat actions over the existing MonCore connection."""

import asyncio
from collections import OrderedDict
import hashlib
import json

from src.System.Logs import get_logger


logger = get_logger(__name__)


class NapCatActionsMixin:
    def _init_napcat_actions(self):
        self._napcat_action_tasks = {}
        self._napcat_action_results = OrderedDict()


    async def _send_napcat_receipt(self, receipt):
        auth = self._qzone_device_auth()
        await self.ws_client.send({"command": "napcatActionBot",
                                   "subCommand": "success" if receipt["status"] == "success" else "error",
                                   "data": {**receipt, **auth}})

    async def _schedule_napcat_action_host(self, message):
        data = message.get("data")
        if not isinstance(data, dict):
            return
        request_id, action = data.get("request_id"), data.get("action")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return
        if not isinstance(action, str) or not action or len(action) > 80:
            return
        fingerprint = hashlib.sha256(json.dumps(
            {key: data.get(key) for key in ("expected_bot_id", "action", "params")},
            sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        receipt = {"request_id": request_id, "action": action, "status": "failed", "result": None}
        running = self._napcat_action_tasks.get(request_id)
        cached = self._napcat_action_results.get(request_id)
        previous = running or cached
        if previous and previous[0] != fingerprint:
            await self._send_napcat_receipt({**receipt, "code": "REQUEST_CONFLICT", "message": "请求 ID 对应的操作不一致"})
            return
        if running:
            return
        if cached:
            await self._send_napcat_receipt(cached[1])
            return
        if len(self._napcat_action_tasks) >= 8:
            await self._send_napcat_receipt({**receipt, "code": "ACTION_BUSY", "message": "QQ 操作正在处理中，本次未执行"})
            return
        task = asyncio.create_task(self._handle_napcat_action_host(message, fingerprint))
        self._napcat_action_tasks[request_id] = (fingerprint, task)

        def finished(completed):
            self._napcat_action_tasks.pop(request_id, None)
            if not completed.cancelled() and completed.exception() is not None:
                logger.warning("QQ 能力操作回执发送失败: request_id=%s", request_id)

        task.add_done_callback(finished)

    async def _handle_napcat_action_host(self, message, fingerprint):
        from ...napcat.actions import NapCatActionError
        from ....app import napcat_api

        data = message["data"]
        request_id, action = data["request_id"], data["action"]
        receipt = {"request_id": request_id, "action": action, "status": "unknown", "result": None,
                   "code": "DELIVERY_UNKNOWN", "message": "操作结果未确认，请先查询实际状态"}
        try:
            self._qzone_device_auth()  # Never execute if this device cannot send authenticated receipts.
            active_bot = getattr(napcat_api, "bot", None)
            if not active_bot or str(active_bot.self_id) != str(data.get("expected_bot_id") or ""):
                raise NapCatActionError("当前 QQ 账号与目标不一致", "BOT_MISMATCH")
            if message.get("subCommand") != "execute":
                raise NapCatActionError("不支持的操作指令", "INVALID_ACTION")
            result = await napcat_api.execute_action(action, data.get("params"))
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
                result = {"truncated": True, "message": "操作已成功，返回数据过大，请缩小查询范围"}
            receipt.update(status="success", code="", message="操作成功", result=result)
        except NapCatActionError as error:
            receipt.update(status=error.status, code=error.code, message=str(error))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("QQ 能力操作结果未确认: request_id=%s action=%s", request_id, action)
        finally:
            # Cache uncertain outcomes too: a reconnect must not repeat a remote side effect.
            self._napcat_action_results[request_id] = (fingerprint, receipt)
            while len(self._napcat_action_results) > 256:
                self._napcat_action_results.popitem(last=False)
        await self._send_napcat_receipt(receipt)

    async def report_napcat_event(self, event):
        """Persist selected event fields, then deliver with authenticated ACKs."""
        from ....core.router.napcat_events import normalize_napcat_event
        from ..event_outbox import get_event_relay

        payload = normalize_napcat_event(event)
        if payload is None:
            return False
        return await get_event_relay().enqueue(payload)

    async def _handle_napcat_event_ack(self, message):
        from ..event_outbox import get_event_relay

        data = message.get("data")
        if isinstance(data, dict):
            await get_event_relay().acknowledge(data, message.get("subCommand"))
