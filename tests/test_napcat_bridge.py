"""NapCat expansion must preserve request identity and existing chat delivery."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.client.ws.websocket import WebSocketClient
from src.plugins.BotCore.core.router.napcat_events import normalize_napcat_event
from src.plugins.BotCore.core.router import napcat_commands
from src.plugins.BotCore.core.router.local_policy import is_allowed_by_local_policy
from src.plugins.BotCore.core.business.message.napcat_input import enrich_input, send_input_feedback, summarize_forward


def event_for(message):
    return PrivateMessageEvent.model_construct(self_id=10001, user_id=20002, message_id=77,
                                              message=message, original_message=message, to_me=False)


class NapCatBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = WebSocketClient("ws://test.invalid")
        self.client.send = AsyncMock(return_value=True)
        self.api = MonCoreAPI(self.client)
        self.auth = patch.object(self.api, "_qzone_device_auth", return_value={"device_id": "device", "device_credential": "credential"})
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.napcat = SimpleNamespace(bot=SimpleNamespace(self_id="10001"), execute_action=AsyncMock(return_value={"message_id": 101}))
        self.bot_patch = patch("src.plugins.BotCore.app.napcat_api", self.napcat)
        self.bot_patch.start()
        self.addCleanup(self.bot_patch.stop)

    def host_message(self, request_id="op-1", action="friend_poke", params=None, expected="10001"):
        return {"command": "napcatActionHost", "subCommand": "execute", "data": {
            "request_id": request_id, "action": action, "params": params or {"user_id": "20002"}, "expected_bot_id": expected}}

    async def finish_hosts(self):
        tasks = [item[1] for item in self.api._napcat_action_tasks.values()]
        if tasks:
            await asyncio.gather(*tasks)

    async def test_host_dispatch_is_nonblocking_and_duplicate_never_reexecutes(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(*args):
            started.set()
            await release.wait()
            return None

        self.napcat.execute_action.side_effect = slow
        message = self.host_message()
        await self.client._handle_message(message)
        await asyncio.wait_for(started.wait(), 1.0)
        await self.client._handle_message(message)
        self.assertEqual(self.napcat.execute_action.await_count, 1)
        release.set()
        await self.finish_hosts()
        await self.client._handle_message(message)
        self.assertEqual(self.napcat.execute_action.await_count, 1)
        receipt = self.client.send.call_args.args[0]
        self.assertEqual(receipt["data"]["status"], "success")
        self.assertIsNone(receipt["data"]["result"])
        self.assertIn("device_credential", receipt["data"])

    async def test_reused_request_id_with_different_payload_is_rejected(self):
        await self.api._schedule_napcat_action_host(self.host_message())
        await self.finish_hosts()
        await self.api._schedule_napcat_action_host(self.host_message(params={"user_id": "30003"}))
        self.assertEqual(self.napcat.execute_action.await_count, 1)
        self.assertEqual(self.client.send.call_args.args[0]["data"]["code"], "REQUEST_CONFLICT")

    async def test_wrong_bot_never_executes(self):
        await self.api._schedule_napcat_action_host(self.host_message(expected="99999"))
        await self.finish_hosts()
        self.napcat.execute_action.assert_not_awaited()
        self.assertEqual(self.client.send.call_args.args[0]["data"]["code"], "BOT_MISMATCH")

    async def test_uncertain_result_is_cached_without_retry(self):
        self.napcat.execute_action.side_effect = asyncio.TimeoutError()
        await self.api._schedule_napcat_action_host(self.host_message())
        await self.finish_hosts()
        await self.api._schedule_napcat_action_host(self.host_message())
        self.assertEqual(self.napcat.execute_action.await_count, 1)
        self.assertEqual(self.client.send.call_args.args[0]["data"]["status"], "unknown")


class NapCatInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_and_forward_expanded_once_for_store_and_chat(self):
        message = Message([MessageSegment.record("ignored"), MessageSegment("forward", {"id": "forward-a"})])
        event = event_for(message)
        fake = SimpleNamespace(bot=object(), execute_action=AsyncMock(side_effect=[
            {"text": "帮我总结一下"}, {"messages": [{"sender": {"nickname": "朋友"}, "content": [{"type": "text", "data": {"text": "会议明天开"}}]}]}]))
        with patch("src.plugins.BotCore.app.napcat_api", fake):
            first = await MonCoreAPI._extract_event_content(event)
            second = await MonCoreAPI._extract_event_content(event)
        self.assertEqual(first, second)
        self.assertEqual(fake.execute_action.await_count, 2)
        self.assertIn("帮我总结一下", first)
        self.assertIn("会议明天开", first)
        self.assertIn("以下为引用内容", first)
        metadata = await MonCoreAPI._build_event_metadata(event, first)
        self.assertEqual(metadata["forward_ids"], ["forward-a"])

    async def test_large_forward_preserves_user_prompt_and_agent_content_limit(self):
        message = Message([MessageSegment("forward", {"id": "f"}), MessageSegment.text("请概括以上内容")])
        event = event_for(message)
        event.__dict__["_mon_napcat_input"] = {"voice": "", "forwards": {"f": "长文本" * 3000}}
        content = await MonCoreAPI._extract_event_content(event)
        self.assertLessEqual(len(content), 4000)
        self.assertIn("请概括以上内容", content)
        self.assertIn("内容已截断", content)

    async def test_enrichment_and_typing_failure_do_not_drop_original_message(self):
        message = Message([MessageSegment.record("ignored"), MessageSegment.text("请回答")])
        event = event_for(message)
        fake = SimpleNamespace(bot=object(), execute_action=AsyncMock(side_effect=RuntimeError("offline")))
        with patch("src.plugins.BotCore.app.napcat_api", fake):
            await send_input_feedback(event)
            content = await MonCoreAPI._extract_event_content(event)
        self.assertIn("请回答", content)
        self.assertIn("暂未能转写", content)

    def test_forward_flattening_does_not_expand_nested_forward_or_raw_objects(self):
        value = summarize_forward({"messages": [{"content": [{"type": "forward", "data": {"id": "x"}}]},
                                               {"content": {"secret": "hidden"}}]})
        self.assertIn("嵌套转发", value)
        self.assertNotIn("hidden", value)


class NapCatEventTests(unittest.TestCase):
    def test_media_identifiers_only_come_from_original_segments_and_are_bounded(self):
        message = Message([MessageSegment.text('file_id=fake record=secret'),
                           MessageSegment("record", {"file": "record-1", "url": "https://ignored"}),
                           MessageSegment("image", {"file": "image-1"}),
                           MessageSegment("video", {"file_id": "video-1", "url": "https://ignored"}),
                           MessageSegment("file", {"file_id": "file-1", "file": "filename.txt"})])
        self.assertEqual(MonCoreAPI._extract_resource_ids(message),
                         {"record": ["record-1"], "image": ["image-1"], "video": ["video-1"], "file": ["file-1"]})
        segments = Message([MessageSegment("record", {"file": str(i)}) for i in range(12)])
        self.assertEqual(len(MonCoreAPI._extract_resource_ids(segments)["record"]), 8)
        self.assertEqual(MonCoreAPI._extract_resource_ids(Message([MessageSegment("record", {"file": "x" * 1025})])), {})

    def test_lifecycle_is_recorded_without_unbounded_heartbeat(self):
        event = {"post_type": "meta_event", "meta_event_type": "lifecycle", "sub_type": "connect", "self_id": 1}
        self.assertEqual(normalize_napcat_event(event), event)
        self.assertIsNone(normalize_napcat_event({**event, "meta_event_type": "heartbeat"}))

    def test_request_normalization_retains_identity_but_excludes_secrets(self):
        result = normalize_napcat_event({"post_type": "request", "request_type": "friend", "self_id": 1,
                                        "user_id": 2, "time": 10, "flag": "flag-a", "token": "secret"})
        self.assertEqual(result["flag"], "flag-a")
        self.assertNotIn("token", result)
        self.assertIsNone(normalize_napcat_event({"post_type": "message"}))
        self.assertIsNone(normalize_napcat_event({"post_type": "request", "request_type": "friend", "flag": "x" * 2049}))

    def test_notice_file_metadata_does_not_forward_paths_or_urls(self):
        result = normalize_napcat_event({"post_type": "notice", "notice_type": "group_upload", "group_id": 4,
                                        "file": {"id": "f", "name": "a.txt", "path": "C:/private", "url": "http://secret"}})
        self.assertEqual(result["file"], {"id": "f", "name": "a.txt"})

    def test_management_commands_use_the_single_ingress(self):
        from src.plugins.BotCore.core.router import commands
        self.assertIs(napcat_commands.command_matcher, commands.command_matcher)
        self.assertIs(napcat_commands.handle_command, commands.handle_command)
