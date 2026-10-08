"""Compatibility adapter for the single command mechanism."""

from nonebot.adapters.onebot.v11 import Message, MessageSegment


class CommandPlugin:
    def __init__(self, config=None):
        self.name, self.config = "command_plugin", config

    async def handle(self, event, message=None):
        from ...core.router.commands import parse_command, execute_command
        command = parse_command(event)
        if command is None:
            return None
        result = await execute_command(event, command)
        return Message(MessageSegment.text(str(result.get("content") or "命令结果未确认。")))
