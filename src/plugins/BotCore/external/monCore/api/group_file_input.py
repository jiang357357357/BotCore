"""Resolve only an explicitly quoted, same-group file before downloading bytes."""

from src.plugins.BotCore.external.napcat.file_limits import MAX_FILE_BYTES
from nonebot.adapters.onebot.v11 import Message


async def prepare_group_file_input(core, event, metadata):
    reply = metadata.get("reply_to") or {}
    bot_qq, group_qq = str(event.self_id), str(event.group_id)
    if not reply.get("message_id") or not any(item.get("qq") == bot_qq for item in metadata.get("mentions", [])):
        return
    # A known text/image quote follows normal chat; an ID-only quote needs get_msg.
    if reply.get("content") and not reply.get("files"):
        return
    from src.plugins.BotCore.app import napcat_api
    if not napcat_api or not napcat_api.bot:
        raise RuntimeError("NapCat 未连接，无法读取引用文件")
    message_id = str(reply["message_id"])
    source = await napcat_api.bot.call_api("get_msg", message_id=message_id, _timeout=15)
    if (not isinstance(source, dict) or source.get("message_type") != "group"
            or str(source.get("group_id") or "") != group_qq
            or str(source.get("message_id") or "") != message_id):
        raise ValueError("只能引用当前群内的文件消息")
    segments = source.get("message")
    if isinstance(segments, str):
        segments = [{"type": segment.type, "data": segment.data} for segment in Message(segments)]
    if not isinstance(segments, list):
        raise ValueError("NapCat 未返回可读取的引用消息")
    files = []
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("type") not in {"file", "video"}:
            continue
        data = segment.get("data") or {}
        if segment.get("type") == "video":
            file_id = str(data.get("file_id") or data.get("file") or "")
            size = data.get("file_size") or 0
            if isinstance(size, str) and size.isascii() and size.isdigit():
                size = int(size)
            if not file_id or type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
                raise ValueError("引用视频信息无效或超过 100 MB")
            files.append({"file_id": file_id, "filename": f"qq-video-{len(files) + 1}.mp4",
                          "file_size": size, "media_kind": "video"})
            continue
        filename = str(data.get("file") or data.get("name") or "")
        file_id = str(data.get("file_id") or "")
        size = data.get("file_size")
        if isinstance(size, str) and size.isascii() and size.isdigit():
            size = int(size)
        if (type(size) is not int or not 0 <= size <= MAX_FILE_BYTES or not file_id
                or not filename or len(filename) > 255 or any(c in filename for c in "/\\\x00\r\n")):
            raise ValueError("引用文件信息无效或单文件超过 100 MB")
        files.append({"file_id": file_id, "filename": filename, "file_size": size})
    if not files:
        return
    if len(files) > 4:
        raise ValueError("一次最多引用 4 个文件")
    allowed = await core.check_access(event, capability="group_agent")
    if allowed.get("approved") is not True:
        raise PermissionError("本群智能体未开启或群、发言人的授权已失效")
    if metadata.get("files") or metadata.get("images"):
        raise ValueError("请先单独发送文件，再引用文件消息并 @智能体")
    metadata["files"] = files
    metadata["group_file_reply"] = {"group_id": group_qq, "message_id": message_id}
