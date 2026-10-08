"""Durable delivery of bounded notices; QQ actions are never retried here."""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from src.System.Logs import get_logger
from .device_credential_store import DeviceCredentialStore


logger = get_logger(__name__)


class EventOutbox:
    RETENTION_SECONDS = 7 * 86400
    MAX_PENDING = 4096
    MAX_BODY_BYTES = 32 * 1024

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DeviceCredentialStore().state_dir / "napcat-events.sqlite3"

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS napcat_event_outbox (
                event_id TEXT PRIMARY KEY, self_id TEXT NOT NULL, body TEXT NOT NULL,
                created REAL NOT NULL, next_attempt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'pending')""")
            connection.execute("CREATE INDEX IF NOT EXISTS napcat_event_due ON napcat_event_outbox (self_id,state,next_attempt)")
            self.path.chmod(0o600)
            return connection
        except Exception:
            connection.close()
            raise

    def enqueue(self, payload: dict, now: float | None = None) -> str:
        stamp = time.time() if now is None else now
        account = str(payload.get("self_id") or "")
        if not account.isdigit() or int(account) <= 0:
            raise ValueError("QQ 事件缺少有效账号")
        # Event normalization runs before this boundary. Never persist credentials.
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(body.encode("utf-8")) > self.MAX_BODY_BYTES:
            raise ValueError("QQ 事件内容过大")
        event_id = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM napcat_event_outbox WHERE created < ?", (stamp - self.RETENTION_SECONDS,))
            if connection.execute("SELECT 1 FROM napcat_event_outbox WHERE event_id=?", (event_id,)).fetchone():
                return event_id
            count = connection.execute("SELECT COUNT(*) FROM napcat_event_outbox WHERE self_id=? AND state='pending'", (account,)).fetchone()[0]
            if count >= self.MAX_PENDING:
                raise ValueError("QQ 事件待发队列已满")
            connection.execute("INSERT INTO napcat_event_outbox (event_id,self_id,body,created,next_attempt) VALUES (?,?,?,?,?)",
                               (event_id, account, body, stamp, stamp))
            # Retain recent ACK identities to suppress duplicate adapter deliveries.
            connection.execute("""DELETE FROM napcat_event_outbox WHERE self_id=? AND state!='pending'
                AND event_id NOT IN (SELECT event_id FROM napcat_event_outbox WHERE self_id=? AND state!='pending'
                ORDER BY created DESC LIMIT 4096)""", (account, account))
        return event_id

    def claim_batch(self, account: str, limit: int = 16, now: float | None = None) -> list[dict]:
        stamp = time.time() if now is None else now
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM napcat_event_outbox WHERE created < ?", (stamp - self.RETENTION_SECONDS,))
            rows = connection.execute("""SELECT event_id,body,attempts FROM napcat_event_outbox
                WHERE self_id=? AND state='pending' AND next_attempt<=? ORDER BY created,event_id LIMIT ?""",
                (str(account), stamp, max(1, min(limit, 16)))).fetchall()
            for row in rows:
                delay = min(300, 15 * 2 ** min(row["attempts"], 5))
                connection.execute("UPDATE napcat_event_outbox SET attempts=attempts+1,next_attempt=? WHERE event_id=?",
                                   (stamp + delay, row["event_id"]))
        return [{"event_id": row["event_id"], "event": json.loads(row["body"])} for row in rows]

    def acknowledge(self, event_id: str, account: str, status: str) -> bool:
        if status not in {"stored", "duplicate", "rejected"}:
            return False
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("UPDATE napcat_event_outbox SET state=? WHERE event_id=? AND self_id=? AND state='pending'",
                                        ("rejected" if status == "rejected" else "acknowledged", event_id, str(account)))
            return cursor.rowcount > 0

    def resume(self, account: str):
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE napcat_event_outbox SET next_attempt=0 WHERE self_id=? AND state='pending'", (str(account),))


class NapCatEventRelay:
    SEND_TIMEOUT = 10.0

    def __init__(self, outbox: EventOutbox | None = None):
        self.outbox = outbox or EventOutbox()
        self._task = None
        self._wake = asyncio.Event()
        self._resume_key = None
        self._resume_at = 0.0

    async def enqueue(self, payload: dict) -> bool:
        try:
            await asyncio.to_thread(self.outbox.enqueue, payload)
        except Exception:
            logger.error("QQ 事件未能写入待发队列，请检查状态目录空间与权限")
            return False
        self.start()
        self._wake.set()
        return True

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def resume(self, account: str):
        await asyncio.to_thread(self.outbox.resume, account)
        self.start()
        self._wake.set()

    async def acknowledge(self, data: dict, sub_command: str):
        status = data.get("status")
        if status in {"stored", "duplicate"} and sub_command != "success":
            return
        account = str(data.get("self_id") or "")
        event_id = data.get("event_id")
        if not account.isdigit() or not isinstance(event_id, str) or len(event_id) != 64:
            return
        changed = await asyncio.to_thread(self.outbox.acknowledge, event_id, account, status)
        if changed and status == "rejected":
            logger.warning("Core 拒绝一条 QQ 事件，已停止重发: event_id=%s code=%s", event_id, str(data.get("code") or "")[:80])

    async def flush_once(self, api, account: str) -> int:
        # The account and socket must both remain the ones this batch was claimed for.
        if getattr(api, "_napcat_event_account", None) != str(account):
            return 0
        client = api.ws_client
        socket = getattr(client, "websocket", None)
        entries = await asyncio.to_thread(self.outbox.claim_batch, account)
        sent = 0
        for entry in entries:
            try:
                delivered = await asyncio.wait_for(
                    self._send_bound(api, account, client, socket, "napcatEvent", entry), timeout=self.SEND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("QQ 事件发送超时，事件保留在队列等待确认或重试")
                break
            if not delivered:
                break
            sent += 1
        return sent

    @staticmethod
    async def _send_bound(api, account, client, socket, command, data):
        if (api.ws_client is not client or getattr(client, "websocket", None) is not socket
                or getattr(api, "_napcat_event_account", None) != str(account)):
            return False
        # Credentials can rotate during a reconnect or while SQLite is busy.
        auth = api._qzone_device_auth()
        return await client.send({"command": command, "data": {**data, **auth}})

    async def resume_backend(self, api, account):
        """Resume already committed Core work even when no new QQ event arrives."""
        client = api.ws_client
        socket = getattr(client, "websocket", None)
        key = (client, socket, str(account))
        now = time.monotonic()
        if self._resume_key == key and now - self._resume_at < 60:
            return
        sent = await asyncio.wait_for(
            self._send_bound(api, account, client, socket, "napcatEventResume", {}), timeout=self.SEND_TIMEOUT)
        if sent:
            self._resume_key, self._resume_at = key, now

    async def _run(self):
        while True:
            self._wake.clear()
            try:
                from ...app import connection_manager, get_moncore_api, is_moncore_ready
                api = get_moncore_api()
                if api and is_moncore_ready():
                    await self.resume_backend(api, str(connection_manager.qq_number))
                    await self.flush_once(api, str(connection_manager.qq_number))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("QQ 事件同步暂不可用，已保留队列等待恢复")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


_relay = None


def get_event_relay() -> NapCatEventRelay:
    global _relay
    if _relay is None:
        _relay = NapCatEventRelay()
    return _relay
