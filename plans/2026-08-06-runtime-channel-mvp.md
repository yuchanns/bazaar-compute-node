# 2026-08-06 Runtime Channel MVP Plan

## 状态

- 模式：Plan
- 状态：Phase 4 与命名收口已完成并处于 review；review 后进入 Phase 5 Task 5A。
- 基线：`main`，当前提交为 `4560831898dd6ddc5dbcc8e3bc077ca6fccee776`。
- 当前更新只纠正 Phase 5 的 WeCom 接入假设及其交叉边界，不提前修改生产代码。新版
  「智能机器人 API 模式」同时支持 WebSocket 长连接与 JSON URL 回调；本 MVP 明确选择
  Bot ID + Secret 的 WebSocket 长连接，不接旧群机器人 webhook、自建应用回调，也不接
  新版智能机器人的 URL 回调模式。

## 1. 目标

把当前 packaged app 基础框架演进为一个可以承载第一组 runtime/channel adapter 的
computer node daemon。首个纵向切片的业务链路是：

```text
WeCom intelligent Bot WebSocket inbound
    -> channel message mapping
    -> SQLite append-only log and session binding
    -> Codex App Server turn
    -> agent calls bcc message check/read/send/unfollow
    -> outbound channel delivery and audit
```

首版由 CLI 负责选择 adapter 组合，后台启动入口为：

```bash
bcn start --channel wecom --runtime codex
bcn stop
bcn restart
```

`--channel` 和 `--runtime` 是 composition root 的 adapter name。CLI 在启动 node 前完成
参数解析，并通过 Python package entry point 动态加载被选择的 Channel、Agent Runtime、
Storage 和 Audit contrib；未选择或未安装的 provider 不会被 import。首版一个进程选择一组
channel/runtime；该组合内部仍支持多个 bcn session 并发。`start`/`run` 必须从显式 CLI 参数
或持久配置解析出非空 `channel` 和 `runtime`，二者没有内置默认值，缺少任一项都在启动前
失败。Phase 1 使用仅存在于测试代码的 Test adapters 完成小闭环，不把测试实现注册为生产
provider entry point。
兼容地直接执行 `bcn --channel ... --runtime ...` 等价于 `bcn start --channel ... --runtime ...`。
`bcn` daemon 将配置、SQLite 运行态和日志保存在持久化 data directory，运行进程在后台
维护，`stop`/`restart` 通过本机 command transport 和稳定 endpoint 做优雅生命周期控制；
不额外保存 PID、JSON 或 lock discovery 文件。

首版必须满足以下产品语义：

1. Python 全链路使用 async；一个 node 可以同时运行多个 session。
2. 一个固定的 agent runtime session 实例就是一个 agent；每个 agent runtime session
   使用独立的 Codex App Server 子进程，但所有 agent 共用同一个稳定 workspace。
3. 首次 channel inbound 创建并持久化三方绑定：
   `runtime_session_id <-> bcn_session_id <-> channel_session_id`。
4. bcn 启动时只生成一个 workspace UUID，写入 SQLite；workspace 固定为
   `$HOME/.bcn/workspaces/{uuid}/`，后续启动复用，不按 agent 拆分。
5. Channel 只把“有消息待处理”的提醒送进 runtime；真实消息由 agent 通过 `bcc`
   工具读取。
6. 第一版提供 `bcc message check/read/send/unfollow`，不引入 task queue、claim、lease 或
   exactly-once 语义。
7. 审批能力属于 Channel port，由每个 Channel contrib 实现审批策略；当前 WeCom
   实现永远返回批准，不提供人工审批 UI。
8. `bcc` wrapper 在 node 生命周期内注入到 `$HOME/.bcn/bin/`，node 将该目录加入每个 runtime
   子进程的 PATH；每次启动重写 wrapper，退出时只删除本次 node 生成的 wrapper 文件，避免
   后续迭代复用旧 bin；wrapper 不放在启动临时目录。

## 2. 已确认的边界与非目标

### 已确认的边界

- App Server adapter 使用 stdio/JSONL 双向协议；`initialize` 完成后建立 thread，
  通过 `turn/start` 注入文本，消费异步事件直到 `turn/completed`。
- `turn/completed` 是 turn 的权威终态；中途 error notification 不能直接视为最终失败。
- `thread/read(includeTurns=true)` 用于进程异常或断线后的对账恢复。
- App Server 当前是 experimental，官方 Python SDK 为 beta 且和匹配的 Codex CLI 版本
  绑定；runtime adapter 必须隔离版本差异，不让 Codex 类型进入 core。
- App Server 的反向 approval request 由 runtime adapter 转成 core 的中立请求，再路由到
  当前 bcn session 对应的 Channel approval port；WeCom 当前固定返回 approved。
- bcc reminder 固定使用：

  ```text
  [inbox notice session=<bcn_session_id>]
  Inbox update: <n> unread message(s).
  Use bcc message check to read them.
  ```

- `bcc message check` 返回标准化 canonical text envelope；`read` 返回历史窗口；`send`
  返回 outbound receipt 或带 `Error`、`Code`、`Next action` 标签的错误文本。
- group following 是 provider-neutral core 语义：Channel 投递的所有群消息先落库；
  unfollowed 时不计入未读、不触发 Runtime；明确提及机器人时开始 following，之后所有
  已投递群消息持续进入未读/reminder，直到 agent 调用 unfollow。DM 始终是
  followed，unfollow 对 DM 是返回成功的幂等 no-op。Channel 能力不改变这套 core 状态机；
  无法投递全量群消息的
  provider 只会造成可观测的 ingress gap。

### 首版非目标

- Codex runtime 的 TCP WebSocket、Unix-socket WebSocket、HTTP RPC 和远程 runtime
  transport；这一非目标不包含 Phase 5 明确需要的 WeCom Channel WebSocket。
- Codex dynamic tools、MCP 形式的 bcc tools、request-user-input 或 MCP elicitation。
- 企业微信人工 approval UI、管理员代审和跨 Channel 的统一审批策略。
- 多 workspace、每 agent 独立 workspace、全局 skill 安装。
- 完整管理命令面、task queue、claim/lease、分布式锁、exactly-once delivery。
- 生产部署、发布 tag、数据库迁移到远端服务。

## 3. 目标模块边界

依赖方向固定为 `app/cli -> contrib -> core`；`core` 不依赖 Codex、企业微信、SQLite
或 CLI。

```text
src/bazaar_compute_node/
├── core/
│   ├── models/          # session, message, cursor, turn, delivery state
│   ├── ports/           # channel, runtime, storage, command service ports
│   └── orchestration/   # session routing and per-session lifecycle
├── contrib/
│   ├── sqlite/          # SQLite schema, migrations and repositories
│   ├── codex_app_server/# async App Server process and protocol adapter
│   ├── logging/         # local operational audit sink
│   └── wecom/           # WeCom intelligent Bot WebSocket adapter and message mapping
└── app/
    └── cli/             # bcn composition root, local IPC and bcc wrapper

tests/
└── support/             # controllable Test adapters and test-only provider plugin
```

具体包名可以在 Code 模式开始时按仓库惯例调整，但不能反转依赖方向或让 core import
provider SDK types。

### 3.1 Async runtime 与外部依赖

#### 3.1.1 Async loop

- 全链路使用 Python 标准库 `asyncio`；CLI 的同步 console entrypoint 只在 composition root
  调用一次 `asyncio.run(async_main(...))`，下层 coroutine 使用
  `asyncio.get_running_loop()`，不自行创建或持有 event loop。
- 使用 Python 默认 event loop policy：Unix 使用 `SelectorEventLoop`，Windows 使用
  `ProactorEventLoop`。不在 MVP 强制引入 `uvloop`，避免破坏 Windows subprocess/IPC 的
  同一套语义。
- runtime process 使用 `asyncio.create_subprocess_exec`；stdio/JSONL、IPC、HTTP、timeout、
  cancellation 和 session lock 都使用 asyncio 原生 awaitable。同步阻塞调用只能位于明确的
  storage 或 executor boundary，不能阻塞主 event loop。
- 每个 bcn session 的 command、cursor、turn 和 outbound safety gate 仍按既定 session lock
  串行化；async loop 负责 node 内 I/O multiplexing，不改变业务状态机的串行顺序。

#### 3.1.2 Runtime dependencies

| 用途 | 外部库 | 作用域与规则 |
| --- | --- | --- |
| SQLite async adapter | `aiosqlite` | 仅由 `contrib/sqlite` 使用；通过每个 connection 的共享 worker thread 将 SQLite 操作移出主 loop。MVP 使用 long-lived connection、显式 transaction 和 repository lock，不引入 connection pool。 |
| WeCom WebSocket transport | `aiohttp` | 仅由 `contrib/wecom` 使用；真实 endpoint 在认证后会发送带 MASK bit 的服务端 frame，严格 RFC parser 会以 1002 `incorrect masking` 断开，因此复用 `aiohttp` 的兼容 reader 承载 WebSocket framing/TLS；Bot 认证、应用层 heartbeat、重连、回执队列和 lifecycle 仍由 adapter-local client 明确定义。 |
| WeCom media boundary | `aiohttp`、`cryptography` | 仅由 `contrib/wecom` 使用；下载五分钟有效的媒体 URL 并执行 provider 规定的 AES 解密。复用 application-scoped HTTP session，统一 timeout、TLS、response classification 和 shutdown。 |

App Server 不引入 provider SDK 或 JSON-RPC 第三方封装：使用标准库 `asyncio` subprocess/stream、
`json` 和 adapter-local protocol types，避免 experimental wire schema 和 SDK 版本绑定进入
core。CLI、model、日志、correlation、ID、重试状态机分别使用标准库
`argparse`、`dataclasses`/`typing`、`logging`、`contextvars`、`uuid`/`secrets`；本地生成的
UUID 统一使用 RFC 9562 UUIDv7，不为这些能力增加第三方依赖。

#### 3.1.3 Development dependencies

- `pytest`：测试 runner。
- `pytest-asyncio`：async core、repository、IPC、runtime 和 Channel adapter 测试；每个
  async test 使用受控 event loop，不共享生产 loop 状态。
- `ruff`：格式化和静态检查基线，版本由 uv lock 固定。

以下不进入 MVP dependency set：`uvloop`、`anyio`/`trio`、SQLAlchemy、Pydantic、`orjson`、
`tenacity`、FastAPI/Starlette、App Server provider SDK、WeCom provider SDK、`pyee` 和
`python-dotenv`。WeCom adapter 直接使用 `aiohttp` 实现薄协议 client，以便兼容真实 endpoint 的 masked server frame，并保留原始 frame、
区分 ack/timeout/connection-loss、禁止 SDK 内部自动重发模糊 unknown state，并将 provider
类型完全限制在 `contrib/wecom`；公开的 WecomTeam SDK 只作为协议与行为参考，不进入运行时
依赖。其他依赖只有出现明确需求并完成真实 adapter 验证后才单独引入。

## 4. 核心数据模型与 SQLite

### 4.1 节点和 workspace

启动时由业务层生成并由 storage 初始化或读取唯一的 UUIDv7 `workspace_id`；storage adapter
不重复承担 UUIDv7 格式校验：

```text
data_dir:   $HOME/.bcn
workspace:  $HOME/.bcn/workspaces/{workspace_id}/
database:   persistent SQLite database under data_dir
```

`$HOME/.bcn` 是固定的产品数据根目录；不能通过环境变量、CLI 参数或构造参数覆盖，也不能
把业务状态放进 runtime 启动时创建的临时目录。稳定目录布局为：

```text
$HOME/.bcn/
├── bin/                       # lifecycle-scoped bcc wrappers
├── workspaces/{uuidv7}/       # shared agent workspace
│   └── attachments/           # eagerly materialized inbound attachments
└── ...                        # SQLite data and other persistent node state
```

临时目录只用于 IPC endpoint metadata 和生命周期临时文件；运行中的 `bcc` wrapper 必须位于
`$HOME/.bcn/bin/`，由 node 启动时生成并在 node 退出时删除，避免重启后复用旧 wrapper。

业务启动阶段只依赖 core `IStorage` port 的 `initialize`，由注入的 storage implementation
读取或创建 node identity；业务层随后根据返回的 `workspace_id` 创建 workspace directory。
core/application 不执行 SQLite SQL，也不依赖 SQLite-specific migration objects。

### 4.2 SQLite schema 设计

这一节把 MVP 的表设计展开到可以直接进入 migration 的程度。具体 Python repository
类名可以在 Code 模式调整，但字段职责、约束和状态边界固定如下：

- ID 使用 UUID 或 provider opaque ID 的 `TEXT`；不把 provider ID 当作本地主键。
- 消息域的本地 `message_id`、`attachment_id` 与 `outbound_message_id` 使用 RFC 9562 UUIDv7
  的 `TEXT` primary key；由 repository 负责生成和格式校验，provider message ID 仍只是
  普通关联字段。
- 本地时间使用 UTC Unix milliseconds 的 `INTEGER`，字段统一使用 `_at_ms` 后缀；展示层
  再格式化为 canonical time。
- 本地序列使用 `INTEGER`；`inbound_messages.seq` 和 `runtime_events.event_seq` 分别是
  node-local monotonic sequence，MVP 不物理删除这两类 append-only 记录。
- JSON metadata 使用 `TEXT` 保存应用校验过的 JSON object；不依赖 SQLite JSON extension。
  provider 原始 payload 只保存受控引用，不把完整 inbound WebSocket frame、token、cookie 或 credential
  写入数据库。
- 所有状态值、必填字段、默认值、关联关系和去重规则都由 core/application/repository
  校验；SQLite schema 只声明字段与物理行身份，不承载业务约束。未知 provider 状态不能
  折叠成失败。
- migration 的表创建语法属于 `contrib/sqlite` 的 adapter-private 实现；storage
  port 只暴露启动、初始化和 repository 操作。替换存储时替换注入的 implementation 及其
  schema 初始化，不把 SQLite DDL 抽象成业务层的通用表创建 API。

关系可以先固定为以下形态：

```text
node_state
  └── channel_sessions ─── bcn_sessions ─── runtime_sessions ─── runtime_turns
          ├── inbound_messages ─── inbound_attachments
          └── outbound_messages
bcn_sessions ─── consumer_cursors
all lifecycle and command operations ─── runtime_events
schema_migrations
```

MVP 约束为一个 channel session 对应一个 bcn session、一个 bcn session 对应一个 runtime
session；同一 runtime session 下同一时刻最多一个 active turn。这些都是 application
state machine 和 repository transaction 的不变量，不写入 SQLite constraint。将来要支持
一个 channel session 多 agent 时，只调整 orchestration 规则和逻辑绑定检查，不改变 message
log 的本地 `seq` 语义。

#### 4.2.1 表定义

表注释和生命周期先固定如下；migration 草案中的 English `--` 注释与此表一一对应：

| 表 | 注释 | 生命周期 |
| --- | --- | --- |
| `node_state` | 节点身份、共享 workspace 绑定和 schema version 缓存 | singleton，更新 metadata，不删除 |
| `channel_sessions` | provider conversation/thread 到 channel session 的规范化绑定和 following 状态 | get-or-create，状态关闭，不删除历史身份 |
| `bcn_sessions` | 对外稳定的 bcn session，以及 channel/workspace 关系 | 创建后状态迁移，保留终态 |
| `runtime_sessions` | agent runtime process、provider thread 和恢复状态 | 创建后记录启动/退出/对账状态，不复用 ID |
| `runtime_turns` | 单个 runtime turn 的终态、unknown 和 reconciliation 状态 | append identity，状态有限迁移 |
| `inbound_messages` | provider inbound 的规范化、去重和 node-local seq 日志 | append-only |
| `inbound_attachments` | inbound 附件的中立描述、workspace 路径和物化终态 | message append 时创建，物化终态后不变 |
| `outbound_messages` | 每次 send command 的 draft、fresh-check 证据和 delivery 结果 | 一次 attempt 一行，状态有限迁移 |
| `consumer_cursors` | 每个 bcn session 的 delivered cursor 与最新 inbox snapshot | singleton per session，原地更新 |
| `runtime_events` | 跨 node/channel/runtime/session/turn/command 的运行和审计事件 | append-only |
| `schema_migrations` | migration version、checksum 和执行记录 | append-only ledger |

下面是 schema 级草案。它不是要求直接复制的 migration SQL，但所有列都应在实现时落成
等价字段。为保持 storage port 可替换，DDL 不使用 `NOT NULL`、`DEFAULT`、`CHECK`、
`UNIQUE` 或 `FOREIGN KEY`；`PRIMARY KEY` 仅用于物理行定位，不表达业务关联或唯一性。
SQL 中的 `--` 注释是 migration 可读性文档，真正的字段语义与不变量由 core/application/
repository 定义。

```sql
-- Singleton node identity, workspace binding, and cached schema version.
CREATE TABLE node_state (
    -- Fixed application-managed row key for the singleton state record.
    singleton_key INTEGER PRIMARY KEY,
    -- Stable identifier of this node installation.
    node_id TEXT,
    -- Cached version of the migration ledger.
    schema_version INTEGER,
    -- UUID of the shared workspace used by all runtime sessions.
    workspace_id TEXT,
    -- Creation time of the node state.
    created_at_ms INTEGER,
    -- Last update time of node metadata or schema cache.
    updated_at_ms INTEGER,
    -- Non-sensitive node metadata encoded as JSON.
    metadata_json TEXT
);

-- Provider conversation/thread identity and channel-level following state.
CREATE TABLE channel_sessions (
    -- Stable local identifier for the normalized channel session.
    id TEXT PRIMARY KEY,
    -- Selected channel adapter name.
    channel TEXT,
    -- Provider-native conversation identity used for lookup.
    provider_conversation_key TEXT,
    -- Provider-native thread or reply identity when one exists.
    provider_thread_key TEXT,
    -- Normalized target category used by the command layer.
    target_kind TEXT,
    -- Application-managed following flag.
    following INTEGER,
    -- Application-managed channel session lifecycle state.
    state TEXT,
    -- Non-sensitive provider identity references encoded as JSON.
    provider_identity_ref_json TEXT,
    -- Creation time of the channel session.
    created_at_ms INTEGER,
    -- Last update time of channel identity or lifecycle state.
    updated_at_ms INTEGER,
    -- Last normalized inbound time observed for this session.
    last_inbound_at_ms INTEGER,
    -- Last outbound attempt time observed for this session.
    last_outbound_at_ms INTEGER
);

-- Stable bcn session bound to one channel session and the shared workspace.
CREATE TABLE bcn_sessions (
    -- Stable local identifier exposed to the runtime command wrapper.
    id TEXT PRIMARY KEY,
    -- Application-managed association to a channel session.
    channel_session_id TEXT,
    -- UUID of the shared workspace used by this session.
    workspace_id TEXT,
    -- Application-managed bcn session lifecycle state.
    state TEXT,
    -- Creation time of the bcn session.
    created_at_ms INTEGER,
    -- Last update time of session state or metadata.
    updated_at_ms INTEGER,
    -- Last message or runtime activity time.
    last_activity_at_ms INTEGER,
    -- Time at which the session reached its stopped state.
    stopped_at_ms INTEGER,
    -- Non-sensitive session metadata encoded as JSON.
    metadata_json TEXT
);

-- One agent runtime process/thread binding and process recovery state.
CREATE TABLE runtime_sessions (
    -- Stable local identifier for one runtime process lifecycle.
    id TEXT PRIMARY KEY,
    -- Application-managed association to a bcn session.
    bcn_session_id TEXT,
    -- Application-managed channel session association for correlation.
    channel_session_id TEXT,
    -- Selected agent runtime adapter name.
    runtime TEXT,
    -- Runtime adapter or protocol version used for this process.
    runtime_version TEXT,
    -- Provider-native runtime thread identifier when available.
    provider_thread_id TEXT,
    -- Application-managed process lifecycle state.
    process_state TEXT,
    -- Operating-system process identifier when the process is running.
    process_pid INTEGER,
    -- Last known process exit code.
    last_exit_code INTEGER,
    -- Creation time of the runtime session record.
    created_at_ms INTEGER,
    -- Last update time of runtime process state or metadata.
    updated_at_ms INTEGER,
    -- Process start time.
    started_at_ms INTEGER,
    -- Process stop time.
    stopped_at_ms INTEGER,
    -- Last time persisted state was reconciled with the process.
    last_reconciled_at_ms INTEGER,
    -- Stable application error category from the latest failure.
    last_error_kind TEXT,
    -- Redacted summary of the latest runtime failure.
    last_error_message TEXT,
    -- Non-sensitive runtime metadata encoded as JSON.
    metadata_json TEXT
);

-- Durable runtime turn state used for completion, interruption, and reconciliation.
CREATE TABLE runtime_turns (
    -- Stable local identifier for one runtime turn.
    turn_id TEXT PRIMARY KEY,
    -- Application-managed association to the runtime session.
    session_id TEXT,
    -- Provider-native turn identifier when available.
    provider_turn_id TEXT,
    -- Client message identifier that caused this turn.
    client_user_message_id TEXT,
    -- Application-managed turn lifecycle state.
    state TEXT,
    -- Turn start time.
    started_at_ms INTEGER,
    -- Turn completion time when a terminal result is known.
    completed_at_ms INTEGER,
    -- Latest normalized runtime event name.
    last_event_name TEXT,
    -- Stable application error category for the turn.
    error_kind TEXT,
    -- Redacted summary of the turn failure.
    error_message TEXT,
    -- Non-sensitive turn metadata encoded as JSON.
    metadata_json TEXT
);

-- Append-only normalized inbound message log with the node-local delivery sequence.
CREATE TABLE inbound_messages (
    -- Stable local UUIDv7 message identifier used as the physical row identity.
    message_id TEXT PRIMARY KEY,
    -- Node-local monotonic sequence used for cursor and snapshot boundaries.
    seq INTEGER,
    -- Application-managed association to a bcn session.
    session_id TEXT,
    -- Application-managed association to a channel session.
    channel_session_id TEXT,
    -- Channel adapter name that normalized the message.
    channel TEXT,
    -- Provider-native message identifier used for application-level deduplication.
    provider_message_id TEXT,
    -- Provider timestamp, if supplied.
    provider_time_ms INTEGER,
    -- Local receipt time.
    received_at_ms INTEGER,
    -- Stable provider sender identifier.
    sender_id TEXT,
    -- Display name captured at receipt time.
    sender_display_name TEXT,
    -- Normalized sender or event type.
    message_type TEXT,
    -- Canonical target used by reply commands.
    canonical_target TEXT,
    -- Provider-native thread identifier when available.
    provider_thread_id TEXT,
    -- Provider-native identifier of the message being replied to.
    reply_to_provider_message_id TEXT,
    -- Normalized message body.
    body TEXT,
    -- Whether this inbound explicitly mentions the agent.
    mentions_agent INTEGER,
    -- Immutable decision that this inbound enters the runtime unread stream.
    notifies_runtime INTEGER,
    -- Controlled reference to retained provider payload data.
    provider_payload_ref TEXT,
    -- Non-sensitive normalized metadata encoded as JSON.
    metadata_json TEXT
);

-- Provider-neutral inbound attachments materialized into the shared workspace.
CREATE TABLE inbound_attachments (
    -- Stable local UUIDv7 attachment identifier.
    attachment_id TEXT PRIMARY KEY,
    -- Application-managed association to the owning inbound message.
    message_id TEXT,
    -- Stable zero-based order within the owning message.
    ordinal INTEGER,
    -- Provider-neutral attachment category used as a reading hint.
    kind TEXT,
    -- Validated media type when known.
    media_type TEXT,
    -- Untrusted provider filename retained only for display.
    original_name TEXT,
    -- Workspace-relative path for a successfully materialized attachment.
    relative_path TEXT,
    -- Materialized plaintext byte size when known.
    byte_size INTEGER,
    -- Terminal materialization state: ready or failed.
    state TEXT,
    -- Stable failure category without provider credentials or URLs.
    error_kind TEXT,
    -- Local receipt time for the attachment descriptor.
    created_at_ms INTEGER,
    -- Time at which materialization reached a terminal state.
    materialized_at_ms INTEGER,
    -- Non-sensitive attachment metadata encoded as JSON.
    metadata_json TEXT
);

-- Outbound command attempts, fresh-check evidence, provider receipt, and delivery state.
CREATE TABLE outbound_messages (
    -- Stable local identifier for one outbound command attempt.
    outbound_message_id TEXT PRIMARY KEY,
    -- Stable identifier of the originating command invocation.
    command_id TEXT,
    -- Application-managed association to a bcn session.
    session_id TEXT,
    -- Application-managed association to a channel session.
    channel_session_id TEXT,
    -- Canonical target supplied to the send command.
    target TEXT,
    -- Outbound message body captured for retry and audit.
    body TEXT,
    -- Provider-neutral outbound content format; ordinary messages default to markdown.
    content_format TEXT,
    -- Optional local inbound message identity the runtime intends to reply to.
    reply_to_message_id TEXT,
    -- Application-managed delivery lifecycle state.
    state TEXT,
    -- Application-managed fresh-check result.
    fresh_check_state TEXT,
    -- Inbound snapshot boundary used by the command.
    snapshot_seq INTEGER,
    -- Current inbound boundary observed during fresh-check.
    current_inbound_seq INTEGER,
    -- Provider-native message identifier after provider acceptance.
    provider_message_id TEXT,
    -- Controlled reference to the provider delivery receipt.
    provider_receipt_ref TEXT,
    -- Creation time of the outbound attempt.
    created_at_ms INTEGER,
    -- Time at which the provider call was attempted.
    provider_attempted_at_ms INTEGER,
    -- Completion time of the provider call.
    completed_at_ms INTEGER,
    -- Time at which a refused draft was persisted.
    draft_saved_at_ms INTEGER,
    -- Stable application error category for the attempt.
    error_kind TEXT,
    -- Redacted summary of the outbound failure.
    error_message TEXT,
    -- Human- and machine-actionable next step.
    next_action TEXT,
    -- Non-sensitive outbound metadata encoded as JSON.
    metadata_json TEXT
);

-- Per-session delivery cursor and the latest inbox snapshot used by fresh-check.
CREATE TABLE consumer_cursors (
    -- Stable bcn session identifier used as the cursor record identity.
    session_id TEXT PRIMARY KEY,
    -- Highest inbound sequence already delivered by check.
    delivered_through_seq INTEGER,
    -- Latest inbound sequence observed by check or read.
    inbox_snapshot_seq INTEGER,
    -- Operation that produced the latest snapshot.
    inbox_snapshot_source TEXT,
    -- Time at which the latest snapshot was recorded.
    inbox_snapshot_at_ms INTEGER,
    -- Last check operation time.
    last_check_at_ms INTEGER,
    -- Last read operation time.
    last_read_at_ms INTEGER,
    -- Last cursor or snapshot update time.
    updated_at_ms INTEGER
);

-- Append-only operational and audit events with cross-component correlation fields.
CREATE TABLE runtime_events (
    -- Node-local monotonic sequence for the event log.
    event_seq INTEGER PRIMARY KEY,
    -- Stable event identifier for external correlation.
    event_id TEXT,
    -- Event creation time.
    created_at_ms INTEGER,
    -- Normalized log severity.
    level TEXT,
    -- Stable event name.
    event_name TEXT,
    -- Application-managed event state.
    state TEXT,
    -- Event duration when the operation has completed.
    duration_ms INTEGER,
    -- Node identifier that emitted the event.
    node_id TEXT,
    -- Channel adapter name associated with the event.
    channel TEXT,
    -- Runtime adapter name associated with the event.
    runtime TEXT,
    -- Channel session correlation identifier.
    channel_session_id TEXT,
    -- Bcn session correlation identifier.
    bcn_session_id TEXT,
    -- Agent runtime session correlation identifier.
    runtime_session_id TEXT,
    -- Runtime turn correlation identifier.
    turn_id TEXT,
    -- Provider or protocol request correlation identifier.
    request_id TEXT,
    -- Local command correlation identifier.
    command_id TEXT,
    -- Related inbound message sequence when available.
    inbound_seq INTEGER,
    -- Related outbound message identifier when available.
    outbound_message_id TEXT,
    -- Stable application error category.
    error_kind TEXT,
    -- Runtime error type after redaction.
    error_type TEXT,
    -- Redacted error summary.
    error_message TEXT,
    -- Controlled reference to a retained traceback.
    traceback_ref TEXT,
    -- Non-sensitive event metadata encoded as JSON.
    metadata_json TEXT
);

-- Immutable migration ledger and checksum verification record.
CREATE TABLE schema_migrations (
    -- Monotonic migration version used for application-level ledger checks.
    version INTEGER PRIMARY KEY,
    -- Human-readable migration name.
    migration_name TEXT,
    -- Migration content checksum.
    checksum TEXT,
    -- Migration application time.
    applied_at_ms INTEGER,
    -- Migration execution duration.
    duration_ms INTEGER
);
```

#### 4.2.2 约束、索引与非表状态

- DDL 只保留用于物理行定位的 `INTEGER PRIMARY KEY`、本地 identity primary key 或消息
  UUIDv7 primary key；不把它们当作跨表关联、业务唯一性或状态校验。应用层必须在写入前
  校验 required fields、ID 格式和对象归属。
- `channel_sessions` 的逻辑查找键是
  `(channel, provider_conversation_key, provider_thread_key)`。没有 thread 的 provider
  identity 由 adapter 规范化为空字符串或显式 sentinel；repository 在 session lock 内
  执行 get-or-create，发现重复绑定时返回已有记录或冲突错误。
- `bcn_sessions.channel_session_id` 与 `runtime_sessions.bcn_session_id` 表达 MVP 的
  一对一绑定，但由 application/repository 在同一 session lock 与显式事务中读取并校验，
  不使用 SQL unique 或 foreign-key constraint。
- 同一 runtime session 同时最多一个 `starting/running/unknown/reconciling` turn 是
  application state machine 的不变量。repository 在事务内检查 active turn，再插入或更新
  turn；`provider_turn_id` 只是对账键，不作为本地 turn 主键。
- `inbound_messages.message_id` 是 repository 生成的 UUIDv7 primary key；`seq` 仍是独立的
  node-local monotonic sequence，用于 cursor/snapshot 边界并建立普通索引。append-only
  语义下不做 delete，因此 node 重启后继续递增。provider 去重逻辑键是
  `(channel, provider_message_id)`，由 adapter/repository 在同一事务中检查，不能只按
  provider message id 全局去重。
- `mentions_agent` 是统一 inbox message 的 provider-neutral 提及标记；每个 Channel
  adapter 各自解析 provider 的 @/mention 语义，但进入 core 后都必须映射为这一布尔字段。
  `notifies_runtime` 是 core 在同一 inbound transaction 中按 target kind、当前 following
  和 mention transition 计算的不可变决策。两者都是应用层布尔值，不用 SQLite
  `CHECK` 代替 core 校验。DM 必须始终 `notifies_runtime=true`；group 在显式
  mention 时先将 session 转为 following，再将当前 inbound 标记为可通知。
- `inbound_attachments` 与 `inbound_messages` 是一对多关系；`ordinal` 在同一消息内稳定且
  连续。`kind` 只帮助 Runtime 选择读取工具，首版至少覆盖 `image`、`audio`、`video`、
  `file`，未知类型降级为 `file`，不形成不同存储模型。provider 原名是不可信展示数据，
  不参与目录或最终文件名生成；媒体 URL、AES key、response URL 等临时 credential
  不进入该表、普通 metadata、日志或错误文本。
- ready 附件的 `relative_path` 由共享 `AttachmentMaterializer` 生成，固定为
  `attachments/<attachment-id>/content.<ext>`；扩展名从验证后的 media type 映射，未知时使用
  `.bin`。路径始终相对共享 workspace，不能接受 Channel 提供的绝对路径、`..`、symlink
  跳转或原始文件名拼接。failed 附件没有 `relative_path`，只保留稳定 `error_kind`；
  Runtime 不会得到指向不存在文件的伪路径。
- `outbound_messages` 记录的是 send command attempt，而不只是已经送达的 provider message。
  fresh-check 拒绝时写 `state='rejected'`、`fresh_check_state='failed'/'required'` 和
  `draft_saved_at_ms`，因此 `Draft saved: yes` 有本地审计依据且不会伪称 provider 已调用。
- `reply_to_message_id` 只保存 runtime 选择的本地 inbound message id；command service
  在创建 outbound 前校验该消息属于当前 bcn session 且 canonical target 兼容，再将
  中立 reply reference（本地 id、provider message id、target）交给 Channel。它是
  best-effort 发送提示：Channel 可以映射为 provider reply，也可以忽略并按普通消息发送；
  core 不记录或返回 provider 是否真实应用引用回复。
- `snapshot_seq` 是 command 使用的最近观察边界，`current_inbound_seq` 是 fresh-check
  重新读取到的当前边界；二者必须同时保存，便于解释“为什么拒绝”。`read` 返回的历史
  window 末 seq 不等于 snapshot seq；snapshot 始终记录该 session 当时的最新 inbound
  最大 seq。
- `runtime_events` 的关联列保持普通字段：启动失败、session 创建失败或 runtime 崩溃时也
  必须能够写入错误事件。repository 只允许 INSERT；如实现成本可控，append-only 保护放在
  repository API、事务和测试中，而不是依赖 SQL trigger，避免把另一类 storage 锁定在
  SQLite 约束语义上。
- 建议建立以下索引：

  ```sql
  CREATE INDEX idx_inbound_session_seq
      ON inbound_messages (session_id, seq);
  CREATE INDEX idx_inbound_seq
      ON inbound_messages (seq);
  CREATE INDEX idx_inbound_channel_received
      ON inbound_messages (channel_session_id, received_at_ms);
  CREATE INDEX idx_inbound_attachments_message_ordinal
      ON inbound_attachments (message_id, ordinal);
  CREATE INDEX idx_outbound_session_created
      ON outbound_messages (session_id, created_at_ms);
  CREATE INDEX idx_outbound_state_created
      ON outbound_messages (state, created_at_ms);
  CREATE INDEX idx_runtime_sessions_state
      ON runtime_sessions (process_state, updated_at_ms);
  CREATE INDEX idx_runtime_turns_session_state
      ON runtime_turns (session_id, state, started_at_ms);
  CREATE INDEX idx_runtime_events_session_seq
      ON runtime_events (bcn_session_id, event_seq);
  CREATE INDEX idx_runtime_events_name_seq
      ON runtime_events (event_name, event_seq);
  CREATE INDEX idx_runtime_events_created
      ON runtime_events (created_at_ms);
  ```

审批请求在当前 MVP 不单独建表：WeCom 永远自动批准，approval request 的生命周期绑定
runtime process，`approval.requested`/`approval.decided` 及 `request_id` 写入
`runtime_events` 即可。引入人工审批或跨重启 pending approval 前，必须新增独立 approval
表和恢复协议，不能把 event log 伪装成可恢复队列。

#### 4.2.3 migration 与 repository 边界

1. Migration 以 `BEGIN IMMEDIATE` 包住 DDL 和 `schema_migrations` ledger 写入；同一
   `version` 的 `migration_name` 或 checksum 不一致时由 application ledger check 触发启动
   失败，不能静默跳过。
2. 首个 migration 创建 `schema_migrations`、`node_state` 和 singleton row；随后按
   `channel_sessions/bcn_sessions/runtime_sessions/runtime_turns`、message/attachment/cursor/
   outbound、`runtime_events` 的依赖顺序创建表和索引。`node_state.schema_version` 是 ledger
   最新 version 的缓存，不是第二套迁移事实源。
3. SQLite schema 只声明列、注释和查询索引，不声明 `NOT NULL`、`DEFAULT`、`CHECK`、
   `UNIQUE` 或 `FOREIGN KEY`；业务必填项、默认值、关系、去重和状态迁移由 core/application/
   repository port 实现，storage adapter 必须提供等价语义。每个 SQLite connection 都显式
   配置 WAL、busy timeout；repository 不把 implicit transaction 当作并发边界，所有 cursor、
   fresh-check 和状态转换使用显式事务。
4. inbound、outbound body 和 provider identity 是业务所需的本地数据，不进入实时 info log；
   `metadata_json`、error message 和 provider receipt ref 在写入前执行统一脱敏。
5. MVP 不提供 inbound/runtime event 的 retention delete；未来要归档时必须先定义 seq
   边界、cursor 迁移和审计可查询性，再增加独立 archive migration。

### 4.3 一致性规则

1. Channel 先用 application 提供的 provider-neutral ingress gate 检查
   `(channel, provider_message_id)`；重复 frame 直接返回既有结果，不重复下载或生成文件。新
   inbound 的 provider-specific base64、临时 URL、AES payload 或 SDK media object 都在 Channel
   内解析，并通过 `ChannelContext.attachments` 的共享 `AttachmentMaterializer` 写入其管理的
   staging 文件；成功内容在校验大小与路径后原子 rename 到最终路径，失败则形成不含敏感信息的
   terminal descriptor。Channel 只有在所有附件均达到 `ready` 或 `failed` 后，才向
   application 交付统一 `InboundMessage`；其中不再包含 provider media schema，附件已经是本地
   id、展示名、kind、media type、workspace-relative path/failed category。application 随后在
   显式事务和 channel/session lock 内再次校验 provider id，分配本地 `seq`、get-or-create
   channel/bcn session、执行 following transition 和 `notifies_runtime` 决策，并 append inbound
   message/attachments。事务提交后正文与全部附件描述同时进入 `check`、`read`、unread 和
   Runtime notice。进程崩溃或事务回滚遗留的 staging/无引用最终文件由启动 reconciliation 清理，
   不能生成第二条 message。DM session 创建后固定 following，unfollow 不改变状态；group
   session 默认 unfollowed，`mentions_agent=true` 在同一事务中将其转为 following。provider id
   去重、channel 到 bcn/runtime 的关联以及必填字段校验全部由 application/repository 执行，
   不依赖 SQL unique、foreign-key 或 check 约束。
2. `message check` 只从当前 consumer cursor 读取 `notifies_runtime=true` 的后续消息，
   按 drain 语义推进 cursor；已落库的 quiet group inbound 不计入 unread/reminder。
   `message read` 是纯历史读取，可返回同一 session 的 quiet 与 notifying inbound 供开始
   following 后补齐上下文，但不回溯改写它们的 `notifies_runtime` 也不推进 cursor。
   check/read 都记录本次实际观察到的最新 inbox snapshot seq，供后续 `send` 做
   fresh check；snapshot 的更新不能偷偷推进已交付 cursor。
3. `message send` 必须在同一 bcn session 的串行 command 边界内执行 fresh check：将当前
   inbound 最大 seq 与该 session 最近一次 `check`/`read` 的 snapshot seq 比较。若发现更
   新 inbound，必须在调用 Channel port 前拒绝，不产生 provider send；保存安全 draft 和
   audit event，并返回带 `Error`、`Code`、`Draft saved`、`Next action` 标签的可执行理由。
   没有可用 snapshot 时也必须拒绝并要求先执行 `check`/`read`，不能把缺少上下文当成
   fresh。fresh check 通过后才写 `pending` outbound，再调用 Channel port；成功后写 `sent`
   和 provider receipt，失败后写 `failed`。数据库更新不能伪称为 Channel 已送达。
4. Channel 调用返回状态不确定时写入 `unknown`/可恢复状态并明确告知不能盲目重试；只有
   provider 明确确认失败时才写 `failed`。fresh check 的线性化点由 session command lock
   和 inbound append 的事务边界定义：在检查点前提交的新 inbound 必须触发拒绝，检查点后
   才到达的 inbound 不回溯改写已经开始的 outbound。
5. 不使用 task claim 或 lease 代替 cursor；并发控制只保证同一个 bcn session 的 turn 与
   cursor 操作有序。各状态迁移、active-turn 数量和跨表关联由 core state machine 与
   repository transaction 共同保证，SQLite 只是一个可替换的持久化实现。

## 5. Runtime 生命周期

### 5.1 启动

1. 解析 `--channel` 与 `--runtime`，拒绝未知或不兼容的 adapter name。
2. 使用固定的 `$HOME/.bcn` data directory，通过 storage port 启动持久化实现；SQLite adapter
   在自己的实现内部执行 migrations。
3. 业务层调用 `storage.initialize` 读取或生成唯一 workspace UUID，并创建共享 workspace。
4. 启动本地 command service，确保 `$HOME/.bcn/bin/` 下存在本次 node 生命周期对应的
   `bcc` wrapper。composition 按平台基线和已选 Runtime 通过
   `IRuntime.environment_variable_names()` 声明的扩展构造封闭的子进程环境白名单，
   PATH 由白名单中的宿主 PATH 前缀该 wrapper 目录得到，再由 bcn 生成
   `BCN_ENDPOINT` / `BCN_SESSION_ID` / `BCN_RUNTIME_SESSION_ID` /
   `BCN_COMMAND_CAPABILITY`。Runtime adapter 只能使用这份完整环境，不得再合并
   daemon `os.environ`。
5. 按已选择的 runtime/channel composition 恢复可恢复的 `runtime_sessions`；不假设上次
   进程仍然存在。
6. Channel adapter 开始接收 inbound。

本地 command service 必须有独立的 transport port。Unix 优先使用 Unix domain socket；
Windows 使用命名管道或等价的仅本机 IPC。若跨平台实现成本要求 fallback 到 loopback
TCP，必须使用每个 node 的随机 capability token、仅绑定 loopback，并把 endpoint/token
限制在临时目录权限内，不能暴露宿主凭据。

### 5.2 首次 inbound 与 session 创建

1. Channel adapter 解析 provider event，并在共享 ingress gate 确认不是重复消息后，将
   provider-specific 附件交给 `ChannelContext.attachments` 物化。adapter 最终只向抽象 port
   交付统一 `InboundMessage`：target kind、provider-neutral `mentions_agent` 以及零到多个已经
   含本地 relative path/failed category 的 `InboundAttachment`；不交付 URL、base64、AES key、
   SDK media object，也不决定 following。
2. Application 以 channel conversation/thread identity 取得 session lock；Storage 查找
   channel session，并再次校验 provider id 与全部 attachment path/terminal state。
3. 在同一事务中 get-or-create channel/bcn session，将统一 inbound message 与全部 terminal
   attachment descriptor 无条件落库；DM 固定
   followed，group 默认 unfollowed 并在 `mentions_agent=true` 的 inbound 时原子转为 followed。按转换后
   状态固定当前 inbound 的 `notifies_runtime`。
4. `notifies_runtime=false` 时事务提交后结束，不创建/启动 RuntimeSession、不增加
   unread count、不发 reminder。`notifies_runtime=true` 时才创建或恢复 runtime binding，
   启动该 bcn session 的独立 Codex App Server process，并设置共享 workspace 为 cwd 或
   runtime 支持的 workspace 参数。
5. 完成 `initialize`、`thread/start` 或恢复已有 thread，再按 notifying unread count
   发送 reminder turn。

### 5.3 Reminder 与消息工具

App Server wire request 只发送普通 `turn/start`，不直接写 Codex transcript 文件，也不
伪造 `response_item`：

```json
{"id":2,"method":"turn/start","params":{"threadId":"<codex_thread_id>","input":[{"type":"text","text":"[inbox notice session=<bcn_session_id>]\nInbox update: <n> unread message(s).\nUse bcc message check to read them."}]}}
```

runtime 进程通过 PATH 找到 `bcc`；wrapper 从 `BCN_SESSION_ID` 取得当前 bcn session，
command service 再从 IPC 连接校验并反查 runtime binding，不要求 agent 传裸 channel id。

wrapper 的调用面保持 shell-friendly 且与 session 中的既有工具调用形态一致：

```bash
bcc message check
bcc message read --target "<canonical-target>" [--around "<message-id>"]
bcc message send --target "<canonical-target>" [--reply-to "<local-inbound-message-id>"] <<'BCCMSG'
Message body.
BCCMSG
bcc message unfollow
```

正文从 stdin 读取，不放进命令行参数；成功结果写 stdout，已处理的参数、fresh-check 或
provider 错误写 stderr 并返回非零退出码。错误文本仍使用稳定的 `Error`、`Code`、可选
`Draft saved`、`Next action` 标签，避免 runtime 只能依赖退出码猜测是否可以重试。

`bcc message check` 的最小输出形态：

```text
[target=#work:6632e039 msg=25e7bff4 time=2026-08-04 19:25:39 type=human mentioned=true] @sender: message body
No more new messages.
```

有附件时不创建 Codex-specific image input，也不把附件伪装进正文；`check`/`read` 的同一
canonical serializer 在所属消息文本末尾追加一个 Raft-compatible inline suffix。ready
附件直接给出 Runtime cwd 可解析的 workspace-relative path，不再要求 agent 调用二次下载
命令：

```text
[target=#work:6632e039 msg=25e7bff4 time=2026-08-04 19:25:39 type=human mentioned=true] @sender: message body [1 attachment: image.png (id:019f0000-0000-7000-8000-000000000001, path:attachments/019f0000-0000-7000-8000-000000000001/content.png)]
No more new messages.
```

多附件按 `ordinal` 输出并使用复数 `attachments`；failed descriptor 保留原位置、local id 与稳定
failure category，但不输出 path：

```text
[2 attachments: image.png (id:019f0000-0000-7000-8000-000000000001, path:attachments/019f0000-0000-7000-8000-000000000001/content.png), report.pdf (id:019f0000-0000-7000-8000-000000000002, state:failed, error:download_failed)]
```

文件名只作 JSON-escaped 展示，不能包含未转义的换行或控制字符。suffix 属于普通 tool-result
文本，因此所有 Runtime 都能看到；ready path 的扩展名来自已验证 media type，Runtime 可据此
选择图片、音频或通用文件读取工具，但文件是否读取仍由 agent 决定。`check` 与 `read` 必须对
同一 message/attachment snapshot 生成逐字相同的 suffix，避免一次消费后路径或状态漂移。

`bcc message read` 负责历史窗口与定位字段，例如 local seq、完整 UUID、thread id 和
reply target；每次 `check`/`read` 都返回或内部记录 inbox snapshot seq。`bcc message send`
负责 target 校验、fresh check、outbound log 和 provider receipt。`bcc message unfollow` 只作用于
当前 `BCN_SESSION_ID` 绑定的 group channel session，在 session command lock 内将 following
转为 false 并记录 audit event。DM 或已是 unfollowed 的 group 调用都是幂等 no-op：
返回成功 exit code，stdout/stderr 均为空，不改变状态。工具结果
必须保留 sender identity，不能只返回无来源的正文。

四类工具都使用面向 agent 的纯文本 stdout，不把 provider SDK 对象直接暴露给 runtime：

```text
# check: canonical inbound envelope
[target=<canonical-target> msg=<short-message-id> time=<provider-time> type=human mentioned=<true|false>] @sender: message body
No more new messages.

# read: historical window with positioning fields
Read window: <n> returned, seq <first-seq>-<last-seq>, oldest to newest.
[1/<n> seq=<local-seq> msg=<full-message-id> time=<provider-time> type=human mentioned=<true|false> notifiesRuntime=<true|false> threadId=<thread-id> replyTarget=<canonical-target>] @sender: message body

# send: confirmed outbound receipt
Message sent to <canonical-target>. Message ID: <outbound-message-id>

# unfollow: confirmed group mention/following transition
Session unfollowed. New group messages will remain in history without notifying this runtime until attention is requested again.
```

`check` 的 envelope 保留 agent 判断来源所需的 target、短消息 id、time、type、sender、
`mentioned` 和 body；`read` 额外提供完整 message id、local seq、`notifiesRuntime`、threadId
和 replyTarget，便于定位、区分 quiet history 与构造回复。`mentioned` 是统一 wire 名，
对应 core `InboundMessage.mentions_agent`；Runtime 不需要理解任何 provider mention schema。
`send --reply-to` 表达中立的“尝试回复某条 inbound”意图；参数使用 bcn 本地 message id，
不接收 provider-native id。未指定时是普通发送。Channel 能力不支持时忽略该提示并继续
发送正文；core 不关心、不记录也不在 receipt 中报告引用回复是否真实发生。
`send` 成功必须返回可追踪的 message id，不能只返回 boolean；provider receipt 及
本地 delivery 状态同时写入 outbound log。若 provider 只确认已排队而未确认送达，输出应
使用明确的 queued/unknown 状态，不能伪称为 sent。unfollow 完成后到达的 group
inbound 仍然落库供 `read` 查看，但不进入 `check` 或 reminder；下一条
`mentions_agent=true` 的 inbound 会在同一事务中重新开启 following。

`bcc message send` 从 stdin 接收的普通正文默认是 `content_format=markdown`；纯文本是其
自然子集，不需要额外 text 模式。core 只保存正文与中立格式，Channel adapter 按
`delivery_constraints` 将其映射到 provider 支持的消息类型；不支持 Markdown 的 Channel 必须
明确声明降级/拒绝策略，不让 Runtime 感知 provider wire type。

`bcc message send` 的 fresh check 是发送前的安全门：command service 需要拿到同一 session
最近的 snapshot，并在 session command lock 内重新读取当前 inbound 最大 seq。当前值大于
snapshot，或 snapshot 缺失时，均不得调用 Channel provider。拒绝输出采用稳定标签，便于
runtime 判断是需要先读消息还是可以安全重试：

```text
Error: New inbound message(s) arrived after the latest inbox snapshot; outbound send was refused.
Code: SEND_FRESH_CHECK_FAILED
Draft saved: yes
Next action: Run `bcc message check` to read the new messages, then retry `bcc message send` if still appropriate.
```

snapshot 缺失使用 `SEND_FRESH_CHECK_REQUIRED`，`Next action` 指向
`bcc message check` 或 `bcc message read`。`Draft saved: yes` 只表示本地已保存可安全重试
的 draft，且本次没有 provider call；若 provider call 已开始但返回状态不确定，必须使用
单独的 unknown 状态，不能伪装成 fresh-check 拒绝或允许盲目重试。所有拒绝都沿用同一组
可机器解析的标签；例如 target 不可回复时仍然返回 `Error`、`Code`、`Draft saved`、
`Next action`，而不是抛出未格式化的 provider exception：

```text
Error: Thread target is not found or is not replyable: <canonical-target>
Code: SEND_FAILED
Draft saved: yes
Next action: Run `bcc message read` or `bcc message check` for this target to verify whether the message already landed; retry only after stable verification.
```

### 5.4 Codex turn 与审批

- 每个 bcn session 一个 process supervisor；多个 bcn session 可以并发运行。
- 同一个 Codex thread 同时只允许一个 active turn；不同 session 之间不共享 turn lock。
- Codex 反向 approval request 由 runtime adapter 转成 `ApprovalRequest`，由当前 session
  的 Channel port 决定 `ApprovalDecision`；每个 Channel contrib 必须实现自己的审批策略。
- WeCom MVP 的审批实现永远返回 approved，不等待人工输入；不能使用会阻止 approval
  request 发出的 `approvalPolicy=never` 来冒充自动批准，具体 Codex policy 字段以锁定的
  App Server schema 为准。
- runtime adapter 使用原始 request id 回写 Channel 决策，保证一次 approval request
  只对应一次 Codex response；Channel 不需要理解 Codex JSON-RPC payload。
- `turn/started`、item started/delta/completed、error、`turn/completed` 转成中立 runtime
  event；只由 `turn/completed` 更新 turn 的最终状态。
- `error.willRetry=true` 时保持 turn running，等待最终事件；不能仅凭 error notification
  结束本地 session。
- Codex turn completion 不代表 Channel outbound 已送达；两套状态分别持久化。

### 5.5 断线、异常和关闭

1. 子进程 EOF、协议解析失败或异常退出时，将 active local turn 标记为 `unknown`，不把
   未确认的 provider 状态写成 failed。
2. 按退避策略重启 process，并重新 `initialize`。
3. 使用 `thread/read(includeTurns=true)` 对账，判断 thread 是否已经完成、失败或仍有
   可恢复 turn。
4. 对可以恢复的 conversation 继续发送下一次 turn；无法判断的 turn 生成可审计的
   unknown 状态，避免重复执行不可逆操作。
5. graceful shutdown 先停止接收新 inbound，再等待 bounded 的 IPC/SQLite flush 和
   runtime process cleanup；超时留下可恢复状态。

## 6. Channel adapter 设计

首个 Channel adapter 是 WeCom intelligent Bot，但 core 只依赖抽象 Channel port：

- `receive`：消费 WebSocket 的 `aibot_msg_callback`，在 adapter 内抹除 provider
  message/media 差异；必要时先调用 context 中的共享 attachment materializer，最终只向
  application 交付带本地附件路径的统一 `InboundMessage`、channel session identity 和
  canonical target。`aibot_event_callback` 是独立的 provider event stream；Task 5A 识别并
  计数但不把它伪装成用户消息触发 Runtime。`enter_chat`、`template_card_event` 和
  `feedback_event` 当前均不支持，也不进入后续 task；WeCom event type 不扩散进 core。
- `send`：接收包含 canonical target、body、content format 和可选中立 reply reference 的
  `ChannelSendRequest`，根据平台能力映射关联被动回复、主动发送与消息级 reply，
  仅返回 provider-neutral delivery receipt；是否真实应用消息级 reply 不进入 core 状态。
- `request_approval`：接收 core 的中立 `ApprovalRequest`，返回 `ApprovalDecision`；每个
  Channel contrib 自己实现审批策略。
- `delivery_constraints`：表达 provider 是否能投递全量群消息等平台约束，只用于
  运维可观测性与产品限制提示，不改写 core following state machine。

Channel factory 从无参调用改为接收中立 `ChannelContext`；context 包含 `node_id`、所选
Channel 的非敏感配置、provider-neutral ingress dedupe gate，以及共享
`AttachmentMaterializer` capability。materializer 接受 bytes 或 bounded async byte stream，
统一负责 staging、大小/配额校验、受控路径、atomic rename 和 relative-path descriptor；它不
接受 provider URL、AES key 或 SDK object。base64 解码、provider-authenticated download、AES
解密等差异仍由对应 Channel 完成，再把明文字节流交给 materializer。WeCom 的 `bot_id` 来自
持久配置 `[channel.wecom]`，Secret 固定从 `BCN_WECOM_BOT_SECRET` 读取，缺失时在连接前 fail
closed。Secret 只由 WeCom factory/client 持有，不进入 Runtime context、wrapper、SQLite、
日志、异常文本或 health 响应。

Bot 面向外部用户，Runtime 处理的是不可信输入，因此 Runtime 子进程不继承 daemon
完整环境。composition 统一按“平台基线 + 已选 Runtime 显式扩展 + 用户显式追加 +
bcn 生成值”构造封闭白名单：

- POSIX 基线仅继承 `HOME`、`PATH`、已存在的 `TMPDIR` 与 UTF-8 locale 所需的
  `LANG` / `LC_ALL` / `LC_CTYPE`；Windows 基线仅继承 `PATH`、`USERPROFILE`、
  `HOMEDRIVE`、`HOMEPATH`、`APPDATA`、`LOCALAPPDATA`、`SystemRoot`、`WINDIR`、
  `COMSPEC`、`PATHEXT`、`TEMP` 和 `TMP`。实现前通过 Linux/macOS/Windows 真实
  process matrix 收敛到实际必需集合；新增名称需要真实失败证据，删除后要复验，不因
  当前 daemon 恰好存在就继承。
- Runtime 扩展是各 contrib 通过 `IRuntime.environment_variable_names()` 声明、由
  application 统一校验和读取值的封闭集合；application 不按 adapter name 分支，也不包含
  provider-specific 变量名。Codex 首版只声明官方公开的非 token 位置/证书变量：
  `CODEX_HOME`、`CODEX_SQLITE_HOME`、`CODEX_CA_CERTIFICATE` 和 `SSL_CERT_FILE`；缺失值
  就使用 Codex 默认，不传空字符串。
- 持久配置允许用户在 `[runtime.env]` 的 `include` 中追加精确变量名列表；值仍只从
  daemon 环境读取。不支持通配符、前缀匹配、直接配置值或“继承全部”开关；
  任一已配置名在 daemon 环境中缺失时都在启动 Runtime 前 fail closed。这一层是
  operator 对暴露面的显式授权；配置和启动日志应提醒 token/key 类变量会被
  Runtime 及其工具读取，但绝不回显变量值。
- `BCN_*` 是 bcn 保留 namespace；除 bcn 自己生成的 session 变量外不可由用户追加。
  已选 Channel/Storage/Audit adapter 注册的 credential 名（本期包含
  `BCN_WECOM_BOT_SECRET`）也是强制拒绝集，不能被 `[runtime.env]` 覆盖。MVP 的
  Codex 认证默认使用 `CODEX_HOME` 下的已持久登录或 OS credential store；若用户确实
  选择追加 runtime token/API key，则这是明确接受该变量对 Runtime 可见，不得记录为 bcn
  默认安全边界。
- bcn 生成值只有前缀 wrapper 目录的 `PATH`、`BCN_ENDPOINT`、
  `BCN_SESSION_ID`、`BCN_RUNTIME_SESSION_ID` 和 `BCN_COMMAND_CAPABILITY`。
  `RuntimeCommandContext.environment_for_session` 改为必填并返回完整权威环境；所有
  Runtime adapter 必须把它原样作为 subprocess `env`，不得再合并 `os.environ`。

配置形态固定为：

```toml
[runtime.env]
include = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]
```

`include` 只保存名称，不保存值；上例仅演示显式追加语义，不把 proxy 变量变成
默认白名单。

白名单只是减少外部输入直接诱导 Runtime 枚举环境时的凭据暴露面；同 OS 用户下的
文件、credential store 和其他进程仍不是强隔离边界。若威胁模型要求抵御恶意 Runtime，
后续必须使用独立 OS identity/process、受限 filesystem 或 secret broker，不能把环境白名单
宣称为完整 sandbox。

当前 WeCom 实现的 `request_approval` 永远返回 approved；后续可替换为人工审批，但不把
Codex JSON-RPC request payload 暴露给 Channel 实现。

WeCom adapter 不特判 following：群聊 inbound WebSocket frame 中的明确提及映射为统一
`mentions_agent=true`，core 照常开启 following；之后若 provider 投递 inbound，core 照常持久化
并通知 Runtime。但新版 WeCom 智能机器人不投递未再次明确提及机器人的群消息，
因此 following 期间这些消息根本不进入 bcn，也无法由 cursor 或 Codex thread 补回。
这是 WeCom capability gap，不是 core 分支；Telegram 等能投递全量群消息的 Channel 可以完整
实现同一 contract。切换到同一 WeCom Bot 的 URL callback 只改 transport，不改变该投递能力。

## 7. 日志与可观测性

日志分成实时进程日志和持久化运行事件两层，二者不能互相替代：

### 7.1 实时进程日志

- `app/cli` 在 composition root 只初始化一次进程 logger，默认写 stderr；当 stderr 不是
  TTY 时关闭颜色，避免日志被下游采集器污染。
- 日志不默认写独立文件；需要持久追踪时依赖 SQLite 运行事件。后续可以增加 OpenTelemetry
  exporter，但 node MVP 不依赖远程 telemetry backend 才能诊断。
- 使用稳定的事件前缀和显式关联字段，避免只输出自然语言句子。首版事件名包括：

  ```text
  node.start
  node.stop
  channel.inbound.received
  session.created
  runtime.process.started
  runtime.process.exited
  runtime.turn.started
  runtime.turn.completed
  approval.requested
  approval.decided
  bcc.command.started
  bcc.command.completed
  bcc.send.fresh_check.passed
  bcc.send.fresh_check.failed
  channel.outbound.pending
  channel.outbound.sent
  channel.outbound.failed
  ```

- 每条相关日志尽可能包含 `node_id`、`channel`、`runtime`、`channel_session_id`、
  `bcn_session_id`、`runtime_session_id`、`turn_id`、`request_id`、local `seq` 和
  provider id；未知字段省略，不使用伪造值。
- runtime、Channel、storage、IPC 和 approval 的异常边界使用带 traceback 的异常日志；
  observer/logging failure 不能覆盖原始业务错误。异常同时转换成 core 的稳定
  `error_kind`、摘要和可审计状态。
- `bcc` 命令只记录命令名、参数摘要和耗时；正文、完整参数、provider payload、token 和
  credentials 默认不进入 info 日志。需要 debug 内容时必须先脱敏并设置明确的大小上限。
- 如果未来增加交互式 CLI channel，它可以暂时移除或替换进程日志 sink，避免后台日志
  干扰交互式渲染；daemon channel 不应因此丢失错误记录。

### 7.2 持久化运行事件

SQLite 增加独立的 `runtime_events` append-only 表，与 inbound/outbound message log、
Codex turn 状态和 delivery 状态分开：

- `seq`：node-local monotonic event sequence。
- `created_at`、`level`、`event_name`、`status`、`duration_ms`。
- `node_id`、channel/runtime、三方 session ids、turn/request/provider ids 和相关 message
  seq。
- `error_kind`、`error_type`、`error_message`、脱敏后的 traceback 摘要或引用。
- 受控的 `metadata` JSON；禁止保存 credentials 和未脱敏 provider payload。

所有 session lifecycle、runtime process/turn、approval、bcc command、Channel inbound/
outbound 和 recovery action 都写入运行事件。SQLite 事件用于重启后的诊断和状态对账；
stderr 日志用于实时堆栈，不把一次 logger 输出当成唯一事实来源。

### 7.3 日志验收

- 正常启动、session 创建、runtime turn、bcc command、approval、Channel delivery 和
  shutdown 都产生可检索的事件序列。
- 异常边界的 stderr 日志包含 traceback；对应 SQLite event 至少包含稳定错误类型、摘要、
  关联 session 和处理状态。
- 多 session 并发时事件不能串 session；日志和 audit event 不泄露凭据、完整消息正文或
  未脱敏 tool/provider payload。

## 8. 实施顺序

### Task 0：async application bootstrap

- 保留参数解析、help 和 version 等纯同步 CLI 行为；同步 console entrypoint 只负责解析参数，
  然后把正常运行路径交给一次 `asyncio.run(async_main(...))`。
- 建立最小 async application lifecycle：startup、bounded shutdown、取消传播和资源清理由
  composition root 统一负责；下层模块不得自行创建或持有 event loop。
- 此步不接入真实 Channel、runtime、SQLite 或 IPC，只提供后续 adapter 可以依赖的 async
  生命周期边界。
- 使用真实 packaged process 验证 `bcn --help`、正常退出和 SIGINT 路径，确保入口改造不
  破坏现有命令行为。

依赖：无。产出：async application entrypoint、lifecycle skeleton 和真实进程 smoke check。

### Phase 1：core contracts 与 Test adapter 可运行闭环

目标：建立不依赖真实 provider 或 SQLite 的 domain contract、session orchestration、Test
adapter 和最小可运行应用闭环；在进入真实持久化和 provider adapter 前，先用测试代码中的
可控 Channel/Runtime/Storage/Audit 验证从 inbound 到 runtime turn、`bcc` command 和 outbound
的完整路径。

#### Task 1A：domain model 与状态边界

- 定义 session、message、cursor、turn、delivery、approval 和 runtime event 的中立类型。
- 为 session lifecycle、turn lifecycle、outbound delivery、fresh-check 和 unknown 状态
  写出允许的状态迁移及终态语义；必填字段、默认值和关联关系由 model/repository 校验，
  不下沉到 SQLite DDL。
- 固定 ID、UTC milliseconds、local sequence、canonical target 和受控 metadata 的表示
  规则，使后续 storage、runtime、channel adapter 使用同一份语义。

依赖：无。产出：core domain types、状态迁移表和错误分类草案。

#### Task 1B：ports、并发和生命周期契约

- 定义 storage、runtime、channel、approval、command service 和 logger/audit 的 port；明确
  async contract、取消语义、超时边界、provider call 的 unknown 结果和关闭顺序。
- 明确同一 bcn session 的 command/turn/cursor 串行边界，以及不同 session 的隔离边界。
- 固定 core 不依赖 provider SDK、SQLite driver、具体 logger 或 IPC implementation 的依赖
  方向。

依赖：Task 1A 的 domain model。产出：port interfaces、并发边界和生命周期契约。

#### Task 1C：approval、audit 和 correlation contract

- 定义中立 `ApprovalRequest`/`ApprovalDecision`，以及 runtime request id 到当前 Channel
  approval handler 的关联规则。
- 定义 `AuditEvent`、correlation context、稳定 `error_kind` 和敏感字段脱敏边界；异常
  traceback 只作为受控摘要或引用，不让 provider payload 进入 core event。
- 让 runtime、Channel、storage、IPC 和 command service 能够使用同一组 session/turn/request
  correlation 字段。

依赖：Task 1B 的 ports、并发和生命周期契约。产出：approval/audit/correlation contract 和
错误边界。

#### Task 1F：agent lifecycle state machine 与 tick contract

- 在 core 定义统一 `AgentState`：`created`、`starting`、`idle`、`working`、压缩开始、
  压缩中、压缩结束、`stopping`、`stopped`、`failed`、`unknown`、`reconciling`；明确
  每个状态的持久化语义、终态语义和 unknown 不确定性边界。
- 提供纯 transition reducer 和中立 tick/signal contract；同一 session 的重复 tick 必须
  幂等，非法迁移必须 fail closed；runtime/channel 无法识别的活动状态统一 fallback 到
  `working`，不允许 runtime 或 Channel adapter 自己定义 agent 状态。
- 由 `SessionOrchestrator` 作为 agent state 的唯一 writer：session creation 驱动
  `created -> starting`，runtime 启动确认进入 `idle`，active turn 进入 `working`，turn
  terminal event 回到 `idle`，停止、失败和恢复分别进入对应状态；压缩阶段独立记录，工具
  调用解析后的 operation/arguments/status 只进入 audit log，不扩张 agent lifecycle state。
- 先用 Test adapter orchestration 验证正常 lifecycle、failure、unknown/reconcile、stop 和
  runtime/channel 交错 tick；不在本 Task 引入 provider-specific state 或新的 storage 实现。

执行顺序：Task 1C 之后、Task 1D 之前。依赖：Task 1A 的 domain model、Task 1B 的 lifecycle
port 和 Task 1C 的 correlation contract。后续由 Phase 2 持久化、Phase 5 Channel tick 和
Phase 6 restart/reconciliation 消费与扩展。

#### Task 1D：Test adapters 与 core orchestration harness

- 在 `tests/support` 中实现 `MemoryStorage`、`TestChannel`、`TestRuntime` 与 `RecordingAudit`；
  内存事务必须支持提交/回滚，不能把测试状态写进 SQLite 或 provider adapter。
- 实现 core session orchestration，驱动真实 contract、session routing、approval callback
  和状态转换；Test adapter 不得反向成为 core 或生产 contrib 的依赖。Orchestration 必须允许
  Channel inbound 触发 runtime reminder，并由 runtime adapter 回调 command service，而不是
  只在测试中直接调用 core 方法。
- 测试正常 inbound、turn completion、outbound delivery、provider failure、unknown turn、
  fresh-check refusal 和 graceful cancellation。
- 测试多个 session 的 cursor、turn、workspace identity 和 correlation 不串线。

依赖：Task 1F 的 agent lifecycle state machine、approval/audit/correlation contract。产出：不导入真实 provider 的 core
orchestration 测试套件、可替换的 Test Channel/Runtime 与内存 Storage/Audit adapters。

#### Task 1E：动态 composition、daemon lifecycle、command service 与 `bcc` 小闭环

- 在 `app/cli` 建立通用 composition root，解析 adapter name，并通过 Python entry point
  动态加载 provider factory；`app` 不包含 `TestNode` 或任何 provider-specific Node 类，
  新增 provider 只需安装 contrib package 并注册 entry point。未知、未安装或不兼容组合
  必须在启动前失败。
- 提供 `bcn start`、`bcn stop`、`bcn restart` 的 daemon lifecycle；start 将 foreground
  runner 脱离当前终端并复用持久化组合配置与稳定本机 endpoint，stop 通过本机 command
  transport 发送 shutdown，restart 复用同一配置。Unix 使用 socket 文件，Windows 使用
  per-user named pipe 与 named mutex，不额外持久化 PID/lock 文件。foreground runner 只作为内部进程和真实
  integration test seam，不改变 daemon 默认行为。
- 暴露 session-scoped `check`/`read`/`send` command service，并提供最小本机 command
  transport；transport 只传递 core command/result，不泄露 adapter/provider 对象。
- 生成可执行的 `bcc` wrapper，让 Test Runtime 使用 `BCN_SESSION_ID` 调用 command service；
  先覆盖当前开发平台的本机路径，同时保留跨平台 transport seam，生产级 Windows/Unix
  hardening 后置到 Phase 3。
- Test Channel 提供受控 inbound 注入和 outbound/approval 观察接口；Test Runtime 提供
  受控 turn script，能够在 turn 内实际执行 `bcc message check/read/send`，并把 command
  结果继续驱动后续 runtime 行为。
- 通过只安装在测试环境、不会进入生产 wheel 或生产 entry point metadata 的最小 test-plugin
  fixture 启动真实进程，验证 daemon ready、stop 和 restart，再通过其本机 endpoint 验证
  `Test Channel → core orchestration → Memory Storage → Test Runtime → bcc command service
  → Test Channel` 的全链路，以及多 session 的 check/read/send、approval、fresh-check refusal、
  provider failure、unknown turn 和 graceful shutdown；这一步不依赖 SQLite 或真实 provider，
  也不为生产 registry 增加 test override seam。

依赖：Task 1D。产出：可启动的 test-only node、最小 command service、`bcc` wrapper 和小闭环
integration tests。

Phase 验收：test-only plugin 只在测试环境暴露选中的 Test entry point，在不依赖 SQLite 的
情况下进入后台 ready；从 Test Channel 注入 inbound 后，Memory Storage 能观察到记录，
Test Runtime 能在 turn 内实际调用 `bcc` command 并收到结果，最终 outbound 能回到 Test
Channel。`bcn stop` 和 `bcn restart` 能通过本机控制面完成优雅生命周期，多 session 不串线，
退出时能清理 runtime、command service、storage 和运行态元数据；生产 distribution 不包含
任何 Test adapter entry point。

### Phase 2：SQLite 与 workspace

目标：提供可替换 storage port 的首个 SQLite contrib，实现计划 4.2 的字段、日志、cursor、
session mapping 和显式事务语义。

#### Task 2A：data directory、workspace 和 migration foundation

- 扩展 storage port 的 node initialization，SQLite implementation 实现 node identity 和共享
  workspace UUID 的持久化；workspace path 由业务启动阶段创建，位于稳定 data directory，
  不把业务状态放进临时目录。
- 建立 migration ledger、checksum/application-level version check、WAL、busy timeout 和
  显式 transaction helper；SQLite migration DDL 保持在 adapter-private 实现内，启动时发现
  同一 version 的 name/checksum 不一致必须 fail closed。
- 创建 4.2 中的 tables、column comments 和普通查询索引；DDL 不使用 domain `NOT NULL`、
  `DEFAULT`、`CHECK`、`UNIQUE` 或 `FOREIGN KEY`，primary row identity 也不代替业务关联。

依赖：Phase 1。产出：可重复启动的 SQLite schema 与 migration bootstrap。

#### Task 2B：session/workspace mapping repository

- 实现 `node_state`、`channel_sessions`、`bcn_sessions` 和 `runtime_sessions` 的读写，
  包括首次 inbound 的 get-or-create、workspace 绑定、三方 ID 关系和恢复时的 reconcile lookup。
- 在 repository transaction/session lock 内实现一对一 binding、required field、ID format
  和对象归属校验；重复逻辑 key 返回已有记录或明确冲突，不依赖 SQL unique/foreign key。
- 让 session state transition 只能经过命名操作，禁止通用 update 绕过 state machine。

依赖：Task 2A 的 migration/transaction foundation。产出：session/workspace repository 与
association invariant tests。

#### Task 2C：message log、cursor 和 inbox snapshot repository

- 实现 `inbound_messages` append-only log、node-local seq、provider-level application dedupe、
  repository-generated UUIDv7 message IDs、`consumer_cursors` 和独立 inbox snapshot。
- 固定 `check` 推进 delivered cursor、`read` 不推进 cursor、两者都更新 snapshot 的事务
  语义；snapshot seq 与 delivery cursor 不混用。
- 在显式事务与 session lock 下验证重复 inbound、并发 check/read、provider message id
  冲突和跨 session 隔离。

依赖：Task 2B 的 session/workspace repository。产出：message/cursor repository、snapshot
semantics 和真实临时 SQLite tests。

#### Task 2D：outbound、runtime turn 和 event repository

- 实现 `outbound_messages` 的 pending/sent/failed/unknown/rejected/draft 记录，以及
  `snapshot_seq`、`current_inbound_seq`、provider receipt 和 next action 审计字段。
- 实现 `runtime_turns` 和 `runtime_events` repository；event 只允许 append，turn 状态只
  允许合法 transition，错误事件即使 session 创建或 runtime 启动失败也能落库。
- 关联字段保持普通字段，由 application/repository 校验；不要用 trigger 或 SQLite-specific
  constraint 把 append-only 和 state machine 语义固化到 storage。

依赖：Task 2C 的 message/cursor repository。产出：outbound/turn/event repository 和状态
审计 tests。

Phase 验收：真实临时 SQLite 文件验证 workspace UUID 重启复用、migration checksum fail-closed、
重复 inbound 不重复入库、check/read/snapshot 语义、outbound unknown/rejected 边界、active
turn application invariant、runtime event append-only，以及显式事务下的多 session 并发；不
以 SQLite DDL 拒绝非法行作为验收依据。

### Phase 3：production local command service 与 bcc

目标：在 Phase 1 Test adapter 小闭环的 command contract 基础上，接入 SQLite-backed session
state，完成可审阅的跨平台本地 IPC、持久 wrapper 和三类 message command。

#### Task 3A：composition root 与 command service lifecycle

- 将 Phase 1 的 Test composition 扩展为真实 storage/channel/runtime 的 adapter factory，
  让 `app/cli` 暴露完整的 `--channel`、`--runtime` 组合；未知或不兼容 adapter name 在启动前清晰
  失败。
- 完善 command service 的启动、停止、health/error boundary 和 session-scoped dispatch；
  service 只调用 core ports，不把 provider SDK 对象返回给 wrapper。
- 固定一个 node process 可服务多个 bcn session，但每个 command 必须经过 session binding
  校验。

依赖：Phase 1 的 Test composition 与 Phase 2 的 SQLite/workspace。产出：production
composition root、command service lifecycle 和 dispatch contract。

#### Task 3B：local IPC、wrapper 和 session binding

- 将 Phase 1 的开发 transport 替换或升级为 Unix domain socket、Windows named pipe 或等价
  本机 IPC；loopback fallback 只允许随机 capability token、loopback bind 和受限 endpoint
  metadata。
- 将 POSIX `bcc` 与 Windows `bcc.ps1` 注入到 `$HOME/.bcn/bin/`，为每个 runtime process
  注入 PATH 和 `BCN_SESSION_ID`；node 退出时删除本次生成的 wrapper 文件；wrapper 不携带
  宿主凭据和管理能力。
- command service 端校验 IPC client binding、environment session id 和 bcn/runtime mapping，
  防止跨 session 串读 cursor、snapshot 或 outbound target。

依赖：Task 3A 的 service lifecycle。产出：跨平台本机 transport、wrapper 和 binding validation。

#### Task 3C：check/read 查询与 canonical serializer

- 实现 `bcc message check`、`bcc message read --target ... [--around ...]` 的参数解析、
  session routing、cursor/snapshot 调用和 canonical text serializer。
- 固定 stdout/stderr、退出码、sender identity、target、short/full message id、local seq、
  threadId、replyTarget 和历史窗口边界；不把 provider SDK object 泄露给 runtime。
- 以 Test Channel 和真实 SQLite repository 验证 check drain、read non-drain、snapshot
  更新、无新消息输出和 target 定位错误。

依赖：Task 3B 的 local IPC、wrapper 和 session binding。产出：check/read command contract
和 golden text tests。

#### Task 3D：send fresh-check safety gate 与 delivery command

- 实现 `bcc message send --target ...` 的 stdin body、target validation、fresh-check、draft
  保存、outbound audit 和 provider call ordering。
- 在同一 session command lock 内比较 snapshot 与最新 inbound seq；snapshot 缺失或有新 inbound
  时，provider call 前拒绝并返回稳定的 `Error`/`Code`/`Draft saved`/`Next action` 标签。
- fresh-check 通过后才写 pending 并调用 Channel；区分 sent、queued、unknown 和明确 failed，
  不把数据库写成功伪称 provider 已送达，也不允许 unknown 盲目重试。

依赖：Task 3C 的 check/read command contract。产出：send safety gate、delivery state machine
和 provider-call ordering tests。

Phase 验收：真实本机 IPC 启动的 runtime 子进程能找到持久 wrapper；check/read/send 在多 session
下不串线；新 inbound 或缺 snapshot 时 send 不触发 Channel provider call，并输出稳定 refusal；
fresh-check 通过后才进入 pending/send，unknown 与明确 failed 可区分。

### Phase 4：Codex App Server adapter

目标：在 runtime contrib 中建立可恢复的异步 process/protocol adapter，并把双向 request、turn
event 和 approval 转成 Phase 1 的中立 contract。

#### Task 4A：process supervisor 与 JSONL transport

- 实现 async subprocess supervisor、stdio JSONL reader/writer、启动/停止/EOF/parse failure
  边界和 request id routing；不得阻塞 event loop。
- 固定 runtime version probe、workspace/cwd 注入、子进程环境和 bounded shutdown；进程异常
  必须带关联 session/runtime 事件。
- 若 SDK async facade 存在阻塞或版本耦合，使用同一 runtime port 的 native
  `asyncio.create_subprocess_exec`，隔离 provider types。

依赖：Phase 3。产出：真实 process transport、supervisor lifecycle 和 failure classification。

#### Task 4B：thread/turn protocol adapter

- 实现 initialize、thread start/resume、turn start、event stream、interrupt 和 turn completion
  的 provider protocol mapping。
- 保存 provider thread/turn ids 与本地 runtime session/turn/client user message mapping；
  `turn/completed` 是唯一权威本地终态，`error.willRetry=true` 不能提前结束 turn。
- 将 provider event 转成中立 runtime event，保留必要 request/turn correlation，不把 provider
  wire schema 传播到 core 或 Channel。
- production 通过固定 `$HOME/.bcn/config.toml` 的 `[node]`/`[runtime]` 表及 CLI 覆盖值选择
  channel、runtime、storage、audit、model 和 effort；CLI 覆盖 config。`channel` 与 `runtime`
  必须由 CLI 或配置显式给出，没有内置默认值，缺少任一项都在 composition 前失败；storage
  缺省为 `sqlite`。Task 4D 移除临时 test audit 后，audit 缺省为 production `logging` adapter。
  model/effort 都未设置时省略对应 provider 参数，交给 provider 默认值。测试场景显式固定
  `gpt-5.6-luna` 与 `effort=max`，不得将测试选择写死在 production runtime/client。Unix 使用
  socket endpoint；Windows 使用 per-user named pipe 与 named mutex，不保存 PID/lock 文件或
  运行 provider 元数据。
- 真实场景验证不只断言 wire response：使用自然语言驱动真实 turn，验证同一 thread 的自然
  follow-up、跨进程 resume 后的自然 follow-up、两个真实 process 的交错 event stream、interrupt，
  以及 `error.willRetry=true` 保持非终态。不得断言模型的精确回答文本；应观察 provider/local
  thread-turn id、event 顺序、session correlation、resume history 和 terminal state。

依赖：Task 4A 的 process supervisor 与 JSONL transport。产出：thread/turn mapping、event
normalization 和 protocol tests。

#### Task 4C：approval bridge 与 runtime-facing bcc flow

- 将 runtime 反向 approval request 转成 `ApprovalRequest`，交由当前 Channel port 决定，再
  用原始 request id 回写 `ApprovalDecision`；一次 request 只允许一次 response。
- 在 runtime process 中配置共享 workspace、稳定 PATH 和 `BCN_SESSION_ID`，使 reminder turn
  能够调用 `bcc`，但不在 wire request 中伪造 transcript item。
- 记录 process/request/turn/approval/transport event；异常只写脱敏摘要或受控 traceback 引用。

依赖：Task 4B 的 thread/turn protocol adapter。产出：双向 approval bridge、reminder-to-bcc
integration。

#### Task 4D：Test adapter 边界与必填 provider selection

- 将 `contrib/dummy` 的可控实现全部迁到 `tests/support`：`DummyChannel`、`DummyRuntime`、
  `DummyTurnPlan` 分别改为 `TestChannel`、`TestRuntime`、`TestTurnPlan`，内存 storage/audit
  改为 `MemoryStorage` 与 `RecordingAudit`；同步迁移测试 fixture、adapter name、canonical target 和
  文件名，不保留 import、class、entry point 或 adapter-name 兼容 alias。
- 从 production package metadata 删除所有 dummy channel/runtime/storage/audit/control entry
  point；删除 `NodeApplication`、`AdapterRegistry`、`SessionOrchestrator` 中的 dummy 默认值。
  `start`/`run` 必须从 CLI 或持久配置显式解析非空 `channel` 与 `runtime`，不得回退到任何
  Test adapter；storage 继续缺省为 `sqlite`。
- 新增 production `logging` audit adapter，替换临时 dummy audit 默认值；`RecordingAudit`
  只供测试观察，不进入 production package、默认配置或 provider registry。
- 保留真实 subprocess、dynamic entry point、daemon start/stop/restart 和本机 control 验证，
  但通过只安装在测试环境的最小 test-plugin fixture 暴露 Test adapters；fixture 不进入
  production wheel，也不通过生产代码新增 factory override、环境开关或兼容分支。
- 固定可复用的双向 adapter contract matrix：core 使用 TestChannel+TestRuntime；真实 Codex
  使用 TestChannel+CodexRuntime；后续真实 WeCom 使用 WeComChannel+TestRuntime；完整产品
  验收使用 WeComChannel+CodexRuntime。真实 Channel 的 inbound 必须从其 provider ingress
  路径进入，TestRuntime 只替代 runtime；真实 Runtime 场景则必须经 TestChannel inbound port
  注入，不直接写 storage。
- 更新 README、配置示例、CLI/parser/registry/composition tests 和本计划中的 adapter 边界；
  验证 production distribution/entry point inventory 不含 Test adapter，`channel` 或 `runtime`
  缺失时启动 fail closed。

依赖：Task 4C。产出：test-only adapter support、production logging audit、必填 Channel/Runtime
selection、无测试 adapter 泄漏的 production package，以及可供 Phase 5 复用的 Channel
contract harness。Task 4D 完成并 review 后才能进入 Task 5A。

Phase 验收：真实 runtime process 使用 `model=gpt-5.6-luna`、`effort=max` 完成 initialize ->
reminder -> bcc check -> bcc send；自然对话场景验证 agent 会根据真实消息决定 check/read/send，
而不是机械执行固定 transcript。第二条真实 inbound 在第一次 check 完成后、第一次 send 前
到达时，必须触发 fresh-check refusal；agent 重新 check/read 后，若上下文不冲突则继续发送
兼容结果，若上下文冲突则重新思考并发送不同结果。验证 canonical target、stdin body、draft、
fresh-check state、provider receipt 和 audit，而不是精确回答文本。两个 session 并发时
process、thread、workspace、turn 和 SQLite mapping 互不混淆；provider retry notification
不错误终结本地 turn。不得用 fake/mock/httptest、直接写 SQLite 或生产注入制造这些场景；第二
条 inbound 必须从实际 Channel 入站路径进入。production package/entry point inventory 不含
Test adapters，`channel`/`runtime` 未显式配置时启动失败；TestChannel+CodexRuntime 的真实
自然问答与 TestChannel+TestRuntime 的 core/daemon contract 均通过。

### Phase 5：WeCom intelligent Bot WebSocket adapter 与端到端编排

目标：以新版「智能机器人 API 模式」的 WebSocket 长连接接入首个真实 Channel contrib，
把配置、连接所有权、provider identity、inbound、approval、outbound 和 session routing
汇合为可运行的端到端路径。当前协议依据优先级为企业微信官方文档、WecomTeam 官方 Python
SDK 与 Node.js SDK；旧群机器人 webhook、自建应用 XML callback 和 OpenClaw plugin 的兼容
分支不得进入该 adapter。同一个智能机器人同一时间只能选择 WebSocket 或 URL callback
其中一种接入方式；Phase 5 不尝试双开。

企业微信把 WebSocket inbound 的 `cmd` 命名为 `aibot_msg_callback` 和
`aibot_event_callback`；这里的 `callback` 只是 provider wire 命名，实际仍是现有
WebSocket 上的 inbound frame，不存在 HTTP callback server。本计划正文统一称为
“inbound WebSocket frame”，只在引用精确 `cmd` 或区分官方 URL callback 模式时保留原词。

实现前固定并记录以下协议基线；官方文档无法直接确认的字段以同组织 SDK 的不可变源码提交
与真实 Bot 帧交叉验证，不能把 SDK 行为上升为 provider 保证，也不直接依赖 SDK：

- 企业微信智能机器人 API 接入：<https://developer.work.weixin.qq.com/document/path/101463>
- 企业微信长连接配置：<https://open.work.weixin.qq.com/help2/pc/21661>
- 企业微信智能机器人帮助：<https://open.work.weixin.qq.com/help?doc_id=21657>
- 腾讯官方接入说明：<https://cloud.tencent.com/document/product/1759/121473>
- 腾讯官方群聊 @ / 单聊触发规则：<https://cloud.tencent.com/document/product/1813/134138>
- WecomTeam 官方 Python SDK：<https://github.com/WecomTeam/wecom-aibot-python-sdk>
- WecomTeam 官方 Node.js SDK 不可变基线：
  <https://github.com/WecomTeam/aibot-node-sdk/tree/80615b987ef69c6028ad764924609247c0725955>

#### Task 5A：配置、WebSocket lifecycle、inbound mapping 与 dedupe

- 先扩展 provider-neutral inbound contract 与 repository：新增 `mentions_agent` 与
  `notifies_runtime`，在单个 channel/session transaction 内完成去重、无条件 append、
  group mention/following transition 和 unread/reminder 决策。DM 始终 followed，group 默认
  unfollowed。Task 5A 先完成这个通用 ingress foundation，Task 5C 再增加 agent-driven
  unfollow command 和完整的继续编排，不把语义塞进 WeCom adapter。
- 在同一 provider-neutral inbound contract 中新增 `InboundAttachment`、
  `inbound_attachments` repository，以及由 application 通过 `ChannelContext` 提供给所有
  Channel 的共享 `AttachmentMaterializer`。各 Channel 先把 URL/AES、base64、SDK object 等
  provider 格式转换为明文 bytes/bounded async byte stream，再调用 materializer；materializer
  生成本地 UUIDv7，在共享 workspace 的 `attachments/<attachment-id>/` 下完成 staging、
  大小/配额校验、原子 rename 与 crash reconciliation，并返回带 relative path 的中立
  descriptor。Channel 向抽象 port 交付的 `InboundMessage` 已经只包含这些本地 descriptor；
  core/application 不再下载、解密或理解 provider media schema。`check`/`read` 使用同一
  canonical serializer，把 ready path 或 failed category 作为所属消息的 inline suffix 输出。
- 扩展持久配置与 composition：`[channel.wecom].bot_id` 是非敏感必填项；
  `BCN_WECOM_BOT_SECRET` 是唯一 Secret 来源。新增 `ChannelContext` 并将 Channel factory 改为
  context factory；未选择 WeCom 时不读取其配置或凭据，选中后缺少任一必填值都在连接前
  fail closed。
- 固定 WecomTeam 官方 Python/Node.js SDK 的不可变源码提交作为协议参考；使用
  `aiohttp` 编写 adapter-local thin client，不引入 SDK 的 event emitter、自动 `.env`、
  隐式重发或 provider 类型。连接默认 endpoint `wss://openws.work.weixin.qq.com`；私有部署
  endpoint 只能来自显式 `[channel.wecom].websocket_url`，不得作为隐藏环境覆盖。
- 建立连接后以 `aibot_subscribe` 帧发送 `headers.req_id` 与 `body.bot_id` / `body.secret`，
  只在对应回执 `errcode=0` 后进入 ready；认证失败、transport close、应用层 `cmd=ping`
  heartbeat/ack、指数退避、bounded shutdown 都映射为明确的 Channel lifecycle state。
  WebSocket control ping/pong 与应用层 JSON `cmd=ping` 是两层机制，不能只开启 library ping。
- 默认沿用官方 SDK 的客户端策略：30 秒应用层 heartbeat、连续 2 次 ack 缺失判死、1/2/4/8/
  16/30 秒退避上限；bcn 为重连加入 bounded jitter，并把网络重试预算与认证失败预算分开。
  这些数值是 SDK 策略而非企业微信公开 SLA，配置与日志不得描述为 provider 保证。
- 同一个 Bot 只允许一个 active connection owner。收到 `disconnected_event` 表示另一连接已
  建立时，当前 node 停止自动重连并进入可诊断的 degraded state，避免两个实例互相踢下线。
- 对 JSON object 原始 frame 做 adapter-local 宽松字段读取，未知字段不使 reader 崩溃；随后对
  `aibot_msg_callback` 直接映射并严格校验 core 必填值：`body.msgid` 是 provider dedupe key，
  `headers.req_id` 只用于本次被动回复/回执 correlation；`body.aibotid` 参与 account scope，
  群聊 conversation 使用 `body.chatid`，单聊使用 `body.from.userid`，并保留 `chattype`、
  `create_time`、`msgtype`、`quote` 与媒体的短期 URL/AES key 为受控引用。官方资料未确认
  `create_time` 单位，真实 Bot 验证前只保存 raw value，不猜测秒或毫秒。
- 将 DM 与 group target kind 及群聊的显式提及映射为统一 inbox 字段
  `mentions_agent`；DM 不依赖该信号且始终通知 Runtime。WeCom 群聊 inbound frame
  只能观察到已明确提及机器人的消息，因此映射为 `mentions_agent=true`；
  adapter 不直接读写 `ChannelSession.following`。
- 覆盖官方协议声明的 text/image/mixed/voice/file/video 与 event 类型；无法消费的类型必须
  记录稳定的 unsupported classification，不能丢成空文本。按当前官方 SDK 类型，image/file/
  video 是五分钟有效的加密下载 `url` 加可选 `aeskey`；mixed 的 `msg_item` 分别映射 text 或
  image；voice 当前是 `content` 转写文本，不伪造为二进制附件。WeCom adapter 使用 `aiohttp`
  流式下载并在 `contrib/wecom` 完成 AES-256-CBC 解密，再把明文 bounded byte stream 交给共享
  materializer；文件名只能从受控的 `Content-Disposition` 解析为展示 metadata，adapter 不自行
  拼接最终路径。下载、解密与 workspace 物化必须在 URL 过期前完成；成功后 Channel 交付带
  ready local path 的统一 message，失败时交付无 path 的 failed descriptor。`response_url`、
  媒体 URL 和 AES key 都只存在于本次 ingress 的短期内存状态，按临时 credential 脱敏，不写
  SQLite、完整日志、异常、health 或普通 metadata。
- 以 `body.msgid` 接入 Phase 2 application-level dedupe 与 channel session lookup。重连后即使
  provider 重投同一 inbound frame 也只追加一次；没有官方 replay cursor 或补发保证时，不能把
  重连描述为能够找回断线期间消息。
- 在统一 Runtime 启动边界实现上述封闭环境白名单：先按平台复制最小基线，
  再加入当前 `IRuntime.environment_variable_names()` 声明的扩展、`[runtime.env]` 中
  `include` 的用户显式追加和 bcn session capability。application 对 Runtime 声明与用户追加
  做格式、保留 namespace 和 adapter credential 拒绝校验，只对用户显式追加执行存在性
  fail-closed；Runtime 声明的缺失可选值不传。修改
  `RuntimeCommandContext` contract 为必填的完整权威环境，并修正现有 Codex adapter
  的 `dict(os.environ)` + `update(...)` 合并行为。
- 加入结构性 credential-boundary 验证：daemon 环境注入 sentinel Channel/Storage/
  Audit/API token 后，实际传给 subprocess spec 的 key set 仍必须与平台/Runtime 白名单完全
  相等，且不含 sentinel；日志只记录 policy version/hash 和缺失的变量名，不记录值。
  Linux/macOS/Windows 再分别以真实 Codex App Server 的登录、initialize、自然对话、
  `bcc` 和 resume 覆盖验证精确白名单不破坏运行链路。
- 验证 WeCom Secret 和其他禁止 credential 不进入任意 Runtime 子进程环境、SQLite、
  wrapper、日志、异常或 health 输出；同时保留“同 OS 用户不是强隔离”的边界说明。

依赖：Task 4D 与命名收口 review。产出：真实 WebSocket lifecycle、message inbound mapping、
provider event 识别与计数、dedupe、provider-neutral attachment materialization、identity 与
credential-boundary tests；使用 WeComChannel+TestRuntime 独立验证真实 Bot ingress、routing
和媒体落盘，不要求真实 Codex Runtime 同时参与。

#### Task 5B：outbound、receipt 与 Channel approval policy

- 普通 outbound 默认保持 Markdown。WeCom 被动回复将当前批次放入支持 Markdown 的
  `stream.content`，主动发送使用 `msgtype="markdown"` 与 `markdown.content`。
- WeCom adapter 在 provider 内部将一个逻辑 outbound 按每批最多 20480 UTF-8 bytes
  分成多个独立完整 Markdown 消息。splitter 不破坏 Unicode code point，优先在空行、
  换行和 Markdown block 边界切分；跨批 fenced code block 在上一批闭合并在下一批重开，
  且这些辅助字符计入 byte limit。实现不假设企业微信 Markdown 等于完整
  CommonMark/GFM；支持子集以真实 Bot 渲染结果为准。
- 实现两条明确的 provider send 路径：原 inbound WebSocket frame 的约三分钟
  passive-reply window 仍有效时，优先使用 `aibot_respond_msg` 并透传原
  `headers.req_id`；provider wire 要求该回复使用
  `msgtype="stream"`，但 bcn 只发送一帧：新 stream id、完整 Markdown 正文和
  `finish=true`。passive-reply window 已过期、node 重启或被动回复已结束时，改用
  `aibot_send_msg` 主动发送，群聊目标为 `chatid`，单聊目标为 `userid`，并生成独立 req_id。
  两者都会向同一会话发出机器人消息；被动路径的 req_id 只证明 wire correlation，
  不声称企业微信 UI 会渲染为引用回复或 provider thread。
- passive-reply window 可用时，第一批发送一帧完整被动回复，其余批次
  按顺序使用 `aibot_send_msg`；window 不可用时全部批次都使用主动 Markdown。同一逻辑 outbound
  的批次串行发送，每批都等待自己的 provider ack 后才发下一批。普通
  event inbound frame 不使用 `aibot_respond_msg`。欢迎语和模板卡片
  event 更新是五秒窗口；窗口外不得继续复用旧 req_id。
- Runtime 只在 `bcc message send` 时提交一个完整逻辑 outbound；Channel 的超限分批是
  provider delivery detail，每批都是独立完整消息，不增量刷新已发内容。provider `stream`
  只是首批被动回复 envelope，不是 bcn 的流式产品语义。
- WeCom 不支持对任意某条历史消息应用 message-level reply intent。
  `ChannelSendRequest.reply_to` 非空时仍按正常被动/主动路径发送正文并忽略该提示；
  inbound frame `req_id` 的 passive-response correlation 也不进入引用回复状态。
  支持该能力的 Channel 可以把中立 reply reference 映射为 provider reply，但无需向 core
  报告是否真实应用。
- provider ack 的 `errcode=0` 只确认企业微信长连接服务接受并处理该命令，不代表终端展示、
  用户收到或已读；非零回执是 confirmed failed，发送前
  失败是 failed；已有前缀批次确认成功后遇到明确失败是 partial；任一批回执超时或
  连接断开使整个逻辑 outbound 为 unknown。partial/unknown 都禁止从头自动重发。
  canonical receipt 关联本地 outbound id、总批数、已确认前缀数，以及每批的
  provider req_id、stream id（若有）、errcode/errmsg 摘要和时间。
- 在 Channel port 上实现当前 approval policy：每个 approval request 返回 approved；保留
  后续替换为人工策略的接口，不把 runtime wire payload 暴露给 Channel。
- 记录 inbound received、approval requested/decided、outbound pending/sent/partial/failed/unknown
  事件，保证 receipt 与本地 delivery 可关联。

依赖：Task 5A 的连接、identity、inbound mapping 与 dedupe。产出：真实被动/主动
provider send、receipt classification 与 approval adapter。

出站媒体上传不属于 Task 5B：官方类型注释与 Node SDK 对 `chunk_index` 起点存在差异，只有
先用真实 Bot wire capture 消除 0/1 起点歧义后才能另行规划；本期只做入站媒体、被动文本流
和主动 Markdown。

#### Task 5C：session orchestration 与平台 delivery rules

- 首次 inbound 创建 channel/bcn mapping；只有 `notifies_runtime=true` 时才创建或恢复
  runtime binding。后续同一 conversation/thread 复用既有 session；不同 conversation
  不能共享 cursor、process 或 turn。
- 在通用 orchestration 中保留真正的 following state machine：Channel 已投递的每条 inbound
  都先落库；unfollowed 时不进入该 session 的 unread count 且不触发 Runtime turn，
  group 的 `mentions_agent=true` 在同一事务中开启 following 并让当前消息进入未读；
  followed 时所有后续已投递 inbound 都进入未读并持续 reminder，直到 agent 调用
  `bcc message unfollow`。DM 创建后固定 followed，所有 inbound 都通知 Runtime；
  unfollow 对 DM 成功返回但不改变 following。这个语义不能由
  provider 的“是否投递”隐式替代。
- 所有 Channel adapter 都只负责标准化 target kind 与 `mentions_agent`，不自行改写
  following 规则。能投递全量群消息的 Channel 在 group unfollowed/followed 两种状态下都
  继续将 inbound 交给 core；WeCom 群聊因 provider 只投递明确提及机器人的消息，
  实际只能提供 `mentions_agent=true` 的群聊 inbound frame。core 仍然正常开启和保持
  following，但对未被 provider 投递的消息无法落库、计入未读或由 fresh-check/
  cursor/Codex thread 补回；这个 gap 只记为 Channel capability，不引入 WeCom-specific
  orchestration branch。
- 将 group `chatid` 与 DM `userid` 映射为不同 canonical target kind；quote 只表示本次已投递
  消息的引用内容，不建立 provider 不存在的 thread。Channel health/capability 明确
  暴露 group full-ingress 是否可用，让 operator 知道 following 是完整还是存在 provider gap，
  但不在 Runtime prompt 中伪造已收到的消息。
- 同步 runtime developer instruction 与 `bcc --help`：说明 group mention 会开启 following，
  agent 在确认不再需要后续群上下文时调用 `bcc message unfollow`；DM 上的同一命令
  是无输出的成功 no-op。
- restart 只重建 WebSocket、重新认证并恢复持久化的 following 与 session/runtime thread
  mapping；不声称恢复断线期间的
  provider event。graceful shutdown 先停止 receive/reconnect，再 bounded 等待当前 reply ack，
  超时 outbound 留为 unknown，然后关闭 heartbeat 与 WebSocket。
- 将 runtime completion、Channel outbound 和 session state 的边界保持独立，不能用一个
  boolean 表示整条链路成功。

依赖：Task 5B 的 outbound delivery 与 Channel approval policy。产出：provider-neutral
mention/following state machine、`bcc message unfollow`、平台 capability 与真实测试
channel flow。

Phase 验收分三层：

1. TestChannel+TestRuntime 的 core contract：使用能投递全量群消息的 test-only Channel
   验证 quiet group inbound 先落库但不进入 unread/reminder；`mentions_agent=true` 的 inbound 原子开启
   following 并触发 turn；后续 quiet inbound 持续通知；unfollow 后新消息仍落库但
   恢复静默；DM 始终通知且 unfollow 成功 no-op。同时验证 `send --reply-to`
   的本地 message/session/target 归属校验；Channel 应用或忽略该提示都不改变 delivery contract。
   使用真实临时 workspace 文件验证多附件顺序、受控相对路径、原子可见性、failed descriptor、
   重复 inbound 不重复物化，以及 restart 清理 staging/无引用文件；`check` 与 `read` 对同一
   message 输出逐字相同的 inline suffix。
   这一层只验证 provider-neutral contract，
   不替代真实 Channel 验收。
2. WeComChannel+TestRuntime：以真实测试 Bot 完成认证，验证 DM、群聊 mention、引用与
   image、file 至少两种媒体 inbound frame 的 identity/message mapping、真实下载/AES 解密、
   `workspace/attachments` 物化及 suffix path 可读；验证群聊 inbound frame 进入相同的
   following state machine，同时以真实未提及消息确认 provider 不会投递、此期间 bcn 无法
   落库，且 health/capability 如实暴露 gap；验证标题、列表、链接、引用、代码块和表格的
   真实 Markdown 渲染，以及 20480-byte 边界和超限自动分批后的顺序送达；
   验证 `send --reply-to` 被忽略时正文仍正常送达，以及
   单帧完整 passive reply（provider `stream.finish=true` envelope）、主动 fallback、
   非零 ack、ack
   timeout/断线 unknown、单连接 `disconnected_event` 和 bounded shutdown 的可解释状态。
3. WeComChannel+CodexRuntime：用真实 Bot 与真实 Codex App Server 完成 DM 持续自然问答和群聊
   attention 自然问答；已被 provider 投递的后续补充触发真实 fresh-check refusal/recovery，
   不验收 provider 无法提供的未提及群聊 ingress。两个 conversation 并发时不共享 cursor、
   process 或 turn。再发送一张真实图片和一个真实文件，确认 Codex 从 `check/read` 的普通
   tool-result suffix 发现 workspace-relative path 并按自然任务需要读取，不依赖
   Codex-specific `localImage` 注入或 `bcc attachment view`。确认所有 Runtime 都只使用
   平台/Runtime 封闭白名单构造的统一
   spawn environment，WeCom Secret 和所有禁止 credential 不在 runtime 子进程、
   SQLite、wrapper、日志或 health 中，且
   provider receipt、outbound audit 与本地 delivery state 可关联。

Phase 5 不以旧群机器人 webhook、自建应用 XML callback、新版 URL callback、SDK 内部 event
emitter 单元测试或人工构造 provider frame 替代真实 Bot 验收。

### Phase 6：恢复与运维边界

目标：处理进程、provider、SQLite 和 node 重启后的不确定状态，并形成可诊断的运维闭环。

#### Task 6A：process restart、turn reconciliation 与 unknown handling

- 子进程 EOF、协议解析失败或异常退出时，将 active local turn 标记为 unknown，不猜测为
  failed/completed；按退避策略重启并重新 initialize。
- 使用 `thread/read(includeTurns=true)` 或等价 runtime reconciliation 查询 provider 状态，
  将可确认的 completed/failed/recoverable 结果落回本地；不可判断的 turn 保留 unknown 和
  可审计 next action。
- provider send 状态不确定时禁止自动重复执行不可逆 outbound；明确失败才转 failed，可
  恢复项必须经过 fresh-check 和显式 retry policy。

依赖：Phase 5。产出：restart/reconciliation state machine 与 failure tests。

#### Task 6B：backoff、overload 与 graceful shutdown

- 分离 process restart backoff、provider retry、command timeout 和 shutdown timeout；同一
  session 的退避不能阻塞其他 session。
- graceful shutdown 先停止接收新 inbound，再 bounded flush IPC/SQLite、完成可安全终止的
  runtime cleanup；超时留下可恢复状态和 recovery event。
- 对启动、runtime、Channel、SQLite、IPC 和 approval failure 定义 stable error kind、用户
  可执行 next action 和日志 correlation。

依赖：Task 6A 的 process restart、turn reconciliation 与 unknown handling。产出：backoff/
shutdown behavior 和 operational error contract。

#### Task 6C：诊断、版本与 migration 运维面

- 完成实时 stderr 日志、SQLite `runtime_events` 查询、traceback redaction、correlation
  filtering 和运行状态对账；日志不得泄露凭据、完整正文或未脱敏 provider payload。
- 补充 runtime/provider version mismatch、migration checksum mismatch、schema corruption
  和 data directory 权限错误的 fail-closed 启动提示。
- 验证 append-only event、session isolation、restart history 和 recovery event 的可查询性，
  为未来 telemetry exporter 保留边界但不依赖远程 backend。

依赖：Task 6B 的 backoff、overload 与 graceful shutdown。产出：运维诊断、启动失败信息和
recovery audit。

Phase 验收：在真实 runtime process 被终止、node 重启、重复 inbound、provider send 失败、
unknown turn 和 shutdown timeout 场景下，状态可解释、不会静默丢消息，也不会无证据重复执行
已完成 turn；日志与 runtime event 能按 session/turn/request 还原故障路径。

## 9. 验证原则

- TestChannel、TestRuntime、MemoryStorage 与 RecordingAudit 只存在于 `tests/support` 或
  test-only plugin fixture，不进入 production package、entry point、默认配置或 runtime
  dependency；它们用于隔离验证 port contract，不能替代真实 provider 的端到端验收。
- core 状态机可测试纯逻辑；持久化使用真实 SQLite 文件；IPC 使用真实本机 transport；
  runtime 使用真实 Codex App Server；Channel 使用授权的真实测试环境。
- adapter 验证先独立后组合：TestChannel+CodexRuntime 验证真实 Runtime，
  WeComChannel+TestRuntime 通过 Bot ID + Secret 的真实 WebSocket 验证真实 Channel，最终再用
  WeComChannel+CodexRuntime 验证产品链路；不得用旧 webhook 或手工 frame 注入替代 Channel
  ingress。
- 凭据通过本机受控环境注入，不写进 repository、SQLite、wrapper 内容或日志。
- 每个阶段完成后至少检查：

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run --locked bcn --help
  uv build
  python -m compileall src
  ```

- Ruff 是项目的格式与静态检查基线；实现阶段将把 Ruff 的版本和配置纳入项目开发依赖，
  确保本地、CI 和 uv lock 使用同一套规则。
- 端到端验证必须覆盖多 session 并发、进程异常、cursor drain/read 区分、provider id
  去重、snapshot/fresh-check 拒绝、outbound retry/failure、共享 workspace 重启复用和
  日志/audit event 关联。
- SQLite 验证必须覆盖 `PRAGMA integrity_check`、migration checksum mismatch、application-
  level 复合绑定、provider dedupe、active-turn 并发不变量和 append-only event 保护；测试
  数据使用真实临时 SQLite 文件，不用内存 fake repository 替代表结构。另需检查 migration
  DDL 不含 `NOT NULL`、`DEFAULT`、`CHECK`、`UNIQUE` 或 `FOREIGN KEY`，避免约束重新渗入
  storage-specific schema。
- 日志验证必须检查事件序列、session/turn/request correlation、traceback 可见性和敏感
  字段脱敏。
- fresh-check 验证必须覆盖：先观察 snapshot 后插入 inbound 时拒绝且 provider 未被调用；
  snapshot 缺失时拒绝；没有新 inbound 时才创建 pending 并调用 provider；provider 状态
  unknown 不被当作可盲目重试的 failed。

## 10. 主要风险与决策点

1. **App Server 版本漂移**：实验性协议可能变化。先固定 SDK/CLI 版本组合，在 initialize
   阶段记录版本并对不兼容失败；协议字段只存在于 contrib adapter。
2. **SDK async 边界**：若 SDK 通过线程包装同步 I/O，仍不能让阻塞操作进入 event loop；
   adapter port 要允许切换到 native asyncio JSONL process。
3. **IPC 跨平台差异**：先抽象 command service transport；Unix socket、Windows named
   pipe 和 loopback fallback 必须保持相同的 session authentication 与本机可见性。
4. **不确定的 turn 状态**：进程死亡时不能把 unknown 猜成 failed 或 completed；恢复先
   对账，再决定是否继续。
5. **Channel 投递与审批语义**：provider receipt、Codex completion、本地 outbound state
   和 Channel approval decision 不能合并成一个 boolean；这些状态需要独立可审计。
6. **发送前 freshness 与并发竞态**：snapshot、inbound append、pending outbound 和
   provider call 之间存在 TOCTOU 风险；必须由 session command lock、SQLite 事务和明确的
   fresh-check 线性化点共同定义行为，不能用一次非原子的 count 查询声称绝对没有新消息。
7. **共享 workspace 并发写入**：所有 runtime 共享 workspace，计划中不假设文件操作天然
   串行；未来若 skill 或 agent 写入冲突，需要由 runtime policy 或 workspace lock 单独
   解决，不在本首版隐式引入 agent-specific workspace。
8. **日志敏感信息与体积**：tool 参数、消息正文和 provider payload 可能包含敏感信息或
   产生高日志量；默认只记录摘要和关联 id，完整内容必须显式启用并受脱敏/大小限制。
9. **Runtime environment 白名单漂移**：面向外部用户的 Bot 会把不可信输入交给
   Runtime，因此子进程必须使用封闭白名单，不能继承 daemon 完整环境；新平台、
   新 Runtime 或认证/证书变更都可能使白名单过宽或过窄。平台基线和 Runtime 扩展必须
   是代码审查的封闭集合，通过三平台真实 process matrix 验证。`[runtime.env]`
   只能精确追加变量名，且是 operator 可审计的显式授权；它不得覆盖 bcn 保留名或
   adapter credential 拒绝集。该白名单是必要的减露面措施，但不能
   隔离同 OS 用户可读的文件、credential store 或其他进程；需要强隔离时仍必须使用
   独立 OS identity/process、受限 filesystem 或 secret broker。
10. **附件资源耗尽与不可信内容**：provider 文件名、media type 和内容都不可信，且下载可能
    耗尽磁盘、内存或连接预算。共享 materializer 必须流式处理，设置单文件/单消息大小和数量
    上限、下载 timeout、总并发预算与 workspace 配额；路径只能由本地 ID 生成，并拒绝 symlink/
    traversal。eager materialization 只保证 Runtime 首次看到消息时文件已达到 terminal state，
    不构成恶意文件扫描或同 OS 用户隔离；共享 workspace 中的 Runtime 仍可能在之后修改文件，
    audit 不能把 path 存在误报为内容不可变。

## 11. Code 模式入口条件

进入 Code 模式前需要确认本计划本身，无需再次选择子方案。Phase 1 至 Phase 4 与命名收口已
完成；当前 review 通过后，下一项必须按本节更新后的范围进入 Phase 5 Task 5A。Task 5A 先
完成官方资料/SDK 源码/真实 Bot 的协议基线和 credential boundary，再实现 WebSocket
lifecycle 与 inbound mapping；完成 review 后才能进入 Task 5B，不并行提前写 outbound 或
session orchestration。
