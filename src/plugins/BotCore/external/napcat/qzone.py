"""Validation and explicit delivery outcomes for QQ Space publishing."""

from urllib.parse import urlsplit


class QzonePublishError(RuntimeError):
    def __init__(self, message: str, code: str, status: str = "failed"):
        super().__init__(message)
        self.code = code
        self.status = status


def normalize_qzone_payload(content: str, images=None, ugc_right: int = 4) -> dict:
    """Project limits; visibility defaults to friends, independently of NapCat."""
    if not isinstance(content, str) or not content.strip() or len(content.strip()) > 2000:
        raise ValueError("说说正文不能为空，且最多 2000 字")
    if isinstance(ugc_right, bool) or not isinstance(ugc_right, int) or ugc_right not in {1, 4, 64}:
        raise ValueError("可见范围只支持公开(1)、好友(4)或仅自己(64)")
    if images is None:
        images = []
    if not isinstance(images, list) or len(images) > 9:
        raise ValueError("说说最多附带 9 张图片")
    normalized_images = []
    for image in images:
        if not isinstance(image, str) or len(image) > 2048 or any(char.isspace() or ord(char) < 32 for char in image):
            raise ValueError("图片必须是有效的 HTTP(S) URL")
        try:
            url = urlsplit(image)
            valid = url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password
            url.port  # Validate an explicitly supplied port as well.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("图片仅支持 HTTP(S) URL，不接受本地文件路径或内嵌数据")
        normalized_images.append(image)
    return {"content": content.strip(), "images": normalized_images, "ugc_right": ugc_right}
