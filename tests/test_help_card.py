"""QQ help card requests keep their reply paired with the requesting command."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI


class HelpCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_result_matches_its_request_id(self):
        client = Mock()
        sent = asyncio.Event()

        async def send(_message):
            sent.set()
            return True

        client.send = AsyncMock(side_effect=send)
        api = MonCoreAPI(client)

        waiting = asyncio.create_task(api.render_help_card("/帮助", timeout=1))
        await sent.wait()
        request_id = client.send.await_args.args[0]["data"]["request_id"]
        await api._handle_render_card({
            "subCommand": "success",
            "data": {"request_id": "other-request", "images_base64": ["wrong"]},
        })
        self.assertFalse(waiting.done())

        await api._handle_render_card({
            "subCommand": "success",
            "data": {"request_id": request_id, "images_base64": ["image-page"]},
        })
        self.assertEqual(await waiting, ["image-page"])
        self.assertFalse(api.pending_card_requests)

    async def test_render_failure_returns_text_fallback_signal(self):
        client = Mock()
        client.send = AsyncMock(side_effect=ConnectionError("offline"))
        api = MonCoreAPI(client)

        self.assertEqual(await api.render_help_card("/帮助"), [])
        self.assertFalse(api.pending_card_requests)


if __name__ == "__main__":
    unittest.main()
