"""Agent processing notices and final replies travel through the real WS dispatcher."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent, GroupMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.client.ws.websocket import WebSocketClient
from src.plugins.BotCore.external.napcat.api.api import NapCatAPI
from src.plugins.BotCore.core.business.message.base_message_service import BaseMessageService


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9cN7sAAAAASUVORK5CYII="


class AgentReplyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = WebSocketClient("ws://test.invalid/ws")
        self.api = MonCoreAPI(self.client)
        self.outgoing = asyncio.Queue()
        self.requests = []

        async def send(message):
            await self.outgoing.put(message)
            return True

        self.client.send = AsyncMock(side_effect=send)
        clock_patch = patch.multiple(MonCoreAPI, MODE_DETECTION_GRACE=0.02, AGENT_REPLY_TIMEOUT=0.5)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addAsyncCleanup(self._finish_requests)

    async def _finish_requests(self):
        for task in self.requests:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.requests, return_exceptions=True)

    async def start_request(self, content="帮我完成任务", timeout=0.08, user_id=20002):
        message = Message(content)
        event = PrivateMessageEvent.model_construct(
            self_id=10001,
            user_id=user_id,
            message_id=77,
            message=message,
            original_message=message,
            to_me=False,
        )
        task = asyncio.create_task(self.api.request_reply(event, timeout=timeout, need_voice=False))
        self.requests.append(task)
        sent = await asyncio.wait_for(self.outgoing.get(), 0.5)
        self.assertEqual(sent["command"], "chat")
        return task, sent["data"]["request_id"]

    async def processing(self, request_id, mode="agent"):
        await self.client._handle_message({
            "command": "chat",
            "subCommand": "processing",
            "data": {"request_id": request_id, "mode": mode},
        })

    async def reply(self, request_id, content):
        await self.client._handle_message({
            "command": "reply",
            "data": {"request_id": request_id, "content": content},
        })

    def assert_no_pending_requests(self):
        self.assertFalse(self.api.pending_requests)
        self.assertFalse(self.api.pending_chat_modes)

    async def test_real_dispatch_delivers_agent_processing_to_matching_future(self):
        future = asyncio.get_running_loop().create_future()
        self.api.pending_chat_modes["agent-request"] = future

        await self.client._handle_message({
            "command": "chat",
            "subCommand": "processing",
            "data": {"request_id": "agent-request", "mode": "agent"},
        })

        self.assertTrue(future.done(), "WS chat processing must reach MonCoreAPI's mode handler")
        self.assertEqual(future.result(), "agent")

    async def test_agent_final_reply_survives_the_normal_timeout(self):
        waiting, request_id = await self.start_request(timeout=0.05)
        await self.processing(request_id)

        await asyncio.sleep(0.09)
        await self.reply(request_id, "Agent 最终结果")

        result = await asyncio.wait_for(waiting, 0.5)
        self.assertIsNotNone(result, "An acknowledged Agent request must outlive the normal timeout")
        self.assertEqual(result["content"], "Agent 最终结果")
        self.assert_no_pending_requests()

    async def test_late_agent_notice_still_extends_wait_for_final_reply(self):
        waiting, request_id = await self.start_request(timeout=0.08)
        # The notice arrives after the old 10-second detection window (scaled to .02).
        await asyncio.sleep(0.035)
        await self.processing(request_id)
        await asyncio.sleep(0.09)
        await self.reply(request_id, "迟到模式通知后的最终结果")

        result = await asyncio.wait_for(waiting, 0.5)
        self.assertIsNotNone(result, "Agent notices arriving during the normal wait must still extend it")
        self.assertEqual(result["content"], "迟到模式通知后的最终结果")
        self.assert_no_pending_requests()

    async def test_normal_request_timeout_removes_reply_and_mode_futures(self):
        waiting, request_id = await self.start_request(timeout=0.03)
        await self.processing(request_id, "normal")

        result = await asyncio.wait_for(waiting, 0.3)
        self.assertEqual(result["error_code"], "REPLY_TIMEOUT")
        self.assertTrue(result["content"])
        self.assert_no_pending_requests()

    async def test_missing_mode_notice_keeps_long_reply_waiting(self):
        waiting, request_id = await self.start_request(timeout=0.03)
        await asyncio.sleep(0.12)
        self.assertFalse(waiting.done(), "Unknown mode must not use the normal chat deadline")
        await self.reply(request_id, "模式通知缺失后的最终结果")
        self.assertEqual((await asyncio.wait_for(waiting, 0.5))["content"], "模式通知缺失后的最终结果")
        self.assert_no_pending_requests()

    async def test_agent_notice_after_old_short_deadline_still_accepts_reply(self):
        waiting, request_id = await self.start_request(timeout=0.03)
        await asyncio.sleep(0.12)
        await self.processing(request_id)
        await self.reply(request_id, "超过旧短等待的最终结果")
        result = await asyncio.wait_for(waiting, 0.5)
        self.assertEqual(result["content"], "超过旧短等待的最终结果")
        self.assert_no_pending_requests()

    async def test_incomplete_processing_notice_does_not_confirm_normal_mode(self):
        waiting, request_id = await self.start_request(timeout=0.03)
        await self.client._handle_message({"command": "chat", "subCommand": "processing",
                                         "data": {"request_id": request_id, "status": "processing"}})
        self.assertFalse(self.api.pending_chat_modes[request_id].done())
        await self.processing(request_id)
        await self.reply(request_id, "完整模式确认后的结果")
        self.assertEqual((await asyncio.wait_for(waiting, 0.5))["content"], "完整模式确认后的结果")
        self.assert_no_pending_requests()

    async def test_unknown_mode_wait_is_bounded_and_reports_timeout(self):
        with patch.object(MonCoreAPI, "AGENT_REPLY_TIMEOUT", 0.05):
            waiting, request_id = await self.start_request(timeout=0.01)
            result = await asyncio.wait_for(waiting, 0.3)
        self.assertEqual(result["error_code"], "REPLY_TIMEOUT")
        self.assertTrue(result["content"])
        self.assert_no_pending_requests()
        callback = AsyncMock()
        self.api.reply_callbacks.append(callback)
        await self.reply(request_id, "超过总等待上限的旧回复")
        callback.assert_not_awaited()

    async def test_late_agent_notice_does_not_restart_the_unknown_mode_deadline(self):
        with patch.object(MonCoreAPI, "AGENT_REPLY_TIMEOUT", 0.2):
            waiting, request_id = await self.start_request(timeout=0.01)
            await asyncio.sleep(0.13)
            await self.processing(request_id)
            result = await asyncio.wait_for(waiting, 0.14)
        self.assertEqual(result["error_code"], "REPLY_TIMEOUT")
        self.assert_no_pending_requests()

    async def test_cancellation_removes_reply_and_mode_futures(self):
        waiting, request_id = await self.start_request()
        self.assertIn(request_id, self.api.pending_requests)
        waiting.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await waiting

        self.assert_no_pending_requests()

    async def test_concurrent_requests_keep_processing_and_replies_paired(self):
        first, first_id = await self.start_request("第一条", timeout=0.3)
        second, second_id = await self.start_request("第二条", timeout=0.3)
        self.assertNotEqual(first_id, second_id)
        await self.processing(second_id)
        self.assertFalse(self.api.pending_chat_modes[first_id].done())
        await self.reply("unrelated-request", "不得误匹配")
        self.assertFalse(first.done())
        self.assertFalse(second.done())

        await self.reply(second_id, "第二条的结果")
        second_result = await asyncio.wait_for(second, 0.5)
        self.assertEqual(second_result["content"], "第二条的结果")
        self.assertFalse(first.done())
        await self.reply(first_id, "第一条的结果")
        self.assertEqual((await asyncio.wait_for(first, 0.5))["content"], "第一条的结果")
        self.assert_no_pending_requests()

    async def test_slow_progress_card_does_not_block_processing_or_final_reply(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def send_images(_user_id, _images):
            started.set()
            await release.wait()
            return {"message_id": "progress-message-1"}

        napcat = Mock()
        napcat.send_private_images = AsyncMock(side_effect=send_images)
        napcat.send_text_message = AsyncMock()
        waiting, request_id = await self.start_request(timeout=0.05)
        with patch("src.plugins.BotCore.app.napcat_api", napcat):
            try:
                await asyncio.wait_for(self.client._handle_message({
                    "command": "sendMessageHost",
                    "data": {
                        "request_id": "progress-1",
                        "target_type": "user",
                        "target_qq_number": "20002",
                        "content": "处理中",
                        "images_base64": [PNG],
                    },
                }), 0.2)
                await asyncio.wait_for(started.wait(), 0.2)
                await self.processing(request_id)
                await asyncio.sleep(0.09)
                await self.reply(request_id, "进度卡仍在发送时得到最终结果")
                result = await asyncio.wait_for(waiting, 0.3)
                self.assertEqual(result["content"], "进度卡仍在发送时得到最终结果")
                self.assertFalse(release.is_set())
                self.assert_no_pending_requests()
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(*self.api._host_message_tasks), 0.5)

        acknowledgement = await asyncio.wait_for(self.outgoing.get(), 0.2)
        self.assertEqual(acknowledgement["command"], "sendMessageBot")
        self.assertEqual(acknowledgement["data"]["request_id"], "progress-1")
        napcat.send_text_message.assert_not_awaited()


class AgentFinalMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_notice_is_delivered_as_private_and_group_text(self):
        for group in (False, True):
            with self.subTest(group=group):
                napcat = Mock()
                napcat.send_group_images = AsyncMock()
                napcat.send_private_images = AsyncMock()
                backend = Mock()
                backend.store_message = AsyncMock(return_value=True)
                backend.request_reply = AsyncMock(return_value={"content": "等待回复已超时，请查看任务结果。",
                                                                "error_code": "REPLY_TIMEOUT", "images_base64": []})
                service = BaseMessageService(Mock(), Mock(), napcat)
                service._ensure_moncore_api = AsyncMock(return_value=backend)
                event = (GroupMessageEvent.model_construct(user_id=20002, group_id=30003) if group
                         else PrivateMessageEvent.model_construct(user_id=20002))
                service._build_text_message = AsyncMock(return_value=Message("等待回复已超时，请查看任务结果。"))
                result = await service._store_and_request_reply(event, "timeout-fixture")
                self.assertEqual(result.extract_plain_text(), "等待回复已超时，请查看任务结果。")
                napcat.send_group_images.assert_not_awaited()
                napcat.send_private_images.assert_not_awaited()

    async def test_disabled_reply_cards_with_empty_images_deliver_private_and_group_text(self):
        for group in (False, True):
            with self.subTest(group=group):
                napcat = Mock()
                napcat.send_group_images = AsyncMock()
                napcat.send_private_images = AsyncMock()
                backend = Mock()
                backend.store_message = AsyncMock(return_value=True)
                backend.request_reply = AsyncMock(return_value={"content": "【智能体】最终回复", "images_base64": []})
                service = BaseMessageService(Mock(), Mock(), napcat)
                service._ensure_moncore_api = AsyncMock(return_value=backend)
                event = (GroupMessageEvent.model_construct(user_id=20002, group_id=30003) if group
                         else PrivateMessageEvent.model_construct(user_id=20002))
                service._build_text_message = AsyncMock(return_value=Message("【智能体】最终回复"))
                result = await service._store_and_request_reply(event, "reply-format-fixture")
                self.assertEqual(result.extract_plain_text(), "【智能体】最终回复")
                service._build_text_message.assert_awaited_once_with("【智能体】最终回复", event)
                napcat.send_group_images.assert_not_awaited()
                napcat.send_private_images.assert_not_awaited()

    async def test_group_final_card_uses_group_target_and_failure_keeps_text(self):
        napcat = NapCatAPI()
        napcat.bot = Mock()
        napcat.bot.call_api = AsyncMock(return_value={"message_id": "group-card"})
        backend = Mock()
        backend.store_message = AsyncMock(return_value=True)
        backend.request_reply = AsyncMock(return_value={"content": "群内最终回复", "images_base64": [PNG]})
        service = BaseMessageService(Mock(), Mock(), napcat)
        service._ensure_moncore_api = AsyncMock(return_value=backend)
        event = GroupMessageEvent.model_construct(user_id=20002, group_id=30003)
        self.assertIsNone(await service._store_and_request_reply(event, "群聊 30003"))
        self.assertEqual(napcat.bot.call_api.call_args.args[0], "send_group_msg")
        self.assertEqual(napcat.bot.call_api.call_args.kwargs["group_id"], 30003)
        self.assertNotIn("user_id", napcat.bot.call_api.call_args.kwargs)
        napcat.bot.call_api.return_value = {}
        service._build_text_message = AsyncMock(return_value=Message("群内最终回复"))
        result = await service._store_and_request_reply(event, "群聊 30003")
        self.assertEqual(result.extract_plain_text(), "群内最终回复")
        service._build_text_message.assert_awaited_once_with("群内最终回复", event)

    async def test_group_host_card_receipt_and_fallback_stay_in_group(self):
        napcat = Mock()
        napcat.send_group_images = AsyncMock(side_effect=RuntimeError("image failed"))
        napcat.send_private_images = AsyncMock()
        napcat.send_text_message = AsyncMock(return_value={"message_id": "group-text", "api": "send_group_msg"})
        client = WebSocketClient("ws://test.invalid")
        client.send = AsyncMock(return_value=True)
        api = MonCoreAPI(client)
        with patch("src.plugins.BotCore.app.napcat_api", napcat):
            await api._handle_send_message_host({"data": {
                "request_id": "group-final", "target_type": "group", "target_qq_number": "30003",
                "content": "最终结果", "images_base64": [PNG]}})
        napcat.send_group_images.assert_awaited_once_with("30003", [PNG])
        napcat.send_private_images.assert_not_awaited()
        napcat.send_text_message.assert_awaited_once_with(target_type="group", target_id="30003", content="最终结果")
        self.assertEqual(client.send.call_args.args[0]["data"]["message_id"], "group-text")

    async def test_actual_image_api_missing_message_id_falls_back_to_final_text(self):
        napcat = NapCatAPI()
        napcat.bot = Mock()
        napcat.bot.call_api = AsyncMock()
        backend = Mock()
        backend.store_message = AsyncMock(return_value=True)
        backend.request_reply = AsyncMock(return_value={
            "content": "应保留的最终回复", "images_base64": [PNG],
        })
        service = BaseMessageService(Mock(), Mock(), napcat)
        service._ensure_moncore_api = AsyncMock(return_value=backend)
        event = PrivateMessageEvent.model_construct(user_id=20002)

        for api_result in (None, {}, {"message_id": ""}, {"message_id": True}, {"message_id": {"id": 42}}):
            with self.subTest(api_result=api_result):
                napcat.bot.call_api.reset_mock()
                napcat.bot.call_api.return_value = api_result
                message = await service._store_and_request_reply(event, "测试私聊")

                self.assertIsInstance(message, Message)
                self.assertEqual(message.extract_plain_text(), "应保留的最终回复")
                napcat.bot.call_api.assert_awaited_once()

    async def test_same_contact_progress_image_precedes_final_text(self):
        napcat = NapCatAPI()
        napcat.bot = Mock()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def send(_action, **parameters):
            calls.append(parameters)
            if len(calls) == 1:
                started.set()
                await release.wait()
            return {"message_id": len(calls)}

        napcat.bot.call_api = AsyncMock(side_effect=send)
        progress = asyncio.create_task(napcat.send_private_images("20002", [PNG]))
        final = None
        try:
            await asyncio.wait_for(started.wait(), 0.2)
            final = asyncio.create_task(napcat.send_text_message("user", "20002", "最终回复"))
            await asyncio.sleep(0)
            self.assertEqual(napcat.bot.call_api.await_count, 1)
            self.assertFalse(final.done())
        finally:
            release.set()
            tasks = [progress] + ([final] if final is not None else [])
            await asyncio.wait_for(asyncio.gather(*tasks), 0.5)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["message"][0].type, "image")
        self.assertEqual(calls[1]["message"].extract_plain_text(), "最终回复")


if __name__ == "__main__":
    unittest.main()
