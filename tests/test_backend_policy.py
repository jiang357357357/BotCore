import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.config.bot_config import BotConfig
from src.plugins.BotCore.core.router import commands, message_handlers
from src.plugins.BotCore.core.router.backend_policy import is_allowed_by_backend, check_backend_permission
from src.plugins.BotCore.external.monCore.api.moncore_api import MonCoreAPI

APP = "src.plugins.BotCore.app"


class BackendPermissionTests(unittest.IsolatedAsyncioTestCase):
    def test_old_config_is_ignored_and_not_saved_again(self):
        old = {"private_default_permit": False, "group_default_permit": False,
               "private_allow_list": ["1"], "private_deny_list": ["2"],
               "group_allow_list": ["3"], "group_deny_list": ["4"], "voice_mode_enabled": False,
               "command_prefix": "?", "available_commands": ["自定义"], "enable_global_commands": False}
        config = BotConfig.from_dict(old)
        self.assertFalse(config.voice_mode_enabled)
        self.assertFalse((set(old) - {"voice_mode_enabled"}) & set(config.to_dict()))

    def test_all_commands_share_core_permission_rule(self):
        self.assertTrue(any(checker.call is commands.is_command_message
                            for checker in commands.command_matcher.rule.checkers))
        self.assertFalse(any(name.endswith("_cmd") for name in vars(commands)))

    async def test_one_event_coalesces_but_next_event_rechecks_after_revocation(self):
        api = Mock(check_access=AsyncMock(side_effect=[{"approved": True}, {"approved": False}]))
        event = PrivateMessageEvent.model_construct(user_id=200001)
        with patch(APP + ".ensure_moncore_ready", AsyncMock(return_value=True)), patch(APP + ".get_moncore_api", return_value=api):
            results = await asyncio.gather(*(is_allowed_by_backend(event) for _ in range(12)))
            self.assertEqual(results, [True] * 12)
            api.check_access.assert_awaited_once()
            self.assertFalse(await is_allowed_by_backend(PrivateMessageEvent.model_construct(user_id=200001)))
            self.assertEqual(api.check_access.await_count, 2)

    async def test_disconnected_core_never_uses_old_allowlist_or_superusers(self):
        api = Mock(check_access=AsyncMock(return_value={"approved": True}))
        with patch(APP + ".ensure_moncore_ready", AsyncMock(return_value=False)), patch(APP + ".get_moncore_api", return_value=api):
            self.assertFalse(await is_allowed_by_backend(PrivateMessageEvent.model_construct(user_id=200001)))
            api.check_access.assert_not_awaited()

    async def test_admin_and_other_target_go_to_core(self):
        api = Mock(request_command=AsyncMock(return_value={"status": "failed", "content": "denied"}))
        event = GroupMessageEvent.model_construct(self_id=100001, user_id=200001, group_id=300001)
        with patch(APP + ".ensure_moncore_ready", AsyncMock(return_value=True)), patch(APP + ".get_moncore_api", return_value=api):
            from nonebot.adapters.onebot.v11 import Message, MessageSegment
            await commands.execute_command(event, commands.CommandInput("语音", Message("开启")))
            api.request_command.assert_awaited_with(event, "语音", "开启", mentions=[], argument_types=["text"])
            await commands.execute_command(event, commands.CommandInput("记忆", Message(MessageSegment.at(200002))))
            api.request_command.assert_awaited_with(event, "记忆", "", mentions=["200002"], argument_types=["at"])

    async def test_access_transport_handles_response_timeout_and_failed_send(self):
        socket = Mock(send=AsyncMock(return_value=True))
        api = MonCoreAPI(socket)
        event = PrivateMessageEvent.model_construct(user_id=200001)
        async def respond(payload):
            await api._handle_access_check({"subCommand": "success", "data": {"request_id": payload["data"]["request_id"], "approved": True}})
            return True
        socket.send.side_effect = respond
        self.assertTrue((await api.check_access(event))["approved"])
        socket.send.side_effect = None
        self.assertFalse((await api.check_access(event, timeout=0.01))["approved"])
        socket.send.return_value = False
        self.assertFalse((await api.check_access(event))["approved"])
        self.assertFalse(api.pending_access_requests)

    async def test_ordinary_message_uses_core_decision_before_business_processing(self):
        event = PrivateMessageEvent.model_construct(user_id=200001, message="fixture")
        from nonebot.adapters.onebot.v11 import Message
        event.get_message = Mock(return_value=Message("fixture"))
        with patch.object(message_handlers, "is_allowed_by_backend", AsyncMock(return_value=False)), patch.object(
                message_handlers.private_message_service, "handle_normal_message", AsyncMock()) as process:
            await message_handlers.handle_message(Mock(), event)
            process.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
