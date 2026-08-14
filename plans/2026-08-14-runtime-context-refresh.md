# Runtime Context 变化后的会话过期

## 状态

- 当前阶段：Task 1.1 至 Task 1.4 已完成；Task 1.5 实施完成，等待 review。
- 实施分支：`f-20260814-runtime-context-refresh`。
- 基线：`main@029c316e41f776078eed755ca6e5b74ef3abac6a`。
- 当前未提交 diff 只包含 Task 1.5 的计划与测试基础设施；本计划不授权 commit、push、PR、发布或部署。

## 目标

1. 把 runtime provider 观察到的 skill 与 `AGENTS.md` 变化收敛为 provider-neutral 的
   `RuntimeExpire(runtime_session_id)`。
2. core 收到一次有效 `RuntimeExpire` 后，把当时全部 live runtime session 标记为 expired，并通过现有
   `RuntimeQueue` 分发同一种事件。
3. 已处于 `IDLE` 的 session 立即停止旧 provider process 并清除 live binding，不主动创建新的 runtime session。
4. 正在 turn、compaction 或 recovery 的 session 由现有 active loop 立即消费 `RuntimeExpire`，但不打断当前工作；
   状态回到 `IDLE` 后立即执行同一 stop/clear 流程。
5. 后续 notifying inbound 通过现有 session establishment 创建新的 runtime session、provider thread 与 bcc
   capability，同时保留 durable message、cursor、outbound state 与 runtime attempt。

## 问题与证据

- 当前 `IRuntime` 只暴露 session/turn lifecycle，没有承载 provider 主动发起的 node-scoped expire event。
  `SessionOrchestrator` 也只有 channel receive loop 和 per-session runtime queue，无法接收并 fan-out 一次全局刷新。
- 当前 Codex app-server protocol 会为已注册的 local skill roots 发送 connection-scoped `skills/changed`，payload
  为空，语义是 skill catalog 已失效。BCN 每个 live runtime session 各持有一个 app-server process，因此同一次
  文件变化可能由多个 connection 分别上报。
- 当前 app-server 不会自动为 `AGENTS.md` 发送专用通知；它提供 connection-scoped `fs/watch` 与
  `fs/changed`。watch 支持绝对文件路径、目标尚不存在后创建以及原子替换，适合由 Codex adapter 注册
  workspace 与 Codex home 的 `AGENTS.md`。
- `JsonlProcessSupervisor` 当前把所有非 response 消息写入同一个 `_incoming` queue，活跃
  `CodexTurnEventStream` 是唯一 consumer。若再增加一个 idle watcher 直接读取该 queue，会与 turn stream 争抢
  notification；而 session `IDLE` 时没有 consumer，context event 也无法及时进入 core。
- 已合并的 runtime idle recycling 已具备 confirmed stop、进程内 live registry、UUIDv7 runtime session、timer
  与 capability rollover，并已采用“关闭后等待下一条 inbound 再创建”的 lifecycle。本需求只需让 context change
  复用同一 stop-only 路径。

## 已确认边界

- `RuntimeExpire` 是瞬时控制事件，不写入 SQLite，也不复用 turn lifecycle 的 `RuntimeEvent` 或用户可见
  `StreamEvent`。
- neutral event 只表达“当前 runtime 已过期”与发出事件的 runtime session id；core 不感知
  `skills/changed`、`fs/changed`、watch id、文件路径或 provider wire payload。
- Codex adapter 把 `skills/changed` 与属于已注册 `AGENTS.md` watch 的 `fs/changed` 映射为同一种 neutral event。
  workspace root `AGENTS.md` 与 Codex home `AGENTS.md` 都纳入 watch；每个新建或 reconcile 的 app-server
  connection 都重新注册 watch。
- transport 仍保持单 notification consumer。已识别的 context change 在 JSONL route 边界直接投递到
  runtime-owned event mailbox；turn、approval 与其他 notification 继续进入原 `_incoming` queue。
- 一次有效 `RuntimeExpire` 以“收到事件时仍 live 的 runtime session id 集合”为 fan-out 范围。core 先把全部
  target id 加入 `expired_runtime_ids`，再向每个现有 per-session `RuntimeQueue` 投递
  `RuntimeExpire(target_runtime_session_id)`。同一组旧 connection 后续重复上报时，source id 已在该 set 中，
  不重复 fan-out 或 stop。
- 新 runtime session 发出的后续 `RuntimeExpire` 的 source id 不在 set 中，因此重新 snapshot 当时全部 live
  session 并执行新一次 fan-out。旧 active session 的 id 始终只在 set 中保留一次。
- per-session runtime queue 串行消费 inbound、activity、idle expiry 与 `RuntimeExpire`。现有 active loop 同时
  await turn task 与 queue task，因此 active turn 期间也会立即取出 `RuntimeExpire`，但只保留 expired 标记；
  inbound 继续按现有 steer 语义进入当前 turn，stop/clear 在该 turn terminal 并回到 `IDLE` 后执行。
- context expire 与 timer expiry 可以同时命中同一个 runtime id，并按 per-session queue 的实际到达顺序处理。
  任一事件先完成 stop/clear 后，另一事件因 live runtime identity 已变化而成为 stale，不增加优先级状态、deadline
  判定或新的锁。
- 清理已到期 timer 是正常且幂等的：timer 已离开时间轮时无需再次摘除；unfinished watcher 统一 cancel/await；
  watcher 已投递的旧 `_RuntimeExpiry` 继续由 runtime id、timer id 与 generation 校验丢弃。
- 刷新不打断 `WORKING`、`COMPACTION_STARTING`、`COMPACTING`、`COMPACTION_COMPLETED`、`STARTING` 或
  `RECONCILING`。session 进入 `FAILED`、stop 或 discard 路径时同步清除该旧 runtime id 的 expired 标记。
- expire 是 core 主动终止 live runtime 的正常 lifecycle，终态为无 live binding；reconcile 保留给 core 仍视为
  live、但 provider connection/process 状态未知或丢失的异常恢复。
- context expire 不算用户 activity。stop/clear 同步取消旧 timer 与 watcher；下一条 notifying inbound 创建新
  runtime 时，以该 inbound 作为新 activity 建立完整 idle timeout。
- old runtime environment 的 bcc binding 在 stop/discard 时失效；下一条 notifying inbound 创建的新 provider
  environment 只能取得新 runtime session id 对应的新 capability。

## 设计

### Provider-neutral `RuntimeExpire`

在 `core/runtime.py` 增加不可变 `RuntimeExpire`，只携带 `runtime_session_id`。同一类型既是 `IRuntime` 向 core
报告 source runtime 过期的 neutral event，也是 core fan-out 后写入既有 per-session `RuntimeQueue` 的控制项。
`IRuntime` 增加单消费者 `receive_expire()`；runtime adapter 负责把 provider-specific notification 写入自己的
async mailbox，orchestrator 持有唯一 receive task。

`TestRuntime` 提供受控的 expire 投递，用于验证 core fan-out、重复事件去重、active 延迟与 shutdown 收尾。
runtime stop 前由 orchestrator cancel/await receive task，因此 adapter mailbox 不需要跨 lifecycle 持久化。

### JSONL notification 分流与 Codex watch

`JsonlProcessSupervisor` 接受同步 notification router。`_route_message()` 在 response correlation 之后、写入
`_incoming` 之前调用 router；router 只返回是否已消费，不执行 await、不取得 core lock，也不启动 detached task。
已消费的 context notification 写入 `CodexAppServerRuntime` 的 runtime event mailbox；其余消息保持现有顺序进入
turn stream。

`CodexAppServerClient` 增加 `fs/watch` request builder 与 response/notification parser。每次
`_open_connection()` initialize confirmed 后，为以下绝对路径注册 connection-scoped watch：

1. 当前 runtime workspace root 的 `AGENTS.md`；
2. 当前 connection 生效的 Codex home 下的 `AGENTS.md`。

watch id 在单 connection 内稳定且互不冲突。adapter 只接受已登记 watch id，且 changed path 必须属于对应目标；
合法 `fs/changed` 与 `skills/changed` 都生成 `RuntimeExpire(session.id)`。connection stop 自然释放 watch；
reconcile 新开 connection 时执行相同注册，确保恢复后的进程继续观察 context。

### Core fan-out、去重与 active loop

`SessionOrchestrator.start()` 在 runtime start confirmed 后创建 `bcn-runtime-expire-events` task，循环 await
`receive_expire()`。收到 event 后先确认 source runtime id 仍是当前 live registry 中的值；source id 已在
`expired_runtime_ids` 时直接忽略重复上报。

对有效 event，orchestrator snapshot 当前全部 live runtime session id，先把它们加入 `expired_runtime_ids`，再向
每个仍存在的 per-session runtime queue 投递 `RuntimeExpire(runtime_session_id)`。全局 receive task 只 fan-out，
provider stop 由对应 per-session worker 执行。

runtime worker 消费 `RuntimeExpire` 时比较 live identity。state 已为 `IDLE` 时进入 stop/clear；state 仍活跃时不
中断当前工作，由已经写入的 `expired_runtime_ids` 保留意图。turn task terminal 后，worker 调用共同的
expired-at-IDLE 检查点。confirmed stop 或旧 session discard 后移除旧 expired id；stale queue item 只按 identity
校验丢弃。

### Stop/clear 与下一次 session establishment

worker 复用现有 `_concurrency.for_session(session_id)`，在临界区内再次确认 queue 顺序、live id、expired id 与
`AgentState.IDLE`，并执行：

1. bounded stop 旧 provider；
2. 完成 timer watcher、live mapping、state 与 capability 旧 binding 的既有清理；
3. 清除旧 runtime id 的 expired 标记并写入 context expire requested/completed 或 failed audit；
4. 保持无 live binding，等待下一条 notifying inbound 进入现有 session establishment。

expire 过程由对应 per-session worker 独占；全局 event task 只投 mailbox，不直接 stop provider。多个 `IDLE`
session 的 worker 可以并行 stop，同一个 BCN session 始终受既有 FIFO queue 与 session lock 串行化。下一条
notifying inbound 复用现有 `_ensure_runtime_session_or_discard()` 创建新 UUIDv7 runtime session 与 provider thread。

## 实施顺序

### Task 1.1：建立 neutral expire event 与单消费者 notification route

修改文件：

- `src/bazaar_compute_node/core/runtime.py`
- `src/bazaar_compute_node/contrib/codex_app_server/process.py`
- `tests/support/src/bcn_test_support/runtime.py`
- `tests/contrib/test_codex_app_server.py`

实施动作：

1. 增加 `RuntimeExpire` 与 `IRuntime.receive_expire()` contract，并允许同一类型进入现有 `RuntimeQueue`。
2. 为 `TestRuntime` 增加 lifecycle-bounded expire event mailbox。
3. 为 JSONL supervisor 增加同步 notification router，并保持 response、provider request、turn notification 与
   process-close 的现有顺序和错误语义。
4. 覆盖 router 消费、未消费消息继续进入 turn stream、process stop 与 receive task cancellation。

Focused tests：expire event 值校验与单消费者等待；notification router 只摘取目标 notification；活跃 turn 的
terminal/approval/stream 顺序保持；supervisor stop 后所有 pending task 收敛。

完成条件与停止点：core port 与 JSONL transport 可以安全承载 idle 时到达的 neutral expire event；提交业务
diff，停在 Task 1.1 review，不进入 Task 1.2。

### Task 1.2：映射 Codex skill 与 `AGENTS.md` 变化

修改文件：

- `src/bazaar_compute_node/contrib/codex_app_server/client.py`
- `src/bazaar_compute_node/contrib/codex_app_server/runtime.py`
- `tests/contrib/test_codex_app_server.py`

实施动作：

1. 增加 `fs/watch` request/response 与 `skills/changed`、`fs/changed` parser。
2. 每个新 connection initialize 后注册 workspace/Codex home `AGENTS.md` watch；start 与 reconcile 共用同一
   `_open_connection()` 路径。
3. notification router 把 skill change 和已登记 AGENTS watch 事件投递为 `RuntimeExpire`；connection stop
   清除其 watch registry。
4. 让多个 connection 对同一文件变化的上报携带各自 runtime session identity，供 core 批次去重。

Focused tests：精确 wire builder/parser；workspace 与 Codex home watch registration；文件不存在后创建、普通写入
与原子替换均形成 `RuntimeExpire`；skill change 形成同类 event；start/reconcile connection 都具备 watch；其他
`fs/changed` 保持 provider-local。

完成条件与停止点：Codex adapter 能在 turn active 与 idle 两种状态下持续产出 provider-neutral
`RuntimeExpire`；提交业务 diff，停在 Task 1.2 review，不进入 Task 1.3。

### Task 1.3：fan-out 并过期全部 live session

修改文件：

- `src/bazaar_compute_node/core/orchestration/session.py`
- `src/bazaar_compute_node/app/application.py`
- `tests/contrib/test_orchestration.py`
- `tests/app/test_composition.py`

实施动作：

1. 管理 runtime expire receive task，并把 `RuntimeExpire` fan-out 到当时全部 live per-session queues。
2. 维护单一 `expired_runtime_ids` set，按 source/live identity 去重和丢弃 stale event。
3. 复用 runtime worker 对 queue task 与 turn task 的并行等待：`IDLE` 收到事件后立即 stop/clear，active/compaction/
   recovery 立即消费事件但延迟到下一次 confirmed `IDLE`。
4. 在现有 session lock 内复用 stop/discard/state/audit contract 清除旧 runtime、timer 与 capability binding；
   expire 以无 live binding 结束，reconcile 继续只承载异常恢复。
5. 协调 pending inbound、timer `_RuntimeExpiry`、context `RuntimeExpire` 与 shutdown；任一 expire 先清除 live
   runtime 后，另一事件按 identity noop。

Focused tests：一个 event stop 多个 idle session；同一批每个旧 connection 重复上报只 stop 一次；新 session 上的
后续 event 触发下一批；active turn 与各 compaction state 不被中断且到 `IDLE` 后 stop；expire 前后 inbound FIFO；
下一条 notifying inbound 创建新 runtime/thread/capability；旧 bcc capability 失效且新 environment 成功；timer 与
context expire 的两种到达顺序均只 stop 一次，另一事件 noop；expire、inbound 和 daemon shutdown 竞态全部结构化
收尾。

完成条件与停止点：所有 live session 都按 state 安全 stop/clear，重复 provider event 不产生重复 stop；
提交业务 diff，停在 Task 1.3 review，不进入最终验收。

### Task 1.4：最终验收

验证范围：

1. focused tests 与完整 test suite；Ruff、Pyright、compileall、lock verification 和 `git diff --check`。
2. Neovim LSP 实际 attach 后检查全部改动 Python 文件 diagnostics。
3. 使用真实 Codex runtime 建立至少两个 live session；更新 skill 后确认全部 idle session 的 provider process
   停止且 live binding 清空，没有主动创建新 runtime。
4. 使用真实 Codex active turn 更新 workspace `AGENTS.md`；确认其他 idle session 立即 stop，active session 的
   turn 正常 terminal 后 stop；下一条 inbound 才创建新 runtime/thread/PID 并读取新 instructions。
5. 验证普通写入、文件创建与原子替换路径；验证下一条 inbound 创建的 bcc environment 使用新 runtime id/
   capability，durable message、cursor、outbound state 与 runtime attempt 持续可读。
6. 在正值 `runtime.idle_timeout` 下验证 timer 与 context expire 任一先到都只完成一次 stop/clear，另一事件 noop，
   下一条 notifying inbound 使用完整 idle timeout。

完成条件与停止点：提供业务 diff 与真实 provider 验收证据，停在 task review；不执行 commit、push、PR、发布
或部署。

### Task 1.5：收口 standalone 跨平台验收基础设施

修改文件：

- `tests/support/src/bcn_test_support/environment.py`
- `tests/support/src/bcn_test_support/lifecycle.py`
- `tests/support/src/bcn_test_support/__init__.py`
- `tests/conftest.py`
- `tests/test_support.py`
- `tests/contrib/test_orchestration.py`
- `tests/contrib/test_codex_app_server.py`

实施动作：

1. 由 `tempfile.TemporaryDirectory()` 选择各平台系统临时目录，在同一临时根内提供 HOME、CODEX_HOME、BCN
   data、workspace 与固定短文件名 endpoint，并由 context 统一恢复环境和删除临时状态。
2. pytest 的自动 `basetemp` 与 standalone 验收复用同一个 system-temp helper；完整 pytest 保持原 HOME，仅继续
   隔离自身 BCN data name，standalone 的 HOME 不进入 pytest 进程环境。
3. 提供 provider-neutral terminal wait：同一 session 的新 outbound 已发送、core lifecycle 到达 `IDLE` 或已确认
   runtime discard，并且对应 active runtime turn 已清除；provider audit 与 stderr 只保留为超时诊断。
4. 真实 provider 验收的 local endpoint 直接使用 system-temp fixture 根，不再从 HOME/BCN data 目录派生深层
   socket path；真实 Codex 凭据只由验收调用方显式复制到临时 CODEX_HOME。

Focused tests：临时根、子目录、环境恢复与清理；pytest `basetemp` 复用；普通 terminal 与 context-expire 后
discard terminal；真实 Codex e2e 使用短 endpoint 且不依赖 provider audit 名。

完成条件与停止点：Linux focused/full/static/LSP gate 全绿；Windows 与 macOS 在新 exact head 上复核
system-temp、neutral wait 与真实 Codex runtime-expire 主路径，随后停在 task review；不执行 PR、发布或部署。

## 最终验收

- skill 或有效 `AGENTS.md` watch 变化最终只形成 provider-neutral `RuntimeExpire`。
- 一次有效 `RuntimeExpire` fan-out 覆盖当时全部 live session；同批旧 connection 的重复事件不会重复 stop。
- `IDLE` session 立即 stop/clear，active session 的当前工作完整结束后立即 stop/clear；expire 以无 live binding
  结束，reconcile 只承载异常恢复。
- 下一条 notifying inbound 才产生新的 runtime session id、provider thread、provider process 与 bcc capability。
- context expire 与 timer expiry 任一先完成 stop/clear 后，另一事件按旧 runtime identity noop，timer cleanup 与
  stale expiry 不产生错误。
- durable message、cursor、outbound state 与 runtime attempt 在刷新前后持续可读写。
