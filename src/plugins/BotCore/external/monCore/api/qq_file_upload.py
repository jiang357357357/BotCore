"""Bound received files to this exact chat request before sending small WS frames."""

import base64
import hashlib
import secrets

from src.plugins.BotCore.external.napcat.file_limits import MAX_FILE_BYTES, MAX_IMAGE_BYTES


async def upload_input_files(core, metadata, request_id, qq_number, is_group):
    files, images = metadata.get("files") or [], metadata.get("images") or []
    if not files and not images:
        return
    if is_group and not metadata.get("group_file_reply"):
        return
    try:
        if len(files) + len(images) > 4:
            raise ValueError("一次最多发送 4 个文件或图片")
        from src.plugins.BotCore.app import napcat_api
        if not napcat_api:
            raise RuntimeError("NapCat 未连接")
        uploaded = []
        items = [*(('file', index, item) for index, item in enumerate(files, 1)),
                 *(('image', index, item) for index, item in enumerate(images, 1))]
        for kind, index, item in items:
            size = int(item.get("file_size") or 0)
            if kind == "file":
                filename, file_id = str(item.get("filename") or ""), str(item.get("file_id") or "")
                if not filename or not file_id or size < 0 or size > MAX_FILE_BYTES:
                    raise ValueError("文件信息无效或单文件超过 100 MB")
                if item.get("media_kind") == "video":
                    blob, extension = await napcat_api.read_received_video(file_id, size)
                    filename = f"qq-video-{index}.{extension}"
                else:
                    blob = await (napcat_api.read_received_file(file_id, size) if is_group
                                  else napcat_api.read_private_file(file_id, size))
            else:
                image_file = str(item.get("file") or "")
                if is_group or not image_file or size < 0 or size > MAX_IMAGE_BYTES:
                    raise ValueError("图片信息无效或单张图片超过 8 MiB")
                blob, extension = await napcat_api.read_private_image(image_file, size)
                filename = f"qq-image-{index}.{extension}"
            upload_id = secrets.token_hex(16)
            head = {"phase": "start", "upload_id": upload_id, "request_id": request_id,
                    "qq_number": qq_number, "message_id": metadata.get("onebot_message_id"),
                    "filename": filename, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
            if is_group:
                head.update(is_group=True, sender_user_id=metadata.get("sender_user_id"),
                            quoted_message_id=metadata["group_file_reply"]["message_id"])
            if not await core.ws_client.send({"command": "fileUpload", "data": head}):
                raise RuntimeError("QQ 文件上传通道已断开")
            for offset in range(0, len(blob), 96 * 1024):
                chunk = base64.b64encode(blob[offset:offset + 96 * 1024]).decode("ascii")
                if not await core.ws_client.send({"command": "fileUpload", "data": {
                        "phase": "chunk", "upload_id": upload_id, "content_base64": chunk}}):
                    raise RuntimeError("QQ 文件上传中断")
            if not await core.ws_client.send({"command": "fileUpload", "data": {"phase": "finish", "upload_id": upload_id}}):
                raise RuntimeError("QQ 文件上传中断")
            uploaded.append(upload_id)
        metadata["file_upload_ids"] = uploaded
    except Exception as error:
        metadata["file_upload_error"] = str(error)[:180]
