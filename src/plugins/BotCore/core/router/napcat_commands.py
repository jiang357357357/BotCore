"""Legacy import: QQ management commands use the single Core command ingress."""

from .commands import command_matcher, handle_command

__all__ = ["command_matcher", "handle_command"]
