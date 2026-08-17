"""
BotCore 插件启动模块
入口文件，负责插件的初始化和配置
所有运行时配置从 .monconfig 读取
"""

from src.System.Logs import get_logger
import asyncio
import os
import time
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
connection_manager = ConnectionManager(
    server_ip=_mc.get("IP") or "127.0.0.1",
    ws_port=int(_mc.get("WS_PORT", "40011")),
    http_host=_mc.get("HTTP_HOST", "127.0.0.1"),
    pairing_token=os.getenv("MON_QQBOT_PAIRING_TOKEN") or _mc.get("PAIRING_TOKEN"),
)
_moncore_reconnect_lock = asyncio.Lock()
_last_moncore_reconnect_attempt = 0.0
_MONCORE_RECONNECT_COOLDOWN = 10.0
_bot_info_sync_task: Optional[asyncio.Task] = None
_BOT_INFO_SYNC_INTERVAL = float(os.getenv("MONBOT_INFO_SYNC_INTERVAL", "300"))


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

    启动时如果 MonCore 还没准备好，BotCore 会继续运行。后续消息到来时，
    这里会按需再按固定本机地址连接一次，避免 supported_contacts/groups
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
        logger.info(f"MonCore 当前不可用，开始按固定本机地址恢复连接：{reason}")

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
    ws_client = connection_manager.get_ws_client()
    if ws_client and connection_manager.is_connected:
        if ctx.moncore_api is None:
            ctx.moncore_api = MonCoreAPI(
                ws_client,
                server_ip=connection_manager.server_ip,
                http_port=connection_manager.http_port,
                http_host=connection_manager.http_host,
            )
            logger.info(f"MonCore API 已在专用通道连接成功后初始化 (server_ip={connection_manager.server_ip}, http_port={connection_manager.http_port})")
        else:
            ctx.moncore_api.ws_client = ws_client
            ctx.moncore_api.server_ip = connection_manager.server_ip
            ctx.moncore_api.http_port = connection_manager.http_port
            ctx.moncore_api.http_host = connection_manager.http_host
            ctx.moncore_api.register_ws_handlers()
            logger.info(
                "MonCore API 已重新绑定到当前专用通道 "
                f"(server_ip={connection_manager.server_ip}, http_port={connection_manager.http_port})"
            )
    else:
        logger.warning("WebSocket 客户端未就绪，MonCoreAPI 初始化延迟")
    _ensure_bot_info_sync_task()
    asyncio.create_task(sync_bot_info_once("registered"))


connection_manager.register_on_registered(_on_registered_callback)


async def sync_bot_info_once(reason: str = "manual") -> bool:
    """从 NapCat 拉取机器人资料、好友和群聊并上报 MonCore。"""
    if not is_moncore_ready():
        logger.debug(f"跳过 Bot 信息同步，MonCore 未就绪: {reason}")
        return False
    if not napcat_api.bot:
        logger.debug(f"跳过 Bot 信息同步，NapCat bot 未就绪: {reason}")
        return False

    contacts = None
    groups = None
    nickname = None
    avatar_url = None
    signature = None

    try:
        login_info = await asyncio.wait_for(napcat_api.get_bot_login_info(), timeout=5.0)
        if login_info:
            nickname = str(login_info.get("nickname") or "").strip() or None
            qq_number = str(login_info.get("user_id") or login_info.get("userId") or "").strip()
            if qq_number:
                # 版本参数绕过浏览器对固定 QQ 头像 URL 的旧缓存。
                avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq_number}&s=100&v={int(time.time())}"
                signature = await asyncio.wait_for(napcat_api.get_bot_signature(qq_number), timeout=5.0)
            if nickname:
                sync_runtime_bot_name(nickname)
    except Exception as e:
        logger.warning(f"Bot 信息同步获取账号资料失败: {e}")
    try:
        next_contacts = await asyncio.wait_for(napcat_api.get_friend_list(), timeout=5.0)
        if next_contacts:
            contacts = next_contacts
        else:
            logger.warning(f"Bot 信息同步拿到空好友列表，跳过覆盖: {reason}")
    except Exception as e:
        logger.warning(f"Bot 信息同步获取好友列表失败: {e}")

    try:
        next_groups = await asyncio.wait_for(napcat_api.get_group_list(), timeout=5.0)
        if next_groups:
            groups = next_groups
        else:
            logger.warning(f"Bot 信息同步拿到空群聊列表，跳过覆盖: {reason}")
    except Exception as e:
        logger.warning(f"Bot 信息同步获取群聊列表失败: {e}")

    if nickname is None and avatar_url is None and signature is None and contacts is None and groups is None:
        logger.debug(f"Bot 信息同步无有效资料，跳过上报: {reason}")
        return False

    ws_client = connection_manager.get_ws_client()
    if not ws_client:
        logger.debug(f"跳过 Bot 信息同步，WebSocket 未就绪: {reason}")
        return False

    return await ws_client.send_bot_info(
        nickname=nickname,
        avatar_url=avatar_url,
        signature=signature,
        contacts=contacts,
        groups=groups,
    )


async def _bot_info_sync_loop():
    """定时同步好友/群聊列表，覆盖启动后新增好友或新增群聊的场景。"""
    while True:
        try:
            await asyncio.sleep(max(30.0, _BOT_INFO_SYNC_INTERVAL))
            await sync_bot_info_once("periodic")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Bot 信息定时同步失败: {e}", exc_info=True)


def _ensure_bot_info_sync_task():
    global _bot_info_sync_task
    if _bot_info_sync_task and not _bot_info_sync_task.done():
        return
    _bot_info_sync_task = asyncio.create_task(_bot_info_sync_loop())
    logger.info(f"Bot 信息定时同步已启动: interval={max(30.0, _BOT_INFO_SYNC_INTERVAL):.0f}s")

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
            connection_manager.schedule_registration_recovery()
    else:
        logger.info("MonCore 已连接，跳过重复连接")


@get_driver().on_shutdown
async def shutdown_disconnect_moncore():
    """关闭时断开 MonCore 连接"""
    global _bot_info_sync_task
    if _bot_info_sync_task and not _bot_info_sync_task.done():
        _bot_info_sync_task.cancel()
        _bot_info_sync_task = None
    logger.info("正在断开 MonCore 连接...")
    await connection_manager.stop()
    logger.info("MonCore 连接已断开")
