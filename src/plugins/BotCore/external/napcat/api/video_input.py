"""Bounded incoming video loading; never fetch arbitrary segment URLs."""
from pathlib import Path

from ..file_limits import MAX_FILE_BYTES, FILE_TRANSFER_TIMEOUT_SECONDS


async def read_received_video(api, file_id: str, expected_size: int = 0) -> tuple[bytes, str]:
    if (not api.bot or not file_id or len(file_id) > 1024
            or expected_size < 0 or expected_size > MAX_FILE_BYTES):
        raise ValueError("QQ 视频不可用或超过 100 MB")
    result = await api.bot.call_api("get_file", file_id=file_id, _timeout=FILE_TRANSFER_TIMEOUT_SECONDS)
    location = api._read_mapping_or_attr(result, "file", "")
    if not isinstance(location, str) or not location:
        raise RuntimeError("NapCat 未返回视频缓存路径")
    path = Path(location)
    info = path.lstat()
    if not path.is_file() or path.is_symlink() or info.st_size > MAX_FILE_BYTES:
        raise ValueError("QQ 视频不是受限的普通文件")
    with path.open("rb") as source:
        blob = source.read(MAX_FILE_BYTES + 1)
    if len(blob) > MAX_FILE_BYTES or (expected_size and len(blob) != expected_size):
        raise ValueError("QQ 视频大小与消息记录不符")
    if len(blob) >= 12 and blob[4:8] == b"ftyp":
        extension = "mp4"
    elif blob.startswith(b"\x1a\x45\xdf\xa3"):
        extension = "webm"
    else:
        raise ValueError("QQ 视频格式需为 MP4 或 WebM")
    return blob, extension
