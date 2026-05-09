"""
NapCat 插件层
实现具体功能
"""

from .text import TextPlugin
from .command import CommandPlugin
from .voice_plugin import VoicePlugin
from .image_plugin import ImagePlugin

__all__ = ["TextPlugin", "CommandPlugin", "VoicePlugin", "ImagePlugin"]
