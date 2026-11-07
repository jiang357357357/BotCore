"""Real loopback WebSocket integration with Core's isolated ASGI fixture."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock

import nonebot
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI
from src.plugins.BotCore.external.monCore.client.ws.websocket import WebSocketClient


CORE = Path(__file__).resolve().parents[4] / "Core"
PYTHON = CORE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
FIXTURE = CORE / "Application/Domain/BOT/tests/serve_connection_fixture.py"


@unittest.skipUnless(PYTHON.is_file() and FIXTURE.is_file(), "requires the EDEN Core virtual environment")
class CoreConnectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_transport_replacement_preserves_new_reply_and_stops_old_reconnect(self):
        clients, requests = [], []
        with tempfile.TemporaryDirectory(prefix="qqbot-ownership-network-") as temporary:
            directory = Path(temporary)
            metadata = directory / "fixture.json"
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                           "MON_LOG_ROOT": str(directory / "logs"), "MON_LOG_START_DIR": str(directory / "logs/core")}
            with (directory / "core.log").open("w", encoding="utf-8") as output:
                process = subprocess.Popen([str(PYTHON), str(FIXTURE), str(metadata)], cwd=directory,
                    env=environment, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    async def ready():
                        while not metadata.is_file():
                            if process.poll() is not None:
                                self.fail("Fixture Core failed: " + (directory / "core.log").read_text(encoding="utf-8"))
                            await asyncio.sleep(0.02)
                    await asyncio.wait_for(ready(), 15)
                    info = json.loads(metadata.read_text(encoding="utf-8"))

                    async def connect():
                        client = WebSocketClient(f"ws://127.0.0.1:{info['port']}/ws/{info['bot_id']}/",
                            device_id=info["device_id"], device_credential=info["credential"])
                        api = MonCoreAPI(client)
                        clients.append(client)
                        self.assertTrue(await client.connect())
                        client.is_registered = True
                        async def confirmed():
                            while not client.qq_command_protocol:
                                await asyncio.sleep(0.01)
                        await asyncio.wait_for(confirmed(), 5)
                        return client, api

                    def event(text):
                        message = Message(text)
                        return PrivateMessageEvent.model_construct(self_id=int(info["bot_id"]), user_id=20002,
                            message_id=77, message=message, original_message=message, to_me=False)

                    old, old_api = await connect()
                    waiting = asyncio.create_task(old_api.request_reply(event("slow"), timeout=0.1, need_voice=False))
                    requests.append(waiting)
                    async def processing():
                        while not old_api.pending_chat_modes or not all(f.done() for f in old_api.pending_chat_modes.values()):
                            if waiting.done():
                                self.fail("Chat settled before processing acknowledgement: " + repr(waiting.result()) +
                                          "; fixture log: " + (directory / "core.log").read_text(encoding="utf-8")[-4000:])
                            await asyncio.sleep(0.01)
                    try:
                        await asyncio.wait_for(processing(), 5)
                    except TimeoutError:
                        self.fail("Processing acknowledgement missing; pending=" + repr(old_api.pending_chat_modes) +
                                  "; request stack=" + repr([(frame.f_code.co_name, frame.f_lineno) for frame in waiting.get_stack()]) +
                                  "; fixture log: " + (directory / "core.log").read_text(encoding="utf-8")[-4000:])
                    current, api = await connect()
                    result = await asyncio.wait_for(waiting, 5)
                    self.assertEqual(result["error_code"], "CONNECTION_REPLACED")
                    self.assertFalse(old.enable_reconnect)
                    self.assertTrue(old.superseded)
                    self.assertFalse(old_api.pending_requests)
                    callback = AsyncMock()
                    api.reply_callbacks.append(callback)
                    reply = await asyncio.wait_for(api.request_reply(event("current"), timeout=0.1, need_voice=False), 5)
                    self.assertEqual(reply["content"], "fixture reply")
                    await asyncio.sleep(0.35)
                    callback.assert_not_awaited()
                    self.assertTrue(current.is_connected)
                    self.assertFalse(api.pending_requests)
                finally:
                    try:
                        for request in requests:
                            if not request.done():
                                request.cancel()
                        await asyncio.gather(*requests, return_exceptions=True)
                        for client in clients:
                            await client.disconnect(disable_reconnect=True)
                            if client._receive_task:
                                await asyncio.wait_for(client._receive_task, 2)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            await asyncio.to_thread(process.wait, 5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            await asyncio.to_thread(process.wait, 5)
