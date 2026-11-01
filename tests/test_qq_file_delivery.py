"""Exercise 100 MB private files with local cache copies and mocked OneBot calls."""

import base64
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.napcat.api.api import NapCatAPI
from src.plugins.BotCore.external.napcat.file_limits import MAX_FILE_BYTES, FILE_TRANSFER_TIMEOUT_SECONDS
from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.client.ws.websocket import WebSocketClient


class QqFileDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def file_api(self):
        client = WebSocketClient("ws://fixture.invalid/ws")
        client.send = AsyncMock(return_value=True)
        api = MonCoreAPI(client)
        api._send_message_host_ack = AsyncMock()
        return api

    async def test_received_cache_file_accepts_100_mb_and_rejects_oversize_metadata_before_api(self):
        with tempfile.TemporaryDirectory(prefix="qq-file-cache-") as temporary:
            file = Path(temporary) / "received.bin"
            with file.open("wb") as stream:
                stream.truncate(MAX_FILE_BYTES)
            api = NapCatAPI()
            api.bot = SimpleNamespace(call_api=AsyncMock(return_value={"file": str(file)}))
            content = await api.read_private_file("received-file", MAX_FILE_BYTES)
            self.assertEqual(len(content), 100_000_000)
            api.bot.call_api.assert_awaited_once_with("get_file", file_id="received-file", _timeout=FILE_TRANSFER_TIMEOUT_SECONDS)
            api.bot.call_api.reset_mock()
            with self.assertRaisesRegex(ValueError, "100 MB"):
                await api.read_private_file("received-file", MAX_FILE_BYTES + 1)
            api.bot.call_api.assert_not_awaited()
            with self.assertRaisesRegex(ValueError, "消息记录"):
                await api.read_private_file("received-file", MAX_FILE_BYTES - 1)

    async def test_100_mb_upload_uses_exact_temporary_bytes_and_cleans_up_after_ack(self):
        content = b"a" * MAX_FILE_BYTES
        temporary_paths = []

        async def onebot(action, **fields):
            self.assertEqual(action, "upload_private_file")
            self.assertEqual(fields["_timeout"], FILE_TRANSFER_TIMEOUT_SECONDS)
            file = Path(fields["file"])
            temporary_paths.append(file)
            self.assertEqual(file.stat().st_size, MAX_FILE_BYTES)
            with file.open("rb") as stream:
                self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), hashlib.sha256(content).hexdigest())
            return {"file_id": "fixture-only"}

        api = NapCatAPI()
        api.bot = SimpleNamespace(call_api=AsyncMock(side_effect=onebot))
        result = await api.send_private_file("123456789", "large.bin", content)
        self.assertEqual(result["file_id"], "fixture-only")
        self.assertTrue(temporary_paths and all(not path.exists() for path in temporary_paths))
        api.bot.call_api.reset_mock()
        with self.assertRaises(ValueError):
            await api.send_private_file("123456789", "oversize.bin", b"a" * (MAX_FILE_BYTES + 1))
        api.bot.call_api.assert_not_awaited()

    async def test_bounded_chunks_reconstruct_100_mb_before_private_file_ack(self):
        api = self.file_api()
        content = b"a" * MAX_FILE_BYTES
        napcat = SimpleNamespace(send_private_file=AsyncMock(return_value={"file_id": "fixture-only"}))
        context = {"request_id": "qq_file_fixture", "target_type": "user", "target_qq_number": "123456789"}
        await api._handle_file_send({**context, "file_phase": "start", "filename": "large.bin",
                                    "size": MAX_FILE_BYTES, "sha256": hashlib.sha256(content).hexdigest()})
        self.assertIn(context["request_id"], api.pending_file_sends)
        for offset in range(0, len(content), 96 * 1024):
            await api._handle_file_send({**context, "file_phase": "chunk",
                "content_base64": base64.b64encode(content[offset:offset + 96 * 1024]).decode("ascii")})
        with patch.dict(sys.modules, {"src.plugins.BotCore.app": SimpleNamespace(napcat_api=napcat)}):
            await api._handle_file_send({**context, "file_phase": "finish"})
        napcat.send_private_file.assert_awaited_once_with("123456789", "large.bin", content)
        self.assertNotIn(context["request_id"], api.pending_file_sends)
        self.assertEqual(api._send_message_host_ack.call_args.args[0], "success")
        await api._handle_file_send({**context, "file_phase": "start", "filename": "oversize.bin",
                                    "size": MAX_FILE_BYTES + 1, "sha256": "0" * 64})
        self.assertNotIn(context["request_id"], api.pending_file_sends)

    async def test_corrupt_chunks_never_reach_napcat(self):
        api = self.file_api()
        context = {"request_id": "qq_file_corrupt", "target_type": "user", "target_qq_number": "123456789"}
        await api._handle_file_send({**context, "file_phase": "start", "filename": "small.bin", "size": 1, "sha256": "0" * 64})
        await api._handle_file_send({**context, "file_phase": "chunk", "content_base64": "YQ=="})
        await api._handle_file_send({**context, "file_phase": "finish"})
        self.assertEqual(api._send_message_host_ack.call_args.args[0], "error")

    async def test_group_file_transport_preserves_target_type_and_uses_group_upload(self):
        api = self.file_api()
        content = b"group file"
        napcat = SimpleNamespace(send_group_file=AsyncMock(return_value={"file_id": "group-fixture"}))
        context = {"request_id": "qq_file_group", "target_type": "group", "target_qq_number": "333333"}
        await api._handle_file_send({**context, "file_phase": "start", "filename": "group.txt",
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        # Equal numeric targets in different conversation types must not share an upload.
        await api._handle_file_send({**context, "target_type": "user", "file_phase": "finish"})
        napcat.send_group_file.assert_not_awaited()
        self.assertEqual(api._send_message_host_ack.call_args.args[0], "error")
        await api._handle_file_send({**context, "file_phase": "start", "filename": "group.txt",
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        await api._handle_file_send({**context, "file_phase": "chunk", "content_base64": base64.b64encode(content).decode()})
        with patch.dict(sys.modules, {"src.plugins.BotCore.app": SimpleNamespace(napcat_api=napcat)}):
            await api._handle_file_send({**context, "file_phase": "finish"})
        napcat.send_group_file.assert_awaited_once_with("333333", "group.txt", content)
        self.assertEqual(api._send_message_host_ack.call_args.args[1]["api"], "upload_group_file")

    async def test_group_napcat_upload_sends_exact_bytes_and_removes_temporary_file(self):
        api = NapCatAPI()
        temporary = []

        async def upload(action, **fields):
            self.assertEqual(action, "upload_group_file")
            self.assertEqual(fields["group_id"], "333333")
            temporary.append(Path(fields["file"]))
            self.assertEqual(temporary[-1].read_bytes(), b"group bytes")
            return {"file_id": "group-file"}

        api.bot = SimpleNamespace(call_api=AsyncMock(side_effect=upload))
        self.assertEqual((await api.send_group_file("333333", "group.txt", b"group bytes"))["file_id"], "group-file")
        self.assertTrue(temporary and all(not path.exists() for path in temporary))
