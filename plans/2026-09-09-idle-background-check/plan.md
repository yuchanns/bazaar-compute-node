# 空闲回收改为到期时检测后台任务

## 背景

轮次结束后由 `_start_runtime_timer_if_idle`（`core/orchestration/orchestrator.py:399`）决定是否启动空闲
定时器：它先调用 `has_background_job`，provider 报告仍有后台任务就直接返回，不启动定时器。定时器没有启动，
后台任务结束后就需要另一条通路把它重新拉起来，于是两个 provider 各自在后台集合由非空转空时发出
`RuntimeBackgroundIdle`（`contrib/codex/runtime.py:710`、`contrib/claude/runtime.py:511`），orchestrator 的
`_handle_runtime_background_idle`（`orchestrator.py:871`）收到后再调用同一个方法。

这条通路的存在使得后台状态必须由 provider 主动推送，事件缺失即意味着会话永远不再被检查。Codex 在 Windows
上没有可用的后台查询结果，`has_background_job` 直接返回 `True`（`contrib/codex/runtime.py:522`），
`item/completed` 触发的后台刷新也跳过 Windows（`contrib/codex/runtime.py:575`），因此 Windows 上定时器
从不启动，空闲回收整体失效。

## 目标

轮次结束即启动空闲定时器，后台任务的检测推迟到定时器到期时进行：查询结果为空则停止会话，非空或查询失败则
重新计时。`agent.idle_timeout <= 0` 时依旧不启动定时器。

上下文变更同样改为惰性：变更时只在内存里留下标记，销毁推迟到能够安全销毁的时刻。检测改由主动查询之后，
`RuntimeBackgroundIdle` 及其两侧实现随之移除，Codex 的 Windows 分支一并解除。

## 调研结论

### Codex

`thread/backgroundTerminals/list` 在三个平台都返回可用结果，实测记录：

- Linux `codex-cli 0.153.4`：普通后台命令运行期间返回条目，任务自然结束后返回空列表。
- Windows `codex-cli 0.149.0`：默认模式与 `--enable unified_exec` 下均能返回运行中的条目，任务结束后清空。
- macOS `codex-cli 0.153.4`：`sleep 15` 运行期间返回一条 `command=sleep 15`，`item/completed` 到达后清空。

该方法标注为 experimental，要求客户端声明 `capabilities.experimentalApi`；
`build_initialize_params`（`contrib/codex/client.py:62`）已经声明该能力。响应中的 `processId` 是 provider 的
不透明标识，macOS 上 `osPid` 为 `null`，判定只依据列表是否为空。

该列表的内容是 codex 自身 unified_exec 注册表中未退出的进程（codex 源码 `core/src/unified_exec/process_manager.rs` 的 `list_processes`），`codex-code-mode-host` 与 MCP 服务进程
不属于其中，Windows 实测也确认 code-mode 不出现在列表里，因此常驻辅助进程不影响判定。

### Claude Code

版本 `2.1.247`。stream 中的 `system/background_tasks_changed` 携带当前全部后台任务的完整列表：

```
{"type":"system","subtype":"background_tasks_changed","tasks":[{"task_id":"bn9p8izo3","task_type":"local_bash","description":"Sleep 8 seconds in background"}]}
{"type":"system","subtype":"background_tasks_changed","tasks":[]}
```

要点：

- 该事件是全量快照而非增量，最近一次的 `tasks` 即当前后台任务集合，集合为空时为 `tasks: []`。
- 常规后台 Bash 的 `task_type` 为 `local_bash`，与 `local_agent`、`local_workflow` 并列出现在同一列表中。
- 事件只在集合发生变化时发出，会话开始没有初始快照，`system/init` 不含任务字段。
- 后台任务随 Claude Code 进程退出而终止（自然退出与 SIGTERM 均已验证），`resume` 是新进程恢复历史会话，
  `contrib/claude/runtime.py:467` 每次都新建 `ProcessSupervisor` 并 `start()`，不存在连接已有进程的路径。
  实测 resume 一个曾运行后台任务的会话，整条流只有 `system/init`，没有 `background_tasks_changed`。
  因此连接建立时的后台集合按空初始化。

### 时序

两个 provider 都出现过轮次已经结束而后台命令仍在运行的情况：macOS 上 `turn/completed` 到达时
`backgroundTerminals/list` 仍返回一条；Claude 上模型起完后台命令即回复结束当前轮，任务继续运行。
后台集合以 provider 的当前结果为准。

## 设计

### 定时器与到期检测

`_start_runtime_timer_if_idle` 保留 IDLE 与未过期两项校验后直接启动定时器。

定时器到期且通过既有的身份与 IDLE 校验后，在 `_stop_runtime_session_locked` 之前调用
`has_background_job`。返回非空、或调用抛出异常时，清除 `binding.expired` 并调用 `_start_runtime_timer`
重新计时，本次不停止会话。返回空才继续原有的停止流程。

查询失败按仍有后台任务处理：失败的结果是多保留一个周期，下次到期重新查询。

### 上下文变更

上下文变更的目的是让后续轮次用上新的 skills 与配置，销毁只是达成它的手段。把两者拆开之后，变更时不再立即
销毁，只保留标记，销毁发生在两个已经安全的时刻。

标记沿用现有的 `_expired_runtime_ids`：`_receive_runtime_event_loop`（`orchestrator.py:1029`）收到 provider
发来的 `RuntimeExpire` 生命周期事件时，已经把该 runtime 下所有会话的 id 写入这个集合。这个生命周期事件与写入
标记的动作都保留，变的是随后不再向 per-actor 队列投递同名的队列项去触发立即停止。

标记在两处被消费：

1. 定时器到期走到销毁时，销毁本身会经 `_discard_runtime_session`（`orchestrator.py:359`）把 id 从
   `_expired_runtime_ids` 移除，标记随之消失。
2. 新的 turn 进入 `_establish_runtime_session`（`orchestrator.py:1419`）时先看标记：没有标记说明上下文一定是
   最新的，不论是从未变更还是刚刚重建过，直接进入原有流程；有标记则查一次后台任务，为空就先销毁再按现有的
   `_discard_runtime_session` + `_create_runtime_session` 重建，非空则保留当前会话继续这一轮，标记留到下一次
   被消费。

因此后台任务存在时不会被上下文变更杀掉，而一旦它结束，最近的一次定时器到期或下一轮开始就会完成重建。

### Codex

`has_background_job` 去掉 Windows 分支，所有平台都查询 `thread/backgroundTerminals/list`。`start()` 中的
Windows 告警一并移除。

`item/completed` 触发的后台刷新失去用途：到期时的查询已经覆盖了同样的信息。移除 `_refresh_background_state`
（`contrib/codex/runtime.py:689`）、`_background_refresh_tasks` 及其在 `stop()` 中的取消逻辑、
`_Connection.background_state_lock` 与 `background_job_present`、`_record_background_state`
（`contrib/codex/runtime.py:733`）。`has_background_job` 直接返回
`parse_background_terminals_response(response)`。

### Claude Code

`_observe_background`（`contrib/claude/runtime.py:598`）改为只消费 `system/background_tasks_changed`：取
事件中的 `tasks`，把其中的 `task_id` 整体替换 `_Connection.active_background_task_ids`。全量快照使
`task_started`、`task_updated`、`task_notification` 三个分支不再需要，`background_active` 字段随之移除。
`local_bash` 因为不再按 `task_type` 过滤而自动纳入。

`has_background_job` 维持现有形状，返回 `active_background_task_ids` 非空或 `client.provider_wake_active`。

### 事件

`RuntimeBackgroundIdle` 的两个发射点与唯一消费者都被移除后，删除
`core/runtime.py` 中的事件类型、`orchestrator.py` 的 `_handle_runtime_background_idle`、`_RuntimeQueueItem`
联合类型成员（`orchestrator.py:93`）、`_consume_runtime_queue_item` 的 `case`（`orchestrator.py:652`）
以及 `orchestrator.py:1000` 的分支。

## Tasks

### Task 1：定时器无条件启动，检测移到到期时

Files: `src/bazaar_compute_node/core/orchestration/orchestrator.py`、
`tests/contrib/test_orchestration.py`。

- `_start_runtime_timer_if_idle` 去掉 `has_background_job` 调用。
- `_stop_expired_runtime_if_idle` 在定时器到期分支停止会话前查询后台任务，非空或异常则重置
  `binding.expired` 并重新计时。
- 调整 `test_runtime_idle_timeout`（`tests/contrib/test_orchestration.py:3826`），补一个到期时报告有后台任务
  的用例，断言会话保留且定时器重新启动。
- 运行 `uv run pytest tests/contrib/test_orchestration.py -k idle`。

### Task 2：上下文变更改为惰性标记

Files: `src/bazaar_compute_node/core/orchestration/orchestrator.py`、
`tests/contrib/test_orchestration.py`。

- `_receive_runtime_event_loop` 保留 provider 的 `RuntimeExpire` 生命周期事件与写入 `_expired_runtime_ids`
  的动作，不再向 per-actor 队列投递队列项；随之移除 `_handle_runtime_context_expire`、`_RuntimeQueueItem`
  中的 `RuntimeExpire` 成员与对应 `case`，以及 `_stop_expired_runtime_if_idle` 中的 `context_expired` 分支
  和轮次结束后 `orchestrator.py:732` 的调用。
- `_establish_runtime_session` 开头消费标记：会话 id 在 `_expired_runtime_ids` 中时查询后台任务，为空则
  `_stop_runtime_session` + `_discard_runtime_session` + `_create_runtime_session` 重建，非空则沿用当前会话。
- 补用例：标记存在且无后台任务时下一轮使用新会话；标记存在且有后台任务时下一轮沿用旧会话且标记保留。
- 运行 `uv run pytest tests/contrib/test_orchestration.py -k 'idle or expire'`。

### Task 3：Codex 解除 Windows 限制并删除刷新通路

Files: `src/bazaar_compute_node/contrib/codex/runtime.py`、`tests/contrib/test_codex.py`。

- `has_background_job` 与 `start()` 移除 `os.name == "nt"` 分支。
- 移除 `item/completed` 的刷新触发、`_refresh_background_state`、`_background_refresh_tasks`、
  `background_state_lock`、`background_job_present`、`_record_background_state`。
- 删除 `test_windows_codex_runtime_assumes_background_job`（`tests/contrib/test_codex.py:875`）与
  `test_codex_background_state_reports_only_the_idle_edge`（`tests/contrib/test_codex.py:657`），
  保留并按新形状调整 `test_codex_runtime_reports_background_job`（`tests/contrib/test_codex.py:907`）。
- 运行 `uv run pytest tests/contrib/test_codex.py`。

### Task 4：Claude 改用全量快照

Files: `src/bazaar_compute_node/contrib/claude/runtime.py`、`tests/contrib/test_claude.py`。

- `_observe_background` 改为消费 `background_tasks_changed`，整体替换任务集合；移除 `background_active`。
- 按新形状重写 `test_claude_background_tasks_emit_only_the_idle_edge`
  （`tests/contrib/test_claude.py:625`），覆盖 `local_bash` 出现在集合中、以及 `tasks: []` 清空集合。
- 运行 `uv run pytest tests/contrib/test_claude.py`。

### Task 5：删除 RuntimeBackgroundIdle

Files: `src/bazaar_compute_node/core/runtime.py`、
`src/bazaar_compute_node/core/orchestration/orchestrator.py`、
`src/bazaar_compute_node/contrib/codex/runtime.py`、
`src/bazaar_compute_node/contrib/claude/runtime.py`、相关测试。

- 删除事件类型、handler、联合类型成员与两处 dispatch 分支。
- 重写依赖该事件的 `test_real_codex_background_idle_event_restarts_runtime_timer`
  （`tests/contrib/test_codex.py:1237`）与 `test_real_claude_background_idle_event_restarts_runtime_timer`
  （`tests/e2e/test_claude_runtime.py:579`）：改为验证定时器到期时查询到后台任务并重新计时。
- 运行 `uv run scripts/pyright_lsp_check.py --outputjson .`。

### Task 6：真实运行验证

- 按 `tests/e2e` 既有方式，用 TestChannel 起一个 `idle_timeout` 较短的 Claude 会话，起一个后台命令，
  确认到期时会话保留、任务结束后的下一次到期完成回收。
- Codex 侧同样跑一次。
- 运行 `uv run scripts/pyright_lsp_check.py --outputjson .` 与 `ruff format --check`。
