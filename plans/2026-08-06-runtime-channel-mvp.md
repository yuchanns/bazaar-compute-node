# 2026-08-06 Runtime Channel MVP Plan

## 状态

- 模式：Plan
- 状态：待进入 Code 模式
- 基线：`main`，当前提交为 `47c79fe20b26853a827b4a6dabc1a906b01345cd`
- 基线仓库状态：只有 packaged application、`bcn` CLI 和 uv 基础配置，基线时工作区 clean；
  当前新增的是本计划文件，已有的空 `AGENTS.md` 未修改
- 本计划定义首个可运行纵向切片；用户确认计划后，按其中阶段进入 Code 模式并实施生产代码。
  当前轮次先只更新计划文档，避免在方案调整完成前提前改动生产代码。

## 1. 目标

把当前 packaged app 基础框架演进为一个可以承载第一组 runtime/channel adapter 的
computer node daemon。首个纵向切片的业务链路是：

```text
WeCom inbound
    -> channel normalization
    -> SQLite append-only log and session binding
    -> Codex App Server turn
    -> agent calls bcc message check/read/send
    -> outbound channel delivery and audit
```

首版由 CLI 负责选择 adapter 组合，启动入口为：

```bash
bcn --channel wecom --runtime codex
```

`--channel` 和 `--runtime` 是 composition root 的 adapter slug。CLI 在启动 node 前完成
参数解析和 capability 校验，再加载对应的 Channel 与 Agent Runtime contrib。首版一个
进程选择一组 channel/runtime；该组合内部仍支持多个 bcn session 并发。Dummy pair 只用于
测试和开发 composition，不作为默认生产入口。

首版必须满足以下产品语义：

1. Python 全链路使用 async；一个 node 可以同时运行多个 session。
2. 一个固定的 agent runtime session 实例就是一个 agent；每个 agent runtime session
   使用独立的 Codex App Server 子进程，但所有 agent 共用同一个稳定 workspace。
3. 首次 channel inbound 创建并持久化三方绑定：
   `agent_runtime_session_id <-> bcn_session_id <-> channel_session_id`。
4. bcn 启动时只生成一个 workspace UUID，写入 SQLite；workspace 固定为
   `~/.bcn/workspaces/{uuid}/`，后续启动复用，不按 agent 拆分。
5. Channel 只把“有消息待处理”的提醒送进 runtime；真实消息由 agent 通过 `bcc`
   工具读取。
6. 第一版只提供 `bcc message check/read/send`，不引入 task queue、claim、lease 或
   exactly-once 语义。
7. 审批能力属于 Channel port，由每个 Channel contrib 实现审批策略；当前 WeCom
   实现永远返回批准，不提供人工审批 UI。
8. `bcc` wrapper 持久化注入到 `~/.bcn/bin/`，node 将该目录加入每个 runtime 子进程的
   PATH；wrapper 不放在启动临时目录。

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
- 企业微信群聊是否收到后续消息由平台的 @ 规则决定；following 只能复用同一个 agent
  runtime thread，不能伪造平台没有投递给机器人的群消息。

### 首版非目标

- WebSocket、Unix-socket WebSocket、HTTP RPC、远程 runtime transport。
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
│   ├── storage_sqlite/  # SQLite schema, migrations and repositories
│   ├── codex_app_server/# async App Server process and protocol adapter
│   ├── wecom/           # WeCom webhook/send adapter and normalization
│   └── dummy/           # deterministic Channel and Agent Runtime adapters for core checks
└── app/
    └── cli/             # bcn composition root, local IPC and bcc wrapper
```

具体包名可以在 Code 模式开始时按仓库惯例调整，但不能反转依赖方向或让 core import
provider SDK types。

## 4. 核心数据模型与 SQLite

### 4.1 节点和 workspace

启动时由 storage 初始化或读取唯一的 `workspace_uuid`：

```text
data_dir:   platform user data directory for bcn
workspace:  ~/.bcn/workspaces/{workspace_uuid}/
database:   persistent SQLite database under data_dir
```

`~/.bcn` 是产品语义上的逻辑根目录，实际路径由跨平台 resolver 计算；不能把业务
状态放进 runtime 启动时创建的临时目录。稳定目录布局为：

```text
~/.bcn/
├── bin/                       # persistent bcc wrappers
├── workspaces/{uuid}/         # shared agent workspace
└── ...                        # SQLite data and other persistent node state
```

临时目录只用于 IPC endpoint metadata 和生命周期临时文件；`bcc` wrapper 本身必须位于
`~/.bcn/bin/`，以便 node 重启后仍能复用并由所有 runtime session 使用。

### 4.2 SQLite schema 设计

这一节把 MVP 的表设计展开到可以直接进入 migration 的程度。具体 Python repository
类名可以在 Code 模式调整，但字段职责、约束和状态边界固定如下：

- ID 使用 UUID 或 provider opaque ID 的 `TEXT`；不把 provider ID 当作本地主键。
- 本地时间使用 UTC Unix milliseconds 的 `INTEGER`，字段统一使用 `_at_ms` 后缀；展示层
  再格式化为 canonical time。
- 本地序列使用 `INTEGER`；`inbound_messages.seq` 和 `runtime_events.event_seq` 分别是
  node-local monotonic sequence，MVP 不物理删除这两类 append-only 记录。
- JSON metadata 使用 `TEXT` 保存应用校验过的 JSON object；不依赖 SQLite JSON extension。
  provider 原始 payload 只保存受控引用，不把完整 webhook、token、cookie 或 credential
  写入数据库。
- 所有状态值、必填字段、默认值、关联关系和去重规则都由 core/application/repository
  校验；SQLite schema 只声明字段与物理行身份，不承载业务约束。未知 provider 状态不能
  折叠成失败。

关系可以先固定为以下形态：

```text
node_state
  └── channel_sessions ─── bcn_sessions ─── runtime_sessions ─── runtime_turns
          ├── inbound_messages
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
    workspace_uuid TEXT,
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
    channel_session_id TEXT PRIMARY KEY,
    -- Selected channel adapter slug.
    channel_slug TEXT,
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
    bcn_session_id TEXT PRIMARY KEY,
    -- Application-managed association to a channel session.
    channel_session_id TEXT,
    -- UUID of the shared workspace used by this session.
    workspace_uuid TEXT,
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
    agent_runtime_session_id TEXT PRIMARY KEY,
    -- Application-managed association to a bcn session.
    bcn_session_id TEXT,
    -- Application-managed channel session association for correlation.
    channel_session_id TEXT,
    -- Selected agent runtime adapter slug.
    runtime_slug TEXT,
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
    agent_runtime_session_id TEXT,
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
    -- Node-local monotonic sequence used for cursor and snapshot boundaries.
    seq INTEGER PRIMARY KEY,
    -- Stable local message identifier.
    message_id TEXT,
    -- Application-managed association to a bcn session.
    bcn_session_id TEXT,
    -- Application-managed association to a channel session.
    channel_session_id TEXT,
    -- Channel adapter slug that normalized the message.
    channel_slug TEXT,
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
    -- Controlled reference to retained provider payload data.
    provider_payload_ref TEXT,
    -- Non-sensitive normalized metadata encoded as JSON.
    metadata_json TEXT
);

-- Outbound command attempts, fresh-check evidence, provider receipt, and delivery state.
CREATE TABLE outbound_messages (
    -- Stable local identifier for one outbound command attempt.
    outbound_message_id TEXT PRIMARY KEY,
    -- Stable identifier of the originating command invocation.
    command_id TEXT,
    -- Application-managed association to a bcn session.
    bcn_session_id TEXT,
    -- Application-managed association to a channel session.
    channel_session_id TEXT,
    -- Canonical target supplied to the send command.
    target TEXT,
    -- Outbound message body captured for retry and audit.
    body TEXT,
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
    bcn_session_id TEXT PRIMARY KEY,
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
    -- Channel adapter slug associated with the event.
    channel_slug TEXT,
    -- Runtime adapter slug associated with the event.
    runtime_slug TEXT,
    -- Channel session correlation identifier.
    channel_session_id TEXT,
    -- Bcn session correlation identifier.
    bcn_session_id TEXT,
    -- Agent runtime session correlation identifier.
    agent_runtime_session_id TEXT,
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

- DDL 只保留用于物理行定位的 `INTEGER PRIMARY KEY` 或本地 identity primary key；不把它们
  当作跨表关联、业务唯一性或状态校验。应用层必须在写入前校验 required fields、ID 格式
  和对象归属。
- `channel_sessions` 的逻辑查找键是
  `(channel_slug, provider_conversation_key, provider_thread_key)`。没有 thread 的 provider
  identity 由 adapter 规范化为空字符串或显式 sentinel；repository 在 session lock 内
  执行 get-or-create，发现重复绑定时返回已有记录或冲突错误。
- `bcn_sessions.channel_session_id` 与 `runtime_sessions.bcn_session_id` 表达 MVP 的
  一对一绑定，但由 application/repository 在同一 session lock 与显式事务中读取并校验，
  不使用 SQL unique 或 foreign-key constraint。
- 同一 runtime session 同时最多一个 `starting/running/unknown/reconciling` turn 是
  application state machine 的不变量。repository 在事务内检查 active turn，再插入或更新
  turn；`provider_turn_id` 只是对账键，不作为本地 turn 主键。
- `inbound_messages.seq` 是 node-local monotonic sequence；append-only 语义下不做 delete，
  因此 node 重启后继续递增。provider 去重逻辑键是
  `(channel_slug, provider_message_id)`，由 adapter/repository 在同一事务中检查，不能只按
  provider message id 全局去重。
- `outbound_messages` 记录的是 send command attempt，而不只是已经送达的 provider message。
  fresh-check 拒绝时写 `state='rejected'`、`fresh_check_state='failed'/'required'` 和
  `draft_saved_at_ms`，因此 `Draft saved: yes` 有本地审计依据且不会伪称 provider 已调用。
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
      ON inbound_messages (bcn_session_id, seq);
  CREATE INDEX idx_inbound_channel_received
      ON inbound_messages (channel_session_id, received_at_ms);
  CREATE INDEX idx_outbound_session_created
      ON outbound_messages (bcn_session_id, created_at_ms);
  CREATE INDEX idx_outbound_state_created
      ON outbound_messages (state, created_at_ms);
  CREATE INDEX idx_runtime_sessions_state
      ON runtime_sessions (process_state, updated_at_ms);
  CREATE INDEX idx_runtime_turns_session_state
      ON runtime_turns (agent_runtime_session_id, state, started_at_ms);
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
   `channel_sessions/bcn_sessions/runtime_sessions/runtime_turns`、message/cursor/outbound、
   `runtime_events` 的依赖顺序创建表和索引。`node_state.schema_version` 是 ledger 最新
   version 的缓存，不是第二套迁移事实源。
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

1. Channel inbound 在一个显式事务和 channel/session lock 内完成 provider id 去重、分配本地
   `seq`、写入 inbound log 和 get-or-create channel/bcn session；provider id 去重、channel
   到 bcn/runtime 的关联以及必填字段校验全部由 application/repository 执行，不依赖 SQL
   unique、foreign-key 或 check 约束。
2. `message check` 从当前 consumer cursor 读取后续消息，并按既定 drain 语义推进
   cursor；`message read` 是纯读取，不推进 cursor。两者都要记录本次操作看到的最新
   inbox snapshot seq，供后续 `send` 做 fresh check；snapshot 的更新不能偷偷推进已交付
   cursor。
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

1. 解析 `--channel` 与 `--runtime`，拒绝未知或不兼容的 adapter slug。
2. 解析跨平台 data directory，打开 SQLite，执行 migrations。
3. 读取或生成唯一 workspace UUID，创建共享 workspace。
4. 启动本地 command service，确保 `~/.bcn/bin/` 下存在平台对应的 `bcc` wrapper，
   并把该稳定目录加入每个 runtime 子进程的 PATH。为每个 runtime 子进程注入
   `BCN_SESSION_ID=<bcn_session_id>`，让同一个 wrapper 能路由到正确的 session。
5. 按已选择的 runtime/channel composition 恢复可恢复的 `runtime_sessions`；不假设上次
   进程仍然存在。
6. Channel adapter 开始接收 inbound。

本地 command service 必须有独立的 transport port。Unix 优先使用 Unix domain socket；
Windows 使用命名管道或等价的仅本机 IPC。若跨平台实现成本要求 fallback 到 loopback
TCP，必须使用每个 node 的随机 capability token、仅绑定 loopback，并把 endpoint/token
限制在临时目录权限内，不能暴露宿主凭据。

### 5.2 首次 inbound 与 session 创建

1. Channel adapter 将 provider event 转成 core `InboundMessage`。
2. Storage 以 channel conversation/thread identity 查找 channel session。
3. 不存在时，在同一事务中创建 channel session 和 bcn session，并创建 runtime binding
   记录；Codex process/thread 可以延迟到事务提交后启动。
4. 启动该 bcn session 的独立 Codex App Server process，设置共享 workspace 为 cwd 或
   runtime 支持的 workspace 参数。
5. 完成 `initialize`、`thread/start` 或恢复已有 thread，再发送 reminder turn。

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
bcc message send --target "<canonical-target>" <<'BCCMSG'
Message body.
BCCMSG
```

正文从 stdin 读取，不放进命令行参数；成功结果写 stdout，已处理的参数、fresh-check 或
provider 错误写 stderr 并返回非零退出码。错误文本仍使用稳定的 `Error`、`Code`、可选
`Draft saved`、`Next action` 标签，避免 runtime 只能依赖退出码猜测是否可以重试。

`bcc message check` 的最小输出形态：

```text
[target=#work:6632e039 msg=25e7bff4 time=2026-08-04 19:25:39 type=human] @sender: message body
No more new messages.
```

`bcc message read` 负责历史窗口与定位字段，例如 local seq、完整 UUID、thread id 和
reply target；每次 `check`/`read` 都返回或内部记录 inbox snapshot seq。`bcc message send`
负责 target 校验、fresh check、outbound log 和 provider receipt。工具结果必须保留 sender
identity，不能只返回无来源的正文。

三类工具都使用面向 agent 的纯文本 stdout，不把 provider SDK 对象直接暴露给 runtime：

```text
# check: canonical inbound envelope
[target=<canonical-target> msg=<short-message-id> time=<provider-time> type=human] @sender: message body
No more new messages.

# read: historical window with positioning fields
Read window: <n> returned, seq <first-seq>-<last-seq>, oldest to newest.
[1/<n> seq=<local-seq> msg=<full-message-id> time=<provider-time> type=human threadId=<thread-id> replyTarget=<canonical-target>] @sender: message body

# send: confirmed outbound receipt
Message sent to <canonical-target>. Message ID: <outbound-message-id>
```

`check` 的 envelope 只保留 agent 判断来源所需的 target、短消息 id、time、type、sender 和
body；`read` 额外提供完整 message id、local seq、threadId 和 replyTarget，便于定位和构造
回复。`send` 成功必须返回可追踪的 message id，不能只返回 boolean；provider receipt 及
本地 delivery 状态同时写入 outbound log。若 provider 只确认已排队而未确认送达，输出应
使用明确的 queued/unknown 状态，不能伪称为 sent。

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

首个 Channel adapter 是 WeCom bot，但 core 只依赖抽象 Channel port：

- `receive`：把 webhook/event 规范化为 inbound message 和 channel session identity。
- `send`：根据 canonical target 发送 outbound message，并返回 provider receipt。
- `normalize_identity`：稳定映射群聊、单聊、thread/reply 和 sender identity。
- `request_approval`：接收 core 的中立 `ApprovalRequest`，返回 `ApprovalDecision`；每个
  Channel contrib 自己实现审批策略。
- `delivery_constraints`：表达群聊必须重复 @ 等平台约束，不让 core 猜 provider 行为。

当前 WeCom 实现的 `request_approval` 永远返回 approved；后续可替换为人工审批，但不把
Codex JSON-RPC request payload 暴露给 Channel 实现。

企业微信群聊的 following 语义只表示继续使用既有 bcn/Codex session；没有 @ 的消息若
平台未投递给 bot，不能由 bcn 的 cursor 或 Codex thread 补回。单聊才可以实现持续接收
后续消息。

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
  `bcn_session_id`、`agent_runtime_session_id`、`turn_id`、`request_id`、local `seq` 和
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

### Phase 1：core contracts

目标：建立不依赖 provider、SQLite 或具体 transport 的 domain contract 和 session
orchestration 骨架。

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

#### Task 1D：Dummy adapters 与 core orchestration harness

- 在 `contrib/dummy` 中分别实现 Dummy Channel 与 Dummy Agent Runtime，驱动真实 core
  contract、session routing、approval callback 和状态转换；Dummy 不得反向成为 core 依赖。
- 测试正常 inbound、turn completion、outbound delivery、provider failure、unknown turn、
  fresh-check refusal 和 graceful cancellation。
- 测试多个 session 的 cursor、turn、workspace identity 和 correlation 不串线。

依赖：Task 1C 的 approval/audit/correlation contract。产出：不导入真实 provider 的 core
contract/orchestration 测试套件。

Phase 验收：core 在没有 provider import 的情况下，由 Dummy adapters 驱动完成 session
routing、状态迁移、错误分类和 approval/audit correlation；并发测试能够证明 session
边界，不依赖 SQLite constraint。

### Phase 2：SQLite 与 workspace

目标：提供可替换 storage port 的首个 SQLite contrib，实现计划 4.2 的字段、日志、cursor、
session mapping 和显式事务语义。

#### Task 2A：data directory、workspace 和 migration foundation

- 实现 data-dir resolver、node identity、共享 workspace UUID 初始化和 workspace 创建；
  workspace 位于稳定 data directory，不把业务状态放进临时目录。
- 建立 migration ledger、checksum/application-level version check、WAL、busy timeout 和
  显式 transaction helper；启动时发现同一 version 的 name/checksum 不一致必须 fail closed。
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
  `consumer_cursors` 和独立 inbox snapshot。
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

### Phase 3：local command service 与 bcc

目标：把 core 的 session-scoped 能力暴露给 runtime 子进程，完成可审阅的本地 IPC、持久
wrapper 和三类 message command。

#### Task 3A：composition root 与 command service lifecycle

- 让 `app/cli` 暴露 `--channel`、`--runtime`，在 composition root 将 slug 映射到 contrib
  factory；未知或不兼容 slug 在启动前清晰失败。
- 建立 command service 的启动、停止、health/error boundary 和 session-scoped dispatch；
  service 只调用 core ports，不把 provider SDK 对象返回给 wrapper。
- 固定一个 node process 可服务多个 bcn session，但每个 command 必须经过 session binding
  校验。

依赖：Phase 2。产出：composition root、command service lifecycle 和 dispatch contract。

#### Task 3B：local IPC、wrapper 和 session binding

- Unix 使用 Unix domain socket，Windows 使用 named pipe 或等价本机 IPC；loopback fallback
  只允许随机 capability token、loopback bind 和受限 endpoint metadata。
- 将 POSIX `bcc` 与 Windows `bcc.ps1` 持久化到 `~/.bcn/bin/`，为每个 runtime process
  注入 PATH 和 `BCN_SESSION_ID`；wrapper 不携带宿主凭据和管理能力。
- command service 端校验 IPC client binding、environment session id 和 bcn/runtime mapping，
  防止跨 session 串读 cursor、snapshot 或 outbound target。

依赖：Task 3A 的 service lifecycle。产出：跨平台本机 transport、wrapper 和 binding validation。

#### Task 3C：check/read 查询与 canonical serializer

- 实现 `bcc message check`、`bcc message read --target ... [--around ...]` 的参数解析、
  session routing、cursor/snapshot 调用和 canonical text serializer。
- 固定 stdout/stderr、退出码、sender identity、target、short/full message id、local seq、
  threadId、replyTarget 和历史窗口边界；不把 provider SDK object 泄露给 runtime。
- 以 Dummy Channel 和真实 SQLite repository 验证 check drain、read non-drain、snapshot
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

Phase 验收：真实 runtime process 完成 initialize -> reminder -> bcc check -> bcc send；两个
session 并发时 process、thread、workspace、turn 和 SQLite mapping 互不混淆；provider retry
notification 不会错误终结本地 turn。

### Phase 5：WeCom adapter 与端到端编排

目标：接入首个真实 Channel contrib，把 provider identity、inbound、approval、outbound 和
session routing 汇合为可运行的端到端路径。

#### Task 5A：provider identity、inbound normalization 与 dedupe

- 接入真实 webhook/event reader，规范化 conversation/thread/reply/sender identity、message
  type、provider time 和 canonical target。
- 将重复 webhook、provider message id、群聊/单聊 target 和 thread/reply 语义映射到 Phase 2
  的 application-level dedupe 与 channel session lookup。
- 处理 provider credential boundary；凭据只通过受控本机环境注入，不进入 SQLite、wrapper
  或日志。

依赖：Phase 4。产出：真实 inbound normalization、dedupe 和 identity tests。

#### Task 5B：outbound delivery 与 Channel approval policy

- 实现 canonical target 到 provider send 的 mapping，返回 provider receipt/queued/unknown/
  failed 的明确分类，并把本地 outbound state 与 provider state 分开。
- 在 Channel port 上实现当前 approval policy：每个 approval request 返回 approved；保留
  后续替换为人工策略的接口，不把 runtime wire payload 暴露给 Channel。
- 记录 inbound received、approval requested/decided、outbound pending/sent/failed/unknown
  事件，保证 receipt 与本地 delivery 可关联。

依赖：Task 5A 的 provider identity、inbound normalization 与 dedupe。产出：真实 provider
send/approval adapter。

#### Task 5C：session orchestration 与平台 delivery rules

- 首次 inbound 创建 channel/bcn/runtime mapping，后续同一 conversation/thread 复用既有
  session；不同 conversation 不能共享 cursor、process 或 turn。
- 实现群聊重复 @、following、reply/thread target、shutdown stop-receive 和 provider delivery
  limitation；未投递给 bot 的消息不能由本地 cursor 补回。
- 将 runtime completion、Channel outbound 和 session state 的边界保持独立，不能用一个
  boolean 表示整条链路成功。

依赖：Task 5B 的 outbound delivery 与 Channel approval policy。产出：session orchestration、
平台规则和真实测试 channel flow。

Phase 验收：真实测试 channel 中首次消息创建 session，后续消息复用同一 runtime thread；
两个 conversation 并发运行时不共享 cursor/process/turn；重复 webhook、approval、fresh-
check refusal、provider receipt 和 outbound audit 均可解释。

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

- `contrib/dummy` 只用于 core contract 和 orchestration 验证，不作为生产 Channel/runtime
  adapter，也不能替代真实 provider 的端到端验收。
- core 状态机可测试纯逻辑；持久化使用真实 SQLite 文件；IPC 使用真实本机 transport；
  runtime 使用真实 Codex App Server；Channel 使用授权的真实测试环境。
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

## 11. Code 模式入口条件

进入 Code 模式前需要确认本计划本身，无需再次选择子方案。若直接按当前最优路径实施，
第一批代码只覆盖 Phase 1 和 Phase 2：先建立 core contracts、SQLite migrations、唯一
workspace 初始化和三方 session binding；完成后再接 local command service，避免先写
provider-specific runtime 逻辑造成核心模型返工。
