# Bazaar Compute Node

Bring Your Own Agents to the bazaar, and build with your teams in the open.

不必独自建造——带上你的智能体，来集市。

<p align="left">
  <a href="https://github.com/yuchanns/bazaar-compute-node/actions"><img
    src="https://github.com/yuchanns/bazaar-compute-node/actions/workflows/release.yml/badge.svg"
    alt="Release"
  /></a>
  <a href="https://pypi.org/project/bazaar-compute-node"><img
    src="https://img.shields.io/pypi/v/bazaar-compute-node.svg"
    alt="Python Package Index"
  /></a>
</p>

**Documentation is coming soon...**

## Quick Start / 快速开始

Python 3.14+ is required.

需要 Python 3.14 或更高版本：

```sh
# Install / 安装
uv tool install bazaar-compute-node
bcn --version

# Upgrade / 升级
uv tool upgrade bazaar-compute-node

# Add an Agent / 添加智能体
# allowed_chats lists the chats let in by name: Telegram chat ids (a private
# chat's id is the other side's user id). Others wait to be reviewed on the
# server; with no server and no list, everyone is let in
# allowed_chats 是按名放行的会话：Telegram 的 chat id（私聊的 chat id 就是对方
# 的 user id）。其余的等服务端审核；没接服务端也没写名单时，谁来都放行
bcn agent add \
  --name Tifa \
  --channel telegram \
  --runtime codex \
  --set channel.token_env=BCN_TELEGRAM_TIFA_TOKEN \
  --set channel.allowed_chats=["123456789"]

# Configure the runtime environment / 配置运行时环境
# Example file content / 示例文件内容:
# BCN_TELEGRAM_TIFA_TOKEN=replace-with-your-token

# Register the user-level system service / 注册用户级宿主机服务
bcn system-service install --env-file ~/.config/bcn/runtime.env

# Start the registered service / 启动已注册的服务
bcn system-service start

# Inspect registration and bcn health / 查看注册状态和 bcn 运行状态
bcn system-service status

# Restart or stop the registered service / 重启或停止已注册的服务
bcn system-service restart
bcn system-service stop
```

## Service logs / 服务日志

```text
Linux:   journalctl --user -u bcn.service -f
macOS:   tail -f ~/.bcn/system-service.log
Windows: Get-Content "$env:USERPROFILE\.bcn\system-service.log" -Wait
```

## Features / 功能

- ✅ Done / 已完成
- 🚧 Working in progress / 开发中

### Facilities / 通用能力

| Status / 状态 | Ability / 能力 |
| --- | --- |
| ✅ | Reminders / 定时器 |
| ✅ | Attachments / 附件 |
| ✅ | Multi-Agents / 多智能体 |
| ✅ | Teams / 团队协作 |

### Channels / 渠道

| Status / 状态 | Channel / 渠道 |
| --- | --- |
| ✅ | WeCom / 企业微信 |
| ✅ | Telegram |
| ✅ | Lark / 飞书 |
| 🚧 | GitHub |
| 🚧 | GitLab |

### Harnesses

| Status / 状态 | Runtime / 运行时 |
| --- | --- |
| ✅ | Codex |
| ✅ | Claude Code |
| 🚧 | Pi |
| 🚧 | Hermes Agent |
| 🚧 | DeepSeek Harness |

### Platforms / 平台

| Status / 状态 | Platform / 平台 |
| --- | --- |
| ✅ | Windows |
| ✅ | macOS |
| ✅ | Linux |

## License / 许可证

This project is licensed under the [AGPLv3](./LICENSE).
本项目采用 [AGPLv3](./LICENSE) 许可证。

## Credits / 致谢

This project is inspired by [raft.build](https://raft.build).
本项目的灵感来自 [raft.build](https://raft.build)。
