"""QQ Space requests and authenticated publishing receipts, without retries."""

import asyncio
from collections import OrderedDict
import hashlib
import json

from ...napcat.qzone import QzonePublishError, normalize_qzone_payload
from src.System.Logs import get_logger


logger = get_logger(__name__)


class QzonePublishMixin:
    def _init_qzone(self):
        self._qzone_publish_results = OrderedDict()
        self._qzone_publish_lock = asyncio.Lock()
        self._qzone_publish_tasks = {}

    @staticmethod
    def _qzone_device_auth() -> dict:
        from ....app import connection_manager

        store = connection_manager.credential_store
        credential = store.device_credential()
        if not credential:
            raise QzonePublishError("设备凭据不可用，请重新连接 MonCore 后再发布", "AUTH_UNAVAILABLE")
        return {"device_id": store.device_id(), "device_credential": credential}


    async def _schedule_qzone_publish_host(self, message: dict):
        """Keep the shared WebSocket receive loop free while NapCat uploads/publishes."""
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return
        if request_id in self._qzone_publish_tasks:
            return  # The running request will send its single authoritative receipt.
        if self._qzone_publish_tasks:
            try:
                auth = self._qzone_device_auth()
            except QzonePublishError:
                return
            await self.ws_client.send({"command": "qzonePublishBot", "subCommand": "error", "data": {
                **auth, "request_id": request_id, "status": "failed", "code": "PUBLISH_BUSY",
                "message": "已有动态正在发布，本条未发布，请等待上一条结果。",
            }})
            return
        task = asyncio.create_task(self._handle_qzone_publish_host(message))
        self._qzone_publish_tasks[request_id] = task

        def finished(completed):
            self._qzone_publish_tasks.pop(request_id, None)
            if not completed.cancelled() and completed.exception() is not None:
                logger.warning("空间发布处理未能完成回执：request_id=%s", request_id)

        task.add_done_callback(finished)

    async def _handle_qzone_publish_host(self, message: dict):
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return
        try:
            auth = self._qzone_device_auth()
        except QzonePublishError:
            logger.warning("空间发布未执行：设备凭据不可用")
            return
        from ....app import napcat_api

        fingerprint = hashlib.sha256(json.dumps({
            key: data.get(key) for key in ("expected_bot_id", "content", "images", "ugc_right")
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        async with self._qzone_publish_lock:
            cached = self._qzone_publish_results.get(request_id)
            if cached:
                old_fingerprint, receipt = cached
                if old_fingerprint != fingerprint:
                    receipt = {"request_id": request_id, "status": "failed", "code": "REQUEST_CONFLICT",
                               "message": "同一发布请求 ID 的内容不一致，未重复发布"}
            else:
                receipt = {"request_id": request_id, "status": "unknown", "code": "DELIVERY_UNKNOWN",
                           "message": "发布结果未确认，请先查看 QQ 空间，勿立即重复发布"}
                try:
                    if message.get("subCommand") != "publish":
                        raise QzonePublishError("不支持的空间操作", "INVALID_ACTION")
                    active_bot = getattr(napcat_api, "bot", None)
                    expected_bot_id = data.get("expected_bot_id")
                    if not active_bot or not expected_bot_id or str(active_bot.self_id) != str(expected_bot_id):
                        raise QzonePublishError("当前 QQ 账号与发布目标不一致，动态未发布", "BOT_MISMATCH")
                    payload = normalize_qzone_payload(data.get("content"), data.get("images"), data.get("ugc_right", 4))
                    result = await napcat_api.publish_qzone(**payload)
                    if not isinstance(result.get("tid"), str) or not result["tid"].strip():
                        raise QzonePublishError("发布回执缺少说说 ID，请先查看 QQ 空间", "MISSING_TID", "unknown")
                    receipt = {"request_id": request_id, "status": "published", "tid": result["tid"],
                               "api": "send_qzone_msg", "message": "说说已发布"}
                except QzonePublishError as error:
                    receipt = {"request_id": request_id, "status": error.status, "code": error.code, "message": str(error)}
                except ValueError as error:
                    receipt = {"request_id": request_id, "status": "failed", "code": "INVALID_PAYLOAD", "message": str(error)}
                except Exception:
                    logger.warning("空间发布结果无法确认：request_id=%s", request_id)
                finally:
                    # Retain even an interrupted/unknown outcome: repeating it could create a second post.
                    self._qzone_publish_results[request_id] = (fingerprint, receipt)
                    while len(self._qzone_publish_results) > 256:
                        self._qzone_publish_results.popitem(last=False)
        await self.ws_client.send({"command": "qzonePublishBot",
                                   "subCommand": "success" if receipt["status"] == "published" else "error",
                                   "data": {**receipt, **auth}})
