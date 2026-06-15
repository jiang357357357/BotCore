"""
BotCore 插件启动模块
入口文件，负责插件的初始化和配置
所有运行时配置从 .monconfig 读取
"""

from src.System.Logs import get_logger
import asyncio
import os
import time
from pathlib import Path
from typing import Optional
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot

from src.System.MonConfig import MonConfig
from .config import BotConfig
from .external import Storage, Config
from .external.napcat import NapCatAPI
from .external.monCore import ConnectionManager, MonCoreAPI

logger = get_logger(__name__)
driver = get_driver()

# 加载 .monconfig
mon_config = MonConfig()

# 加载机器人配置
config_path = os.path.join(os.path.dirname(__file__), "config", "config.json")
bot_config = BotConfig.load_from_file(config_path)


def _load_pyproject_hub_config() -> dict:
    """读取 pyproject.toml 中的 [tool.monbot.hub] 配置。"""
    pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
    if not pyproject_path.is_file():
        return {}

    try:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib

        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        return (
            data.get("tool", {})
            .get("monbot", {})
            .get("hub", {})
        )
    except Exception as e:
        logger.warning(f"读取 pyproject.toml Hub 配置失败: {e}")
        return {}


def _build_hub_address(pyproject_hub: dict, monconfig_hub: dict) -> Optional[str]:
    """优先使用 pyproject.toml 的 Hub 配置，兼容 .monconfig ADDRESS。"""
    address = pyproject_hub.get("address")
    if address:
        return str(address)

    ip = pyproject_hub.get("ip")
    port = pyproject_hub.get("port")
    if ip and port:
        return f"tcp://{ip}:{int(port)}"

    return monconfig_hub.get("ADDRESS") or None


def _get_hub_timeout(pyproject_hub: dict, monconfig_hub: dict) -> float:
    """优先使用 pyproject.toml 的 Hub 查询超时配置。"""
    timeout = pyproject_hub.get("query_timeout")
    if timeout is not None:
        return float(timeout)
    return float(monconfig_hub.get("QUERY_TIMEOUT", "5"))


class BotContext:
    """机器人全局状态容器，替代模块级 global 变量"""

    def __init__(self, voice_enabled: bool = True):
        self._moncore_api: Optional[MonCoreAPI] = None
        self._supported_contacts: list[str] = []
        self._supported_groups: list[str] = []
        self._supported_keywords: list[str] = []
        self._voice_mode_enabled: bool = voice_enabled

    @property
    def moncore_api(self) -> Optional[MonCoreAPI]:
        return self._moncore_api

    @moncore_api.setter
    def moncore_api(self, value: Optional[MonCoreAPI]):
        self._moncore_api = value

    def get_supported_contacts(self) -> list[str]:
        return self._supported_contacts.copy()

    def get_supported_groups(self) -> list[str]:
        return self._supported_groups.copy()

    def get_supported_keywords(self) -> list[str]:
        return self._supported_keywords.copy()

    def update_supported_contacts_and_groups(self, contacts: list[str], groups: list[str]):
        self._supported_contacts = contacts if contacts else []
        self._supported_groups = groups if groups else []
        logger.info(f"已更新后端支持的QQ号列表: contacts={len(self._supported_contacts)}, groups={len(self._supported_groups)}")

    def update_supported_keywords(self, keywords: list[str]):
        self._supported_keywords = keywords if keywords else []
        logger.info(f"已更新后端支持的关键词列表: keywords={len(self._supported_keywords)}")

    @property
    def voice_mode_enabled(self) -> bool:
        return self._voice_mode_enabled

    @voice_mode_enabled.setter
    def voice_mode_enabled(self, value: bool):
        self._voice_mode_enabled = value
        logger.info(f"语音模式已{'启用' if value else '禁用'}")


ctx = BotContext(
    voice_enabled=getattr(
        bot_config,
        "voice_mode_enabled",
        mon_config.get("features", "VOICE_MODE_ENABLED", default=True, cast=bool),
    )
)


def __getattr__(name: str):
    """模块级 __getattr__：为深层 lazy import 提供动态值（PEP 562）"""
    if name == "moncore_api":
        return ctx.moncore_api
    if name == "voice_mode_enabled":
        return ctx.voice_mode_enabled
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_supported_contacts() -> list[str]:
    return ctx.get_supported_contacts()


def get_supported_groups() -> list[str]:
    return ctx.get_supported_groups()


def get_supported_keywords() -> list[str]:
    return ctx.get_supported_keywords()


def update_supported_contacts_and_groups(contacts: list[str], groups: list[str]):
    ctx.update_supported_contacts_and_groups(contacts, groups)


def update_supported_keywords(keywords: list[str]):
    ctx.update_supported_keywords(keywords)


def sync_runtime_bot_name(name: Optional[str]) -> bool:
    """同步 NapCat 当前登录昵称到运行时触发名，不写入本地配置。"""
    nickname = str(name or "").strip()
    if not nickname:
        return False

    if bot_config.bot_name == nickname and bot_config.bot_nicknames == []:
        return False

    bot_config.bot_name = nickname
    bot_config.bot_nicknames = []
    logger.info(f"已同步 NapCat 运行时昵称: {nickname}")
    return True


def get_voice_mode() -> bool:
    return ctx.voice_mode_enabled


def set_voice_mode(enabled: bool) -> bool:
    ctx.voice_mode_enabled = enabled
    bot_config.voice_mode_enabled = enabled
    saved = bot_config.save_to_file(config_path)
    if saved:
        logger.info(f"语音模式已持久化到配置文件: {config_path}")
    else:
        logger.error(f"语音模式持久化失败: {config_path}")
    return saved


def get_moncore_api() -> Optional[MonCoreAPI]:
    return ctx.moncore_api


# 初始化外部服务
external_config = Config()
storage = Storage()
napcat_api = NapCatAPI()

# 从 .monconfig [moncore] 读取后端连接参数
_mc = mon_config.section("moncore")
_hub = mon_config.section("hub")
_pyproject_hub = _load_pyproject_hub_config()
connection_manager = ConnectionManager(
    manual_ip=_mc.get("IP") or None,
    ws_port=int(_mc.get("WS_PORT", "8000")),
    http_host=_mc.get("HTTP_HOST", "localhost"),
    enable_discovery=False,
    hub_address=_build_hub_address(_pyproject_hub, _hub),
    hub_timeout=_get_hub_timeout(_pyproject_hub, _hub),
)
_moncore_reconnect_lock = asyncio.Lock()
_last_moncore_reconnect_attempt = 0.0
_MONCORE_RECONNECT_COOLDOWN = 10.0


def is_moncore_ready() -> bool:
    """判断当前 MonCore 专用通道是否可用。"""
    ws_client = connection_manager.get_ws_client()
    return bool(
        ctx.moncore_api
        and connection_manager.is_connected
        and connection_manager.is_registered
        and ws_client
        and ws_client.is_connected
    )


async def ensure_moncore_ready(reason: str = "按需检查") -> bool:
    """
    确保 MonCore 可用。

    启动时如果 MonHub/MonCore 还没准备好，BotCore 会继续运行。后续消息到来时，
    这里会按需再向 MonHub 查询一次并完成注册，避免 supported_contacts/groups
    长期为空导致所有消息被过滤。
    """
    global _last_moncore_reconnect_attempt

    if is_moncore_ready():
        return True

    now = time.monotonic()
    if now - _last_moncore_reconnect_attempt < _MONCORE_RECONNECT_COOLDOWN:
        logger.debug("MonCore 当前不可用，仍在重连冷却期内，跳过本次按需查询")
        return False

    async with _moncore_reconnect_lock:
        if is_moncore_ready():
            return True

        now = time.monotonic()
        if now - _last_moncore_reconnect_attempt < _MONCORE_RECONNECT_COOLDOWN:
            logger.debug("MonCore 当前不可用，仍在重连冷却期内，跳过本次按需查询")
            return False

        _last_moncore_reconnect_attempt = now
        logger.info(f"MonCore 当前不可用，开始按需向 MonHub 查询并恢复连接：{reason}")

        if connection_manager.get_ws_client():
            await connection_manager.stop()
        ctx.moncore_api = None

        success = await connection_manager.start()
        if success and is_moncore_ready():
            logger.info("MonCore 按需恢复成功")
            return True

        logger.warning("MonCore 按需恢复失败，本次消息将跳过后端处理")
        return False


async def _on_registered_callback():
    """注册成功后的回调（此时已经连接专用通道）"""
    if ctx.moncore_api is None:
        ws_client = connection_manager.get_ws_client()
        if ws_client and connection_manager.is_connected:
            ctx.moncore_api = MonCoreAPI(
                ws_client,
                server_ip=connection_manager.server_ip,
                http_port=connection_manager.http_port,
                http_host=connection_manager.http_host,
            )
            logger.info(f"MonCore API 已在专用通道连接成功后初始化 (server_ip={connection_manager.server_ip}, http_port={connection_manager.http_port})")
        else:
            logger.warning("WebSocket 客户端未就绪，MonCoreAPI 初始化延迟")


connection_manager.register_on_registered(_on_registered_callback)

from .core.router import commands  # noqa: E402
from .core.router import message_handlers  # noqa: E402

logger.info("BotCore 插件启动模块已加载")


@get_driver().on_bot_connect
async def on_bot_connect(bot: Bot):
    """Bot 连接成功后的回调"""
    logger.info(f"Bot 已连接: {bot.self_id}")

    napcat_api.set_bot(bot)
    logger.info("已设置 NapCat API 的 bot 实例")

    connection_manager.qq_number = str(bot.self_id)
    logger.info(f"已设置 QQ 号: {connection_manager.qq_number}")

    if not connection_manager.is_connected:
        logger.info("正在启动 MonCore 连接流程...")
        success = await connection_manager.start()
        if success:
            logger.info("MonCore 连接成功，系统已就绪")
        else:
            logger.error("MonCore 连接失败，系统将继续运行但无法与后端通信")
    else:
        logger.info("MonCore 已连接，跳过重复连接")


@get_driver().on_shutdown
async def shutdown_disconnect_moncore():
    """关闭时断开 MonCore 连接"""
    logger.info("正在断开 MonCore 连接...")
    await connection_manager.stop()
    logger.info("MonCore 连接已断开")
