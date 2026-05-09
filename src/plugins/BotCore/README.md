# BotCore 插件

MonBot 的核心业务插件，负责处理 QQ 消息、与 MonCore 后端通信、管理机器人状态。

## 架构概览

BotCore 采用分层架构，共 6 层：

```
Layer 0: NoneBot2 框架层（外部依赖）
Layer 1: 生命周期 & 单例管理层（app.py）
Layer 2: 配置层（config/）
Layer 3: 路由层（core/router/）
Layer 4: 业务逻辑层（core/business/）
Layer 5: 外部服务层（external/）
Layer 6: 工具层（core/utils/）
```

## 目录结构

```
BotCore/
├── __init__.py                 # 插件注册点
├── app.py                      # 入口 + 全局单例管理
│
├── config/                     # Layer 2: 配置层
│   ├── bot_config.py          # BotConfig 类定义
│   └── config.json            # 配置文件
│
├── core/                       # 核心业务
│   ├── router/                # Layer 3: 路由层
│   │   ├── commands.py        # 命令路由（/帮助 /角色 /语音）
│   │   └── message_handlers.py # 消息路由（关键词/普通消息）
│   │
│   ├── business/              # Layer 4: 业务逻辑层
│   │   ├── command/
│   │   │   └── command_service.py      # 命令业务处理
│   │   └── message/
│   │       ├── group_message_service.py    # 群聊消息业务
│   │       └── private_message_service.py  # 私聊消息业务
│   │
│   └── utils/                 # Layer 6: 工具层
│       └── audio_utils.py     # 音频下载/清理
│
├── external/                   # Layer 5: 外部服务层
│   ├── monCore/               # MonCore 后端通信
│   │   ├── connection_manager.py  # 连接管理器
│   │   ├── api/
│   │   │   └── moncore_api.py     # 后端 API 封装
│   │   └── client/
│   │       ├── udp.py             # UDP 服务发现
│   │       └── ws/
│   │           ├── websocket.py   # WebSocket 客户端
│   │           └── callback_handler.py  # 连接回调处理
│   │
│   ├── napcat/                # NapCat QQ API
│   │   └── api/
│   │       └── api.py         # QQ API 封装
│   │
│   ├── config.py              # 外部配置管理（未使用）
│   └── storage.py             # 数据存储（未使用）
│
└── plugins/                    # 旧插件层（死代码，待删除）
    ├── command/
    ├── text/
    ├── voice_plugin.py
    └── image_plugin.py
```

## 各层职责

### Layer 1: app.py - 生命周期 & 单例管理

**职责**：
- 插件启动/关闭钩子（`on_bot_connect` / `on_shutdown`）
- 全局单例持有（`bot_config` / `napcat_api` / `moncore_api` 等）
- 全局状态持有（白名单 / 语音模式开关）
- 对外暴露 getter/setter 函数

**关键变量**：
```python
bot_config: BotConfig              # 机器人配置
storage: Storage                   # 数据存储（未使用）
napcat_api: NapCatAPI             # QQ API 服务
connection_manager: ConnectionManager  # 连接管理器
moncore_api: MonCoreAPI           # 后端 API（延迟初始化）

supported_contacts: list[str]     # 后端支持的联系人白名单
supported_groups: list[str]       # 后端支持的群聊白名单
supported_keywords: list[str]     # 后端支持的关键词列表
voice_mode_enabled: bool          # 语音模式开关
```

### Layer 2: config/ - 配置层

**职责**：
- 定义机器人配置结构（`BotConfig` 类）
- 从 `config.json` 加载配置
- 支持序列化/反序列化

**配置项**：
- 机器人名字 / 昵称
- 命令前缀
- 触发规则（`enable_mention_reply` / `enable_name_mention`）
- 关键词回复

### Layer 3: core/router/ - 路由层

**职责**：
- 注册 NoneBot 事件处理器（`on_command` / `on_message`）
- 命令解析 & 分发
- 白名单过滤
- 关键词触发判断
- 群聊/私聊分流

**原则**：
- 只做"收到什么 → 交给谁"
- 不包含业务逻辑
- 只记录日志，不处理数据

**文件**：
- `commands.py`：处理命令消息（`/帮助` / `/角色` / `/语音`）
- `message_handlers.py`：处理普通消息（关键词触发 / 普通消息）

### Layer 4: core/business/ - 业务逻辑层

**职责**：
- 纯业务逻辑处理
- 不直接接触 NoneBot 事件对象
- 调用外部服务层完成功能

**服务**：
- `CommandService`：命令业务处理
  - 生成帮助文本
  - 获取角色信息
  - 语音模式开关
- `GroupMessageService`：群聊消息业务
  - 存储消息到后端
  - 请求 AI 回复
  - 语音/文本降级处理
- `PrivateMessageService`：私聊消息业务
  - 存储消息到后端
  - 请求 AI 回复（私聊必回）
  - 语音/文本降级处理

### Layer 5: external/ - 外部服务层

**职责**：
- 封装外部服务 API
- 屏蔽底层实现细节
- 提供统一接口

**服务**：

#### monCore/ - MonCore 后端通信

- `ConnectionManager`：连接管理器
  - UDP 服务发现
  - WebSocket 登录注册
  - 专用通道连接
  - 重连机制
- `MonCoreAPI`：后端 API 封装
  - `store_message()`：存储消息
  - `request_reply()`：请求回复
  - 接收后端推送（白名单更新）
- `WebSocketClient`：WebSocket 客户端
  - 连接/断线/重连
  - handler 注册
  - 消息收发
- `UDPDiscovery`：UDP 服务发现
  - 广播发现服务器 IP

#### napcat/ - NapCat QQ API

- `NapCatAPI`：QQ API 封装
  - `get_friend_list()`：获取好友列表
  - `get_group_list()`：获取群列表
  - `get_login_info()`：获取登录信息
  - `get_user_avatar_url()`：生成用户头像 URL
  - `get_group_avatar_url()`：生成群头像 URL

### Layer 6: core/utils/ - 工具层

**职责**：
- 无状态纯函数
- 不依赖任何业务对象

**工具**：
- `audio_utils.py`：音频处理
  - `download_audio()`：异步 HTTP 下载音频
  - `cleanup_audio_file()`：清理临时文件

## 核心流程

### 启动流程

```
bot.py
  → nonebot.load_plugins("src/plugins")
  → BotCore/__init__.py
      → import app
          → 实例化全局单例
          → 注册 on_bot_connect 钩子
          → 导入 commands / message_handlers
              → 注册 NoneBot 事件处理器

NoneBot 启动
  → Bot 连接成功
      → on_bot_connect 触发
          → napcat_api.set_bot(bot)
          → connection_manager.start()
              → UDP 广播发现服务器
              → WebSocket 连接登录端点
              → 发送注册包（QQ号 + 好友 + 群）
              → 收到专用通道 URL
              → 切换到专用 WebSocket
              → MonCoreAPI 初始化
              → 注册 mappingHost / keywordsHost 处理器
```

### 消息处理流程

```
收到 QQ 消息
  → NoneBot 事件分发
      → 命令消息（/开头）
          → commands.py
              → CommandService
                  → /帮助：动态获取命令列表
                  → /角色：调用后端获取角色信息
                  → /语音：切换 voice_mode_enabled
      
      → 普通消息
          → message_handlers.py
              → _is_supported_by_backend()  # 白名单过滤
                  ✗ 不在白名单 → 丢弃
                  ✓ 在白名单 → 继续
              
              → _is_keyword_trigger()  # 触发判断
                  群聊：@机器人 / 名字 / 后端关键词
                  私聊：名字 / 后端关键词
              
              → 关键词触发
                  → GroupMessageService / PrivateMessageService
                      → moncore_api.store_message()  # 存储
                      → moncore_api.request_reply()  # 请求回复
                      → 有 audio_url + voice_mode 开启
                          → download_audio()
                          → MessageSegment.record()  # 发语音
                      → 否则发文本
              
              → 普通消息
                  → 群聊：只存储，不回复
                  → 私聊：存储 + 请求回复
```

### 后端通信流程

```
MonCoreAPI
  ├── store_message(event)
  │     → 构造消息数据（user_id, content, avatar_url）
  │     → ws_client.send_json({"action": "store", ...})
  │     → 等待 "store" 响应（最多 10 秒）
  │     → 返回成功/失败
  │
  ├── request_reply(event)
  │     → ws_client.send_json({"action": "reply", ...})
  │     → 等待 "reply" 响应（最多 30 秒）
  │     → 返回 {"content": "...", "audio_url": "..."}
  │
  └── 接收后端推送
        ├── mappingHost
        │     → 更新 supported_contacts / supported_groups
        │
        └── keywordsHost
              → 更新 supported_keywords
```

## 关键设计模式

### 单例模式

`app.py` 中所有服务都是模块级单例，全局共享：

```python
# 在 app.py 中定义
bot_config = BotConfig.load_from_file(config_path)
napcat_api = NapCatAPI()

# 在其他模块中导入
from ...app import bot_config, napcat_api
```

### 延迟初始化

`moncore_api` 初始为 `None`，在专用通道连接成功后才初始化：

```python
moncore_api: MonCoreAPI = None  # 初始为 None

async def _on_registered_callback():
    global moncore_api
    ws_client = connection_manager.get_ws_client()
    moncore_api = MonCoreAPI(ws_client, ...)
```

### 回调驱动

`ConnectionManager` 通过 `CallbackHandler` 处理连接事件：

```python
connection_manager.register_on_connected(callback)
connection_manager.register_on_registered(callback)
connection_manager.register_on_disconnected(callback)
```

### 白名单过滤

后端动态推送白名单，Bot 本身不维护配置：

```python
# 后端推送 mappingHost 消息
{
    "action": "mappingHost",
    "contacts": ["123456", "789012"],
    "groups": ["111111", "222222"]
}

# Bot 更新白名单
update_supported_contacts_and_groups(contacts, groups)
```

### 降级策略

语音发送失败时自动降级为文本：

```python
if voice_mode_enabled and audio_url:
    audio_path = await download_audio(audio_url)
    if audio_path:
        return Message(MessageSegment.record(audio_path))
    else:
        # 降级为文本
        return Message(content)
else:
    return Message(content)
```

## 依赖关系

```
Layer 1 (app.py)        → Layer 2 (config)
                        → Layer 5 (external)

Layer 3 (router)        → Layer 1 (app.py)
                        → Layer 4 (business)

Layer 4 (business)      → Layer 1 (app.py，延迟导入)
                        → Layer 5 (external)
                        → Layer 6 (utils)

Layer 5 (external)      → Layer 6 (utils)
                        → System/Logs

Layer 6 (utils)         → 无依赖
```

## 已知问题

| 问题 | 位置 | 影响 | 建议 |
|------|------|------|------|
| `plugins/` 下四个插件类 | `BotCore/plugins/` | 死代码，从未被调用 | 删除 |
| `Storage` 传入 Service 但未使用 | `message_handlers.py` | 冗余依赖 | 移除参数 |
| `external_config` 实例化但未使用 | `app.py` | 冗余 | 删除 |
| `moncore_api` 初始为 `None` | `app.py` | 连接前调用会静默失败 | 添加检查 |

## 开发指南

### 添加新命令

1. 在 `core/router/commands.py` 中注册命令：

```python
new_cmd = on_command("新命令", block=True)

@new_cmd.handle()
async def handle_new_cmd(event: MessageEvent):
    result = await command_service.handle_new_command(event)
    await new_cmd.finish(Message(result))
```

2. 在 `core/business/command/command_service.py` 中实现业务逻辑：

```python
async def handle_new_command(self, event: MessageEvent) -> str:
    # 业务逻辑
    return "处理结果"
```

### 添加新的消息处理逻辑

在 `core/business/message/` 下的 Service 中添加方法，然后在 `message_handlers.py` 中调用。

### 调用后端 API

```python
from ....app import get_moncore_api

moncore_api = get_moncore_api()
if moncore_api:
    result = await moncore_api.store_message(event)
```

### 调用 QQ API

```python
from ...app import napcat_api

if napcat_api and napcat_api.bot:
    friend_list = await napcat_api.get_friend_list()
```

## 注意事项

1. 所有全局单例都从 `app.py` 导入，不要重复实例化
2. 业务逻辑层不应该直接接触 NoneBot 事件对象
3. 外部服务层要做好异常处理，不要让异常传播到上层
4. 延迟导入 `app.py` 时使用函数内导入，避免循环依赖
5. 日志统一使用 `from src.System.Logs import get_logger`
