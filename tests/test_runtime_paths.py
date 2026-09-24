import tempfile
import unittest
from pathlib import Path

from runtime_paths import find_workspace_root, qqbot_state_dir


class RuntimePathTests(unittest.TestCase):
    def test_frozen_bot_uses_nearest_workspace_state_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "DLC" / "QQBot"
            project_root = workspace / "runtime" / "bot"
            project_root.mkdir(parents=True)
            (workspace / ".monworkspace").write_text("", encoding="utf-8")

            self.assertEqual(find_workspace_root(project_root), workspace.resolve())
            self.assertEqual(
                qqbot_state_dir(project_root),
                workspace.resolve() / ".run" / "qqbot",
            )

    def test_missing_workspace_falls_back_below_project_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary) / "runtime" / "bot"
            project_root.mkdir(parents=True)

            self.assertIsNone(find_workspace_root(project_root))
            self.assertEqual(
                qqbot_state_dir(project_root),
                project_root.resolve() / ".run" / "qqbot",
            )


if __name__ == "__main__":
    unittest.main()
