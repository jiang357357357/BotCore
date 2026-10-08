"""A single QQ command ingress; Core owns names, aliases, arguments and roles."""

from dataclasses import dataclass

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException

from src.System.Logs import get_logger

logger = get_logger(__name__)
COMMAND_PREFIXES = ("/", "!", "！")


@dataclass(frozen=True)
class CommandInput:
    name: str
    arguments: Message


def parse_command(event: MessageEvent):
    message = Message(event.get_message())
    # Only remove a leading mention of this bot and a quoted-message segment.
    while message:
        segment = message[0]
        if segment.type == "reply" or (segment.type == "at" and str(segment.data.get("qq")) == str(event.self_id)):
            message.pop(0)
        elif segment.type == "text" and not str(segment.data.get("text", "")).strip():
            message.pop(0)
        else:
            break
    if not message or message[0].type != "text":
        return None
    head = []
    while message and message[0].type == "text":
        head.append(str(message.pop(0).data.get("text", "")))
    text = "".join(head).lstrip()
    if not text.startswith(COMMAND_PREFIXES):
        return None
    parts = text[1:].split(maxsplit=1)
    if not parts:
        return CommandInput("", Message())
    arguments = Message(MessageSegment.text(parts[1] if len(parts) > 1 else "")) + message
    return CommandInput(parts[0], arguments)


def is_command_message(event: MessageEvent) -> bool:
    return parse_command(event) is not None


command_matcher = on_message(rule=is_command_message, priority=5, block=True)


async def execute_command(event: MessageEvent, command: CommandInput):
    from ...app import ensure_moncore_ready, get_moncore_api, set_voice_mode

    if not command.name:
        return {"status": "failed", "content": "请在命令前缀后输入命令名称，例如 /帮助。"}
    if not await ensure_moncore_ready("QQ 命令"):
        return {"status": "failed", "content": "MonCore 当前不可用，命令未执行，请稍后重试。"}
    api = get_moncore_api()
    if api is None:
        return {"status": "failed", "content": "MonCore 当前不可用，命令未执行，请稍后重试。"}
    text = command.arguments.extract_plain_text().strip()
    mentions = [str(segment.data.get("qq", "")) for segment in command.arguments if segment.type == "at"]
    result = await api.request_command(event, command.name, text, mentions=mentions,
        argument_types=sorted({segment.type for segment in command.arguments}))
    action = result.get("runtime_action")
    if action is not None:
        if (result.get("status") != "success" or result.get("name") != "语音" or not isinstance(action, dict)
                or set(action) != {"type", "enabled"} or action.get("type") != "set_voice_mode" or type(action.get("enabled")) is not bool):
            return {"status": "unknown", "content": "命令回执无效，语音开关未修改，请检查 Core 与 QQBot 版本。"}
        if not set_voice_mode(action["enabled"]):
            result["content"] = "语音开关已在当前运行中修改，但保存配置失败，重启后可能不会保留。"
    return result


async def send_command_result(event, result):
    from ...app import napcat_api

    images = result.get("images_base64")
    if isinstance(images, list) and images:
        try:
            if isinstance(event, GroupMessageEvent):
                await napcat_api.send_group_images(str(event.group_id), images)
            else:
                await napcat_api.send_private_images(str(event.user_id), images)
        except Exception:
            logger.warning("QQ 命令图片发送失败，回退文字", exc_info=True)
        else:
            await command_matcher.finish()
            return
    await command_matcher.finish(Message(MessageSegment.text(str(result.get("content") or "命令结果未确认，请稍后检查。"))))


@command_matcher.handle()
async def handle_command(event: MessageEvent):
    command = parse_command(event)
    if command is None:
        return
    try:
        result = await execute_command(event, command)
    except FinishedException:
        raise
    except Exception:
        logger.error("QQ 命令处理失败", exc_info=True)
        result = {"status": "unknown", "content": "命令处理异常，结果未确认，请先核对实际状态。"}
    logger.info("QQ 命令回复: name=%s status=%s code=%s", command.name, result.get("status"), result.get("code", ""))
    await send_command_result(event, result)
