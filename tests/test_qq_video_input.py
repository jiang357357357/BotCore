"""Incoming video normalization, bounded local bytes, and request-bound uploads."""
import base64
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.napcat.api.api import NapCatAPI
from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.api.qq_file_upload import upload_input_files
from src.plugins.BotCore.external.monCore.api.group_file_input import prepare_group_file_input

MP4 = b"\x00\x00\x00\x18ftypisom" + b"testvideo"


class QqVideoInputTests(unittest.IsolatedAsyncioTestCase):
    def test_video_segments_become_file_inputs_and_do_not_use_segment_urls(self):
        message = Message([MessageSegment("video", {"file": "received-video", "url": "https://invalid.example/private"})])
        files = MonCoreAPI._extract_file_metadata(message)
        self.assertEqual(files, [{"file_id": "received-video", "filename": "qq-video-1.mp4", "file_size": 0, "media_kind": "video"}])

    async def test_reads_mp4_and_webm_from_napcat_cache_with_unknown_or_exact_sizes(self):
        api = NapCatAPI()
        with tempfile.TemporaryDirectory(prefix="qq-video-") as temporary:
            filename = Path(temporary) / "cache"
            api.bot = SimpleNamespace(call_api=AsyncMock(return_value={"file": str(filename)}))
            for blob, extension in [(MP4, "mp4"), (b"\x1a\x45\xdf\xa3webm", "webm")]:
                filename.write_bytes(blob)
                self.assertEqual(await api.read_received_video("received", 0), (blob, extension))
                self.assertEqual(await api.read_received_video("received", len(blob)), (blob, extension))
                with self.assertRaises(ValueError):
                    await api.read_received_video("received", len(blob) + 1)
            filename.write_bytes(b"not video")
            with self.assertRaises(ValueError):
                await api.read_received_video("received", 0)
            api.bot.call_api.reset_mock()
            with self.assertRaises(ValueError):
                await api.read_received_video("received", 100_000_001)
            api.bot.call_api.assert_not_awaited()

    async def test_private_video_upload_carries_exact_bytes_and_is_bound_to_the_current_request(self):
        api = SimpleNamespace(read_received_video=AsyncMock(return_value=(MP4, "mp4")))
        core = SimpleNamespace(ws_client=SimpleNamespace(send=AsyncMock(return_value=True)))
        metadata = {"onebot_message_id": "50", "files": [{"file_id": "received", "filename": "qq-video-1.mp4", "file_size": 0, "media_kind": "video"}]}
        with patch.dict(sys.modules, {"src.plugins.BotCore.app": SimpleNamespace(napcat_api=api)}):
            await upload_input_files(core, metadata, "request-video", "200001", False)
        api.read_received_video.assert_awaited_once_with("received", 0)
        packets = [call.args[0]["data"] for call in core.ws_client.send.await_args_list]
        self.assertEqual(packets[0]["request_id"], "request-video")
        self.assertEqual(packets[0]["message_id"], "50")
        self.assertEqual(packets[0]["filename"], "qq-video-1.mp4")
        self.assertEqual(packets[0]["size"], len(MP4))
        self.assertEqual(base64.b64decode(packets[1]["content_base64"]), MP4)
        self.assertEqual(len(metadata["file_upload_ids"]), 1)

    async def test_group_video_requires_same_group_quote_explicit_mention_and_approved_group(self):
        event = GroupMessageEvent.model_construct(self_id=100001, group_id=300001, user_id=200002, message_id=501)
        metadata = {"onebot_message_id": "501", "sender_user_id": "200002", "mentions": [{"qq": "100001"}],
                    "reply_to": {"message_id": "500", "content": "[视频]", "files": [{"media_kind": "video"}]}}
        source = {"message_id": 500, "message_type": "group", "group_id": 300001,
                  "message": [{"type": "video", "data": {"file": "received-video"}}]}
        api = SimpleNamespace(bot=SimpleNamespace(call_api=AsyncMock(return_value=source)),
                              read_received_video=AsyncMock(return_value=(MP4, "mp4")))
        core = SimpleNamespace(check_access=AsyncMock(return_value={"approved": True}), ws_client=SimpleNamespace(send=AsyncMock(return_value=True)))
        with patch.dict(sys.modules, {"src.plugins.BotCore.app": SimpleNamespace(napcat_api=api)}):
            await prepare_group_file_input(core, event, metadata)
            await upload_input_files(core, metadata, "group-video", "300001", True)
        api.read_received_video.assert_awaited_once_with("received-video", 0)
        head = core.ws_client.send.await_args_list[0].args[0]["data"]
        self.assertEqual(head["quoted_message_id"], "500")
        self.assertEqual(head["sender_user_id"], "200002")
        self.assertTrue(head["is_group"])
        core.check_access.assert_awaited_once_with(event, capability="group_agent")
