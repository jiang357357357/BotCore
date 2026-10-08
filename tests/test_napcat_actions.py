"""Restricted action execution, parameter validation and local staging (no network)."""

import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from nonebot.adapters.onebot.v11.exception import ActionFailed


ROOT = Path(__file__).resolve().parents[1] / "src/plugins/BotCore/external/napcat"
PACKAGE = "_napcat_actions_isolated_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def load(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


catalog = load("action_catalog")
actions = load("actions")


class CatalogTests(unittest.TestCase):
    def test_catalog_is_json_and_all_actions_have_closed_schemas(self):
        json.dumps(catalog.ACTION_CATALOG)
        self.assertGreaterEqual(len(catalog.ACTION_CATALOG), 90)
        for entry in catalog.ACTION_CATALOG.values():
            self.assertFalse(entry["parameters"]["additionalProperties"])
            self.assertIn(entry["target"], {"account", "message", "user", "group", "request", "none"})

    def test_denies_credentials_arbitrary_actions_and_unknown_params(self):
        for action, params in [("get_cookies", {}), ("send_packet", {}),
            ("send_like", {"user_id": "123", "access_token": "secret"}),
            ("send_like", {"user_id": True}), ("send_like", {"user_id": "123", "times": True})]:
            with self.subTest(action=action, params=params), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action(action, params)

    def test_upstream_ids_and_forward_alias(self):
        self.assertEqual(catalog.normalize_action("fetch_ptt_text", {"message_id": -123}), {"message_id": "-123"})
        self.assertEqual(catalog.normalize_action("get_forward_msg", {"id": "opaque-forward-id"}), {"message_id": "opaque-forward-id"})
        self.assertEqual(catalog.normalize_action("set_msg_emoji_like", {"message_id": 12, "emoji_id": 0})["emoji_id"], "0")

    def test_rejects_local_paths_credentials_urls_and_private_urls(self):
        for value in ["C:/private.txt", "file:///tmp/a", "base64://YQ==", "https://user:pass@example.com/a", "http://127.0.0.1/a", "http://localhost/a", "http://[::1]/a"]:
            with self.subTest(value=value), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action("set_qq_avatar", {"file": value})

    def test_forward_nodes_allow_only_typed_segments(self):
        valid = {"user_id": "123", "messages": [{"type": "node", "data": {"user_id": "456", "nickname": "Name",
                    "content": [{"type": "text", "data": {"text": "[CQ:json,data=literal text]"}}, {"type": "face", "data": {"id": 0}}]}}]}
        result = catalog.normalize_action("send_private_forward_msg", valid)
        self.assertEqual(result["messages"][0]["data"]["content"][1]["data"]["id"], "0")
        for kind, data in [("json", {"data": "{}"}), ("image", {"file": "file:///secret"}), ("text", {"text": "hi", "cookie": "secret"})]:
            valid["messages"][0]["data"]["content"] = [{"type": kind, "data": data}]
            with self.assertRaises(catalog.ActionValidationError): catalog.normalize_action("send_private_forward_msg", valid)

    def test_request_subtype_and_filename_validation(self):
        with self.assertRaises(catalog.ActionValidationError):
            catalog.normalize_action("set_group_add_request", {"flag": "f", "approve": True})
        for name in ["../private", "x/y", "CON.txt", "NUL", "com1.png", "lpt9.txt", ".EXPIRES", "x:stream", "trailing."]:
            with self.subTest(name=name), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action("send_online_file", {"user_id": "123", "file_url": "https://example.com/a", "file_name": name})

    def test_upstream_stubs_are_unavailable_and_flash_requires_one_target(self):
        with self.assertRaises(catalog.ActionValidationError) as raised:
            catalog.normalize_action("get_online_clients", {})
        self.assertEqual(raised.exception.code, "UNSUPPORTED_ACTION")
        with self.assertRaises(catalog.ActionValidationError):
            catalog.normalize_action("send_flash_msg", {"fileset_id": "f", "user_id": "123", "group_id": "456"})

    def test_extended_schemas_close_paths_and_require_precise_scopes(self):
        for action, params in [
            ("get_record", {"file_id": "resource"}),
            ("get_record", {"source_message_id": 1, "file_id": "C:/secret"}),
            ("get_record", {"source_message_id": 1, "file_id": "id", "file": "/secret"}),
            ("get_record", {"source_message_id": 1, "file_id": "id", "out_format": "exe"}),
            ("send_online_folder", {"user_id": 1, "folder_path": "C:/"}),
            ("add_custom_face", {"file": "C:/face.png"}),
            ("fetch_emoji_like", {"message_id": 1, "emojiId": 0, "emojiType": 1, "cookie": "credential"}),
            ("send_contact_card", {"target_user_id": 1, "contact_user_id": 2, "contact_group_id": 3}),
            ("send_contact_card", {"target_user_id": 1}),
            ("send_miniapp_card", {"target_user_id": 1, "data": "{}"}),
            ("set_group_member_permissions", {"group_id": 1}),
            ("get_friend_msg_history", {"user_id": 1, "count": 101}),
        ]:
            with self.subTest(action=action, params=params), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action(action, params)
        self.assertEqual(catalog.normalize_action("get_record", {"source_message_id": -1, "file_id": "ABCD.record"})["out_format"], "mp3")
        self.assertEqual(catalog.normalize_action("get_friend_msg_history", {"user_id": 1, "message_seq": -12})["message_seq"], "-12")
        self.assertEqual(catalog.normalize_action("get_group_msg_history", {"group_id": 1, "message_seq": 0})["message_seq"], "0")
        self.assertEqual(catalog.normalize_action("set_group_kick_members", {"group_id": 1, "user_id": [2, 3]})["user_id"], ["2", "3"])
        scopes = catalog.ACTION_CATALOG["forward_group_single_msg"]["scope_targets"]
        self.assertEqual({(x["field"], x["target"]) for x in scopes}, {("group_id", "group"), ("message_id", "message")})
        card = catalog.ACTION_CATALOG["send_contact_card"]["parameters"]
        self.assertEqual(len(card["allOf"]), 2)

    def test_qzone_visibility_and_image_constraints(self):
        self.assertEqual(catalog.normalize_action("send_qzone_msg", {"content": "text"})["ugc_right"], 4)
        valid = catalog.normalize_action("send_qzone_msg", {"content": "", "images": ["https://example.com/p.png"], "ugc_right": 16, "target_uins": [123]})
        self.assertEqual(valid["target_uins"], ["123"])
        for params in [{"content": "  "}, {"content": "text", "ugc_right": 16},
                       {"content": "text", "ugc_right": 4, "target_uins": [123]},
                       {"content": "text", "images": ["file:///secret"]},
                       {"content": "text", "images": ["https://example.com/p.png"] * 10}]:
            with self.subTest(params=params), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action("send_qzone_msg", params)

    def test_avatar_inline_is_bounded_image_data(self):
        for raw in [b"\x89PNG\r\n\x1a\nimage", b"\xff\xd8\xffimage", b"RIFF0000WEBPimage"]:
            encoded = base64.b64encode(raw).decode()
            self.assertEqual(catalog.normalize_action("set_qq_avatar_inline", {"image_base64": encoded})["image_base64"], encoded)
        for encoded in ["invalid!", "file:///secret", base64.b64encode(b"not image").decode(),
                        base64.b64encode(b"\xff\xd8\xff" + b"x" * 1048576).decode()]:
            with self.subTest(size=len(encoded)), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action("set_qq_avatar_inline", {"image_base64": encoded})

    def test_collection_and_generic_array_validation(self):
        for content in ['{"file":"/secret"}', '<xml/>', '[CQ:json,data={}]', 'file:///secret']:
            with self.subTest(content=content), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action("create_collection", {"content": content, "brief": "title"})
        for ids in [[], [True], [1] * 21]:
            with self.subTest(ids=ids), self.assertRaises(catalog.ActionValidationError):
                catalog.normalize_action("set_group_kick_members", {"group_id": 1, "user_id": ids})
        for action in ("get_guild_list", "get_guild_service_profile", "download_file_stream", "upload_file_stream"):
            with self.subTest(action=action), self.assertRaises(catalog.ActionValidationError) as raised:
                catalog.normalize_action(action, {})
            self.assertEqual(raised.exception.code, "UNSUPPORTED_ACTION")


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    def create_runner(self, result=None):
        runner = actions.NapCatActionsMixin()
        runner.bot = types.SimpleNamespace(call_api=AsyncMock(return_value=result))
        runner._message_send_locks = {}
        return runner

    async def test_void_success_and_bridge_only_parameter_stripped(self):
        runner = self.create_runner(None)
        result = await runner.execute_action("set_group_add_request", {"flag": "f", "sub_type": "invite", "approve": False})
        self.assertIsNone(result)
        kwargs = runner.bot.call_api.await_args.kwargs
        self.assertNotIn("sub_type", kwargs)
        self.assertEqual(kwargs["flag"], "f")
        self.assertEqual(kwargs["_timeout"], 45.0)

    async def test_extended_aliases_and_resource_provenance_are_not_sent_upstream(self):
        runner = self.create_runner({"cookie": "page2", "emojiLikesList": []})
        result = await runner.execute_action("fetch_emoji_like", {"message_id": 1, "emojiId": 0, "emojiType": 1, "cursor": "page1"})
        self.assertEqual(result["cursor"], "page2")
        self.assertNotIn("cookie", result)
        self.assertEqual(runner.bot.call_api.await_args.kwargs["cookie"], "page1")
        self.assertNotIn("cursor", runner.bot.call_api.await_args.kwargs)
        await runner.execute_action("get_collection_list", {"category": 2, "count": 7})
        self.assertEqual(runner.bot.call_api.await_args.kwargs["count"], "7")
        await runner.execute_action("get_record", {"source_message_id": 1, "file_id": "resource-id"})
        self.assertNotIn("source_message_id", runner.bot.call_api.await_args.kwargs)
        self.assertEqual(runner.bot.call_api.await_args.kwargs["file_id"], "resource-id")

    async def test_inline_avatar_calls_only_fixed_upstream_api(self):
        runner = self.create_runner({"result": 0, "errMsg": ""})
        encoded = base64.b64encode(b"\xff\xd8\xfffixture").decode()
        await runner.execute_action("set_qq_avatar_inline", {"image_base64": encoded})
        runner.bot.call_api.assert_awaited_once_with("set_qq_avatar", file="base64://" + encoded, _timeout=45.0)

    async def test_large_media_result_keeps_metadata_instead_of_breaking_bridge_ack(self):
        runner = self.create_runner({"file": "cached.wav", "url": "cached.wav", "file_size": "400000", "base64": "a" * (384 * 1024 + 1)})
        result = await runner.execute_action("get_record", {"source_message_id": 1, "file_id": "record"})
        self.assertTrue(result["base64_omitted"])
        self.assertNotIn("base64", result)
        self.assertEqual(result["file"], "cached.wav")
        runner = self.create_runner({"file": "cached.wav", "base64": "fixture"})
        self.assertEqual((await runner.execute_action("get_record", {"source_message_id": 1, "file_id": "record"}))["base64"], "fixture")

    async def test_contact_cards_use_generated_ark_and_correct_destination(self):
        for source, key, destination, send_action in [("contact_user_id", "arkMsg", "target_group_id", "send_group_msg"),
                                                     ("contact_group_id", "arkJson", "target_user_id", "send_private_msg")]:
            runner = self.create_runner()
            runner.bot.call_api.side_effect = [{"result": 0, key: '{"app":"com.tencent.contact","meta":{}}'}, {"message_id": "sent"}]
            result = await runner.execute_action("send_contact_card", {source: 123, destination: 456})
            self.assertEqual(result["message_id"], "sent")
            calls = runner.bot.call_api.await_args_list
            self.assertEqual(calls[0].args, ("send_ark_share",))
            self.assertEqual(calls[1].args, (send_action,))
            self.assertEqual(calls[1].kwargs["message"][0]["type"], "json")
            self.assertNotIn(source, calls[1].kwargs)
            self.assertEqual(len(runner._message_send_locks), 1)

    async def test_miniapp_card_generation_failure_does_not_send_and_timeout_never_retries(self):
        params = {"target_user_id": 123, "type": "bili", "title": "title", "desc": "desc", "picUrl": "https://example.com/p", "jumpUrl": "https://example.com/j"}
        runner = self.create_runner()
        runner.bot.call_api.side_effect = [{"data": {"app": "com.tencent.miniapp", "meta": {}}}, {"message_id": "sent"}]
        await runner.execute_action("send_miniapp_card", params)
        self.assertNotIn("target_user_id", runner.bot.call_api.await_args_list[0].kwargs)
        runner = self.create_runner({"data": {"wrong": True}})
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("send_miniapp_card", params)
        self.assertEqual(raised.exception.status, "failed")
        runner.bot.call_api.assert_awaited_once()
        runner = self.create_runner()
        runner.ACTION_TIMEOUT = 0.01
        async def invoke(action, **kwargs):
            if action == "get_mini_app_ark": return {"data": {"app": "app"}}
            await asyncio.Event().wait()
        runner.bot.call_api.side_effect = invoke
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("send_miniapp_card", params)
        self.assertEqual(raised.exception.status, "unknown")
        self.assertEqual(runner.bot.call_api.await_count, 2)

    async def test_operational_unknown_and_qzone_receipts(self):
        runner = self.create_runner(None)
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("clean_cache", {})
        self.assertEqual(raised.exception.status, "unknown")
        for receipt in [None, {}, {"tid": False}, {"tid": " "}, {"tid": 123}]:
            runner = self.create_runner(receipt)
            with self.subTest(receipt=receipt), self.assertRaises(actions.NapCatActionError) as raised:
                await runner.execute_action("send_qzone_msg", {"content": "hello"})
            self.assertEqual(raised.exception.status, "unknown")
        runner = self.create_runner({"tid": "abc"})
        self.assertEqual(await runner.execute_action("send_qzone_msg", {"content": "hello"}), {"tid": "abc"})

    async def test_history_adds_missing_own_message_peer_only_from_scoped_query(self):
        original = [{"message_type": "private", "user_id": 999, "message_id": 1},
                    {"message_type": "private", "sender": {"user_id": 999}, "message_id": 2},
                    {"message_type": "private", "user_id": 999, "target_id": 555, "message_id": 3},
                    {"message_type": "private", "user_id": 999, "peer_id": 555, "message_id": 4},
                    {"message_type": "private", "user_id": 123, "message_id": 5},
                    {"message_type": "group", "user_id": 999, "message_id": 6}, "invalid"]
        runner = self.create_runner({"messages": original})
        runner.bot.self_id = "999"
        result = await runner.execute_action("get_friend_msg_history", {"user_id": 123})
        self.assertEqual([item["target_id"] for item in result["messages"][:2]], ["123", "123"])
        self.assertEqual(result["messages"][2]["target_id"], 555)
        self.assertEqual(result["messages"][3]["peer_id"], 555)
        self.assertNotIn("target_id", result["messages"][4])
        self.assertNotIn("target_id", result["messages"][5])
        self.assertNotIn("target_id", original[0])

    async def test_online_folder_excludes_expiry_marker_and_custom_face_stages(self):
        for action, payload in [("send_online_folder", {"user_id": 123, "folder_name": "photos", "files": [{"url": "https://example.com/p", "name": "p.png"}]}),
                                ("add_custom_face", {"file_url": "https://example.com/p", "file_name": "p.png"})]:
            runner = self.create_runner({"msgId": "123", "result": 0})
            with tempfile.TemporaryDirectory() as temp:
                runner._staging_root = lambda: Path(temp).resolve()
                async def download(url, destination): destination.write_bytes(b"fixture")
                runner._download_action_file = download
                await runner.execute_action(action, payload)
                kwargs = runner.bot.call_api.await_args.kwargs
                if action == "send_online_folder":
                    folder = Path(kwargs["folder_path"])
                    self.assertEqual([p.name for p in folder.iterdir()], ["p.png"])
                    self.assertTrue((folder.parent / ".expires").exists())
                else:
                    self.assertTrue(Path(kwargs["file"]).is_file())
                    self.assertNotIn("file_url", kwargs)
                for task in runner._staging_cleanup_tasks: task.cancel()
                await asyncio.gather(*runner._staging_cleanup_tasks, return_exceptions=True)

    async def test_unsupported_never_calls_api(self):
        runner = self.create_runner()
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("get_credentials", {})
        self.assertEqual(raised.exception.code, "UNSUPPORTED_ACTION")
        runner.bot.call_api.assert_not_awaited()

    async def test_upstream_unsupported_and_mutating_ambiguity(self):
        runner = self.create_runner()
        runner.bot.call_api.side_effect = ActionFailed(retcode=1404, message="不支持的Api delete_msg")
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("delete_msg", {"message_id": 12})
        self.assertEqual(raised.exception.code, "UNSUPPORTED_API")
        runner.bot.call_api.side_effect = ActionFailed(retcode=1200, message="network timeout")
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("delete_msg", {"message_id": 12})
        self.assertEqual(raised.exception.status, "unknown")
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("get_msg", {"message_id": 12})
        self.assertEqual(raised.exception.status, "failed")

    async def test_timeout_is_single_attempt_and_cancellation_propagates(self):
        runner = self.create_runner()
        runner.ACTION_TIMEOUT = 0.01
        runner.bot.call_api.side_effect = lambda *args, **kwargs: None
        async def hang(*args, **kwargs): await asyncio.Event().wait()
        runner.bot.call_api.side_effect = hang
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("send_like", {"user_id": "123"})
        self.assertEqual(raised.exception.status, "unknown")
        self.assertEqual(runner.bot.call_api.await_count, 1)
        runner.ACTION_TIMEOUT = 5
        task = asyncio.create_task(runner.execute_action("send_like", {"user_id": "123"}))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task

    async def test_missing_send_receipt_is_not_void_success(self):
        for result in [None, {}, {"message_id": True}, {"message_id": {}}, {"message_id": "  "}]:
            runner = self.create_runner(result)
            with self.subTest(result=result), self.assertRaises(actions.NapCatActionError) as raised:
                await runner.execute_action("send_private_msg", {"user_id": "123", "message": [{"type": "text", "data": {"text": "hi"}}]})
            self.assertEqual(raised.exception.status, "unknown")

    async def test_flash_boolean_result_does_not_count_as_success(self):
        runner = self.create_runner({"result": False})
        runner._stage_action_files = AsyncMock(return_value={"files": ["trusted-stage-only"]})
        with self.assertRaises(actions.NapCatActionError) as raised:
            await runner.execute_action("create_flash_task", {"files": [{"url": "https://example.com/a", "name": "a.txt"}]})
        self.assertEqual(raised.exception.status, "unknown")

    async def test_online_file_is_staged_and_retained_without_raw_path_input(self):
        runner = self.create_runner({"result": 0, "msgId": "123"})
        with tempfile.TemporaryDirectory() as temp:
            runner._staging_root = lambda: Path(temp).resolve()
            async def download(url, destination): destination.write_bytes(b"fixture")
            runner._download_action_file = download
            result = await runner.execute_action("send_online_file", {"user_id": "123", "file_url": "https://example.com/file", "file_name": "note.txt"})
            self.assertEqual(result["msgId"], "123")
            kwargs = runner.bot.call_api.await_args.kwargs
            staged = Path(kwargs["file_path"])
            self.assertEqual(staged.read_bytes(), b"fixture")
            self.assertTrue(staged.is_relative_to(Path(temp)))
            self.assertNotIn("file_url", kwargs)
            for task in list(runner._staging_cleanup_tasks): task.cancel()
            await asyncio.gather(*runner._staging_cleanup_tasks, return_exceptions=True)

    async def test_failed_staging_never_calls_upstream_and_cleans_partial_files(self):
        runner = self.create_runner()
        with tempfile.TemporaryDirectory() as temp:
            runner._staging_root = lambda: Path(temp).resolve()
            runner._download_action_file = AsyncMock(side_effect=ValueError("too large"))
            with self.assertRaises(actions.NapCatActionError) as raised:
                await runner.execute_action("create_flash_task", {"files": [{"url": "https://example.com/file", "name": "a.txt"}]})
            self.assertEqual(raised.exception.code, "STAGING_FAILED")
            self.assertEqual(list(Path(temp).iterdir()), [])
            runner.bot.call_api.assert_not_awaited()

    async def test_download_rejects_oversize_response_and_private_redirect_without_network(self):
        runner = self.create_runner()
        for status, headers, length in [(200, {}, runner.MAX_STAGED_FILE_BYTES + 1), (302, {"Location": "http://127.0.0.1/secret"}, None)]:
            response = MagicMock(status=status, headers=headers, content_length=length)
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            session = MagicMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            session.get.return_value = response
            resolver = MagicMock()
            resolver.close = AsyncMock()
            with tempfile.TemporaryDirectory() as temp, patch("aiohttp.ClientSession", return_value=session), patch("aiohttp.TCPConnector"), patch("aiohttp.resolver.DefaultResolver", return_value=resolver):
                destination = Path(temp) / "a.txt"
                with self.subTest(status=status), self.assertRaises(ValueError):
                    await runner._download_action_file("https://example.com/a", destination)
                self.assertFalse(destination.exists())
                self.assertEqual(session.get.call_count, 1)

    async def test_cleanup_refuses_paths_outside_owned_root(self):
        runner = self.create_runner()
        with tempfile.TemporaryDirectory() as temp:
            owned = Path(temp) / "owned"
            outside = Path(temp) / ("a" * 32)
            owned.mkdir()
            outside.mkdir()
            (outside / "keep").write_text("fixture")
            runner._staging_root = lambda: owned.resolve()
            runner._remove_staged_directory(outside)
            self.assertTrue((outside / "keep").exists())

    async def test_slow_typing_does_not_hold_message_send_lock(self):
        runner = self.create_runner()
        typing_started = asyncio.Event()
        release_typing = asyncio.Event()
        async def call_api(action, **kwargs):
            if action == "set_input_status":
                typing_started.set()
                await release_typing.wait()
                return None
            return {"message_id": "123"}
        runner.bot.call_api.side_effect = call_api
        typing = asyncio.create_task(runner.execute_action("set_input_status", {"user_id": "123", "event_type": 1}))
        try:
            await asyncio.wait_for(typing_started.wait(), 1)
            result = await asyncio.wait_for(runner.execute_action("send_private_msg", {
                "user_id": "123", "message": [{"type": "text", "data": {"text": "final"}}],
            }), 1)
            self.assertEqual(result["message_id"], "123")
            self.assertFalse(typing.done())
        finally:
            release_typing.set()
            await typing


if __name__ == "__main__":
    unittest.main()
