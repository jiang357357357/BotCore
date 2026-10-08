"""Offline notices survive restarts and only matching durable ACKs retire them."""

import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.external.monCore.event_outbox import EventOutbox, NapCatEventRelay


class EventOutboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "outbox.sqlite3"
        self.outbox = EventOutbox(self.path)

    def payload(self, account=10001, timestamp=100):
        return {"self_id": account, "time": timestamp, "post_type": "notice", "notice_type": "friend_add", "user_id": 20002}

    def test_restart_and_cross_account_ack_are_isolated(self):
        event_id = self.outbox.enqueue(self.payload(), now=100)
        restored = EventOutbox(self.path)
        self.assertEqual(restored.claim_batch("99999", now=100), [])
        self.assertFalse(restored.acknowledge(event_id, "99999", "stored"))
        self.assertEqual(restored.claim_batch("10001", now=100)[0]["event_id"], event_id)
        self.assertEqual(restored.claim_batch("10001", now=101), [])
        restored.resume("10001")
        self.assertEqual(len(restored.claim_batch("10001", now=101)), 1)
        self.assertTrue(restored.acknowledge(event_id, "10001", "stored"))
        self.assertEqual(restored.claim_batch("10001", now=1000), [])
        self.assertEqual(restored.enqueue(self.payload(), now=1001), event_id)
        self.assertEqual(restored.claim_batch("10001", now=1001), [])

    def test_retry_retains_event_but_rejected_stops_delivery(self):
        event_id = self.outbox.enqueue(self.payload(), now=100)
        self.assertFalse(self.outbox.acknowledge(event_id, "10001", "retry"))
        self.assertEqual(len(self.outbox.claim_batch("10001", now=100)), 1)
        self.assertEqual(len(self.outbox.claim_batch("10001", now=115)), 1)
        self.assertEqual(self.outbox.claim_batch("10001", now=116), [])
        self.assertTrue(self.outbox.acknowledge(event_id, "10001", "rejected"))
        self.assertEqual(self.outbox.claim_batch("10001", now=1000), [])

    def test_full_queue_never_silently_discards_pending(self):
        self.outbox.MAX_PENDING = 1
        event_id = self.outbox.enqueue(self.payload(), now=100)
        with self.assertRaisesRegex(ValueError, "队列已满"):
            self.outbox.enqueue(self.payload(timestamp=101), now=101)
        self.assertEqual(self.outbox.claim_batch("10001", now=102)[0]["event_id"], event_id)

    def test_retention_and_payload_limits(self):
        self.outbox.enqueue(self.payload(), now=100)
        self.assertEqual(self.outbox.claim_batch("10001", now=100 + EventOutbox.RETENTION_SECONDS + 1), [])
        with self.assertRaises(ValueError):
            self.outbox.enqueue({**self.payload(), "comment": "x" * 40000})
        with self.assertRaises(ValueError):
            self.outbox.enqueue({**self.payload(), "self_id": "../other"})


class EventRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.outbox = EventOutbox(Path(self.directory.name) / "outbox.sqlite3")
        self.relay = NapCatEventRelay(self.outbox)
        self.addAsyncCleanup(self.relay.stop)
        self.payload = {"self_id": 10001, "time": 100, "post_type": "request", "request_type": "friend", "flag": "request-1"}

    async def test_lost_ack_resends_same_identity_and_fresh_auth_after_restart(self):
        event_id = self.outbox.enqueue(self.payload)
        api = SimpleNamespace(_qzone_device_auth=Mock(return_value={"device_id": "device-1", "device_credential": "first"}),
                              ws_client=SimpleNamespace(send=AsyncMock(return_value=True)), _napcat_event_account="10001")
        self.assertEqual(await self.relay.flush_once(api, "10001"), 1)
        sent = api.ws_client.send.call_args.args[0]
        self.assertEqual(sent["data"]["event_id"], event_id)
        await self.relay.acknowledge({"event_id": event_id, "self_id": "10001", "status": "stored"}, "error")
        self.outbox.resume("10001")
        api._qzone_device_auth.return_value["device_credential"] = "second"
        restored = NapCatEventRelay(EventOutbox(self.outbox.path))
        self.assertEqual(await restored.flush_once(api, "10001"), 1)
        self.assertEqual(api.ws_client.send.call_args.args[0]["data"]["device_credential"], "second")
        await restored.acknowledge({"event_id": event_id, "self_id": "10001", "status": "duplicate"}, "success")
        self.assertEqual(await restored.flush_once(api, "10001"), 0)
        self.assertNotIn(b"device_credential", self.outbox.path.read_bytes())

    async def test_backpressure_and_socket_switch_leave_claims_pending(self):
        self.outbox.enqueue(self.payload)
        blocked = asyncio.Event()
        api = SimpleNamespace(_qzone_device_auth=Mock(return_value={"device_id": "d", "device_credential": "c"}),
                              ws_client=SimpleNamespace(send=AsyncMock(side_effect=blocked.wait)), _napcat_event_account="10001")
        async def stalled(_message):
            return await blocked.wait()
        api.ws_client.send.side_effect = stalled
        with patch.object(self.relay, "SEND_TIMEOUT", 0.01):
            self.assertEqual(await self.relay.flush_once(api, "10001"), 0)
        self.outbox.resume("10001")
        original_claim = self.outbox.claim_batch
        def switch(*args):
            result = original_claim(*args)
            api._napcat_event_account = "20002"
            return result
        api.ws_client.send.reset_mock()
        with patch.object(self.outbox, "claim_batch", side_effect=switch):
            self.assertEqual(await self.relay.flush_once(api, "10001"), 0)
        api.ws_client.send.assert_not_awaited()
        self.outbox.resume("10001")
        self.assertEqual(len(self.outbox.claim_batch("10001")), 1)

    async def test_credentials_are_refreshed_after_claim_and_for_each_delivery(self):
        self.outbox.enqueue(self.payload)
        self.outbox.enqueue({**self.payload, "flag": "request-2"})
        generation = [0]
        api = SimpleNamespace(_qzone_device_auth=lambda: {"device_credential": str(generation[0])},
                              _napcat_event_account="10001", ws_client=SimpleNamespace())
        original_claim = self.outbox.claim_batch
        def rotate(*args):
            result = original_claim(*args)
            generation[0] += 1
            return result
        async def send(_message):
            generation[0] += 1
            return True
        api.ws_client.send = AsyncMock(side_effect=send)
        with patch.object(self.outbox, "claim_batch", side_effect=rotate):
            self.assertEqual(await self.relay.flush_once(api, "10001"), 2)
        self.assertEqual([call.args[0]["data"]["device_credential"] for call in api.ws_client.send.call_args_list], ["1", "2"])

    async def test_empty_outbox_still_resumes_committed_core_jobs_after_reconnect(self):
        api = SimpleNamespace(_qzone_device_auth=lambda: {"device_id": "d", "device_credential": "c"},
                              _napcat_event_account="10001", ws_client=SimpleNamespace(send=AsyncMock(return_value=True)))
        await self.relay.resume_backend(api, "10001")
        await self.relay.resume_backend(api, "10001")
        api.ws_client.send.assert_awaited_once()
        self.assertEqual(api.ws_client.send.call_args.args[0]["command"], "napcatEventResume")
        self.assertNotIn("event", api.ws_client.send.call_args.args[0]["data"])
        api.ws_client.websocket = object()
        await self.relay.resume_backend(api, "10001")
        self.assertEqual(api.ws_client.send.await_count, 2)
        self.relay._resume_at -= 61
        await self.relay.resume_backend(api, "10001")
        self.assertEqual(api.ws_client.send.await_count, 3)

    async def test_offline_adapter_event_is_persisted_before_any_connection(self):
        from src.plugins.BotCore.core.router.napcat_events import _report
        event = SimpleNamespace(self_id=10001, model_dump=lambda **kwargs: self.payload)
        with patch("src.plugins.BotCore.app.connection_manager", SimpleNamespace(qq_number="10001")), \
                patch("src.plugins.BotCore.external.monCore.event_outbox.get_event_relay", return_value=self.relay), \
                patch.object(self.relay, "start"):
            await _report(event)
        self.assertEqual(self.outbox.claim_batch("10001")[0]["event"], self.payload)
