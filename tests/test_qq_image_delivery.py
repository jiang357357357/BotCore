"""OneBot image delivery accepts only bounded PNG cards."""

import base64
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

_api_path = Path(__file__).resolve().parents[1] / "src/plugins/BotCore/external/napcat/api/api.py"
_api_spec = importlib.util.spec_from_file_location("qq_image_napcat_api", _api_path)
_api_module = importlib.util.module_from_spec(_api_spec)
_api_spec.loader.exec_module(_api_module)
NapCatAPI = _api_module.NapCatAPI
from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI


PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9cN7sAAAAASUVORK5CYII="
)).decode("ascii")


class QQImageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_image_segment_to_private_contact(self):
        api = NapCatAPI()
        api.bot = AsyncMock()
        api.bot.call_api.return_value = {"message_id": 42}

        result = await api.send_private_images("2740954024", [PNG])

        self.assertEqual(result["message_id"], "42")
        args, kwargs = api.bot.call_api.await_args
        self.assertEqual(args, ("send_private_msg",))
        self.assertEqual(kwargs["user_id"], 2740954024)
        self.assertEqual(kwargs["message"][0].type, "image")

    async def test_rejects_invalid_image_before_sending(self):
        api = NapCatAPI()
        api.bot = AsyncMock()

        with self.assertRaises(ValueError):
            await api.send_private_images("2740954024", [base64.b64encode(b"not a PNG").decode()])

        api.bot.call_api.assert_not_awaited()

    async def test_progress_card_ack_keeps_image_message_id_for_quoted_approval(self):
        handler = MonCoreAPI.__new__(MonCoreAPI)
        handler._send_message_host_ack = AsyncMock()
        napcat = unittest.mock.Mock()
        napcat.send_private_images = AsyncMock(return_value={"message_id": "42"})
        napcat.send_text_message = AsyncMock()
        with patch("src.plugins.BotCore.app.napcat_api", napcat):
            await handler._handle_send_message_host({"data": {
                "request_id": "approval-1", "target_type": "user", "target_qq_number": "2740954024",
                "content": "同意或拒绝", "images_base64": [PNG],
            }})
        napcat.send_text_message.assert_not_awaited()
        self.assertEqual(handler._send_message_host_ack.await_args.args[0], "success")
        self.assertEqual(handler._send_message_host_ack.await_args.args[1]["message_id"], "42")

    async def test_progress_card_falls_back_to_text_on_image_failure(self):
        handler = MonCoreAPI.__new__(MonCoreAPI)
        handler._send_message_host_ack = AsyncMock()
        napcat = unittest.mock.Mock()
        napcat.send_private_images = AsyncMock(side_effect=RuntimeError("image unavailable"))
        napcat.send_text_message = AsyncMock(return_value={"message_id": "43", "api": "send_private_msg"})
        with patch("src.plugins.BotCore.app.napcat_api", napcat):
            await handler._handle_send_message_host({"data": {
                "request_id": "approval-2", "target_type": "user", "target_qq_number": "2740954024",
                "content": "同意或拒绝", "images_base64": [PNG],
            }})
        self.assertEqual(handler._send_message_host_ack.await_args.args[1]["message_id"], "43")
        napcat.send_text_message.assert_awaited_once()
