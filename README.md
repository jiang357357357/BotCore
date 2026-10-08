# MonBot

## NapCat 扩展能力

扩展清单按 NapCat **v4.18.28** 源码核对，实际可执行能力取决于安装版本与 QQ 账号权限。需同步更新 BotCore、MonCore；智能体调用还需更新 AgentServer。此功能不自动升级 NapCat。

本 Bot 的个人超级管理员可在私聊中使用：

```text
/QQ能力
/QQ能力 set_group_ban
/QQ事件 request
/QQ自动化
/QQ操作 friend_poke {"user_id":"123456789"}
/QQ操作 set_msg_emoji_like {"message_id":"12345","emoji_id":"128077","set":true}
/QQ操作 set_group_ban {"group_id":"123456789","user_id":"987654321","duration":60}
/QQ操作 delete_qzone_msg {"tid":"发布时返回的说说ID"}
/QQ操作 send_qzone_msg {"content":"一条动态","ugc_right":4}
/QQ操作 get_group_msg_history {"group_id":"123456789","count":20}
```

`/QQ能力 操作名` 返回参数 schema。支持消息/合并转发收发、单条原样转发、原生历史查询、撤回、输入状态/已读、戳一戳/表情回应与明细、收藏表情管理、好友分类与最近联系人、好友与入群申请、群高级设置/公告/精华/签到、群文件、账号资料、OCR/QQ AI 语音、空间发布/删除、群相册/待办、QQ 收藏、在线文件/文件夹与闪传。任意原始 API、凭据读取、任意本地路径和任意 JSON/XML 消息不在开放范围内。

`send_contact_card` 与 `send_miniapp_card` 会生成并实际发送受限名片/小程序卡片；名片来源和收件会话均须授权。上游 `send_ark_share`、`send_group_ark_share`、`get_mini_app_ark` 只生成卡片数据。附件读取须提供 `source_message_id` 与该消息实际包含的 `file_id`，不能通过伪造文件 ID 读取其他会话附件。

频道列表/资料、在线客户端列表在 v4.18.28 上游仍未完整实现；四个流式传输接口需要专用分片传输，当前目录明确标为不可用。`can_send_image`/`can_send_record` 在上游固定返回支持，不能用作真实发送验证。`set_restart`、`bot_exit`、`clean_cache` 为显式运维动作，断链或清理结果无法确认时返回 `unknown`。

普通聊天收到语音时自动尝试转写，收到合并转发时提取最多 30 条引用预览；解析失败仍保留原文字与附件占位。存储与回复复用解析结果，扩展预览受 Agent 的 4000 字消息上限约束，截断会明确标记。三个环境开关默认开启，可设 `false` 关闭：`MON_QQBOT_TRANSCRIBE_VOICE`、`MON_QQBOT_EXPAND_FORWARD`、`MON_QQBOT_INPUT_FEEDBACK`（私聊输入状态提示）。

Agent 提供 `list_qq_capabilities`、`read_qq_action`、`execute_qq_action`、`list_qq_events` 工具；沿用 `/权限` 和 `/审批` 的现有机制。QQ 会话身份来自 Agent 持久会话，MonCore 再校验本 Bot 个人超级管理员；本地 `superusers` 不能绕过它。目标好友/群仍须获准访问，消息撤回/表情/引用校验消息所属会话。好友和入群申请仅允许处理后端已收到的同 Bot 申请；批准申请不会自动授予聊天准入。

管理接口（`<ID>` 为机器人数据库 ID，要求所有者或全局管理员登录）：

```text
GET  /api/devices/qq_bot/<ID>/actions/
GET  /api/devices/qq_bot/<ID>/actions/?action=set_group_ban
POST /api/devices/qq_bot/<ID>/actions/execute/
GET  /api/devices/qq_bot/<ID>/events/?type=request&limit=20
GET  /api/devices/qq_bot/<ID>/automation/
PUT  /api/devices/qq_bot/<ID>/automation/
```

执行正文：`{"action":"操作名","params":{},"request_id":"可选的唯一请求ID"}`。结果区分 `success`、`failed`、`unknown`；结果不明时先核查 QQ 状态，不能直接重试。BotCore 当前实例保留最近 256 个操作结果，不保证跨重启去重。API 以 `bot + request_id + action` 关联回执并校验设备凭据，耗时操作不阻塞聊天收包。HTTP 请求仍须进入持有对应 BotCore WebSocket 的 Core 进程。

通知/申请及生命周期事件写入 Core 数据库，每 Bot 保留最多 4096 条、7 天，支持类型筛选、游标翻页和自动处理结果查询。BotCore 在设备状态目录（默认 `.run/qqbot`，可由 `MON_QQBOT_STATE_DIR` 指定）保存 SQLite 待发队列；断线、重启后补发，收到匹配 QQ 账号与事件 ID 的持久化 ACK 后确认完成。每账号最多 4096 条待发、保留 7 天；队列满或磁盘写入失败会记录错误。补发仅适用于接收事件，QQ 修改操作结果不明时仍不重试。心跳不会逐条入库。

在线文件、文件夹和闪传创建使用 HTTP(S) 文件中转，参数以能力 schema 为准，单文件最多 8 MiB、每批最多 4 个；自有临时文件保留 24 小时以支持异步传输，并在后续操作时清理过期项。在线文件夹根据提供的 URL 与文件名列表构建受控临时目录。

### 自动化与群聊智能体

个人超级管理员可在已授权群内直接发送 `/模式 智能体` 开启本群、`/模式 普通` 关闭本群、`/模式` 查询当前群模式；也支持在命令前 @机器人。开启前检查机器人绑定助手及可用模型，检查失败保持原模式。设置复用本 Bot、本群的 `group_agent` 规则，重启后保留，不修改其他群、私聊或自动化规则。新建群策略默认 `allow_actions:false`，已有操作权限保持原配置；仅 Web 中本机器人的个人超级管理员可切换，群管理员身份不会自动授予该权限。

部署时须应用 Core 的新迁移 `0024_napcat_durable_events`，AgentServer 启动时自动应用群会话表迁移。所有自动化默认关闭。先用 `/QQ自动化` 或 HTTP GET 读取规则，修改后用 `/QQ自动化 {"rules":[...]}` 或 HTTP PUT 提交完整规则列表；提交会替换该 Bot 的全部规则，`{"rules":[]}` 关闭全部规则。每条规则必须命名并明确一个 QQ 号或群号，不接受通配目标。

例如，仅开启指定群的智能体查询（请把示例群号换成已授权群；已有规则须一并保留）：

```text
/QQ自动化 {"rules":[{"name":"group-agent","kind":"group_agent","enabled":true,"target_type":"group","target_id":"123456789","options":{"allow_actions":false}}]}
```

群智能体仍只由本 Bot 的个人超级管理员通过 @、名字或关键词触发；群本身也必须获准访问。会话按 Bot、群、发言人分开保存，不使用私聊角色记忆。默认仅开放当前群的 QQ 查询；规则 `allow_actions:true` 额外授权当前群的修改操作，Core 每次重新检查当前策略。群会话不提供本地文件、命令执行、邮箱或跨会话查询工具，不在群中输出私聊审批详情。群帮助、进度和最终回复支持图片卡片，发送失败退回原群文字。

规则类型与 `options`：

| kind | target_type | options 示例与行为 |
| --- | --- | --- |
| `poke_reply` | `user` / `group` | `{"mode":"static","message":"我在。"}` 固定回复；`{"mode":"agent"}` 唤醒智能体，触发人必须是本 Bot 的个人超级管理员，群内还须启用 `group_agent` |
| `welcome` | `group` | `{"message":"欢迎 {user_id} 加入 {group_id}。"}`，不会欢迎机器人自己 |
| `friend_request` | `user` | `{"comment_equals":"约定备注"}` 仅批准该 QQ 且备注完全相符的申请；省略备注条件时仍只匹配指定 QQ |
| `group_request` | `group` | `{"user_ids":["987654321"],"comment_equals":"约定备注"}`；必须列出允许的申请人／邀请人 |
| `group_file_notice` | `group` | `{"message":"收到 {user_id} 上传的 {file_name}，大小 {file_size} 字节。"}`；仅文件通知，尚不自动下载或解析文件内容 |
| `group_agent` | `group` | `{"allow_actions":false}` 群内查询；`true` 另行开放同群操作 |
| `profile_sync` | `account` | `{"fields":["nickname","signature","avatar"]}` 同步绑定角色选定资料 |

规则可选 `cooldown_seconds`（0–3600，默认 60），普通通知超过 10 分钟、申请超过 24 小时只保存、不自动处理。事件行为先持久记录，再单次执行；处理中断或回执未知会记为 `unknown`，不自动重做。Core 重启后，BotCore 会在连接恢复时及每 60 秒发送经过设备认证的 `napcatEventResume`，恢复已入库但尚未领取的任务，不依赖新的 QQ 通知。自动批准申请不会添加聊天准入权限。

角色资料同步规则使用 `kind:"profile_sync"`、`target_type:"account"`、`target_id:"机器人QQ号"`、`options:{"fields":["nickname","signature","avatar"]}`。显式开启后及绑定角色发生变化时同步所选字段，仅读取所属用户当前绑定角色；头像最多 1 MiB，支持 PNG/JPEG/WebP。查询 `/QQ自动化` 可看到最近同步结果，失败字段不会自动重试。

## 统一 QQ 命令

所有命令共用 `qqCommand` 协议 1。Core 的 `Application/Domain/BOT/Core/command_registry.py` 是名称、别名、用法和权限的唯一目录；`command_service.py` 负责校验、授权与执行，`/帮助` 自动使用这个目录。BotCore 只有一个 `on_message` 命令 matcher，不维护角色或命令名单。前缀固定为 `/`、`!`、`！`；旧 `command_prefix`、`enable_global_commands`、`available_commands` 配置忽略且不再保存。

| 用途 | 命令 | Core 权限与范围 |
| --- | --- | --- |
| 帮助和角色 | `/帮助`（`/help`）、`/角色`（`/设定`） | 已授权私聊或群聊 |
| 语音查询 | `/语音` | 已授权私聊或群聊 |
| 修改全局语音 | `/语音 开启`、`/语音 关闭` | 个人管理员或超级管理员；私聊或群聊 |
| 会话记忆 | `/记忆 [@用户或QQ号]`（`/记忆列表`） | 查询他人需要个人管理员或超级管理员 |
| 会话模式 | `/模式`、`/模式 智能体`、`/模式 普通` | 个人超级管理员；私聊或群聊，群内仅作用于本群 |
| 智能体管理 | `/状态`、`/权限`、`/审批` | 个人超级管理员；仅私聊 |
| 空间与 QQ 管理 | `/说说`、`/QQ能力`、`/QQ操作`、`/QQ事件`、`/QQ自动化` | 个人超级管理员；仅私聊 |
| 历史兼容 | `/好感`、`/好感排行`（`/好感榜`） | 已授权会话，返回功能停用提示 |

权限实时读取 Web 中当前 Bot 的 QQ 授权。超级管理员包含管理员权限，群授权不会提升成员的个人权限；显式用户或群拉黑优先。未知命令和拒绝结果不会落入普通聊天。图片回复失败时发送同一结果的文字。

Core 连接确认声明 `command_protocol:1`。请求包含 `request_id`、`name`、`arguments`、`mentions`、`argument_types`、发送人/群身份、语音状态、引用消息 ID 和已配对设备凭据；回执统一为 `success`、`failed` 或 `unknown`，并携带 `code`、`content` 和可选卡片。语音设置只有成功回执中的 `runtime_action` 可修改，配置保存失败会单独提示。发送中断和超时不会自动重试，先核对实际状态再发新请求。

Core 同时处理最多 4 条命令，每个连接缓存最近 256 条回执。同一请求不会重复执行，改变参数会被拒绝，重放前重新检查权限；去重不跨连接或重启。`qzonePublishHost`、`napcatActionHost` 及其设备认证回执继续承担 Core 授权后的执行；旧 `role`、`memory`、`favorability`、`qzonePublish`、`napcatAction` 用户请求均已停用。必须同步更新 Core 与 BotCore，旧版本提示升级，不回退到另一套命令机制。

验证：BotCore 执行 `python -B tests/run_contracts.py`（临时运行目录），Core 执行 `.venv/Scripts/python.exe -B Application/Domain/BOT/tests/run_napcat_contracts.py`（隔离数据库）。均不连接真实 QQ。

## QQ 空间发布

需要同时更新 MonCore，并使用 NapCat **v4.18.14+**。在机器人私聊中发送：

```text
/说说 正文
/说说 --公开 正文
/说说 --仅自己 正文
```

默认好友可见，支持 `/发动态`、`/发说说` 别名。MonCore 校验设备凭据及当前 Bot 的个人超级管理员权限；仅本地 NoneBot `superusers` 配置不能授予此权限。

命令支持 1 至 2000 字纯文本。图文发布使用已登录的 MonCore 接口 `POST /api/devices/qq_bot/<数据库ID>/qzone/publish/`，正文 `content`、可选 `images`（最多 9 个 HTTP(S) URL）、`ugc_right`（1/4/64，默认 4）。接口仅允许机器人所有者或全局管理员调用。

只有收到有效说说 ID 才确认发布成功；结果未知时请先查看 QQ 空间，不要立即重发。发布请求不自动重试，BotCore 当前 API 实例缓存最近 256 个请求结果，不保证跨重启去重。QQ 空间发布在后台任务执行，同时只允许一条在途发布，不阻塞普通消息接收。

协议：私聊命令发送 `qqCommand` → MonCore 校验权限 → `qzonePublishHost` → NapCat `send_qzone_msg` → `qzonePublishBot` → 原请求结果。新增客户端请求/回执均携带已配对设备凭据；日志不输出凭据。后端回执按 Bot 与请求 ID 关联。多进程部署时 HTTP 请求须进入持有 BotCore WebSocket 的进程。

上游接口：[NapCat SendQzoneMsg](https://github.com/NapNeko/NapCatQQ/blob/v4.18.14/packages/napcat-onebot/action/extends/SendQzoneMsg.ts)。本文中的字数、图数和可见范围是本项目首版约束。

## How to start

1. generate project using `nb create` .
2. create your plugin using `nb plugin create` .
3. writing your plugins under `src/plugins` folder.
4. run your bot using `nb run --reload` .

## Documentation

See [Docs](https://nonebot.dev/)
