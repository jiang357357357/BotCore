"""Resolve writable runtime paths shared by source and frozen BotCore builds."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def find_workspace_root(project_root: Path) -> Optional[Path]:
    root = Path(project_root).resolve()
    return next(
        (candidate for candidate in (root, *root.parents) if (candidate / ".monworkspace").is_file()),
        None,
    )


def qqbot_state_dir(project_root: Path) -> Path:
    root = Path(project_root).resolve()
    workspace_root = find_workspace_root(root)
    return (workspace_root or root) / ".run" / "qqbot"
