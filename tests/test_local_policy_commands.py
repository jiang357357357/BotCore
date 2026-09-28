"""命令与普通消息共享本地许可规则。"""

import unittest
from unittest.mock import patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="~websockets")

from src.plugins.BotCore.app import bot_config
from src.plugins.BotCore.core.router import commands
from src.plugins.BotCore.core.router.local_policy import is_allowed_by_local_policy


class LocalPolicyCommandTests(unittest.TestCase):
    def test_every_command_uses_local_policy(self):
        for name in (
            "help_cmd",
            "rule_cmd",
            "status_cmd",
            "mode_cmd",
            "approval_cmd",
            "voice_cmd",
            "favorability_cmd",
            "favorability_ranking_cmd",
            "memory_cmd",
        ):
            with self.subTest(command=name):
                matcher = getattr(commands, name)
                self.assertTrue(
                    any(checker.call is is_allowed_by_local_policy for checker in matcher.rule.checkers)
                )

    def test_group_deny_list_takes_precedence_over_allow_list(self):
        event = GroupMessageEvent.model_construct(group_id=123, user_id=456)
        with patch.multiple(
            bot_config,
            group_default_permit=False,
            group_allow_list=["123"],
            group_deny_list=["123"],
        ):
            self.assertFalse(is_allowed_by_local_policy(event))

    def test_private_allow_list_applies_when_default_is_deny(self):
        allowed = PrivateMessageEvent.model_construct(user_id=456)
        denied = PrivateMessageEvent.model_construct(user_id=789)
        with patch.multiple(
            bot_config,
            private_default_permit=False,
            private_allow_list=["456"],
            private_deny_list=[],
        ):
            self.assertTrue(is_allowed_by_local_policy(allowed))
            self.assertFalse(is_allowed_by_local_policy(denied))


if __name__ == "__main__":
    unittest.main()
