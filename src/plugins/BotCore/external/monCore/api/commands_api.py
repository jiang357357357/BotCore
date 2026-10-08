"""One correlated command transport; no local roles or automatic retries."""

import asyncio
import secrets

from nonebot.adapters.onebot.v11 import GroupMessageEvent


class CommandsMixin:
    def _init_commands(self):
        self.pending_command_requests = {}

    async def request_command(self, event, name, arguments, *, mentions=None, argument_types=None, timeout=120.0):
        from ....app import get_voice_mode

        if getattr(self.ws_client, "qq_command_protocol", 0) != 1:
            return {"status": "failed", "code": "UNSUPPORTED_PROTOCOL",
                    "content": "当前 Core 尚未支持统一命令，请同步更新 Core 与 QQBot 后重试。"}
        request_id = "cmd_" + secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self.pending_command_requests[request_id] = future
        send_started = False
        try:
            auth = self._qzone_device_auth()
            reply = self._extract_reply_metadata(event)
            payload = {"command": "qqCommand", "data": {
                "request_id": request_id, "name": name, "arguments": arguments,
                "user_id": str(event.user_id),
                "group_id": str(event.group_id) if isinstance(event, GroupMessageEvent) else "",
                "mentions": [] if mentions is None else mentions, "argument_types": ["text"] if argument_types is None else argument_types, "voice_enabled": get_voice_mode(),
                "reply_to_id": str((reply or {}).get("message_id") or ""), **auth,
            }}
            send_started = True
            sent = await self.ws_client.send(payload, raise_on_error=True)
            if not sent:
                return {"status": "failed", "code": "BACKEND_UNAVAILABLE",
                        "content": "MonCore 当前不可用，命令未发送，请稍后重试。"}
            return await asyncio.wait_for(future, timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return {"status": "unknown", "code": "RESULT_UNKNOWN",
                    "content": "命令结果尚未确认，请先检查实际状态，勿立即重复执行。"}
        except Exception:
            return {"status": "unknown" if send_started else "failed", "code": "BACKEND_UNAVAILABLE",
                    "content": "MonCore 连接中断，命令结果未确认，请先检查实际状态。" if send_started else
                               "设备身份暂不可用，命令未发送，请检查 QQBot 配对状态。"}
        finally:
            self.pending_command_requests.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _handle_command_response(self, message):
        data = message.get("data")
        if not isinstance(data, dict):
            return
        request_id = data.get("request_id")
        if not isinstance(request_id, str):
            return
        future = self.pending_command_requests.get(request_id)
        if future is None or future.done():
            return
        if (data.get("status") not in {"success", "failed", "unknown"} or
                not isinstance(data.get("content"), str) or
                (data.get("status") == "success" and message.get("subCommand") != "success")):
            data = {"status": "unknown", "code": "INVALID_RECEIPT",
                    "content": "命令回执无效，请先核对实际状态，勿立即重复执行。"}
        future.set_result(data)
