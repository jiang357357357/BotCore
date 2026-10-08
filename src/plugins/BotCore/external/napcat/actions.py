"""Single-attempt execution of the explicitly supported NapCat action catalog."""

import asyncio
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time
from urllib.parse import urljoin
import uuid

from nonebot.adapters.onebot.v11.exception import ActionFailed

from .action_catalog import ACTION_CATALOG, ActionValidationError, URL, _value, normalize_action


class NapCatActionError(RuntimeError):
    def __init__(self, message: str, code: str, status: str = "failed"):
        super().__init__(message)
        self.code = code
        self.status = status


class NapCatActionsMixin:
    ACTION_TIMEOUT = 45.0
    STAGING_TIMEOUT = 45.0
    STAGING_RETENTION_SECONDS = 24 * 60 * 60
    MAX_STAGED_FILE_BYTES = 8 * 1024 * 1024
    SERIALIZED_SEND_ACTIONS = frozenset({
        "send_private_msg", "send_group_msg", "send_private_forward_msg", "send_group_forward_msg",
        "send_group_ai_record", "send_online_file", "send_flash_msg", "upload_private_file", "upload_group_file",
        "send_online_folder", "forward_friend_single_msg", "forward_group_single_msg",
    })

    async def execute_action(self, action: str, params: dict):
        """Validate and execute once. Never retry a mutation with an unknown result."""
        try:
            payload = normalize_action(action, params)
        except ActionValidationError as error:
            raise NapCatActionError(str(error), error.code) from error
        if not self.bot:
            raise NapCatActionError("NapCat 尚未连接", "BOT_OFFLINE")
        entry = ACTION_CATALOG[action]
        for field in entry.get("bridge_only_fields", []):
            payload.pop(field, None)
        for field, upstream_field in entry.get("parameter_aliases", {}).items():
            if field in payload: payload[upstream_field] = payload.pop(field)
        for field in entry.get("stringify_fields", []):
            if field in payload: payload[field] = str(payload[field])
        # URL staging happens before the mutation: failed downloads are known not sent.
        if entry.get("bridge"):
            try:
                payload = await asyncio.wait_for(self._stage_action_files(action, payload), timeout=self.STAGING_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise NapCatActionError("文件暂存失败或超时，动作未执行", "STAGING_FAILED") from error
        try:
            result = await asyncio.wait_for(self._dispatch_catalog_action(action, payload, entry), timeout=self.ACTION_TIMEOUT)
        except NapCatActionError:
            raise
        except ActionFailed as error:
            info = getattr(error, "info", {})
            info = info if isinstance(info, dict) else {}
            reason = str(info.get("message") or info.get("wording") or "NapCat 拒绝此动作")[:240]
            if info.get("retcode") in {1404, 404} or ("api" in reason.lower() and any(
                token in reason.lower() for token in ("不支持", "不存在", "not found", "unsupported", "not supported")
            )):
                raise NapCatActionError("当前 NapCat 不支持此动作，请核对版本及QQ客户端能力", "UNSUPPORTED_API") from error
            if info.get("retcode") in {1400, 400}:
                raise NapCatActionError("NapCat 参数校验失败：" + reason, "UPSTREAM_INVALID_PARAMS") from error
            status = "unknown" if entry["mutating"] else "failed"
            raise NapCatActionError("NapCat 动作未确认：" + reason, "UPSTREAM_ERROR", status) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status = "unknown" if entry["mutating"] else "failed"
            raise NapCatActionError("NapCat 调用超时或连接异常，请核实结果后再操作", "DELIVERY_UNKNOWN" if entry["mutating"] else "READ_FAILED", status) from error
        expected_id = "message_id" if action in {"send_private_msg", "send_group_msg", "send_private_forward_msg", "send_group_forward_msg", "send_contact_card", "send_miniapp_card"} else "msgId" if action in {"send_online_file", "send_online_folder"} else "tid" if action == "send_qzone_msg" else None
        if expected_id:
            message_id = result.get(expected_id) if isinstance(result, dict) else None
            if (isinstance(message_id, bool) or not isinstance(message_id, (str, int)) or not str(message_id).strip()
                    or (expected_id == "tid" and not isinstance(message_id, str))):
                raise NapCatActionError("NapCat 未返回发送消息ID，请核实结果，勿立即重复发送", "MISSING_RECEIPT", "unknown")
        if action == "create_flash_task" and (not isinstance(result, dict) or type(result.get("result")) is not int or result["result"] != 0):
            raise NapCatActionError("闪传任务创建结果未确认，请先查询再操作", "MISSING_RECEIPT", "unknown")
        if isinstance(result, dict) and type(result.get("result")) is int and result["result"] != 0 and "errMsg" in result:
            raise NapCatActionError("NapCat 返回失败状态：" + str(result.get("errMsg"))[:240], "UPSTREAM_RESULT", "unknown" if entry["mutating"] else "failed")
        if entry.get("confirmation_unknown"):
            raise NapCatActionError("NapCat 已响应清理请求，但上游不返回逐项清理结果，不能确认全部清理成功", "PARTIAL_RESULT_UNKNOWN", "unknown")
        if isinstance(result, dict) and entry.get("result_aliases"):
            result = dict(result)
            for field, public_field in entry["result_aliases"].items():
                if field in result: result[public_field] = result.pop(field)
        if isinstance(result, dict) and entry.get("bounded_media_result") and isinstance(result.get("base64"), str) and len(result["base64"]) > 384 * 1024:
            result = {key: value for key, value in result.items() if key != "base64"}
            result["base64_omitted"] = True
        if action == "get_friend_msg_history" and isinstance(result, dict) and isinstance(result.get("messages"), list):
            # v4.18.28 parses private history without target_id/peer_id. The
            # successful API query itself is scoped to this authorized user.
            # Add only missing destination metadata for the bot's own messages;
            # explicit conflicting destinations remain available for Core to reject.
            messages = []
            self_id = str(getattr(self.bot, "self_id", ""))
            for item in result["messages"]:
                if not isinstance(item, dict):
                    messages.append(item)
                    continue
                sender = item.get("sender")
                sender_id = item.get("user_id") or (sender.get("user_id") if isinstance(sender, dict) else None)
                if (item.get("message_type") == "private" and self_id
                        and str(sender_id) == self_id and item.get("target_id") in (None, "") and item.get("peer_id") in (None, "")):
                    item = {**item, "target_id": payload["user_id"]}
                messages.append(item)
            result = {**result, "messages": messages}
        # None, {}, [], false and 0 are legitimate data for upstream void APIs.
        try:
            json.dumps(result, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise NapCatActionError("NapCat 返回了非JSON结果", "INVALID_RESPONSE", "unknown" if entry["mutating"] else "failed") from error
        return result

    async def _dispatch_catalog_action(self, action, payload, entry):
        dispatch = entry.get("bridge_dispatch")
        if dispatch == "inline_avatar":
            return await self.bot.call_api("set_qq_avatar", file="base64://" + payload["image_base64"], _timeout=self.ACTION_TIMEOUT)
        if dispatch not in {"contact_card", "miniapp_card"}:
            return await self._call_catalog_action(action, payload, entry)
        # Read/generate phase has no sending side effect. Keep errors known failed
        # until the actual send starts; one overall timeout bounds both API calls.
        try:
            if dispatch == "contact_card":
                source_field = "group_id" if "contact_group_id" in payload else "user_id"
                generated = await self.bot.call_api("send_ark_share", **{source_field: payload["contact_" + source_field]}, _timeout=self.ACTION_TIMEOUT)
                if isinstance(generated, dict):
                    if type(generated.get("result")) is not int or generated["result"] != 0:
                        raise ValueError("Card generation failed")
                    ark = generated.get("arkJson" if source_field == "group_id" else "arkMsg")
                else:
                    raise ValueError("Missing card result")
            else:
                generated = await self.bot.call_api("get_mini_app_ark", **{key: value for key, value in payload.items() if not key.startswith("target_")}, _timeout=self.ACTION_TIMEOUT)
                ark = generated.get("data") if isinstance(generated, dict) else None
            if isinstance(ark, str):
                if len(ark) > 128 * 1024: raise ValueError("Oversized card")
                ark = json.loads(ark)
            if not isinstance(ark, dict) or not isinstance(ark.get("app"), str) or not ark["app"]:
                raise ValueError("Invalid generated card")
            serialized = json.dumps(ark, ensure_ascii=False, allow_nan=False)
            if len(serialized.encode("utf-8")) > 128 * 1024: raise ValueError("Oversized card")
        except asyncio.CancelledError:
            # Cancellation may be user initiated or the outer deadline. Propagate
            # it; no retry is performed by this adapter.
            raise
        except Exception as error:
            raise NapCatActionError("名片或小程序生成失败，消息未发送", "CARD_GENERATION_FAILED") from error
        target = "group" if "target_group_id" in payload else "user"
        field = target + "_id"
        upstream_action = "send_group_msg" if target == "group" else "send_private_msg"
        upstream_payload = {field: payload["target_" + field], "message": [{"type": "json", "data": {"data": serialized}}]}
        return await self._call_catalog_action(upstream_action, upstream_payload, ACTION_CATALOG[upstream_action])

    async def _call_catalog_action(self, action, payload, entry):
        # Cooperate with ordinary sends so progress/final cards do not compete
        # with newly exposed rich messages for the same QQ target.
        target = entry["target"]
        field = entry.get("target_field")
        if action == "send_flash_msg":
            target = "group" if "group_id" in payload else "user"
            field = target + "_id"
        if target in {"user", "group"} and action in self.SERIALIZED_SEND_ACTIONS and field in payload:
            locks = getattr(self, "_message_send_locks", None)
            if locks is None:
                locks = self._message_send_locks = {}
            lock = locks.setdefault((target, str(payload[field])), asyncio.Lock())
            async with lock:
                return await self.bot.call_api(action, **payload, _timeout=self.ACTION_TIMEOUT)
        return await self.bot.call_api(action, **payload, _timeout=self.ACTION_TIMEOUT)

    def _staging_root(self):
        root = Path(tempfile.gettempdir()) / "mon-napcat-action-files"
        if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
            raise RuntimeError("Unexpected staging directory link")
        root.mkdir(mode=0o700, exist_ok=True)
        return root.resolve()

    def _remove_staged_directory(self, directory):
        root = self._staging_root()
        directory = Path(directory)
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            return
        resolved = directory.resolve()
        # Explicitly verify every recursive deletion remains inside our owned root.
        if resolved.parent != root or not re.fullmatch(r"[0-9a-f]{32}", resolved.name):
            return
        if resolved.exists():
            shutil.rmtree(resolved)

    def _cleanup_expired_staging(self):
        now = time.time()
        for directory in self._staging_root().iterdir():
            if not directory.is_dir() or directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
                continue
            try:
                expires = float((directory / ".expires").read_text(encoding="ascii"))
            except (OSError, ValueError):
                continue
            if expires <= now:
                self._remove_staged_directory(directory)

    async def _expire_staging(self, directory):
        await asyncio.sleep(self.STAGING_RETENTION_SECONDS)
        self._remove_staged_directory(directory)

    async def _stage_action_files(self, action, payload):
        self._cleanup_expired_staging()
        directory = self._staging_root() / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
        (directory / ".expires").write_text(str(time.time() + self.STAGING_RETENTION_SECONDS), encoding="ascii")
        files = payload["files"] if action in {"create_flash_task", "send_online_folder"} else [{"url": payload["file_url"], "name": payload["file_name"]}]
        try:
            content_directory = directory
            if action == "send_online_folder":
                # The expiry marker is outside the folder shared with QQ.
                content_directory = directory / payload["folder_name"]
                content_directory.mkdir(mode=0o700)
            paths = []
            for item in files:
                if item["name"].casefold() == ".expires":
                    raise ValueError("Reserved staging filename")
                destination = content_directory / item["name"]
                if destination.resolve().parent != content_directory.resolve():
                    raise ValueError("Invalid staged filename")
                await self._download_action_file(item["url"], destination)
                paths.append(str(destination.resolve()))
        except BaseException:
            self._remove_staged_directory(directory)
            raise
        # Online/flash APIs may return before NTQQ consumes the files. Keep them
        # for 24h; on restart the next staged action removes expired directories.
        task = asyncio.create_task(self._expire_staging(directory))
        tasks = getattr(self, "_staging_cleanup_tasks", None)
        if tasks is None:
            tasks = self._staging_cleanup_tasks = set()
        tasks.add(task)
        def finished(completed):
            tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()  # Observe cleanup errors; next staging pass retries expired entries.
        task.add_done_callback(finished)
        if action == "create_flash_task":
            result = {"files": paths}
            if "name" in payload: result["name"] = payload["name"]
            return result
        if action == "send_online_folder":
            return {"user_id": payload["user_id"], "folder_path": str(content_directory.resolve()), "folder_name": payload["folder_name"]}
        if action == "add_custom_face":
            return {"file": paths[0], **{key: value for key, value in payload.items() if key not in {"file_url"}}}
        return {"user_id": payload["user_id"], "file_path": paths[0], "file_name": payload["file_name"]}

    async def _download_action_file(self, url, destination):
        import aiohttp
        from aiohttp.abc import AbstractResolver
        from aiohttp.resolver import DefaultResolver

        class PublicResolver(AbstractResolver):
            def __init__(self):
                self.delegate = DefaultResolver()

            async def resolve(self, host, port=0, family=socket.AF_INET):
                records = await self.delegate.resolve(host, port, family)
                if any(not ipaddress.ip_address(record["host"]).is_global for record in records):
                    raise ValueError("URL resolves to a non-public address")
                return records

            async def close(self):
                await self.delegate.close()

        resolver = PublicResolver()
        try:
            connector = aiohttp.TCPConnector(resolver=resolver)
            async with aiohttp.ClientSession(connector=connector, trust_env=False, timeout=aiohttp.ClientTimeout(total=self.STAGING_TIMEOUT)) as session:
                for _ in range(4):
                    _value(url, URL)
                    async with session.get(url, allow_redirects=False) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location")
                            if not location: raise ValueError("Missing redirect location")
                            url = urljoin(url, location)
                            continue
                        response.raise_for_status()
                        if not 200 <= response.status < 300:
                            raise ValueError("Unexpected download status")
                        if response.content_length is not None and response.content_length > self.MAX_STAGED_FILE_BYTES:
                            raise ValueError("File exceeds 8 MiB")
                        size = 0
                        with destination.open("xb") as output:
                            async for chunk in response.content.iter_chunked(64 * 1024):
                                size += len(chunk)
                                if size > self.MAX_STAGED_FILE_BYTES: raise ValueError("File exceeds 8 MiB")
                                output.write(chunk)
                        return
                raise ValueError("Too many redirects")
        finally:
            await resolver.close()
