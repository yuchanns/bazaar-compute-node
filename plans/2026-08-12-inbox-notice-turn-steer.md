# Inbox Notice Turn Steer

## 目标

当同一 BCN session 已有运行中的 runtime turn，而新的通知型 inbound 到达时，消息仍先按现有契约
持久化到 inbox；随后向当前 turn 追加一条内容无关的 inbox notice，使 agent 可以在同一 turn 内调用
`raft message check` 读取新消息。

steer 只传递 session 和 unread count，不传递消息正文、发送者或 message metadata。inbox、consumer
cursor 和 runtime attempt 仍是消息投递的唯一业务边界，steer 不是投递确认，也不能消费 queued inbound。

## 统一 Runtime 契约

在 provider-neutral `IRuntime` 增加 `steer_turn`：输入 `RuntimeSession`、当前 `RuntimeTurn` 和普通文本
notice，返回 `bool`。steer 不承担消息交付，不创建、不替换、也不推进 turn，core 只需要二元决策：

- `True` 表示当前 turn 已确认接受 notice；
- `False` 统一表示不支持、没有 active binding、provider 拒绝、timeout 或 transport outcome 无法确认。

不支持 steer 的 runtime 返回 `False`。Codex adapter 只有收到并校验通过 `turn/steer` response 才返回
`True`；其他 provider 错误记录必要日志后返回 `False`，但 caller cancellation 继续向上传播。core 不按
provider 名称或失败原因分支，`False` 一律保留 pending inbound 并进入下一轮。

Codex adapter 将 notice 映射到 `turn/steer`，请求携带 provider `threadId`、强制前置条件
`expectedTurnId` 和 text input。它不传 `model`、`cwd`、sandbox 或其他 turn override。响应中的 `turnId`
必须与预期 provider turn 一致，否则作为 protocol failure。

## Orchestration 与异步串行边界

每个 BCN session 已经只有一个 `_runtime_loop`。它作为 session actor，独占 current turn、pending inbound
和 start/steer 决策，不再叠加 `SessionLockRegistry`，也不创建脱离 worker 生命周期的 steer task。

`_ingress_loop` 仍只负责在 storage transaction 中完成 inbound 去重、canonical session 映射和 inbox
持久化，再把原 `_RuntimeNotification` 放入 per-session runtime queue。它不读取或修改 active turn。

`_runtime_loop` 启动一个 turn 后，同时等待该 turn task 的 terminal 与同 session runtime queue 的下一条
notification：

1. notification 先到时，worker 把它和当下可立即 drain 的 notification 收入 pending batch；
2. worker 查询最新 unread count，并通过统一 `IRuntime.steer_turn` 发送与 `turn/start` 完全相同的 inbox
   notice；pending batch 不从业务队列语义中确认完成；
3. terminal 先到时，worker 先收口 current batch，再把 pending batch 作为下一轮输入；
4. 下一轮 `_run_notification` 会重新查询 consumer cursor：若当前 turn 已读完 inbox，则直接返回而不创建
   重复 turn；若仍有 unread，则按现有路径启动 next turn。

所有“当前 turn / pending inbound / 是否 start 或 steer”的业务决策均在唯一 session actor 内顺序执行。
turn task 仅推进其自身 stream 到 terminal；queue read task 只是 actor 的另一个等待源，两者不拥有 session
调度状态，因此无需 core lock。Codex 的 `expectedTurnId` 只是 adapter 对 provider 协议的精确映射；若
provider turn 已在请求抵达前结束，adapter 返回 no-op/拒绝，pending batch 仍由 actor 下一轮处理。

因此回退语义是原生的：

- 当前 turn 读取了 inbox：其完成后 queue worker 发现无 unread，不再启动重复 turn；
- 当前 turn 未读取 inbox，或 steer 未被接受：其完成后原 queue worker按既有逻辑创建下一 turn；
- 多条 inbound 在 active turn 期间到达：每次先落库，actor 可批量 drain，notice 只报告处理 pending batch
  时的最新 unread count，不携带正文。

start 和 steer 共用唯一的 inbox notice 构造逻辑，文本保持现有契约：

```text
[inbox notice session={session_id}]
Inbox update: {unread_count} unread message(s). Use the message command to read them.
```

## Audit

core 只记录二元脱敏结果 `runtime.request.turn.steer.accepted` 或
`runtime.request.turn.steer.not_accepted`。correlation 使用触发 notice 的 inbound seq、BCN/runtime
session、当前本地/provider turn；metadata 仅含 `provider_method=turn/steer` 和 `unread_count`。失败原因
如需诊断，由 runtime adapter 的结构化日志保留，不能改变 core 的 next-turn 决策。

## 实施任务

1. 扩展 `IRuntime` 与 Codex client/runtime：新增 steer 参数构建、响应校验和 provider-neutral 结果映射；
   更新 runtime contract tests，完成后停在 review。
2. 扩展 session orchestration：让唯一 per-session runtime worker multiplex current turn terminal 与
   runtime queue；新 notification 进入 pending batch 后发送 notice，terminal 后仍由既有 unread 查询决定
   是否启动 next turn；完成后停在 review。
3. 补齐 focused tests，证明：
   - steer payload 不包含 inbound 正文、发送者和 metadata；
   - provider thread/turn correlation 与 `expectedTurnId` 精确；
   - 不支持 steer 的 runtime 静默保持旧行为；
   - confirmed steer 后 agent 读取 inbox，不产生重复 next turn；
   - rejected/unknown/terminal race 时 inbound 不丢失，仍在当前 turn 结束后启动 next turn；
   - steer I/O 不阻塞随后 inbound 持久化，且 shutdown 会收拢 session actor 持有的两个等待源；
   - 多条 active-turn inbound 的 unread notice 与 queue/cursor 最终一致。
4. 运行 focused tests、完整 non-real-home suite、Ruff、Pyright、compileall、lock verification、
   `git diff --check`，并对所有改动 Python 文件执行 LSP diagnostics；完成后提供业务 diff，停在 review。

本任务不提交、不推送、不发布、不部署；这些动作需要单独授权。
