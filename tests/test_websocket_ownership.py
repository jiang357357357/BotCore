"""Credentials follow reconnects; displaced runtimes cannot reclaim or reply twice."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.client.ws.websocket import WebSocketClient
from src.plugins.BotCore.external.monCore.connection_manager import ConnectionManager


class WebsocketOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_device_credentials_are_headers_and_reused_on_reconnect(self):
        client = WebSocketClient("ws://127.0.0.1:1/ws/bot/", device_id="device", device_credential="credential")
        socket = Mock(close=AsyncMock())
        client._receive_messages = AsyncMock()
        with patch("src.plugins.BotCore.external.monCore.client.ws.websocket.connect", new_callable=AsyncMock,
                   return_value=socket) as connect:
            self.assertTrue(await client.connect())
            await client._receive_task
            self.assertTrue(await client.connect())
            await client._receive_task
            for call in connect.call_args_list:
                self.assertEqual(call.kwargs["extra_headers"], {
                    "X-Mon-Device-ID": "device", "X-Mon-Device-Credential": "credential",
                })
            self.assertNotIn("credential", connect.call_args.args[0])

    async def test_replaced_connection_stops_recovery_and_settles_pending_reply_without_sending(self):
        client = WebSocketClient("ws://fixture.invalid/ws")
        api = MonCoreAPI(client)
        future = asyncio.get_running_loop().create_future()
        api.pending_requests["pending"] = future
        client.websocket = Mock(recv=AsyncMock(side_effect=ConnectionClosedError(Close(4409, "replaced"), None)))
        client.is_connected = client.is_registered = True
        client._start_reconnect = AsyncMock()
        client.send = AsyncMock()

        await client._receive_messages()

        self.assertTrue(client.superseded)
        self.assertFalse(client.enable_reconnect)
        self.assertFalse(client.is_registered)
        client._start_reconnect.assert_not_awaited()
        self.assertEqual(future.result()["error_code"], "CONNECTION_REPLACED")
        self.assertEqual(future.result()["content"], "")
        client.send.assert_not_awaited()
        with patch("src.plugins.BotCore.external.monCore.client.ws.websocket.connect", new_callable=AsyncMock) as connect:
            self.assertFalse(await client.connect())
            connect.assert_not_awaited()

    async def test_normal_disconnect_preserves_automatic_reconnect_and_pending_request(self):
        client = WebSocketClient("ws://fixture.invalid/ws")
        api = MonCoreAPI(client)
        future = asyncio.get_running_loop().create_future()
        api.pending_requests["pending"] = future
        client.websocket = Mock(recv=AsyncMock(side_effect=ConnectionClosedError(Close(1012, "restart"), None)))
        client.is_connected = client.is_registered = True
        client._start_reconnect = AsyncMock()
        await client._receive_messages()
        client._start_reconnect.assert_awaited_once()
        self.assertFalse(client.superseded)
        self.assertFalse(future.done())
        future.cancel()

    async def test_late_callback_from_replaced_client_cannot_settle_new_client_requests(self):
        old = WebSocketClient("ws://fixture.invalid/old")
        api = MonCoreAPI(old)
        callback = old.disconnect_callbacks[-1]
        current = WebSocketClient("ws://fixture.invalid/current")
        api.ws_client = current
        api.register_ws_handlers()
        api.register_ws_handlers()
        future = asyncio.get_running_loop().create_future()
        api.pending_requests["new-client-request"] = future
        await callback(True)
        self.assertFalse(future.done())
        self.assertNotIn(callback, old.disconnect_callbacks)
        self.assertEqual(len(current.disconnect_callbacks), 1)
        future.cancel()

    async def test_reconnect_already_sleeping_cannot_reclaim_after_replacement(self):
        client = WebSocketClient("ws://fixture.invalid/ws")
        client.connect = AsyncMock(return_value=True)

        async def displaced_while_sleeping(_):
            client.superseded = True
            client.enable_reconnect = False

        with patch("src.plugins.BotCore.external.monCore.client.ws.websocket.asyncio.sleep", displaced_while_sleeping):
            await client._reconnect_loop()
        client.connect.assert_not_awaited()

    async def test_manager_passes_stored_identity_and_blocks_recovery_for_displaced_instance(self):
        manager = ConnectionManager(qq_number="10001")
        manager.credential_store.save_device_credential("fixture-credential")
        fake = WebSocketClient("ws://fixture.invalid/ws")
        fake.connect = AsyncMock(return_value=True)
        fake.is_connected = True
        with patch("src.plugins.BotCore.external.monCore.connection_manager.WebSocketClient", return_value=fake) as factory:
            self.assertTrue(await manager._connect_bot_channel("ws://fixture.invalid/ws"))
        self.assertEqual(factory.call_args.kwargs["device_credential"], "fixture-credential")
        self.assertEqual(factory.call_args.kwargs["device_id"], manager.credential_store.device_id())
        fake.superseded = True
        await manager._on_channel_disconnected(True)
        manager.schedule_registration_recovery()
        self.assertIsNone(manager._recovery_task)
        self.assertFalse(manager.is_registered)
        with patch.object(manager, "discover_server", new_callable=AsyncMock) as discover:
            self.assertFalse(await manager.start())
            discover.assert_not_awaited()
