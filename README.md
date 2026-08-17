# Bazaar Compute Node

Collaborate with your Agents in the bazaar across compute nodes, through any interface, with any harness runtime.
在集市里与你的 Agent 们合作，透过任意界面与任意 Harness。

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

Python 3.14+ is required:

```sh
# Install
uv tool install bazaar-compute-node
bcn --version

# Upgrade
uv tool upgrade bazaar-compute-node

# Start daemon
export BCN_WECOM_BOT_SECRET='your-bot-secret'

bcn start --channel wecom --runtime codex \
  --model gpt-5.6-luna --effort max \
  --sandbox-mode workspace-write \
  --network-access true \
  --idle-timeout 600

# Stop daemon
bcn stop
```

## Features / 功能

- ✅ Done / 已完成
- 🚧 Working in progress / 开发中

### Facilities / 通用能力

| Status / 状态 | Ability / 能力 |
| --- | --- |
| ✅ | Reminders / 定时器 |
| ✅ | Attachments / 附件 |
| 🚧 | Multi-Agents / 多 Agent |
| 🚧 | Teams / 团队协作 |

### Channels / 渠道

| Status / 状态 | Channel / 渠道 |
| --- | --- |
| ✅ | WeCom / 企业微信 |
| ✅ | Telegram |
| 🚧 | GitHub |
| 🚧 | GitLab |

### Harnesses

| Status / 状态 | Runtime / 运行时 |
| --- | --- |
| ✅ | Codex |
| 🚧 | Claude Code |
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
