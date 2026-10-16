"""Reply + explicit @ group-file workflow with local fake OneBot and Core calls."""

import base64
import copy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.group_file_input import prepare_group_file_input
from src.plugins.BotCore.external.monCore.api.qq_file_upload import upload_input_files
from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.client.ws.websocket import WebSocketClient


class GroupFileInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.event = GroupMessageEvent.model_construct(self_id=100001, group_id=300001, user_id=200002, message_id=501)
        self.metadata = {"onebot_message_id": "501", "sender_user_id": "200002", "has_text": False,
            "mentions": [{"qq": "100001"}], "reply_to": {"message_id": "500"}, "group_agent_trigger": True}
        self.source = {"message_id": 500, "message_type": "group", "group_id": 300001,
            "message": [{"type": "file", "data": {"file_id": "received-group-file", "file": "note.txt", "file_size": "4"}}]}
        self.napcat = SimpleNamespace(bot=SimpleNamespace(call_api=AsyncMock(return_value=self.source)),
            read_received_file=AsyncMock(return_value=b"test"), read_private_file=AsyncMock(return_value=b"test"))
        self.core = SimpleNamespace(check_access=AsyncMock(return_value={"approved": True}),
                                    ws_client=SimpleNamespace(send=AsyncMock(return_value=True)))
        self.app = patch.dict(sys.modules, {"src.plugins.BotCore.app": SimpleNamespace(napcat_api=self.napcat)})
        self.app.start()
        self.addCleanup(self.app.stop)

    async def test_quote_fetch_verifies_same_group_and_preflights_before_reading_file_bytes(self):
        await prepare_group_file_input(self.core, self.event, self.metadata)
        self.napcat.bot.call_api.assert_awaited_once_with("get_msg", message_id="500", _timeout=15)
        self.core.check_access.assert_awaited_once_with(self.event, capability="group_agent")
        self.assertEqual(self.metadata["group_file_reply"], {"group_id": "300001", "message_id": "500"})
        self.napcat.read_received_file.assert_not_awaited()
        await upload_input_files(self.core, self.metadata, "request", "300001", True)
        self.napcat.read_received_file.assert_awaited_once_with("received-group-file", 4)
        self.napcat.read_private_file.assert_not_awaited()
        packets = [call.args[0]["data"] for call in self.core.ws_client.send.call_args_list]
        self.assertTrue(packets[0]["is_group"])
        self.assertEqual(packets[0]["sender_user_id"], "200002")
        self.assertEqual(packets[0]["quoted_message_id"], "500")
        self.assertEqual(base64.b64decode(packets[1]["content_base64"]), b"test")
        self.assertEqual(len(self.metadata["file_upload_ids"]), 1)

    async def test_file_alone_or_quote_without_explicit_at_never_downloads(self):
        for changes in ({"mentions": []}, {"reply_to": {}}, {"mentions": [{"qq": "100002"}]}):
            metadata = {**self.metadata, **changes}
            await prepare_group_file_input(self.core, self.event, metadata)
            await upload_input_files(self.core, metadata, "request", "300001", True)
        self.napcat.bot.call_api.assert_not_awaited()
        self.napcat.read_received_file.assert_not_awaited()
        self.core.ws_client.send.assert_not_awaited()

    async def test_private_cross_group_and_wrong_message_quotes_are_rejected_before_download(self):
        for changes in ({"message_type": "private"}, {"group_id": 300002}, {"message_id": 499}):
            self.napcat.bot.call_api.return_value = {**self.source, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await prepare_group_file_input(self.core, self.event, copy.deepcopy(self.metadata))
        self.napcat.read_received_file.assert_not_awaited()
        self.core.check_access.assert_not_awaited()

    async def test_oversize_missing_file_id_bad_filename_and_too_many_files_are_rejected(self):
        file = self.source["message"][0]["data"]
        for changes in ({"file_size": "100000001"}, {"file_size": None}, {"file_id": ""}, {"file": "../secret"}):
            self.napcat.bot.call_api.return_value = {**self.source, "message": [{"type": "file", "data": {**file, **changes}}]}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await prepare_group_file_input(self.core, self.event, copy.deepcopy(self.metadata))
        self.napcat.bot.call_api.return_value = {**self.source, "message": self.source["message"] * 5}
        with self.assertRaises(ValueError):
            await prepare_group_file_input(self.core, self.event, copy.deepcopy(self.metadata))
        self.napcat.read_received_file.assert_not_awaited()

    async def test_exact_100_mb_is_admitted_as_metadata_without_downloading_until_upload(self):
        self.source["message"][0]["data"]["file_size"] = "100000000"
        await prepare_group_file_input(self.core, self.event, self.metadata)
        self.assertEqual(self.metadata["files"][0]["file_size"], 100_000_000)
        self.napcat.read_received_file.assert_not_awaited()

    async def test_revoked_group_mode_and_missing_source_message_do_not_download(self):
        self.core.check_access.return_value = {"approved": False}
        with self.assertRaises(PermissionError):
            await prepare_group_file_input(self.core, self.event, self.metadata)
        self.napcat.bot.call_api.side_effect = RuntimeError("source recalled")
        with self.assertRaises(RuntimeError):
            await prepare_group_file_input(self.core, self.event, self.metadata)
        self.napcat.read_received_file.assert_not_awaited()

    async def test_known_text_quote_is_normal_chat_and_private_upload_keeps_its_existing_reader(self):
        metadata = {**self.metadata, "reply_to": {"message_id": "500", "content": "a text reply"}}
        await prepare_group_file_input(self.core, self.event, metadata)
        self.napcat.bot.call_api.assert_not_awaited()
        metadata["files"] = [{"filename": "note.txt", "file_id": "private-file", "file_size": 4}]
        await upload_input_files(self.core, metadata, "private-request", "200002", False)
        self.napcat.read_private_file.assert_awaited_once_with("private-file", 4)
        self.napcat.read_received_file.assert_not_awaited()

    async def test_reply_and_at_without_extra_text_still_submits_a_file_turn(self):
        socket = WebSocketClient("ws://fixture.invalid/ws")
        socket.send = AsyncMock(return_value=True)
        socket.send_chat_request = AsyncMock(return_value=True)
        api = MonCoreAPI(socket)
        api._extract_event_content = AsyncMock(return_value="@机器人")
        api._build_event_metadata = AsyncMock(return_value=copy.deepcopy(self.metadata))
        api.check_access = AsyncMock(return_value={"approved": True})
        api._wait_for_chat_reply = AsyncMock(return_value={"content": "read the document"})
        self.assertEqual((await api.request_reply(self.event, need_voice=False))["content"], "read the document")
        sent = socket.send_chat_request.call_args.kwargs
        self.assertEqual(sent["content"], "请查看并概述我引用的文件。")
        self.assertTrue(sent["is_group"])
        self.assertEqual(sent["metadata"]["group_file_reply"]["message_id"], "500")
        self.assertEqual(len(sent["metadata"]["file_upload_ids"]), 1)
