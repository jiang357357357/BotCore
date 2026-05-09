#!/usr/bin/env python3
"""
MonBot - NoneBot2 机器人主程序入口
所有配置从 .monconfig 读取，不再依赖 .env 文件
"""

import sys
import os
from pathlib import Path

_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

os.chdir(str(_project_root))

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from src.System.MonConfig import MonConfig

# 加载 .monconfig（从 MonBot/ 目录向上查找）
mon_config = MonConfig()

# ── NoneBot 框架配置 ──
_nb = mon_config.section("nonebot")
nonebot.init(
    driver=_nb.get("driver", "~aiohttp"),
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
if _ob.get("ws_urls"):
    import json
    try:
        ws_urls = json.loads(_ob["ws_urls"])
    except (json.JSONDecodeError, ValueError):
        ws_urls = [u.strip() for u in _ob["ws_urls"].split(",") if u.strip()]
    nonebot.get_driver().config.onebot_ws_urls = ws_urls

if _ob.get("access_token"):
    nonebot.get_driver().config.onebot_access_token = _ob["access_token"]

# ── 注册适配器 ──
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# ── 加载插件 ──
nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    nonebot.run()
