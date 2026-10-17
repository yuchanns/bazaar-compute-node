# Reminder 审批对象回溯

## 状态

- 模式：Plan。
- 状态：Task 1 已完成，等待 review。
- 基线：`main` 当前 HEAD，实施分支 `fix/reminder-authority`。
- Task 串行执行；Task 1 完成并通过检查后停下来等待 review。

## 目标

Reminder fire 创建的 system turn 在 runtime 请求工具审批时，通过对应 Reminder 的原始
inbound anchor 找到人类审批对象。审批仍路由到当前 Reminder turn 所在 conversation，审批请求
不绑定原始 anchor 的 provider reply target。

## 已确认依据

- `Reminder` 已持久化不可变的 `owner_session_id` 与 `anchor_message_id`；Reminder transition 的
  identity validation 不允许后续修改 anchor。
- `ReminderScheduler._materialize_due_reminder()` 在 fire 时已经读取了 anchor，并通过
  `materialize_owned_reminder_message()` 原子推进 Reminder、插入 system Message。
- 当前 Reminder system Message 只记录 `sender_kind=system` 与
  `system_message_kind=reminder`，落库后没有从 fire Message 反查 Reminder 的结构化键。
- `SessionTurnCoordinator.approval_handler()` 当前直接使用 turn 起始 Message 的 sender；因此
  Reminder turn 会在进入 Channel 前按非 human 拒绝。active turn 后续收到 human steer 不会替换
  turn 起始 Message。
- `ChannelApprovalRequest.provider_sender_id` 是审批对象字段；
  `provider_reply_to_message_id` 是独立、可空的回复目标字段。Reminder 的审批对象来自 anchor，
  但审批卡片不需要回复到 anchor。
- storage 已有 `get_reminder(owner_session_id, reminder_id)` 与
  `get_owned_message(agent_id, session_id, message_id, direction=inbound)`，不需要 schema 或 repository
  query 扩展。

## 合同

Reminder fire Message 的 metadata 在现有两个字段之外写入完整 canonical `reminder_id`。审批请求
到达时，只有 `sender_kind=system` 且 `system_message_kind=reminder` 的 Message 才使用该键读取同一
owner session 的 Reminder，再用 Reminder 的 `anchor_message_id` 读取同一 agent、同一 session 的
inbound anchor。

anchor 的 `sender_kind` 为 human 时，它成为审批 authority：

- 批准/拒绝仍由当前 Channel 的 `request_approval()` 决定；
- `target_kind` 与 `provider_thread_id` 继续使用当前 Reminder turn 的 Channel context；
- `provider_sender_id` 使用 human anchor 的 sender id；
- `provider_reply_to_message_id` 继续来自 turn 起始 Message，因此 Reminder 为 `None`。

审批对象为 human 且具有 provider sender id 时进入 Channel。所有已解析到的非 human 审批对象，
以及 Reminder 键缺失、Reminder 不存在、anchor 不存在或 human anchor 没有 provider sender id 等
无法解析审批对象的情况，统一返回 resource 文案
`No person can approve tool use here. Explain in your reply.`。该 reason 会被 runtime 原样提供给
agent；文案只陈述 agent 可理解的事实与下一步动作，不出现 turn、approval target 等 BCN 内部概念。
Reminder fire envelope 的 `sender_kind=system` 只表示触发来源，不参与审批身份语义。审计保留 turn
起始 Message 的 `sender_kind`，并记录实际 `approval_target_kind`，使触发来源与审批对象可以同时
辨认。

## Task 1：持久化 lineage 并回溯审批对象

1. 在 Reminder scheduler materialize 的 system Message metadata 中写入 canonical
   `reminder_id`。
2. 在 `SessionTurnCoordinator` 的现有 approval callback 内联 Reminder → anchor 查询；用解析出的
   human anchor 决定是否进入 Channel，并只把其 sender id 投影为审批对象。
3. 扩充现有 Reminder scheduler 正向合同，确认 fire Message 携带可回溯的 Reminder identity；扩充
   Core orchestration 正向合同，使用真实 `MemoryStorage`、`TestChannel` 与 `TestRuntime` 验证
   Reminder system turn 的审批请求到达当前 Channel、具有 human 审批对象且没有 reply target。
4. 运行 Reminder scheduler 与 orchestration focused tests、Ruff format/check，以及仓库要求的
   `uv run scripts/pyright_lsp_check.py --outputjson .`；发送业务 diff 后停在 review。

## 验证结果

- Reminder / approval focused tests：12 passed，54 deselected。
- Reminder scheduler 与 orchestration 完整测试：66 passed。
- 全量 non-e2e：418 passed，1 skipped，25 deselected。首次运行仅命中已知 daemon process teardown
  5 秒超时；该用例单独重跑通过，随后全量重跑通过。
- Ruff：233 files formatted；check passed。
- Pyright LSP：198 files analyzed，zero diagnostics。
