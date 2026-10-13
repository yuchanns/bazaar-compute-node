# 节点与 bcs 连接的健壮性

## 背景

对照 raft 的实现，节点与 bcs 之间、节点自身的进程管理有五处薄弱：

- bcs 收 `reportEvents` 时把整批事件当一个请求校验，任何一条不合法（例如 `correlation.node_id` 不符合格式），整批
  以 400 拒收。同批里其它事件，包括下行请求的 `control.result` 答复，一起丢失；页面等满 `ANSWER_SECONDS` 才报超时。
- 节点长轮询 `getUpdates` 失败后固定等 `startup_seconds`（60 秒）再试。bcs 重启后，节点最长一分钟才重新连上。
- 本地命令端点 `~/.bcn/bcn.sock` 在进程被杀或崩溃后留在原处；下次启动 `LocalCommandServer` 见文件存在即抛
  `FileExistsError`，节点起不来，system service 反复重启失败。
- 关停只有一个上限：`asyncio.timeout(shutdown_seconds)` 包住 `node.stop()`，到点即退出。未停下的 runtime 子进程
  （`claude`、`codex app-server`）在前台运行、launchd、Windows 计划任务下成为孤儿。
- turn 进行中每到一条消息就 steer 一次，一阵连发的消息变成一串 steer。

## 目标

- 一条坏事件只丢它自己，同批其余事件照常入库、照常交给消费者。
- 轮询失败按指数退避重试：1 秒起，逐次翻倍，封顶 `startup_seconds`，成功一次即回到 1 秒。
- 残留的 socket 文件不再挡住启动；另一个活着的节点占着端点时照旧拒绝启动。
- 关停上限到点后，节点结束自己起的全部 runtime 子进程，确认结束后再退出。
- turn 进行中的消息静默满 3 秒才 steer，期间每来一条重新计时；一次 steer 带上期间全部消息。

## 设计

### 1. 事件逐条校验

- `ReportEventsRequest.events` 改为 `list[dict[str, Any]]`，外层只校验 `run_id` 与 `events` 是列表；请求本身不合法
  （JSON 坏、缺 `run_id`）仍回 400。
- `report_events` 逐条 `Event.model_validate`。通过的照旧 `record_events`、`consumers.consume`；不通过的丢弃，
  以 `logger.warning` 记下 `seq`（取得到时）与原因。
- 回复增加 `rejected`：被丢弃事件的条数（哪几条见 bcs 日志；列表会撑破节点读回复的上限）。`accepted` 语义不变。
- 节点 `ServerAudit` 读回复里的 `rejected`，计入健康里新增的 `rejected` 计数，不重发。

### 2. 轮询退避

- `ServerControl` 的 `_backoff_ms` 从固定值改为当前退避：初值 1 000，`_updates()` 返回 `None` 后等当前值再试并翻倍，
  封顶 `startup_seconds * 1000`；拿到一次正常回复（含空列表）即复位为 1 000。
- 等待仍走 `TimerWheel`。

### 3. 残留 socket

- `LocalCommandServer.start` 在 POSIX 上发现端点路径已存在时，先试连一次：
  - 连得上：另一个节点活着，抛 `FileExistsError`（语义不变）。
  - `ConnectionRefusedError`、`FileNotFoundError`，或路径存在但不是 socket：残留，删掉后照常绑定。
- 做法同 raft `computer/src/internal/ipc-server.ts`。Windows 命名管道随进程消失，没有残留，不改。

### 4. 关停兜底

- runtime 子进程各自成组：`contrib/claude/process.py`、`contrib/codex/process.py` 的 `create_subprocess_exec` 在 POSIX
  上加 `start_new_session=True`，Windows 上加 `creationflags=CREATE_NEW_PROCESS_GROUP`。
- `core/utils` 下新增进程登记处：两个 `ProcessSupervisor` 起进程时登记 pid；进程结束时先对它的进程组发 `SIGKILL`
  （它留下的后台进程随它结束），再注销；只存 pid。
- `_run_node` 在有上限的 `node.stop()` 之后（到点与否）收尾：对登记处里仍在的每个 pid，POSIX 向其进程组发
  `SIGTERM`，每 50 毫秒查一次，至多 2 秒，之后发 `SIGKILL`（等待被取消也照发）；Windows 用 `taskkill /T /F /PID`。
  收尾只受这 2 秒约束，结束后退出。
- `shutdown_seconds` 不变；systemd 单元的 `TimeoutStopSec=15` 仍是最外层兜底。
- 前台运行时 Ctrl-C 不再直接传给 runtime 子进程，由节点统一停。

### 5. steer 防抖

- `_runtime_loop` 在 turn 进行中本就同时等 turn 结束与队列新项；防抖计时器作为第三个等待对象加入：每个 actor 的
  loop 自己持有，建在 `TimerWheel` 上，时长 `steer_quiet_ms`（`AgentOrchestrator` 的参数，默认
  `STEER_QUIET_MS = 3_000`）。
- `_absorb_queue_item` 收到 `_RuntimeNotification`：照旧取消空闲计时器、放进 `pending`，不再立即 steer；计时器重新
  开始计时。`pending` 即 steer 队列。
- 计时器到点且 turn 仍在跑：把 `pending` 里的通知中会话还没收到的那些合成一条 `inbox_notice`，一次 steer 进当前
  turn。`TurnCoordinator.steer_turn` 改为接受一组（消息，上下文）并回答 runtime 是否接受，接受后逐个 `join_turn`。
- 接受的通知移出 `pending`，随本 turn 结算：completion 取本 turn 的结果，`task_done` 与本批一起；不再作为下一批空跑。
  未接受的留在 `pending`。
- turn 先结束：取消计时器，`pending` 照现有逻辑成为下一批。

## 任务

1. 事件逐条校验（bcs `report_events`、协议模型、节点 `ServerAudit` 健康计数）。
2. 轮询退避（`ServerControl`）。
3. 残留 socket（`LocalCommandServer`）。
4. 关停兜底（进程组、登记处、`_run_node` 收尾）。
5. steer 防抖（计时器、多消息 steer）。

每个任务一次提交。

## 验证

- 事件：一批里夹一条 `correlation.node_id="../.."` 的事件，其余事件入库、`control.result` 答复送达，回复的
  `rejected` 列出坏的那条。
- 退避：节点指向一个未启动的 bcs 地址，数出前几次重试的间隔依次翻倍；bcs 起来后下一次轮询成功，退避复位。
- socket：在端点路径放一个残留文件（绑定后关闭、不删），节点照常启动；另起一个节点指向同一路径，启动被拒。
- 关停：测试插件起一个真实子进程（`sleep`），把关停上限调小并让 `stop()` 卡住，断言退出后该 pid 不存在。
- steer：测试 runtime 记录 steer。1 秒内连发三条，只 steer 一次且三条都在；间隔超过 3 秒的两条 steer 两次；turn 在
  计时器到点前结束，三条成为下一 turn 的输入。
