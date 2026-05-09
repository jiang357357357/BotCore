"""
NapCat 依赖层
提供外部服务接口
"""

from .storage import Storage
from .config import Config

__all__ = ["Storage", "Config"]
