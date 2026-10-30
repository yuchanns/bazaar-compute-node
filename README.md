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

Python 3.15+ is required.

需要 Python 3.15 或更高版本：

```sh
uv tool install bazaar-compute-node
bcn contrib add codex --enable
bcn contrib add telegram --enable
bcn agent add \
  --name Tifa \
  --channel telegram \
  --harness codex \
  --set channel.token_env=BCN_TELEGRAM_TIFA_TOKEN

# Set BCN_TELEGRAM_TIFA_TOKEN in the shell, then start the node.
bcn run
```

Contrib packages and enabled entries are saved in `~/.bcn/config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-logging==0.3.*"
[[contribs.enabled]]
entry = "logging"
[contribs.enabled.config]
level = "info"
```

Use `bcn --configer-options ./config.toml run` to select another file.
See [agent commands](contrib/agent-command/README.md) for conversation approval.

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

This project is licensed under the [Apache License 2.0](./LICENSE).
本项目采用 [Apache License 2.0](./LICENSE) 许可证。

## Credits / 致谢

This project is inspired by [raft.build](https://raft.build).
本项目的灵感来自 [raft.build](https://raft.build)。
