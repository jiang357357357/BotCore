"""Message classification and request-bound retry without a live QQ account."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment, PrivateMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.api.qq_file_upload import upload_input_files


def event_for(message):
    return PrivateMessageEvent.model_construct(self_id=10001, user_id=20002, message_id=77,
                                              message=message, original_message=message, to_me=False)


class QqFileInputRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_only_message_is_identified_and_keeps_original_metadata(self):
        event = event_for(Message([MessageSegment("file", {"file_id": "fixture-id", "name": "test.txt", "file_size": 4})]))
        content = await MonCoreAPI._extract_event_content(event)
        metadata = await MonCoreAPI._build_event_metadata(event, content)
        self.assertIn("test.txt", content)
        self.assertIn("4", content)
        self.assertFalse(metadata["has_text"])
        self.assertEqual(metadata["empty_kind"], "file")
        self.assertEqual(metadata["files"][0]["file_id"], "fixture-id")
        self.assertEqual(metadata["files"][0]["filename"], "test.txt")

    async def test_empty_and_reply_only_are_distinguished(self):
        for message, kind in ((Message(), "blank"), (Message([MessageSegment.reply(5)]), "reply_only")):
            event = event_for(message)
            content = await MonCoreAPI._extract_event_content(event)
            metadata = await MonCoreAPI._build_event_metadata(event, content)
            self.assertEqual(metadata["empty_kind"], kind)
            self.assertNotIn("files", metadata)

    async def test_transient_file_read_is_retried_once_before_any_upload(self):
        reader = AsyncMock(side_effect=[TimeoutError("fixture timeout"), b"data"])
        napcat = SimpleNamespace(read_private_file=reader)
        core = SimpleNamespace(ws_client=SimpleNamespace(send=AsyncMock(return_value=True)))
        metadata = {"onebot_message_id": "77", "files": [{"file_id": "id", "filename": "test.txt", "file_size": 4}]}
        with patch.dict(sys.modules, {"src.plugins.BotCore.app": SimpleNamespace(napcat_api=napcat)}), \
             patch("src.plugins.BotCore.external.monCore.api.qq_file_upload.asyncio.sleep", new=AsyncMock()):
            await upload_input_files(core, metadata, "request", "20002", False)
        self.assertEqual(reader.await_count, 2)
        self.assertEqual(core.ws_client.send.await_count, 3)
        self.assertEqual(len(metadata["file_upload_ids"]), 1)
        self.assertNotIn("file_upload_error", metadata)

    async def test_non_transient_failure_is_reported_without_retry_or_upload(self):
        reader = AsyncMock(side_effect=ValueError("fixture invalid file"))
        napcat = SimpleNamespace(read_private_file=reader)
        core = SimpleNamespace(ws_client=SimpleNamespace(send=AsyncMock(return_value=True)))
        metadata = {"onebot_message_id": "77", "files": [{"file_id": "id", "filename": "test.txt", "file_size": 4}]}
        with patch.dict(sys.modules, {"src.plugins.BotCore.app": SimpleNamespace(napcat_api=napcat)}):
            await upload_input_files(core, metadata, "request", "20002", False)
        reader.assert_awaited_once()
        core.ws_client.send.assert_not_awaited()
        self.assertIn("fixture invalid file", metadata["file_upload_error"])

    def test_video_string_size_is_normalized(self):
        message = Message([MessageSegment("video", {"file": "id", "file_size": "25"})])
        self.assertEqual(MonCoreAPI._extract_file_metadata(message)[0]["file_size"], 25)
