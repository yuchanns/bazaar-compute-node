# Bazaar Compute Node

Bazaar Compute Node（`bcn`）是一个可组合的 Agent 计算节点。用户通过 Channel 与
Agent Runtime 沟通：Channel 提供输入任务和接收结果的界面，Runtime 是用户选择的 Agent
Harness 工具。

不同的 Channel 与 Runtime 可以自由组合和扩展，不绑定具体供应商或交互形态。

## 核心能力

- **自由组合**：根据使用场景分别选择 Channel 与 Runtime，并可独立替换和扩展。
- **持续会话**：每个用户和对话拥有独立上下文，节点重启后仍可继续之前的任务。
- **真实工作区**：Runtime 可以在独立工作区中分析、创建和修改文件，而不局限于文本回答。
- **双向附件**：Channel 收到的媒体会进入会话工作区，Runtime 也可以通过 Channel 交付工作区中的文件。
- **可靠交付**：自动处理长结果的分批发送并记录交付状态，避免任务结果静默丢失。
- **安全边界**：复用所选 Harness 的权限机制，按任务需要控制文件与网络访问。
- **跨平台运行**：支持 Linux、macOS 和 Windows，并以后台进程长期运行。

## 当前支持

当前版本提供首组可用组合：

- **WeCom Channel**：通过企业微信与 Agent Runtime 持续沟通；
- **Codex Runtime**：使用 Codex 作为 Agent Harness 工具完成任务。

## Roadmap

bcn 将沿着可组合的 Channel、Runtime 与节点通用能力继续扩展。

### 更多 Harness

- 一个节点允许添加多个 Agents
- Agent Teams 合作

### 更多 Channel

- **Telegram**：通过 Telegram 与 Agent 交互。
- **GitHub**: 通过 GitHub Issue 与 Agent Harness 进行开发。
- **GitLab**: 将 Agent Harness 引入企业私有仓库与团队进行开发。

### 更多 Runtime

- **Claude Code**：支持选择 Claude Code 作为 Agent Harness；
- **pi**：支持选择 pi 作为 Agent Harness。

### 节点能力

- **定时任务**：创建一次性或周期性任务，由节点按计划自动执行并返回结果。

## 安装

项目通过 PyPI 分发，要求 Python 3.14 或更高版本：

```bash
uv tool install bazaar-compute-node
bcn --version
```

升级到 PyPI 上的最新正式版本：

```bash
uv tool upgrade bazaar-compute-node
```

也可以直接从 GitHub 运行尚未发布的源码：

```bash
uvx --from 'git+https://github.com/yuchanns/bazaar-compute-node@main' bcn --version
```

生产环境建议将 `@main` 替换为固定 tag 或 commit，避免运行版本随分支更新。

## Channel

Channel 是用户输入任务、接收结果，并与 Agent Runtime 持续沟通的界面。启动节点时选择一种
Channel 即可。

### WeCom

WeCom Channel 对接企业微信智能机器人，支持单聊、群聊、文本与媒体输入，以及 Markdown
长消息分批和附件返回。收到的媒体会以工作区相对路径提供给 Runtime；Runtime 可以把工作区内的
普通文件作为附件发送，附件路径不能包含符号链接。群聊中需要 @机器人，确保消息能够进入节点。

在 `~/.bcn/config.toml` 中配置 Bot ID：

```toml
[channel.wecom]
bot_id = "your-bot-id"
```

Bot Secret 不写入配置文件，只通过环境变量提供：

```bash
export BCN_WECOM_BOT_SECRET='your-bot-secret'
```

PowerShell：

```powershell
$env:BCN_WECOM_BOT_SECRET = 'your-bot-secret'
```

## Runtime

Runtime 是用户选择的 Agent Harness 工具，用来运行 Agent 并完成任务。启动节点时选择一种
Runtime 即可。

### Codex

使用 Codex Runtime 前，需要先安装并登录
[Codex CLI](https://developers.openai.com/codex/cli/)。

默认配置只允许 Codex 写入当前会话的工作区，并允许其命令访问网络：

```toml
[runtime]
sandbox_mode = "workspace-write"
network_access = true
idle_timeout = 0
```

如需覆盖 Codex 的默认模型或推理强度，可以在 `[runtime]` 中增加 `model` 和 `effort`。
`idle_timeout` 以秒为单位并支持小数；默认值 `0` 与所有 `<= 0` 的值表示 runtime session
常驻。设置为正值后，每条新收到的消息都会刷新空闲回收期限。
除非你明确需要使用运行用户已有的主机权限，否则不要将 `sandbox_mode` 改为
`danger-full-access`。

## 启动节点

在 `~/.bcn/config.toml` 中选择要组合的 Channel 与 Runtime。以下配置使用当前已经落地的
WeCom + Codex 组合：

```toml
[node]
channel = "wecom"
runtime = "codex"
```

启动后台节点：

```bash
uvx --from 'git+https://github.com/yuchanns/bazaar-compute-node@main' bcn start
```

也可以不创建配置文件，直接通过 `--channel`、`--runtime`、`--model`、`--effort`、
`--sandbox-mode` 和 `--network-access` 等参数启动；命令行参数会覆盖配置文件中的对应值。

## 常用操作

```bash
# Stop the background node gracefully.
uvx --from 'git+https://github.com/yuchanns/bazaar-compute-node@main' bcn stop

# Restart the node after changing configuration.
uvx --from 'git+https://github.com/yuchanns/bazaar-compute-node@main' bcn restart

# Inspect available options.
uvx --from 'git+https://github.com/yuchanns/bazaar-compute-node@main' bcn --help
```

节点数据默认保存在 `~/.bcn`。可通过 `BCN_DATA_NAME` 指定 HOME 下的另一数据目录名称，
例如 `BCN_DATA_NAME=.bcn-test`；该值必须是单个目录名。停止节点会优雅关闭后台进程，
不会删除既有会话、工作区或消息记录。

## 许可证

本项目使用 [GNU Affero General Public License v3.0](LICENSE)。

## Credits

本项目的协作模型与 Agent Runtime 设计受到 [Raft.build](https://raft.build) 的启发，谨此致谢。
