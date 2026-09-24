#!/usr/bin/env python3
"""
MonBot - NoneBot2 机器人主程序入口
非敏感配置从 .monconfig 读取，凭据从工作区 Config/ENV/bot.env 读取
"""

import sys
import os
from pathlib import Path

from runtime_paths import find_workspace_root, qqbot_state_dir

_frozen = getattr(sys, "frozen", False)
if _frozen:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
_project_root = Path(sys.executable).resolve().parent if _frozen else Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

os.chdir(str(_project_root))
_workspace_root = find_workspace_root(_project_root)


def _load_workspace_env(filename: str) -> None:
    if _workspace_root is None:
        return
    env_path = _workspace_root / "Config" / "ENV" / filename
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_workspace_env("bot.env")
os.environ.setdefault("MON_QQBOT_STATE_DIR", str(qqbot_state_dir(_project_root)))

if _frozen:
    # Keep writable runtime data outside PyInstaller's bundled resources.
    os.environ.setdefault("MON_LOG_ROOT", str(_project_root / "Data" / "Logs"))
    os.environ.setdefault("MON_BOT_CONFIG_FILE", str(_project_root / "Config" / "bot.json"))
    env_path = _project_root / "Config" / "bot.env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from src.System.MonConfig import MonConfig

# 加载 .monconfig（从 MonBot/ 目录向上查找）
mon_config = MonConfig()

# ── NoneBot 框架配置 ──
_nb = mon_config.section("nonebot")
_debug = mon_config.section("debug")
_hot_reload = not _frozen and _debug.get("hot_reload", "false").lower() in ("true", "1", "yes")
_driver = _nb.get("driver", "~aiohttp")
print(f"[BOT] driver={_driver}, hot_reload={_hot_reload}")
nonebot.init(
    driver=_driver,
    host=_nb.get("host", "127.0.0.1"),
    port=int(_nb.get("port", "8080")),
    superusers={
        uid.strip() for uid in _nb.get("superusers", "").split(",") if uid.strip()
    },
    nickname=mon_config.section("bot").get("nicknames", "MonBot").split(","),
    command_start={"/", "!", "！"},
    command_sep={"."},
)

# ── OneBot V11 适配器配置 ──
_ob = mon_config.section("onebot")
_ws_urls = os.environ.get("MON_ONEBOT_WS_URLS", "").strip() or _ob.get("ws_urls", "")
if _ws_urls:
    import json
    try:
        ws_urls = json.loads(_ws_urls)
    except (json.JSONDecodeError, ValueError):
        ws_urls = [u.strip() for u in _ws_urls.split(",") if u.strip()]
    nonebot.get_driver().config.onebot_ws_urls = ws_urls

_access_token = os.environ.get("MON_ONEBOT_ACCESS_TOKEN", "").strip() or _ob.get("access_token", "")
if _access_token:
    nonebot.get_driver().config.onebot_access_token = _access_token

# ── 注册适配器 ──
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# ── 加载插件 ──
if _frozen:
    # Import by module name: discovery by relative directory is source-only.
    plugin = nonebot.load_plugin("src.plugins.BotCore")
    if plugin is None:
        raise RuntimeError("BotCore plugin failed to load")
else:
    nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        print("EDEN_BOT_SELF_TEST_OK: OneBot adapter and BotCore plugin loaded")
        sys.exit(0)
    if _hot_reload and "--no-reload" not in sys.argv:
        import subprocess
        from watchfiles import watch, DefaultFilter

        print("[HOT_RELOAD] 监听文件变化中...")
        proc = subprocess.Popen([sys.executable, __file__, "--no-reload"])
        try:
            for changes in watch("src", recursive=True, watch_filter=DefaultFilter()):
                print(f"[HOT_RELOAD] 检测到文件变化，重启中...")
                proc.terminate()
                proc.wait()
                proc = subprocess.Popen([sys.executable, __file__, "--no-reload"])
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
    else:
        nonebot.run()
