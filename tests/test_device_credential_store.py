import os
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


class DeviceCredentialStoreTests(unittest.TestCase):
    def test_device_identity_is_stable_and_secret_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DeviceCredentialStore(Path(temporary))
            device_id = store.device_id()
            self.assertEqual(store.device_id(), device_id)

            store.save_device_credential("credential-value")
            self.assertEqual(store.device_credential(), "credential-value")
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


if __name__ == "__main__":
    unittest.main()
