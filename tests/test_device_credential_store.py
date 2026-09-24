import os
import asyncio
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/plugins/BotCore/external/monCore/device_credential_store.py"
)
SPEC = importlib.util.spec_from_file_location("device_credential_store", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
DeviceCredentialStore = MODULE.DeviceCredentialStore

WEBSOCKET_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/plugins/BotCore/external/monCore/client/ws/websocket.py"
)
WEBSOCKET_SPEC = importlib.util.spec_from_file_location("moncore_websocket", WEBSOCKET_PATH)
WEBSOCKET_MODULE = importlib.util.module_from_spec(WEBSOCKET_SPEC)
assert WEBSOCKET_SPEC and WEBSOCKET_SPEC.loader
WEBSOCKET_SPEC.loader.exec_module(WEBSOCKET_MODULE)
WebSocketClient = WEBSOCKET_MODULE.WebSocketClient

CALLBACK_HANDLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/plugins/BotCore/external/monCore/client/ws/callback_handler.py"
)
CALLBACK_HANDLER_SPEC = importlib.util.spec_from_file_location(
    "moncore_callback_handler", CALLBACK_HANDLER_PATH
)
CALLBACK_HANDLER_MODULE = importlib.util.module_from_spec(CALLBACK_HANDLER_SPEC)
assert CALLBACK_HANDLER_SPEC and CALLBACK_HANDLER_SPEC.loader
CALLBACK_HANDLER_SPEC.loader.exec_module(CALLBACK_HANDLER_MODULE)
ConnectionCallbackHandler = CALLBACK_HANDLER_MODULE.ConnectionCallbackHandler


class DeviceCredentialStoreTests(unittest.TestCase):
    def test_device_identity_is_stable_and_secret_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DeviceCredentialStore(Path(temporary))
            device_id = store.device_id()
            self.assertEqual(store.device_id(), device_id)

            store.save_device_credential("credential-value")
            self.assertEqual(store.device_credential(), "credential-value")
            if os.name != "nt":
                mode = stat.S_IMODE(store.device_path.stat().st_mode)
                self.assertEqual(mode, 0o600)

    def test_pairing_token_prefers_environment_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DeviceCredentialStore(Path(temporary))
            store.save_pairing_token("file-token")
            self.assertEqual(store.pairing_token(), "file-token")
            with patch.dict(os.environ, {"MON_QQBOT_PAIRING_TOKEN": "env-token"}):
                self.assertEqual(store.pairing_token(), "env-token")
            store.clear_pairing_token()
            self.assertIsNone(store.pairing_token())


class WebSocketDeviceCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_sends_device_identity_and_credential(self):
        class Socket:
            def __init__(self):
                self.payload = None

            async def send(self, payload):
                self.payload = json.loads(payload)

        client = WebSocketClient("ws://example.invalid")
        socket = Socket()
        client.websocket = socket
        client.is_connected = True

        sent = await client.register(
            "123456789",
            device_id="device-a",
            device_credential="credential-a",
        )

        self.assertTrue(sent)
        self.assertEqual(socket.payload["data"]["device_id"], "device-a")
        self.assertEqual(
            socket.payload["data"]["device_credential"],
            "credential-a",
        )


class RegistrationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_error_finishes_wait_immediately_with_server_reason(self):
        class Manager:
            def __init__(self):
                self.registration_error = None
                self.finished = asyncio.Event()
                self.is_registered = False

            def reject_registration(self, reason):
                self.registration_error = reason
                self.finished.set()

        manager = Manager()
        handler = ConnectionCallbackHandler(manager)

        await handler.handle_register_response(
            {
                "command": "register",
                "subCommand": "error",
                "data": {"message": "首次注册需要绑定令牌，后续连接需要设备凭证"},
            }
        )

        self.assertTrue(manager.finished.is_set())
        self.assertFalse(manager.is_registered)
        self.assertEqual(
            manager.registration_error,
            "首次注册需要绑定令牌，后续连接需要设备凭证",
        )

    async def test_success_marks_registered_and_finishes_wait(self):
        class Socket:
            async def disconnect(self):
                return None

        class Manager:
            def __init__(self):
                self.registration_error = None
                self.finished = asyncio.Event()
                self.is_registered = False
                self.ws_client = Socket()

            def accept_device_credential(self, credential):
                self.credential = credential

            async def _connect_bot_channel(self, bot_url):
                self.bot_url = bot_url
                return True

            def finish_registration(self):
                self.finished.set()

        manager = Manager()
        handler = ConnectionCallbackHandler(manager)

        await handler.handle_register_response(
            {
                "command": "register",
                "subCommand": "success",
                "data": {
                    "bot_id": "123456789",
                    "bot_url": "ws://127.0.0.1:40011/ws/qq_devices/bot/123456789/",
                    "device_credential": "credential-value",
                },
            }
        )

        self.assertTrue(manager.is_registered)
        self.assertTrue(manager.finished.is_set())
        self.assertEqual(manager.credential, "credential-value")


if __name__ == "__main__":
    unittest.main()
