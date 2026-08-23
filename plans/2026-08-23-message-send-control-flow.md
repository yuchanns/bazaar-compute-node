# 2026-08-23 Message Send Control Flow Plan

## 状态

- 模式：Plan。
- 工作分支：`feat/message-send-control-flow`。
- 基线：`main@ca2b271ef52173d764b2c0f899369267982d4aca`，对应 `v0.1.24`。
- 当前分支变更仅包含本文。
- 实施严格按 Task 顺序进行；每个 Task 完成 focused verification 后停下 review。

## 1. 目标

本分支统一重构 `bcc message send` 的 draft、外部输出契约与跨会话控制流：

1. active draft 改为 Agent 进程内、per-session 的单值状态，并提供
   `bcc message send --send-draft --target <target>` 恢复路径；
2. freshness hold 直接返回供 Agent 阅读和执行的 stdout 文本，不再编码为 outbound rejection；
3. 跨会话发送进入统一的 pre-send gate，返回 `bcc handoff send` 指引，不保存 draft；
4. `OutboundMessage` 从真实 provider delivery attempt 开始存在，只持久化 provider attempt
   及其结果。

完整控制流固定为：

```text
bcc message send
    -> resolve target and owning BCN session
    -> select submitted payload or the target session's active draft
    -> enter the pre-send gate
       -> current-session freshness hold text + active in-memory draft
       -> cross-session hold text + handoff guidance
       -> accepted provider delivery attempt
    -> create pending OutboundMessage
    -> call Channel
    -> persist provider delivery outcome
    -> render final CLI text
```

## 2. Active draft

每个 BCN session 在 `SessionCommandService` 中最多拥有一个 active message draft。map 以 resolved
`session_id` 为 key，value 保存完整待发送 payload：

```text
target
body
attachments
reply_to_message_id
created_at_ms
```

所有 draft 读写都位于现有 `ISessionConcurrency.for_session(session_id)` 临界区内。

生命周期固定为：

1. 当前会话的普通 send 命中 freshness hold 时，以本次完整 payload 替换该 session 的 draft；
2. `--send-draft --target <target>` 从 target 对应 session 读取完整 payload，并重新进入 pre-send
   gate；
3. draft 再次命中 freshness hold 时保持 active；
4. provider 返回 `sent` 或 `queued` 后清除 active draft；
5. 普通 revised send 成为该 session 的新 active payload；
6. Agent application stop 时由进程内对象生命周期释放全部 draft。

附件在首次提交时完成 workspace、regular-file、symlink、size 与 SHA-256 校验，draft 保存解析后的
`OutboundAttachment` descriptors。后续发送继续由 Channel adapter 校验 descriptor 与 workspace
文件的一致性。

## 3. Pre-send gate

### 3.1 Target resolution

`--target` 始终为必填参数。target resolution 返回 canonical target、owner Agent、resolved BCN
session 与 Channel session：

1. target 必须存在且属于当前 Agent；
2. target 对应当前 runtime session 时进入 current-session freshness evaluation；
3. target 对应同一 Agent 的另一个 BCN session 时进入 cross-session hold；
4. reply reference 与 attachment descriptors 在进入 gate 前完成校验。

### 3.2 Current-session freshness evaluation

在 session lock 和 storage transaction 中读取 consumer cursor 与最新 inbound seq：

1. snapshot 缺失或 `current_inbound_seq > inbox_snapshot_seq` 时保存/保留 active draft；
2. 查询 snapshot 之后最新 20 条 inbound message，并计算完整 newer-message count；
3. freshness hold 只读取 context，不移动 `delivered_through_seq` 或 `inbox_snapshot_seq`；
4. snapshot 覆盖当前 inbound boundary 时进入 provider delivery attempt。

storage contract 增加按 session 查询 latest inbound、after-snapshot count 与 bounded latest window
的能力。window 按 seq 从旧到新输出，referenced messages 与附件 suffix 复用现有 inbound formatter。

### 3.3 Cross-session hold

resolved target session 与 caller runtime session 不同时，pre-send gate 直接产生跨会话 hold 文本。
该路径返回 handoff 命令模板并记录 body-free command audit。模板要求当前会话编写 self-contained
handoff content，使目标会话在独立上下文中仅凭交接内容即可理解背景、目标与下一步。该路径不写入
active draft、`OutboundMessage` 或调用 Channel provider。

## 4. 外部 CLI 契约

### 4.1 Command syntax

`bcc message send` 支持两种形式：

```bash
bcc message send --target "<target>" <<'BCCMSG'
message
BCCMSG
```

```bash
bcc message send --send-draft --target "<target>"
```

普通形式从 stdin 读取 body，并接受 `--reply-to` 与 repeatable `--attachment`。draft 形式从
target 对应 session 的 active draft 取得完整 payload。

`--send-draft` 与 stdin body、`--reply-to`、`--attachment` 互斥。target session 没有 active
draft 时返回稳定 command error。

### 4.2 Current-session freshness hold output

freshness hold 的最终工具结果是 stdout 文本，stderr 为空，exit code 为 `0`。输出格式固定为：

```text
Unreviewed synced context for this target: <total> messages.
Your message has been saved as a draft. Review this target's synced context before sending.

Read window: <shown> returned, seq <first>-<last>, oldest to newest. <older-bound>. <newer-bound>.

<messages>

End of window: <shown>/<total> shown.

To update the draft, send revised content normally:
  bcc message send --target "<target>" <<'BCCMSG'
  revised message
  BCCMSG
To send the current draft unchanged:
  bcc message send --send-draft --target "<target>"
You can also choose not to send anything.
```

`message/messages` 根据数量选择；bounded window 明确显示 shown/total 与两侧边界。该输出直接作为
command result 交给 `bcc` 打印，不经过 outbound error、`Error`、`Code`、`Draft saved` 或
`Next action` 包装。

### 4.3 Cross-session hold output

跨会话 hold 同样写 stdout、保持 stderr 为空并以 exit code `0` 返回：

```text
Your message was not sent because the target belongs to another conversation.

To continue this work in the target conversation, send a self-contained handoff:
  bcc handoff send --target "<target>" <<'BCCMSG'
  enough context to understand the background, goal, and next action
  BCCMSG
This creates a handoff notice that wakes you in that conversation.

You can also choose not to send anything.
```

### 4.4 Provider attempt output

provider 返回 `sent` 或 `queued` 时继续输出稳定 success text。provider 返回 `partial`、`failed`
或 `unknown` 时，由本次 command outcome 生成对应 error text 与 recovery action；恢复提示是瞬时 CLI
结果，不写入 `OutboundMessage`。

## 5. Outbound persistence

### 5.1 Provider-attempt lifecycle

`OutboundDeliveryState` 只包含：

```text
pending
queued
sent
partial
failed
unknown
```

`OutboundMessage` 在 pre-send gate 通过后以 `pending` 创建，并持有：

```text
outbound_message_id
command_id
session_id
channel_session_id
target
reply_to_message_id
body
attachments
state
snapshot_seq
current_inbound_seq
provider message/receipt fields
created/provider-attempted/completed timestamps
error kind/message
metadata
```

`snapshot_seq` 与 `current_inbound_seq` 是已通过 current-session freshness gate 的审计证据，创建
时均为必填，且 `current_inbound_seq <= snapshot_seq`。

### 5.2 SQLite migration 16

新增 migration 16，以 replacement table 重建 `outbound_messages`：

1. 暂停并在迁移末尾重建 `set_outbound_messages_agent_identity` trigger；
2. 创建 provider-attempt-only 的最终表结构；
3. 复制 `pending/queued/sent/partial/failed/unknown` rows 及 ownership、payload、provider receipt、
   error 与 audit evidence；
4. replacement table 原子替换旧表；
5. 重建 `idx_outbound_session_created`、`idx_outbound_state_created` 与 agent identity trigger。

最终 schema 以 provider attempt 为唯一持久化边界，字段集合不再包含：

```text
fresh_check_state
draft_saved_at_ms
next_action
```

repository insert 直接接受 `pending` outbound；update 只处理 provider outcome transition。codec、
scoped repository 与 test support storage 使用同一最终验证规则。

## 6. Audit 与 runtime instructions

pre-send gate 记录两种 body-free command audit status：

```text
freshness_hold
cross_session_hold
```

freshness hold metadata 记录 target、snapshot seq、current seq、shown/total count 与 draft replacement
flag。cross-session hold metadata 记录 source session、target session 与 canonical target。

runtime developer instruction 描述三条可执行路径：普通 revised send、`--send-draft --target` 与
跨会话 `bcc handoff send --target`。instruction 要求 handoff content 自包含背景、目标与下一步，
使目标会话无需继承当前上下文即可继续处理。freshness/cross-session hold 是需要 Agent 根据文本
选择下一步的正常工具结果。

## 7. 实施 Tasks

### Task 1：重构 pre-send gate 与 per-session draft

涉及：

- `src/bazaar_compute_node/core/command.py`
- `src/bazaar_compute_node/core/storage.py`
- `src/bazaar_compute_node/core/orchestration/command.py`
- `src/bazaar_compute_node/contrib/sqlite/repository.py`
- `src/bazaar_compute_node/contrib/sqlite/scoped_repository.py`
- `tests/support/src/bcn_test_support/storage.py`
- `tests/contrib/test_orchestration.py`
- `tests/contrib/test_sqlite_database.py`

实施：

1. 在 `SessionCommandService` 增加 per-session active draft map；
2. 将 send 重排为 target resolution、payload selection、pre-send gate 与 provider attempt；
3. 实现 current-session draft replace/read/retain/clear 生命周期；
4. 实现 cross-session hold，并保证该分支无 draft/outbound/provider side effect；
5. 增加 latest inbound、after-snapshot count 与 bounded window repository contract；
6. 记录 body-free freshness/cross-session hold audit。

Focused verification：

```bash
uv run pytest tests/contrib/test_orchestration.py tests/contrib/test_sqlite_database.py -q
uv run ruff check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/contrib
uv run ruff format --check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/contrib
```

完成后停下 review。

### Task 2：接通 direct-text CLI 契约

涉及：

- `src/bazaar_compute_node/app/command.py`
- `src/bazaar_compute_node/bcc.py`
- `src/bazaar_compute_node/core/instruction.py`
- `tests/app/test_command_resource.py`
- `tests/app/test_bcc_process.py`
- `tests/test_bcc.py`
- `tests/core/test_instruction.py`

实施：

1. parser 增加 `--send-draft` 及参数互斥校验；
2. command resource 为两类 hold 生成最终文本结果；
3. `bcc` 对 hold 文本执行 verbatim stdout 输出并返回 `0`；
4. freshness output 接入 bounded inbound formatter 与 draft action paths；
5. cross-session output 接入 handoff command template，并要求 handoff content 对目标会话自包含；
6. provider attempt outcome 在 command 层生成 success/error/recovery 文本；
7. 更新 runtime developer instruction。

Focused verification：

```bash
uv run pytest tests/test_bcc.py tests/app/test_command_resource.py tests/app/test_bcc_process.py tests/core/test_instruction.py -q
uv run ruff check src/bazaar_compute_node/app src/bazaar_compute_node/bcc.py src/bazaar_compute_node/core/instruction.py tests/app tests/test_bcc.py tests/core/test_instruction.py
uv run ruff format --check src/bazaar_compute_node/app src/bazaar_compute_node/bcc.py src/bazaar_compute_node/core/instruction.py tests/app tests/test_bcc.py tests/core/test_instruction.py
```

完成后停下 review。

### Task 3：收敛 OutboundMessage 与 SQLite schema

涉及：

- `src/bazaar_compute_node/core/models/states.py`
- `src/bazaar_compute_node/core/models/entities.py`
- `src/bazaar_compute_node/contrib/sqlite/outbound_draft_migration.py`
- `src/bazaar_compute_node/contrib/sqlite/migrations.py`
- `src/bazaar_compute_node/contrib/sqlite/codec.py`
- `src/bazaar_compute_node/contrib/sqlite/repository.py`
- `src/bazaar_compute_node/contrib/sqlite/scoped_repository.py`
- `tests/support/src/bcn_test_support/storage.py`
- `tests/core/test_models.py`
- `tests/contrib/test_sqlite_database.py`
- `tests/contrib/test_orchestration.py`

实施：

1. 将 outbound state machine 收敛到 provider-attempt lifecycle；
2. 将 `OutboundMessage` 创建规则改为 passed snapshot evidence + pending state；
3. 从 entity、codec 与 persistence contract 移出 draft/fresh-check/CLI recovery fields；
4. 注册并执行 migration 16；
5. 更新 SQLite insert/update、scoped query 与 test support storage；
6. 增加 v15 fixture upgrade、provider-attempt row preservation、index/trigger 与 fresh database schema
   tests。

Focused verification：

```bash
uv run pytest tests/core/test_models.py tests/contrib/test_sqlite_database.py tests/contrib/test_orchestration.py -q
uv run ruff check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/core tests/contrib
uv run ruff format --check src/bazaar_compute_node/core src/bazaar_compute_node/contrib/sqlite tests/core tests/contrib
```

完成后停下 review。

### Task 4：全量回归

执行：

```bash
uv sync --locked
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

同时人工核对：

1. current-session freshness hold：direct stdout、exit `0`、bounded context、draft replacement；
2. `--send-draft --target`：重新进入 gate、exact payload、sent/queued 后清除；
3. 两个 BCN session 的 draft 隔离；
4. cross-session hold：direct stdout handoff guidance、无 draft/outbound/provider call；
5. provider sent/queued/partial/failed/unknown output 与 persisted outcome；
6. v15 production-shaped database 升级后的 schema、provider-attempt history、indexes 与 trigger。

完成后停下 final review。
