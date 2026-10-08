"""Qzone publishing must preserve authorization, delivery certainty and request identity."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.exception import ActionFailed, NetworkError
from nonebot.exception import FinishedException

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.napcat.api.api import NapCatAPI, QzonePublishError
from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.core.router import commands


class NapCatQzonePublishTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = NapCatAPI()
        self.api.bot = Mock(self_id="10001")
        self.api.bot.call_api = AsyncMock(return_value={"tid": "qzone-post-1"})

    async def test_default_publish_is_friends_only_and_returns_confirmed_tid(self):
        result = await self.api.publish_qzone("  测试动态  ")

        self.api.bot.call_api.assert_awaited_once_with(
            "send_qzone_msg", content="测试动态", images=[], ugc_right=4, _timeout=60.0
        )
        self.assertEqual(result["tid"], "qzone-post-1")
        self.assertEqual(result["api"], "send_qzone_msg")

    async def test_invalid_parameters_never_reach_napcat(self):
        cases = (
            {"content": "  "},
            {"content": "x" * 2001},
            {"content": "text", "images": ["https://example.test/image.png"] * 10},
            {"content": "text", "images": ["file:///C:/private.png"]},
            {"content": "text", "images": ["data:image/png;base64,AAAA"]},
            {"content": "text", "images": ["https://"]},
            {"content": "text", "images": ["https://example.test/image\n.png"]},
            {"content": "text", "images": ["https://example.test/image\u2003.png"]},
            {"content": "text", "ugc_right": 2},
            {"content": "text", "ugc_right": True},
        )
        for parameters in cases:
            with self.subTest(parameters=parameters):
                with self.assertRaises((ValueError, QzonePublishError)):
                    await self.api.publish_qzone(**parameters)
        self.api.bot.call_api.assert_not_awaited()

    async def test_timeouts_report_unknown_without_retrying(self):
        for error in (asyncio.TimeoutError(), NetworkError("API request timeout")):
            with self.subTest(error=type(error).__name__):
                self.api.bot.call_api.reset_mock()
                self.api.bot.call_api.side_effect = error
                with self.assertRaises(QzonePublishError) as caught:
                    await self.api.publish_qzone("测试动态")
                self.assertEqual(caught.exception.status, "unknown")
                self.api.bot.call_api.assert_awaited_once()

    async def test_missing_tid_cannot_be_reported_as_success(self):
        self.api.bot.call_api.return_value = {"status": "ok"}

        with self.assertRaises(QzonePublishError):
            await self.api.publish_qzone("测试动态")
        self.api.bot.call_api.assert_awaited_once()

    async def test_upstream_action_failure_without_tid_is_unknown_not_retried(self):
        self.api.bot.call_api.side_effect = ActionFailed(
            status="failed", retcode=1200, message="Publish response does not contain tid"
        )

        with self.assertRaises(QzonePublishError) as caught:
            await self.api.publish_qzone("测试动态")
        self.assertEqual(caught.exception.status, "unknown")
        self.api.bot.call_api.assert_awaited_once()

    async def test_unsupported_action_identifies_required_napcat_version(self):
        self.api.bot.call_api.side_effect = ActionFailed(
            status="failed", retcode=1404, message="Unsupported action: send_qzone_msg"
        )

        with self.assertRaises(QzonePublishError) as caught:
            await self.api.publish_qzone("测试动态")
        self.assertEqual(caught.exception.status, "failed")
        self.assertIn("4.18.14", str(caught.exception))
        self.api.bot.call_api.assert_awaited_once()


class MonCoreQzonePublishTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Mock()
        self.client.send = AsyncMock(return_value=True)
        self.api = MonCoreAPI(self.client)
        self.credential_store = Mock()
        self.credential_store.device_id.return_value = "test-device"
        self.credential_store.device_credential.return_value = "test-credential"
        self.manager = SimpleNamespace(credential_store=self.credential_store)
        self.napcat = Mock(bot=Mock(self_id="10001"))
        self.napcat.publish_qzone = AsyncMock(
            return_value={"tid": "qzone-post-1", "api": "send_qzone_msg"}
        )
        self.app_patch = patch.multiple(
            "src.plugins.BotCore.app", connection_manager=self.manager, napcat_api=self.napcat
        )
        self.app_patch.start()
        self.addCleanup(self.app_patch.stop)

    @staticmethod
    def private_event():
        message = Message("测试动态")
        return PrivateMessageEvent.model_construct(
            self_id=10001,
            user_id=20002,
            message_id=77,
            message=message,
            original_message=message,
        )

    @staticmethod
    def host_message(request_id="host-request-1", expected_bot_id="10001"):
        return {
            "command": "qzonePublishHost",
            "subCommand": "publish",
            "data": {
                "request_id": request_id,
                "expected_bot_id": expected_bot_id,
                "content": "测试动态",
                "images": [],
                "ugc_right": 4,
            },
        }


    async def test_host_account_mismatch_does_not_publish(self):
        await self.api._handle_qzone_publish_host(
            self.host_message(expected_bot_id="99999")
        )

        self.napcat.publish_qzone.assert_not_awaited()
        acknowledgement = self.client.send.await_args.args[0]
        self.assertEqual(acknowledgement["command"], "qzonePublishBot")
        self.assertEqual(acknowledgement["data"]["request_id"], "host-request-1")
        self.assertEqual(acknowledgement["data"]["status"], "failed")

    async def test_duplicate_host_requests_publish_only_once_and_replay_same_tid(self):
        async def publish(*_args, **_kwargs):
            await asyncio.sleep(0)
            return {"tid": "qzone-post-1", "api": "send_qzone_msg"}

        self.napcat.publish_qzone.side_effect = publish
        message = self.host_message()
        await asyncio.gather(
            self.api._handle_qzone_publish_host(message),
            self.api._handle_qzone_publish_host(message),
        )
        await self.api._handle_qzone_publish_host(message)

        self.napcat.publish_qzone.assert_awaited_once()
        acknowledgements = [call.args[0] for call in self.client.send.await_args_list]
        self.assertEqual(len(acknowledgements), 3)
        for acknowledgement in acknowledgements:
            self.assertEqual(acknowledgement["command"], "qzonePublishBot")
            self.assertEqual(acknowledgement["data"]["request_id"], "host-request-1")
            self.assertEqual(acknowledgement["data"]["tid"], "qzone-post-1")
            self.assertEqual(acknowledgement["data"]["device_credential"], "test-credential")

    async def test_slow_publish_keeps_other_handlers_live_and_rejects_new_work(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def publish(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {"tid": "qzone-post-1", "api": "send_qzone_msg"}

        self.napcat.publish_qzone.side_effect = publish
        handlers = dict(call.args for call in self.client.register_handler.call_args_list)
        message = self.host_message()
        try:
            # A slow upstream request must not occupy the shared receive loop.
            await asyncio.wait_for(handlers["qzonePublishHost"](message), 0.25)
            await asyncio.wait_for(started.wait(), 0.25)
            unrelated = asyncio.get_running_loop().create_future()
            self.api.pending_requests["other-command"] = unrelated
            await asyncio.wait_for(handlers["error"]({
                "data": {"request_id": "other-command", "user_message": "模型未配置"},
            }), 0.25)
            self.assertEqual(unrelated.result()["content"], "模型未配置")

            # The in-flight duplicate is ignored; another request is rejected, not queued.
            await handlers["qzonePublishHost"](message)
            await handlers["qzonePublishHost"](self.host_message(request_id="other-publish"))
            self.napcat.publish_qzone.assert_awaited_once()
            self.client.send.assert_awaited_once()
            rejected = self.client.send.await_args.args[0]["data"]
            self.assertEqual(rejected["request_id"], "other-publish")
            self.assertEqual(rejected["status"], "failed")
            self.assertEqual(rejected["code"], "PUBLISH_BUSY")
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*self.api._qzone_publish_tasks.values()), 1)

        self.napcat.publish_qzone.assert_awaited_once()
        self.assertFalse(self.api._qzone_publish_tasks)
        self.assertEqual(self.client.send.await_count, 2)
        published = self.client.send.await_args.args[0]["data"]
        self.assertEqual(published["request_id"], "host-request-1")
        self.assertEqual(published["tid"], "qzone-post-1")


if __name__ == "__main__":
    unittest.main()
