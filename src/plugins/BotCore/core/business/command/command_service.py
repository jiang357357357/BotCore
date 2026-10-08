"""Compatibility facade; command execution and help live in MonCore."""

from nonebot.adapters.onebot.v11 import Message


class CommandService:
    def __init__(self, config=None, napcat_api=None):
        self.config, self.napcat_api = config, napcat_api

    async def _run(self, event, name):
        from ...router.commands import CommandInput, execute_command
        result = await execute_command(event, CommandInput(name, Message()))
        return str(result.get("content") or "命令结果未确认。")

    async def get_help_text(self, event):
        return await self._run(event, "帮助")

    async def get_rule_text(self, event):
        return await self._run(event, "角色")
