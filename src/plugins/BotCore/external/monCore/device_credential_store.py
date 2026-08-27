"""BotCore 设备身份与一次性绑定令牌的 Git 外运行态存储。"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from pathlib import Path
from typing import Optional


class DeviceCredentialStore:
    SCHEMA_VERSION = 1

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir) if state_dir else self._default_state_dir()
        self.device_path = self.state_dir / "device-credential.json"
        self.pairing_path = self.state_dir / "pairing-token"

    @staticmethod
    def _default_state_dir() -> Path:
        override = os.getenv("MON_QQBOT_STATE_DIR")
        if override:
            return Path(override).expanduser().resolve()

        from src.System.MonConfig.loader import MonConfig

        config = MonConfig()
        loaded = config.loaded_files()
        for path in loaded:
            for candidate in (path.parent, *path.parent.parents):
                if (candidate / ".monworkspace").is_file():
                    return candidate / ".run" / "qqbot"
        root = loaded[0].parent if loaded else Path.cwd()
        return root / ".run" / "qqbot"

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_device_state(self) -> dict:
        try:
            value = json.loads(self.device_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def device_id(self) -> str:
        state = self._read_device_state()
        value = str(state.get("device_id") or "").strip()
        if value:
            return value
        value = str(uuid.uuid4())
        self._write_device_state(value, None)
        return value

    def device_credential(self) -> Optional[str]:
        value = str(self._read_device_state().get("credential") or "").strip()
        return value or None

    def save_device_credential(self, credential: str) -> None:
        value = str(credential or "").strip()
        if not value:
            raise ValueError("设备凭证不能为空")
        self._write_device_state(self.device_id(), value)

    def _write_device_state(self, device_id: str, credential: Optional[str]) -> None:
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "device_id": device_id,
            "credential": credential or "",
        }
        self._atomic_write(
            self.device_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def pairing_token(self) -> Optional[str]:
        env_value = str(os.getenv("MON_QQBOT_PAIRING_TOKEN") or "").strip()
        if env_value:
            return env_value
        try:
            value = self.pairing_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    def save_pairing_token(self, token: str) -> None:
        value = str(token or "").strip()
        if not value:
            raise ValueError("绑定令牌不能为空")
        self._atomic_write(self.pairing_path, value + "\n")

    def clear_pairing_token(self) -> None:
        self.pairing_path.unlink(missing_ok=True)
