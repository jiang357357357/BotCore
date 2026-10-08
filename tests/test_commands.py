"""Unified command ingress and correlated transport contracts."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.core.router import commands, message_handlers
from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI

APP = "src.plugins.BotCore.app"


def event(message="/帮助", group=False):
    cls = GroupMessageEvent if group else PrivateMessageEvent
    return cls.model_construct(self_id=100001, user_id=200001, group_id=300001,
                               message=Message(message), message_id=400001)


class CommandIngressTests(unittest.IsolatedAsyncioTestCase):
    def test_prefixes_aliases_and_exact_tokens(self):
        for prefix in ("/", "!", "！"):
            parsed = commands.parse_command(event("  " + prefix + "未知 开启  "))
            self.assertEqual(parsed.name, "未知")
            self.assertEqual(parsed.arguments.extract_plain_text().strip(), "开启")
        self.assertEqual(commands.parse_command(event("/模式额外 普通")).name, "模式额外")
        self.assertEqual(commands.parse_command(event("/ ")).name, "")
        for content in ("普通聊天", "请 /帮助", ""):
            self.assertIsNone(commands.parse_command(event(content)))

    def test_reply_and_bot_mention_are_removed_but_target_is_preserved(self):
        message = MessageSegment.reply(4) + MessageSegment.at(100001) + Message(" /记忆 ") + MessageSegment.at(200002)
        parsed = commands.parse_command(event(message, group=True))
        self.assertEqual(parsed.name, "记忆")
        self.assertEqual(parsed.arguments["at"][0].data["qq"], "200002")
        self.assertIsNone(commands.parse_command(event(MessageSegment.at(200002) + Message(" /帮助"))))

    def test_consecutive_text_segments_form_one_command_token(self):
        message = Message([MessageSegment.text(" /语"), MessageSegment.text("音 开启")])
        parsed = commands.parse_command(event(message))
        self.assertEqual(parsed.name, "语音")
        self.assertEqual(parsed.arguments.extract_plain_text(), "开启")

    async def test_unknown_and_denied_commands_never_fall_into_chat(self):
        for content in ("/未知", "！语音 开启", "/"):
            with patch.object(message_handlers, "is_allowed_by_backend", AsyncMock()) as policy, patch.object(
                    message_handlers.private_message_service, "handle_normal_message", AsyncMock()) as chat:
                await message_handlers.handle_message(Mock(), event(content))
                policy.assert_not_awaited()
                chat.assert_not_awaited()

    async def test_voice_applies_only_valid_core_directive_and_reports_save_failure(self):
        result = {"status": "success", "name": "语音", "content": "语音回复已开启。",
                  "runtime_action": {"type": "set_voice_mode", "enabled": True}}
        api = Mock(request_command=AsyncMock(return_value=result))
        with patch(APP + ".ensure_moncore_ready", AsyncMock(return_value=True)), patch(APP + ".get_moncore_api", return_value=api), patch(
                APP + ".set_voice_mode", return_value=True) as setter:
            output = await commands.execute_command(event(group=True), commands.CommandInput("语音", Message("开启")))
            self.assertEqual(output["status"], "success")
            setter.assert_called_once_with(True)
            for bad in ({**result, "status": "failed"}, {**result, "name": "角色"},
                        {**result, "runtime_action": {"type": "set_voice_mode", "enabled": "true"}}):
                setter.reset_mock()
                api.request_command.return_value = bad
                self.assertEqual((await commands.execute_command(event(), commands.CommandInput("语音", Message("开启"))))["status"], "unknown")
                setter.assert_not_called()
            api.request_command.return_value = result.copy()
            setter.return_value = False
            output = await commands.execute_command(event(), commands.CommandInput("语音", Message("开启")))
            self.assertIn("保存配置失败", output["content"])

    async def test_offline_core_cannot_modify_voice(self):
        with patch(APP + ".ensure_moncore_ready", AsyncMock(return_value=False)), patch(APP + ".set_voice_mode") as setter:
            output = await commands.execute_command(event(), commands.CommandInput("语音", Message("开启")))
            self.assertEqual(output["status"], "failed")
            self.assertIn("未执行", output["content"])
            setter.assert_not_called()

    async def test_group_mode_command_strips_bot_mention_and_preserves_group_sender(self):
        message = MessageSegment.at(100001) + Message(" /模式 智能体")
        group_event = event(message, group=True)
        api = Mock(request_command=AsyncMock(return_value={
            "name": "模式", "status": "success", "content": "已开启本群智能体模式。"}))
        with patch(APP + ".ensure_moncore_ready", AsyncMock(return_value=True)), \
             patch(APP + ".get_moncore_api", return_value=api), \
             patch(APP + ".set_voice_mode") as voice:
            parsed = commands.parse_command(group_event)
            result = await commands.execute_command(group_event, parsed)
        self.assertEqual(result["status"], "success")
        api.request_command.assert_awaited_once_with(
            group_event, "模式", "智能体", mentions=[], argument_types=["text"])
        self.assertEqual(group_event.group_id, 300001)
        self.assertEqual(group_event.user_id, 200001)
        voice.assert_not_called()

    async def test_image_failure_falls_back_to_plain_text(self):
        api = SimpleNamespace(send_group_images=AsyncMock(side_effect=RuntimeError("offline")))
        with patch(APP + ".napcat_api", api), patch.object(commands.command_matcher, "finish", AsyncMock()) as finish:
            await commands.send_command_result(event(group=True), {"content": "[CQ:at,qq=all] fixture", "images_base64": ["png"]})
            sent = finish.await_args.args[0]
            self.assertEqual(sent[0].type, "text")
            self.assertEqual(sent.extract_plain_text(), "[CQ:at,qq=all] fixture")


class CommandTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.socket = Mock(qq_command_protocol=1, send=AsyncMock(return_value=True))
        self.api = MonCoreAPI(self.socket)
        patcher = patch.object(self.api, "_qzone_device_auth", return_value={"device_id": "fixture", "device_credential": "test-only"})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def respond(self, payload, **kwargs):
        await self.api._handle_command_response({"command": "qqCommand", "subCommand": "success", "data": {
            "request_id": payload["data"]["request_id"], "name": "语音", "status": "success", "content": "开启",
            "runtime_action": {"type": "set_voice_mode", "enabled": True}}})
        return True

    async def test_group_request_preserves_identity_and_correlated_reply(self):
        self.socket.send.side_effect = self.respond
        with patch(APP + ".get_voice_mode", return_value=False):
            result = await self.api.request_command(event(group=True), "语音", "开启")
        data = self.socket.send.await_args.args[0]["data"]
        self.assertEqual(data["user_id"], "200001")
        self.assertEqual(data["group_id"], "300001")
        self.assertEqual(data["device_id"], "fixture")
        self.assertFalse(data["voice_enabled"])
        self.assertEqual(result["request_id"], data["request_id"])
        self.assertFalse(self.api.pending_command_requests)

    async def test_unknown_receipt_cannot_complete_request_and_timeout_is_uncertain(self):
        async def wrong(payload, **kwargs):
            await self.api._handle_command_response({"subCommand": "success", "data": {
                "request_id": "other", "status": "success", "content": "wrong"}})
            return True
        self.socket.send.side_effect = wrong
        result = await self.api.request_command(event(), "QQ操作", "set_group_name {}", timeout=0.01)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["code"], "RESULT_UNKNOWN")
        self.socket.send.assert_awaited_once()
        self.assertFalse(self.api.pending_command_requests)

    async def test_failed_send_and_missing_device_do_not_claim_permission_denial(self):
        self.socket.send.return_value = False
        result = await self.api.request_command(event(), "帮助", "")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "BACKEND_UNAVAILABLE")
        self.socket.send.reset_mock()
        self.api._qzone_device_auth.side_effect = RuntimeError("no fixture identity")
        self.assertEqual((await self.api.request_command(event(), "帮助", ""))["status"], "failed")
        self.socket.send.assert_not_awaited()

    async def test_malformed_receipt_and_cancellation_release_pending_requests(self):
        async def malformed(payload, **kwargs):
            await self.api._handle_command_response({"subCommand": "error", "data": {
                "request_id": payload["data"]["request_id"], "status": "success", "content": "bad"}})
            return True
        self.socket.send.side_effect = malformed
        self.assertEqual((await self.api.request_command(event(), "帮助", ""))["code"], "INVALID_RECEIPT")
        self.socket.send.side_effect = None
        task = asyncio.create_task(self.api.request_command(event(), "帮助", ""))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.api.pending_command_requests)

    async def test_old_protocol_has_no_legacy_fallback(self):
        self.socket.qq_command_protocol = 0
        result = await self.api.request_command(event(), "帮助", "")
        self.assertEqual(result["code"], "UNSUPPORTED_PROTOCOL")
        self.socket.send.assert_not_awaited()

    async def test_socket_write_failure_is_uncertain_and_is_not_retried(self):
        from src.plugins.BotCore.external.monCore.client.ws.websocket import WebSocketClient
        socket = WebSocketClient("ws://fixture")
        socket.is_connected, socket.qq_command_protocol = True, 1
        socket.websocket = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("write interrupted")))
        socket._handle_send_disconnected = AsyncMock()
        api = MonCoreAPI(socket)
        with patch.object(api, "_qzone_device_auth", return_value={"device_id": "fixture", "device_credential": "test-only"}):
            result = await api.request_command(event(), "语音", "开启")
        self.assertEqual(result["status"], "unknown")
        socket.websocket.send.assert_awaited_once()
        self.assertFalse(api.pending_command_requests)
