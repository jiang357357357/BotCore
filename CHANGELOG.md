# 更新日志

本文件记录 BotCore 的显著变化。

## [Unreleased]

## [1.9.1] - 2026-08-29

### Changed

- 同步 Eden `1.9.1` 产品版本，保持现有 OneBot 和 MonCore 调用契约不变。

### Fixed

- 补齐 BotCore 独立运行时已直接导入的 `websockets` 依赖，避免脱离父启动器环境时导入失败。

## [1.9.0] - 2026-08-27

### Changed

- 移除 MonHub 发现链路，BotCore 改为通过固定本机地址直连 MonCore，并统一模块 `.monconfig` 加载边界。
- OneBot WebSocket 地址和访问令牌支持由工作区私有环境配置覆盖，运行目录不再污染 Git 状态。
- 设备身份存储会沿模块配置祖先定位 Mon 工作区根目录，统一复用根 `.run/qqbot` 中的长期凭证。

## [1.8.0] - 2026-08-05

### Changed

- 同步 BotCore 产品元数据并建立 `1.8.0` 完整发行源码基线。

## [1.7.5] - 2026-08-04

### Changed

- Python 包和锁文件统一到 Mon `1.7.5` 产品版本基线。
