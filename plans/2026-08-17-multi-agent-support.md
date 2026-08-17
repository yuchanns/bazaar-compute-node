# BCN 多 Agent 单进程支持

## 状态

- 当前阶段：设计讨论完成，implementation 尚未开始。
- 实施分支：`f-20260817-multi-agent-support`。
- 基线：`main@889afc3705ab402e5ef833f995f40f041eaebd79`。
- 分支首个 commit 只新增本计划，不包含生产代码、测试或依赖变更。
- 后续严格按本计划 Task 顺序串行开发；每完成一个 Task 都提交当前业务 diff 并停下来等待 review。
- 本计划不包含 PR 创建、发布、部署或配置热加载。

## 目标

1. 在一个 BCN daemon 进程内同时运行零个或多个 Agent；每个 Agent 独立拥有一个 Channel、一个 Runtime、一个 workspace 和一组 live session。
2. 把第三方 bot 实例定义为 BCN Agent：同一种 Channel provider 可以被多个 Agent 使用，但每个 Agent 使用自己配置的凭据环境变量和 provider options。
3. 把现有单例 `[node].channel` / `[node].runtime` 配置升级为顶层 `version = "2"` 和 `[[agent]]` 数组，并在启动时对 v1 配置执行一次性、强制、原子升级后反写 `config.toml`。
4. 删除运行时的 `node_state`、`NodeState`、`NodeIdentity` 和共享 workspace identity；旧安装的 `node_state.workspace_id` 一次性成为迁移后首个 Agent 的 `agent_id`，原 workspace 目录不移动。
5. 让所有 durable conversation、message、outbound、runtime attempt 和 Reminder 数据具有明确的 `agent_id` ownership，保证两个 Agent 使用相同 provider-native thread/message id 时不会碰撞。
6. 保留一个全局 `TimerWheel` 和一个全局 `ReminderScheduler`；Reminder 到期时按 `agent_id` 查找对应 `AgentApplication`，再把 wake 投递给该 Agent 下的 owner session runtime queue。
7. 把 `bcc` wrapper 注入每个 Agent 的 workspace，并由 daemon 绑定 `agent_id + bcn_session_id + runtime_session_id + capability`；Agent 无需自行声明执行者身份。
8. 只对 developer instructions 做与 Raft identity 形式一致的最小修改：首句注入 Agent name，runtime context 注入 Agent ID，并继续使用现有 canonical message header 表达 `@sender`、`type` 和 mention 状态。
9. Agent 启动失败只影响该 Agent。Node 共享设施启动成功后，即使所有 Agent 都启动失败或配置中没有 Agent，daemon 仍进入 ready，并逐个记录 Agent 启动结果和汇总日志。

## 已确认边界

- 仍然只有一个 BCN Python 进程；不会为每个 Agent 增加 worker process。只有 Runtime adapter 按现有设计启动自己的 provider 子进程。
- 一个 Agent 只消费自己的 Channel ingress，并只调用自己的 Runtime。不存在多个 Agent 竞争或混合处理同一个 Channel 消息的设计。
- 不实现配置热加载。新增、删除、重命名或修改 Agent 后需要 restart。
- `config.toml` 是 Agent definition 的权威来源，不增加运行时 Agent registry 数据表。
- `agent.id` 是稳定 UUIDv7 identity，也是 workspace 目录名；`agent.name` 是可读身份和 prompt identity，不能作为主键或 workspace 路径。
- Agent name 与 Agent ID 在同一配置内都必须唯一。重命名不改变 Agent ID、workspace 或历史 ownership。
- v1 只作为自动迁移输入存在。升级完成后，application composition、CLI 和 runtime 不保留 v1/v2 双模式。
- 缺失顶层 `version` 或显式 `version = "1"` 视为 v1；`version = "2"` 按 v2 加载；其他版本直接拒绝，避免未来版本被旧程序降级重写。
- Runtime 子进程环境只使用正向白名单：平台基础变量、Runtime adapter 声明的变量，以及该 Agent runtime 配置显式列出的变量。删除固定 forbidden environment 列表，不从 Channel 配置自动注入任何 secret env。
- 实际数据库完成升级后不再存在 `node_state` 表；运行时 domain、storage interface 和 database object 不再定义或读取 node identity。历史 migration v1 的原始 SQL 和新增的一次性迁移 SQL 仍必须保留 legacy table 名称，用于校验既有 migration checksum、读取旧 workspace id 并删除旧表；除此之外不保留 active `node_state` 路径。
- 不移动 legacy workspace。旧 `~/.bcn/workspaces/{legacy-workspace-id}` 直接成为 `~/.bcn/workspaces/{agent_id}`，其中 `agent_id == legacy-workspace-id`。
- Node-level config、storage、migration、command server、TimerWheel 或 global scheduler 启动失败仍使进程启动失败；Agent-level provider/config/workspace/runtime/channel 失败只记录该 Agent 为 failed 并继续。

## Config v2 contract

正式配置形状如下：

```toml
version = "2"

[node]
storage = "sqlite"
audit = "logging"
database_name = "bcn.sqlite3"
endpoint = "~/.bcn/bcn.sock"

[[agent]]
id = "0198d4e6-29c5-7465-b74b-88db31f0c118"
name = "CloudStrife"

[agent.channel]
kind = "telegram"
token_env = "BCN_TELEGRAM_CLOUDSTRIFE_TOKEN"

[agent.runtime]
kind = "codex"
model = "gpt-5.6-luna"
effort = "max"
sandbox_mode = "workspace-write"
network_access = true
idle_timeout = 600
env_include = ["CODEX_HOME"]

[[agent]]
id = "0198d4e7-2a28-7448-8228-388be1bf70b7"
name = "Tifa"

[agent.channel]
kind = "wecom"
bot_id = "bot-id"
secret_env = "BCN_WECOM_TIFA_SECRET"
websocket_url = "wss://openws.work.weixin.qq.com"

[agent.runtime]
kind = "codex"
model = "gpt-5.6-luna"
sandbox_mode = "workspace-write"
network_access = false
```

配置规则：

- `[node]` 只承载共享设施：storage、audit、database、endpoint 和真正 process-scoped 的设置。
- 每个 `[[agent]]` 必须有非空 `id`、`name`、`agent.channel.kind` 和 `agent.runtime.kind`。
- `[agent.channel]` 中除 `kind` 外的字段直接作为该 Channel instance 的 options；Telegram 使用 `token_env`，WeCom 使用 `secret_env`。
- `[agent.runtime]` 同时承载 Runtime kind、model/effort、sandbox/network/idle timeout/env include 等 Agent-scoped 设置及 provider options。
- 空 `[[agent]]` 列表是有效配置；Node 启动后报告 `configured=0 started=0 failed=0`。
- 不增加 `enabled` 或动态状态字段；配置中存在的 Agent 都会在一次 daemon startup 中尝试启动。

## v1 配置的一次性强制升级

### 迁移输入

启动 `start`、`run` 或 `restart` 时，在创建 application composition 前读取原始 TOML：

1. 无 `version` 或 `version = "1"`：执行一次性 v1 -> v2 迁移。
2. `version = "2"`：直接按 v2 schema 校验和加载。
3. 其他值：配置错误，禁止改写。

v1 迁移只使用旧配置计算一次 effective legacy composition；CLI 不再接收单 Agent
的 v1 adapter flags。配置反写为 v2 后，后续 startup 只读取 `[[agent]]`，不会继续运行
单 Agent compatibility path。

若 effective v1 配置没有 channel/runtime，则迁移为合法的零 Agent v2 配置。若存在一组 channel/runtime，则创建一个迁移 Agent：

```toml
[[agent]]
id = "<legacy-workspace-id-or-new-uuid7>"
name = "default"
```

映射规则：

- `[node].channel` -> `agent.channel.kind`
- `[node].runtime` -> `agent.runtime.kind`
- `[channel.<selected-kind>]` -> 当前 `agent.channel` 的 provider options
- `[runtime].model`、`effort`、`sandbox_mode`、`network_access`、`idle_timeout`、`env.include` -> 当前 `agent.runtime`
- Telegram 未显式配置 token env 时写入 `token_env = "BCN_TELEGRAM_BOT_TOKEN"`
- WeCom 未显式配置 secret env 时写入 `secret_env = "BCN_WECOM_BOT_SECRET"`
- storage、audit、database name 与 endpoint 保留在 `[node]`

### Legacy Agent ID 解析

v1 配置升级只读取旧 SQLite `node_state` singleton row 的
`workspace_id` 作为一次性 migration 输入；不扫描 workspace 目录，也不从部分
durable rows 猜测 identity。

- 已有 `workspace_id`：直接将其写入迁移后的 v2 `agent.id`。
- 数据库没有旧 identity：按 fresh start 走新的 Agent 配置初始化，生成一个新的
  UUIDv7 `agent.id`。
- v2 配置不再进入这条路径，也不保留 v1/v2 并行运行逻辑。

后续数据库 migration 使用迁移后的 Agent ID backfill durable rows，并删除旧
`node_state` 表。旧 workspace identity 只存在于这次迁移输入中，不成为新的
SQLite runtime API。

### 原子反写

- 在 `config.toml` 同目录写入完整 v2 临时文件，权限保持 `0600`。
- flush 并 fsync 临时文件后使用 `os.replace` 原子替换正式文件；替换后 fsync 父目录。
- 只有完成 v2 文档构造与校验后才允许替换。
- 若进程在 config 替换后、database migration 前退出，下次启动读取 v2 config 并继续数据库 migration。
- migration 完成后不保留 runtime v1 loader；测试 fixture 仍保留 v1 文档用于验证一次性升级。

## Durable ownership 与数据库 migration

当前最新 schema migration 为 Reminder migration version 12。本需求新增 version 13 migration，并保持既有 migration statement 和 checksum 不变。

### Model ownership

以下 durable/root model 增加 `agent_id`：

- `ChannelSession`
- `BcnSession`
- `InboundMessage`
- `OutboundMessage`
- `RuntimeAttempt`
- `Reminder`
- `ReminderOccurrence`

`OutboundMessage` 额外保存 `agent_name` snapshot，记录该次发言时的 Agent name。Inbound 已有 `sender` 与 `message_type` snapshot，继续沿用。

`BcnSession.workspace_id` 与 process-local `RuntimeSession.workspace_id` 删除；workspace 一律由 owning `AgentApplication.agent_id` 解析。process-local `RuntimeSession` 增加 `agent_id`，用于 capability、audit 和 Runtime context correlation。

`CorrelationContext.node_id` 改为 `agent_id`。运行时不再依赖 storage 生成 persistent node id。

### Schema migration 13

migration 13 在一个 SQLite transaction 中完成：

1. 从 singleton `node_state.workspace_id` 读取 legacy Agent ID；有 legacy durable rows 但 identity 缺失时 fail closed。
2. 给旧 root tables 增加 `agent_id`，并使用 legacy Agent ID backfill。
3. 给 `outbound_messages` 增加 `agent_name`，旧数据使用迁移配置的默认 name `default`；新数据由 server-side Agent binding 写入。
4. 给 `reminders` 与 `reminder_occurrences` 增加 `agent_id`，保证全局 scheduler 不需借助 Channel 或 Runtime 推断 owner Agent。
5. 删除 `bcn_sessions.workspace_id`。
6. 删除旧 provider identity / inbound dedupe indexes，并创建 Agent-scoped indexes：
   - `(agent_id, channel, provider_thread_id)`
   - `(agent_id, channel, provider_thread_id, provider_message_id)`
7. 为 Reminder global frontier 和 pending recovery 保留全局时间索引，同时增加 Agent/session ownership 查询所需索引。
8. 删除 `node_state` table。

对于全新数据库，既有 migrations 先建立历史 schema，再由 version 13 转换到最终 schema；最终数据库同样不存在 `node_state`。

### Storage contract

- 删除 `NodeIdentity` 和 `IStorage.initialize()`。
- `IStorage.start()` 完成 connection、migration 和 schema readiness；storage lifecycle 只由 node application 持有。
- provider-native lookup 与 dedupe API 显式要求 `agent_id`，不允许只以 Channel kind 和 provider id 查询。
- attachment reconcile 的 ready path 查询按 `agent_id` 过滤。
- Reminder 的 global frontier API 继续跨 Agent 查询；返回的 Reminder 已直接携带 `agent_id`。
- pending Reminder recovery 返回 `(agent_id, owner_session_id)`，而不是只有 session id。
- session UUID 仍全局生成，但 UUID 唯一性不代替 Agent ownership 校验。

## 单进程 application composition

### Node application

保留 `NodeApplication` 作为 daemon composition root，不要求进行纯命名重构。它持有：

```text
NodeApplication
├── configuration
├── shared storage
├── shared audit
├── shared TimerWheel
├── shared ReminderScheduler
├── one LocalCommandServer
├── agent applications: dict[agent_id, AgentApplication]
└── agent startup results
```

### Agent application

新增进程内 `AgentApplication`，每个实例持有：

```text
AgentApplication
├── AgentConfiguration / identity
├── workspace path
├── Channel instance
├── Runtime instance
├── SessionOrchestrator
├── SessionLockRegistry
├── AttachmentMaterializer
├── ReminderCommandService
├── CommandDispatcher
├── live capability bindings
└── workspace-local bcc wrapper
```

一个 AgentApplication 不启动或关闭 shared storage、TimerWheel、ReminderScheduler、audit 或 command server。

`SessionOrchestrator` 继续保持“一组 Channel + Runtime”的现有职责，只增加 immutable Agent context，并移除 storage identity 初始化和 shared storage lifecycle ownership。每个 Agent 自己的 `channel.receive()` 只写入自己的 orchestrator；不新增跨 Agent ingress router。

### Adapter registry

`AdapterRegistry` 拆分 shared 与 Agent-scoped composition：

- storage/audit factory 每个 Node 只加载和实例化一次；
- channel builder/runtime factory 每个 Agent 按自己的 `kind` 与 options 独立加载和实例化；
- 相同 Channel 或 Runtime provider 可以被多次实例化；
- optional control handler 如继续存在，则绑定到对应 Agent 的 Channel + Runtime composition，由 local command router 在 Agent scope 内调用。

## Channel credentials 与 Runtime environment

Channel builder 不再读取固定 credential env name：

- Telegram 从 `[agent.channel].token_env` 取得环境变量名，再从 daemon environment 读取 token。
- WeCom 从 `[agent.channel].secret_env` 取得环境变量名，再从 daemon environment 读取 secret。
- credential env name 必须符合环境变量名称语法；值缺失时只导致该 Agent 启动失败。
- bot token/secret value 不进入 config、日志、audit 或 Runtime environment。

Runtime command environment 采用正向白名单：

```text
platform baseline
+ runtime.environment_variable_names()
+ agent.runtime.env_include
```

删除 `_FORBIDDEN_ENVIRONMENT` 和对应 negative-list 校验。Channel credential env 不会因出现在 `[agent.channel]` 而自动加入 Runtime allowlist；只有 Runtime adapter 声明或用户在该 Agent runtime 中显式列出的名称才会进入子进程环境。

## 全局 ReminderScheduler

Node 只有一个 `ReminderScheduler` 和一个 `TimerWheel`。Reminder scheduler 继续维护一个 global frontier，跨所有 Agent materialize due occurrences。

wake callback 改为：

```python
async def publish_reminder_wake(agent_id: str, session_id: str) -> bool:
    agent = agent_applications.get(agent_id)
    if agent is None or not agent.started:
        return False
    await agent.publish_reminder_wake(session_id)
    return True
```

`AgentApplication.publish_reminder_wake(session_id)` 调用其 `SessionOrchestrator.publish_reminder_wake(session_id)`；orchestrator 再使用现有 per-session runtime queue。由于一个 Agent 可以有多个 BCN session，不把一个 Agent 直接绑定到单一 queue，也不向 scheduler 暴露 orchestrator 的私有 queue map。

行为约束：

- due occurrence 的 durable 写入与 wake 分开；Agent unavailable 不回滚 occurrence。
- wake 返回 false 时记录 `reminder.wake.agent_unavailable`，继续处理其他 Agent，不使 scheduler task 失败。
- occurrence 保持 unread；后续该 Agent 成功启动时，global pending recovery 再次尝试 wake。
- 每个 Agent 的 ReminderCommandService 写入 `agent_id`，并调用同一个 global scheduler `poke()`。
- scheduler 在所有 Agent 启动尝试完成后启动；零个成功 Agent 时仍启动并维护 durable frontier。
- Runtime idle timers 继续使用 shared TimerWheel，但各 Agent 的 runtime/session state 仍由各自 orchestrator 管理。

## Workspace、BCC 与 Agent identity

### Workspace

每个 Agent workspace 固定为：

```text
~/.bcn/workspaces/{agent_id}
```

路径在读取 v2 config 后立即可知，不再等待 storage initialize callback。Attachment materializer、Codex cwd、AGENTS watch、MEMORY.md 和 runtime sandbox root 都使用该 Agent workspace。

### Workspace-local BCC

每个 Agent 启动时在以下位置安装 thin wrapper：

```text
~/.bcn/workspaces/{agent_id}/.bcn/bin/bcc
```

Windows 同目录生成现有 `.cmd` / `.ps1` 变体。该目录只加入该 Agent Runtime 的 PATH。wrapper 自动设置 `BCN_AGENT_ID` 后调用 `python -m bazaar_compute_node.bcc`；Agent 不需要在命令参数中显式提供自己的身份。

Runtime session environment 继续提供 endpoint、BCN session、runtime session 与 capability，并新增 Agent binding。`bcc` request 自动携带 `agent_id`。

Node 的 single `LocalCommandServer` 按 `agent_id` 从 map 中取得 AgentApplication/CommandDispatcher，再校验：

```text
agent_id
+ bcn_session_id
+ runtime_session_id
+ session capability
```

server-side live binding 是可信来源。client 自报 Agent name 不参与授权；outbound 落库的 `agent_id` 与 `agent_name` 由 server 根据 AgentApplication identity 写入。Agent A 的 capability 不能读取、发送或管理 Agent B 的 session、attachment 或 Reminder。

### Developer instructions

只做 Raft-style identity 的最小变更：

```diff
- You are an AI agent in bcn (Bazaar Compute Node) ...
+ You are "{{agent_name}}", an AI agent in bcn (Bazaar Compute Node) ...
```

Runtime Context 改为：

```text
- Agent ID: {{agent_id}}
- Runtime session ID: {{runtime_session_id}}
- Runtime: {{runtime}}
- Workspace: {{workspace}}
```

删除 Node ID placeholder，不改写现有 communication、startup、Reminder、thread、memory 和 notification instructions。现有 canonical message formatter 已提供 `type=human|agent|system`、`@sender` 和 mention 状态，继续作为对话中他人身份的表达，不增加新的 message syntax。

Agent name 或 identity-related runtime context 只能在 restart 后变化；本计划不实现 live provider thread identity refresh。

## Startup、ready 与 shutdown 语义

### Startup

Node startup 顺序：

1. 读取并在需要时原子升级 config v1 -> v2；
2. 校验完整 v2 Node/Agent 配置；
3. 启动 shared storage 并执行 schema migration；
4. 启动 shared TimerWheel；
5. 启动 LocalCommandServer，health 状态仍为 `starting`；
6. 按配置顺序逐个构造并启动 AgentApplication；每个 Agent 失败后完成自己的 bounded cleanup，再继续下一个；
7. 启动 global ReminderScheduler 和 pending recovery；
8. Node 进入 `ready`；
9. 打印每个 Agent 的 succeeded/failed 日志与最终汇总。

日志至少包含：

```text
agent.start.succeeded agent_id=... name=... channel=... runtime=...
agent.start.failed agent_id=... name=... channel=... runtime=... error_type=... error=...
bcn.ready configured=N started=M failed=K endpoint=...
```

不得输出 credential value。`configured=0` 或 `started=0` 都是合法 ready 结果。

### Health

health response 保留 Node-level started/accepting 状态，并增加 Agent 列表：

```json
{
  "ready": true,
  "agents": [
    {
      "agent_id": "...",
      "name": "CloudStrife",
      "status": "started",
      "channel": "telegram",
      "runtime": "codex",
      "channel_health": {}
    }
  ]
}
```

failed Agent 返回 redacted error type/summary，便于在零成功启动时诊断。health 不暴露 secret env value。

### Shutdown

- command server 先停止接受新的 Agent command；
- global scheduler 停止；
- 所有成功或部分启动的 AgentApplication 都尝试独立 stop，单个 stop error 不阻断其他 Agent cleanup；
- 然后停止 command server、TimerWheel、storage；
- 聚合并记录 shutdown errors；
- workspace 与 durable data 不删除，只清理当前进程生成的 live wrapper/binding/process。

## 实施顺序

### Task 1.1：Config v2 与一次性自动升级

修改/新增文件：

- `src/bazaar_compute_node/app/config.py`
- `src/bazaar_compute_node/cli.py`
- `tests/app/test_config.py`
- `tests/test_cli.py`
- `tests/app/test_daemon_process.py`
- 如采用 TOML document writer：`pyproject.toml`、`uv.lock`

实施动作：

1. 定义 v2 Node/Agent/Channel/Runtime immutable configuration model，校验 version、UUIDv7、unique id/name、kind、runtime settings 和 env names。
2. 实现 v1 effective composition -> v2 document 的一次性迁移，覆盖旧 config 字段；CLI 只接受 v2 node-level options。
3. 从旧 `node_state.workspace_id` 复用迁移 Agent ID；没有旧 identity 时按 fresh start
   生成 UUIDv7；无 legacy composition 时写出零 Agent v2。
4. 原子反写 `config.toml`，随后只使用 v2 loader。
5. 调整 start/run/restart argument preparation，使 daemon child 不再承载单一 Agent composition；CLI 不再注册或转发 v1 adapter flags。
6. 覆盖 future version rejection、迁移失败不破坏原 config、重复 identity 拒绝和确定性 TOML 输出。

Focused tests：空 v1、完整 WeCom v1、完整 Telegram v1、v2 CLI Agent composition、旧 CLI
agent flags 拒绝、旧 `node_state.workspace_id` 复用、fresh start UUIDv7、原子 replace
failure、v2 round-trip、unknown version fail closed。

完成条件与停止点：任何 startup 都先得到稳定 v2 configuration；提交 Task 1.1 diff，停下等待 review，不进入数据库或 application composition 改造。

### Task 1.2：Agent durable ownership 与移除 node identity

修改文件：

- `src/bazaar_compute_node/core/models/entities.py`
- `src/bazaar_compute_node/core/models/reminder.py`
- `src/bazaar_compute_node/core/storage.py`
- `src/bazaar_compute_node/core/correlation.py`
- `src/bazaar_compute_node/core/paths.py`
- `src/bazaar_compute_node/contrib/sqlite/migrations.py`
- `src/bazaar_compute_node/contrib/sqlite/database.py`
- `src/bazaar_compute_node/contrib/sqlite/codec.py`
- `src/bazaar_compute_node/contrib/sqlite/repository.py`
- `src/bazaar_compute_node/contrib/sqlite/reminder_codec.py`
- `src/bazaar_compute_node/contrib/sqlite/reminder_repository.py`
- 相关 memory/test storage support
- `tests/core/test_models.py`
- `tests/core/test_ports.py`
- `tests/core/test_reminder.py`
- `tests/contrib/test_sqlite_database.py`
- `tests/contrib/test_sqlite_session_repository.py`
- `tests/contrib/test_sqlite_runtime_repository.py`
- `tests/contrib/test_sqlite_reminder_repository.py`

实施动作：

1. 增加 Agent ownership fields，删除 BcnSession/RuntimeSession workspace identity，CorrelationContext 改用 agent_id。
2. 新增 schema migration 13：backfill Agent ownership、重建 indexes、迁移 Reminder ownership、删除 workspace column 和 `node_state` table。
3. 删除 runtime `NodeState`/`NodeIdentity`/storage initialize contract；storage start 后即 ready。
4. 所有 provider identity、dedupe、attachment 与 session repository API 增加 Agent scope。
5. 保持历史 migrations/checksums 不变，验证旧 version 12 database 可直接升级且最终 schema 无 `node_state`。

Focused tests：真实 version 12 fixture upgrade、legacy id 全表 backfill、旧 workspace path 不移动、同 provider ids 跨 Agent 可共存、同 Agent 内仍 dedupe、missing legacy identity with durable data fail closed、全新 DB 最终 schema、historical checksum verification。

完成条件与停止点：durable storage 能证明 Agent ownership 且 active schema/runtime 无 node identity；提交 Task 1.2 diff，停下等待 review。

### Task 1.3：AgentApplication、adapter instance 与部分启动

修改/新增文件：

- `src/bazaar_compute_node/app/application.py`
- `src/bazaar_compute_node/app/agent.py`
- `src/bazaar_compute_node/app/registry.py`
- `src/bazaar_compute_node/core/channel.py`
- `src/bazaar_compute_node/core/runtime.py`
- `src/bazaar_compute_node/core/orchestration/session.py`
- `src/bazaar_compute_node/contrib/telegram/plugin.py`
- `src/bazaar_compute_node/contrib/wecom/plugin.py`
- Runtime/Channel test support
- `tests/app/test_registry.py`
- `tests/app/test_composition.py`
- `tests/contrib/test_orchestration.py`
- `tests/contrib/test_telegram.py`
- `tests/contrib/test_wecom.py`
- `tests/contrib/test_codex.py`

实施动作：

1. 把 current one-channel/one-runtime composition 抽为 AgentApplication；NodeApplication 持有 shared facilities 与 Agent map。
2. 将 storage lifecycle 提升到 NodeApplication，SessionOrchestrator 只启动/停止自己的 Runtime 和 Channel。
3. 按 Agent config 多次加载同一种 Channel/Runtime provider，使用各自 options 与 workspace。
4. Telegram/WeCom 通过 token_env/secret_env 解析 credential；缺失 credential 是 Agent-local startup failure。
5. Runtime environment 删除 forbidden list，只保留正向 allowlist。
6. 实现 Agent 独立 startup cleanup、status、health record 与零成功 ready。
7. shutdown 逐个收敛所有 Agent，不因单个 Agent failure 中断。

Focused tests：两个 Telegram Agent 不同 env、Telegram + WeCom、两个 Codex Runtime 不同 options、一个成功一个失败、全部失败、零 Agent、shared storage 只 start/stop 一次、Agent channel ingress 不跨 orchestrator、Agent stop error 聚合。

完成条件与停止点：一个 daemon 能稳定持有多个隔离 AgentApplication，并允许部分或零成功；提交 Task 1.3 diff，停下等待 review。

### Task 1.4：单一全局 ReminderScheduler 与 Agent wake map

修改文件：

- `src/bazaar_compute_node/core/orchestration/reminder.py`
- `src/bazaar_compute_node/core/orchestration/reminder_command.py`
- `src/bazaar_compute_node/core/orchestration/session.py`
- `src/bazaar_compute_node/app/application.py`
- `src/bazaar_compute_node/app/agent.py`
- `src/bazaar_compute_node/app/reminder_dispatch.py`
- `tests/core/test_reminder.py`
- `tests/contrib/test_orchestration.py`
- `tests/contrib/test_sqlite_reminder_repository.py`
- `tests/app/test_bcc_reminder_process.py`

实施动作：

1. NodeApplication 只构造一个 global ReminderScheduler；移除 per-Agent scheduler ownership。
2. scheduler materialize 的 occurrence 和 recovery owner 都携带 agent_id。
3. 实现 `agent_id -> AgentApplication -> owner session runtime queue` 的薄 wake path。
4. Agent unavailable/failed 时保留 unread occurrence、记录日志并继续，不终止 global scheduler。
5. 所有 Agent ReminderCommandService 使用同一个 scheduler poke，并强制写入当前 Agent identity。
6. 验证 shared TimerWheel 同时服务 global Reminder frontier 与各 Agent runtime idle timers。

Focused tests：两个 Agent reminder 各自 wake、同一 Agent 多 session queue、failed Agent occurrence pending、下一次成功 startup recovery、全部 Agent failed 时 scheduler 仍运行、一个 wake failure 不阻断其他 due owner、global frontier ordering。

完成条件与停止点：Node 只有一个 scheduler，Reminder wake 不跨 Agent 且 unavailable Agent 不破坏 durable state；提交 Task 1.4 diff，停下等待 review。

### Task 1.5：Workspace-local BCC、capability Agent binding 与 identity prompt

修改文件：

- `src/bazaar_compute_node/app/wrapper.py`
- `src/bazaar_compute_node/app/application.py`
- `src/bazaar_compute_node/app/agent.py`
- `src/bazaar_compute_node/app/command.py`
- `src/bazaar_compute_node/app/transport.py`
- `src/bazaar_compute_node/bcc.py`
- `src/bazaar_compute_node/core/instruction.py`
- `src/bazaar_compute_node/core/runtime.py`
- `src/bazaar_compute_node/core/orchestration/command.py`
- `src/bazaar_compute_node/contrib/codex/runtime.py`
- `tests/app/test_bcc_process.py`
- `tests/app/test_command_resource.py`
- `tests/app/test_transport.py`
- `tests/test_bcc.py`
- `tests/core/test_instruction.py`
- `tests/contrib/test_codex_instruction.py`

实施动作：

1. workspace 统一按 agent_id 解析，移除 deferred node identity callback。
2. 为每个 Agent 安装 workspace-local bcc wrapper，并只加入该 Agent Runtime PATH。
3. bcc 自动携带 BCN_AGENT_ID；single command server 按 Agent map 选择 dispatcher。
4. capability binding 增加 agent_id，并在 read/send/thread/reminder/attachment/control provider 路径统一验证。
5. outbound server-side 写入 Agent ID/name snapshot；禁止 client 覆盖作者身份。
6. developer instructions 只增加 Raft-style Agent name/ID placeholders并删除 Node ID placeholder，其余模板保持不变。
7. Codex start thread 使用当前 Agent identity/workspace；resume 行为保持现有 provider contract。

Focused tests：两个 Agent wrapper/PATH 隔离、Agent A capability 操作 B 被拒绝、伪造 BCN_AGENT_ID 被拒绝、outbound author stamping、attachment workspace 边界、Windows wrapper variants、developer instructions exact rendering、现有大段 instructions 不发生无关变化。

完成条件与停止点：bcc 调用在 Agent 不知情的情况下获得可信执行者 identity，Runtime 明确认知自身 name/ID；提交 Task 1.5 diff，停下等待 review。

### Task 1.6：CLI、daemon readiness、文档与完整回归

修改文件：

- `src/bazaar_compute_node/cli.py`
- `README.md`
- `CHANGELOG.md`
- `tests/test_cli.py`
- `tests/app/test_daemon_process.py`
- `tests/e2e/` 相关 daemon/channel/runtime scenarios
- 其余因 final contract 需要更新的 package smoke 与 fixtures

实施动作：

1. `bcn start/run/restart` 统一从 v2 config 启动全部 Agent；daemon child command 不再展开单 Agent options。
2. readiness 等待 Node health `ready=true`，而不只判断 endpoint 可连接。
3. 输出逐 Agent startup result 与 configured/started/failed summary，覆盖零成功。
4. 更新 Quick Start 为 config v2、per-Agent credential env 和 restart-based configuration workflow。
5. 更新 changelog，说明 config 自动升级、legacy workspace preservation、无 hot reload 和 partial startup。
6. 运行 focused suites、完整 test suite、ruff 与 LSP/pyright，并检查 package smoke。

Focused tests：foreground/background start、stop/restart、v1 first startup 自动反写、v2 subsequent startup 不重复迁移、零成功 daemon 可 health/stop、部分成功 health、Windows endpoint/daemon behavior、package install smoke。

完成条件与停止点：用户可从旧单 Agent 安装一次启动升级到 v2，并在一个 daemon 内运行多个隔离 Agent；提交 Task 1.6 diff，停下等待 final review，不创建 PR 或发布，除非收到明确指令。

## 最终验证矩阵

功能与隔离：

- 同一进程启动两个 Telegram Agent，分别使用不同 token env。
- 同一进程启动 Telegram 与 WeCom Agent，各自 Channel ingress、outbound、approval 和 attachment 不交叉。
- 两个 Codex Agent 使用不同 model、sandbox、network、idle timeout 与 env include。
- 相同 provider_thread_id/provider_message_id 在不同 agent_id 下均能持久化；同 Agent 内仍去重。
- Agent A 的 bcc capability 不能访问 Agent B 的 session、message、Reminder 或 workspace attachment。
- Agent name 在 developer instructions 与 outbound snapshot 中正确出现；Agent ID 作为稳定 identity。

迁移：

- 缺失 version 的 v1 config 在首次 startup 自动成为 canonical v2 并原子反写。
- legacy node_state.workspace_id 成为首个 Agent ID，workspace 目录及 MEMORY/attachments 不移动。
- version 12 SQLite fixture 升级后所有 durable rows 具有同一 legacy agent_id，`node_state` table 与 workspace_id column 消失。
- config 替换后、DB migration 前模拟崩溃，下一次 startup 可继续。
- unknown config version 不被降级或改写。

生命周期：

- configured=0、全部失败、部分成功和全部成功都产生准确 health/log；后三者中 Node shared facilities 正常运行。
- Agent startup failure 清理其 partial resources，不影响下一个 Agent。
- global scheduler 在零成功时仍可 materialize occurrence；Agent 恢复后 pending recovery wake。
- Node shutdown 尝试停止所有 Agent，并最终关闭 shared scheduler/timer/storage/server。

质量门：

```text
uv run pytest <focused suites>
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

相关文件还必须通过编辑器/LSP 诊断检查，无新增 warning/error。最后执行 `git diff --check`，确认 migration、config 示例和 generated lockfile 一致。

## 明确不做

- 不实现 Agent 配置热加载或 filesystem watch。
- 不实现 `bcn agent add/remove/list`；本计划只为后续命令稳定定义 v2 schema 和 Agent ID contract。
- 不实现一个 Channel 消息由多个 Agent 协商、竞争、handoff 或共同处理。
- 不实现跨 Agent shared workspace、shared runtime session、shared capability 或 shared conversation。
- 不为 Agent 增加独立 OS process、socket、database 或 scheduler。
- 不改变现有 Reminder 业务语义、canonical message header 或 Channel provider protocol，除 Agent ownership 外不扩展 Teams/Agent-to-Agent routing。
