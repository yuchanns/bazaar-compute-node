# 个体 Agent 执行模式 `agent.mode = dangerous_individual`

## 1. 现状

`SessionOrchestrator` 已经是 Agent 级实例：构造函数要求 `agent_id`，一个 Agent 一个实例
（`core/orchestration/session.py:139`）。当前的「一个会话一个 runtime」只体现在实例内部按
`bcn_session.id` 分片的几张表：`_runtime_queues`、`_runtime_workers`、`_runtime_sessions`、
`_runtime_timers`、`_session_runtime_states`、`_session_upgrades`，以及 `Runtime._holders`
（`core/runtime.py:211`）和 `app/agent.py` 的 `_session_capabilities`。这些结构的 key 都是不透明
字符串，改变 key 的取值不需要改变类型。

ingress 队列的 key 是 `(channel, provider_thread_id)`（`core/orchestration/session.py:585`），表达的是
同一条对话线内的顺序与去重，与 runtime 归属无关，两种模式下都保持不变。

一个 Agent 现在可以越过自己所在的对话线做三件事，它们是同一批改动的产物：`bcc inbox list` 列出该
Agent 的全部 target；`bcc message read --target` 读任意 target 的历史，`read_message_history` 为此把
caller session 与 source session 分开；`bcc message send --target` 直接发往另一条对话线，
`materialize_outbound_if_fresh` 记 `cross_session` metadata，`finalize_outbound_delivery`
（`core/storage.py:400、438-491`）在目标线写一条 HANDOFF 系统消息，命令层再经 `publish_wake` 唤醒
目标那条线的 runtime。

这三件事之所以存在，是因为在今天的模型里一条对话线就是一个独立的 Agent 实例：要让同一个 Agent 的
两条线协作，只能靠跨线投递加唤醒。个体模式取消了这个前提——一个 Agent 的全部对话线由同一个 runtime
处理，协作不再需要投递。

发送侧的两道校验：`storage.py:379` 要求目标那条线自己有过入站消息；fresh-check 的锚点是发出命令的
runtime 所属会话，`check_outbound_freshness` 取它的最新入站 seq 与快照比较（`core/storage.py:249`、
`core/orchestration/command.py:276`），`_drafts` 与 `_freshness_snapshots` 也按同一 key 保存。

`BCN_SESSION_ID`、`BCN_RUNTIME_SESSION_ID`、`BCN_COMMAND_CAPABILITY` 在 runtime 进程 spawn 时写入
环境（`app/agent.py:436`），`bcc` 侧强制要求 `BCN_SESSION_ID` 存在（`cmd/bcc/_client.py:48`）。
`BCN_AGENT_ID` 已经在同一处注入。

`check_outbound_freshness` 与 `materialize_outbound_if_fresh` 都在 `_TRANSACTIONAL_WRITE_OPERATIONS`
（`contrib/sqlite/storage.py:45`），经 `BEGIN IMMEDIATE`（`contrib/sqlite/database.py:151`）在单一串行
writer 上执行，因此「校验最新 seq 再写 outbound」的线性化由事务提供。现有
`SessionLockRegistry.for_session` 只保护进程内的 `_drafts`。本计划不引入任何新的同步原语。

## 2. 可达范围

全计划只有一条可达规则：**一个 actor 只能读写自己作用域内的对话线**。

- `session` 模式下 actor 就是一条对话线，作用域里只有它自己。inbox 目录、跨线读、跨线发因此都不
  成立，Agent 回到只面对当前这一条线。
- `dangerous_individual` 模式下 actor 是 Agent，作用域是它的全部对话线。列出、读、发都在作用域内
  进行，不存在「跨」这件事。

handoff 随之整体删除，不是留着不用：它表达的是「投递给另一个 actor」，而在这条规则下目标永远与
发送方同属一个 actor，那条路径没有任何输入能走到。删除范围为 `finalize_outbound_delivery` 的
handoff 分支、`materialize_outbound_if_fresh` 的 `cross_session` metadata、
`render_handoff_message_body`（`core/command.py:152`）、`SystemMessageKind.HANDOFF`，以及命令层的
`publish_wake` 参数与它在 `SessionCommandService` 内的唯一调用点（`command.py:139、148、525`）与接线
（`session.py:217`）。`SessionOrchestrator.publish_inbox_wake` 本身保留：Reminder 到期同样经它唤醒
（`app/application.py:90`、`reminder.py:397`）。`SystemMessageKind` 之后只剩 `REMINDER`。已发布版本
写下的 handoff 系统消息由新 migration 删除：那条能力连同它的记录一起消失，留着只会让读取这条对话线
的人撞上一个已经不存在的取值。删除会让某些对话线的最新入站 seq 变小，同一个 migration 因此把越界的
`delivered_through_seq` 收回到剩余的最新入站消息上；否则下一次 check 会被「游标不能后退」挡住。

`bcc inbox list` 这条命令消失，不保留别名。它的实现改作 `bcc inbox check` 的基础，只在
`dangerous_individual` 模式出现，`session` 模式的命令表里没有 inbox 这一族。两者语义不同，因此不是
单纯改名：`list` 列出该 Agent 的全部 target 并分页，包含 pending 为零的历史 target；`check` 只列出
**当前有待处理消息**的 target。存储侧沿用 `storage.read_inbox_catalog` / `list_inbox_targets` /
`InboxTargetPage` / `InboxTargetSummary` 与 SQLite 的 `_INBOX_TARGET_CATALOG_CTE`，在其上按
`pending_count > 0` 过滤；`--limit` / `--offset` 随 `list` 一起去掉，`check` 一次给出全部待处理
target。已发布的 v14 索引属于不可变历史，保持原样。

`read_message_history` 的第一个参数由「调用方对话线」变为 actor：`session` 模式下它就是那条对话线，
取值与今天一致；`dangerous_individual` 模式下它是 Agent。解析出 target 所属对话线后先做作用域校验，
不在作用域内按既有的解析失败路径拒绝。

turn 输入的未读通知按同一条规则取值：未读总数与通知携带的消息**都**覆盖该 actor 作用域内的全部
对话线。`session` 模式下作用域只有一条线，两者取值都与今天一致；`dangerous_individual` 模式下它是
该 Agent 的全部对话线，与一次 `bcc message check` 排空的范围相同。总数与携带的消息仍是两个独立的
量：总数来自计数查询，携带的消息是作用域内最新一段未读，按 target 逐行渲染，行内条数不承诺等于该
对话线的全部未读。因此一个 turn 唤起时，作用域内有几条对话线待处理就渲染几行，Agent 自己决定看
哪些；被 batch 收进同一个 turn 的对话线与随后 steer 进来的对话线，在通知里因此得到同样的对待。

跨对话线取这段窗口需要一次新的存储查询 `list_unread_messages`：在该 Agent 的全部对话线上按各自
游标筛出未读入站消息，按 seq 取最新的一段。逐条对话线各查一次在作用域大时是几百次查询，不采用。
`seq` 在 `messages` 上全局单调，因此跨对话线比较有意义，通知的行序沿用「最新 seq 在前」不变。

`_steer_active_turn` 仍然只描述这次到达的那条消息——它是一次增量，不是整段窗口重投影——顶部总数
同样按作用域取值。它的前置判断由「这条对话线有没有未读」改为「这条到达的消息是否仍未读」：作用域
里别处的未读不该让一条已经被排空的到达再投一次，游标单调，比较消息 seq 即可。作用域由 actor 表达，
不引入模式判断。

计数与窗口由**同一个存储操作**一次读出（`read_unread_summary`，在快照读事务里），两个量因此永远
同源：分开读会在两次读之间被另一次排空插进来，窗口非空而计数归零，破坏「总数 ≥ 携带条数」这条
不变式；按对话线目录分页求和则会在对话线超过一页时把计数做小。`threads_in_reach` 与
`bcc inbox check` 的目录读取同样要翻完全部页，否则旧对话线会静默掉出可达范围。

developer instructions 删除两处：命令族列表里的「**Inbox discovery** — `bcc inbox list`」（其余条目
编号复原），以及「用 `bcc inbox list` 找旧对话」那一段。两处描述的都是随命令一起消失的能力。handoff
相关的提示词此前已随命令撤除，无残留。

提示词只描述该模式下真实存在的能力：不存在的命令一个字都不提，也不写「本模式不支持」之类的反向
提示——那等于告诉它有这个东西。按这条规则，全篇只有 `Runtime Notifications` 一节中「选择查看待处理
内容时调用什么」这一句随模式取值：`session` 模式保持现状，只提 `bcc message check`；
`dangerous_individual` 模式改为 `call \`bcc inbox check\`; use \`bcc message check\` /
\`bcc message read\` when you choose to inspect message content.`。命令族清单两种模式一致，不列 inbox
一族。取值用模板已有的条件渲染表达（`developer_instructions.md` 第 1 行已经在用 `{% if %}`），
`DeveloperInstructionContext` 增加 mode 字段，由 `RuntimeCommandContext` 带到两个 adapter。

`Threads` 一节的「回复 thread 前先读父级上下文，附带的父级引用可能被截断」一并删除：三个 Channel
都会把被引用的消息作为一条独立入站消息落库（`contrib/wecom/channel.py:1204`、
`contrib/telegram/channel.py:803`、`contrib/lark/channel.py:320`），读该对话线的历史就能看到完整原文，
这条提醒描述的截断情形已不存在。

## 3. 模式定义与配置

`[[agent]]` 表新增 `mode`，取值为 `session` 与 `dangerous_individual`，缺省 `session`。取值主体命名的
规则是「谁拥有一个 runtime」：`session` 为每条对话线一个，个体模式为每个 Agent 一个。`dangerous_`
前缀承担第 8 节那条含义：该模式没有人工许可闸门。裸的 `individual` 保留给将来能够保住人工审批的
变体，现在不使用。

```toml
[[agent]]
id = "..."
name = "..."
mode = "dangerous_individual"
```

解析位置为 `_parse_v3_agent`（`app/config.py:290`），存入 `AgentConfiguration.mode`，类型为
`enum.StrEnum`。模式只在进程启动时读取，不支持运行期热切换。

## 4. actor 归属

`agent.mode` 只被一个对象读到。core 中新增 `Actors`，构造时注入 `agent_id` 与 `mode`，整个节点一个
实例，沿现有传 `agent_id` 的路径注入 orchestrator 与命令层，提供同一条规则的正反两面：

- `for_thread(thread_id) -> Actor`：由一条对话线得出答复它的 actor。这是全仓库唯一读 `mode` 的地方。
- `resolve(actor_id) -> Actor`：把跨进程边界回来的字符串还原成 actor，只在 `bcc` 命令入口用一次。

`Actor` 是 `Agent | Thread` 两个 frozen dataclass 变体，各自带着自己的 id。身份因此活在类型里而不是
运行期判断里：拿到 actor 就知道它代表谁，要 id 就 `match actor: case Agent(id) | Thread(id)`，要分情况
就分开 match 并由 pyright 查穷尽，不可能再把 Agent id 当对话线 id 去查库。两个变体可直接作为 dict key，
下面那几张分片表因此从 `dict[str, ...]` 变成 `dict[Actor, ...]`。与 `core/agent.py` 的 `Agent`、第 9 节
之后的 `Thread` 实体同名，在 import 处起别名解决，不为此加前缀。

`_ingress_loop` 构造 `_DurableSessionContext` 时经 `for_thread` 取得 actor 存入 context，此后一路带着
它走，不再从 id 反推。

现有按 `session_id` 分片的结构改为按 actor 分片：

- `_runtime_queues`、`_runtime_workers`：一个 actor 一条 mailbox 与一个消费者；
- `_runtime_sessions`、`_session_runtime_states`、`_runtime_timers`、`_expired_runtime_ids`；
- `_session_upgrades`：升级提示按 actor 只提示一次；
- `Runtime.bind` / `Runtime.holder` / `Runtime.release`：runtime 绑定与失败转移
  （`_hand_turn_to_another_runtime`）按 actor 进行；
- `app/agent.py` 的 `_session_capabilities`，以及 `orchestrator.runtime_session(...)` 的查询参数。

`RuntimeSession.bcn_session_id` 改名为 `actor_id`。这个字段现有的读取点几乎全部是在拿分片 key
（`_discard_runtime_session`、`_cancel_runtime_timer`、`_start_runtime_timer`、`_handle_runtime_expiry`、
`_receive_runtime_event_loop` 等），改名之后这些位置不需要任何分支；adapter 侧把它当 correlation 标签
回传（`contrib/claude/runtime.py:248`、`contrib/codex/runtime.py:246`），改名后含义一致。
`_RuntimeExpiry.bcn_session_id` 同样改名。`RuntimeSession.provider_thread_id` 不动，它指的是 provider
自己的会话，本来就是一个 runtime 一条。`RuntimeSession.channel_session_id` 在 core 与两个 adapter 中
都没有读取点，随之删除；需要来源对话线的地方一律从事件自带的 `_DurableSessionContext` 取。

`SessionLockRegistry.for_session` 继续以真实对话线 id 为 key，不随模式改变：它保护的是游标与草稿
这类按对话线存在的状态，与 runtime 归属无关。

个体模式下不同对话线的通知进入同一条 mailbox，严格串行消费，一次只有一个 active turn。runtime idle
timeout 与 context expire 按 actor 计时，Agent 的 runtime 在全部对话线都空闲后才进入过期流程。

Reminder 不变：`owner_session_id` 保留，到期后在该对话线写入 system message，再经既有唤醒路径进入
actor 队列，消息自身携带原对话线的 target。

## 5. 环境与命令作用域

`BCN_SESSION_ID` 由 `BCN_ACTOR_ID` 取代，注入的就是第 4 节那个 `actor_id`，两种模式注入同一组变量，
这里没有模式分支：`BCN_ACTOR_ID`、`BCN_AGENT_ID`、`BCN_RUNTIME_SESSION_ID`、
`BCN_COMMAND_CAPABILITY`。

`cmd/bcc/_client.py` 只读 `BCN_ACTOR_ID`，缺失时报既有的 `SESSION_REQUIRED` 错误，客户端不判断它
背后是对话线还是 Agent。服务端把它交给 `Actors.resolve` 还原成 actor，按变体决定命令的解析范围；
这是全仓库唯一一处由 id 反推身份的地方。capability 校验路径不变，capability 绑定本来就按 actor 保存。

命令面按模式收敛，wrapper 安装时已知该 Agent 的模式（`app/wrapper.py`），把模式一并写入 wrapper
环境，`bcc` 的命令表与 `--help` 据此生成：

- `session` 模式不提供 inbox 一族；`dangerous_individual` 模式提供 `bcc inbox check`，列出当前有待
  处理消息的 target，不消费、不推进任何游标，没有 `--limit` / `--offset`；
- `bcc message check` 在 `dangerous_individual` 模式下聚合本 Agent 全部对话线的未读，推进各自的
  `ConsumerCursor`，并按每条线自身的 snapshot_seq 更新 `_freshness_snapshots`；整轮排空是**一个**存储
  操作，因此在 SQLite 侧是一个事务：要么每条线的游标都前进，要么一条都不动，中途失败或超时不会留下
  「已标记读过却没交到模型手上」的消息。core 不持有事务，只调用这一个操作，返回之前也不再去拿任何
  对话线的锁——排空已经原子，事后记录 snapshot 拿锁只会让一把被别处占住的锁把整轮结果堵死，而
  snapshot 偏旧只使发送闸门更严；游标保持按对话线，
  不退化为 Agent 全局游标；输出按到达时间、对话线 id、`seq` 稳定排序（入站消息的到达时间是
  `received_at_ms`，`created_at_ms` 只有出站消息才有），每条消息的 envelope 已含 target，无需新增
  字段；
- `bcc message read`、`bcc message send`、`bcc thread unfollow` 的参数与语义两种模式一致，可达范围由
  第 2 节那条规则决定。

help 文案逐字定死，实现时不得改写：

- 新增 `bcc inbox check`，只在 `dangerous_individual` 模式出现：
  `Show pending inbox targets without draining or reading message content.`
- `bcc message check` 的 `Drain the agent inbox (non-blocking). Acks delivered seqs before returning.`
  不变。
- `bcc message send` 的 `short_help` 由 `Send a reply after the session fresh-check gate.` 改为
  `Send a message to a channel, DM, or thread`。
- `bcc inbox` group 的 `Inbox discovery operations` 改为 `Inbox target summary operations`；该 group
  只在 `dangerous_individual` 模式出现。
- `bcc thread unfollow` 的 `short_help` 由 `Stop following a group/thread target.` 改为
  `Stop following a thread you no longer need ordinary delivery for`。
- 其余命令的 help 一字不改。

`bcc reminder` 的 anchor 解析改为按 message id 在 actor 作用域内定位其所属对话线，不再要求调用方
提供对话线标识；`session` 模式下解析结果与现有行为一致。

## 6. 发送、freshness 与草稿

fresh-check 的锚点与草稿归属都是**发送目标那条对话线**：往谁发信就看谁有没有你没读过的新消息，
`--send-draft --target X` 取的就是 X 自己那份草稿。`session` 模式下发送目标只能是当前这条线，取值与
今天完全相同，行为不变；`dangerous_individual` 模式下 `actor_id` 指代 Agent，本来也没有别的取值可选。
两种模式共用一条取值规则，代码里不出现模式判断。

`_freshness_snapshots` 继续按被观察的对话线记录，`message check` 记录被 drain 的每一条，
`message read --target` 记录被读的那一条。`session_id` 继续用于 command capability、发信方审计与
outbound ownership。target-not-replyable 仍先于 fresh-check 判定。不引入新的锁。

## 7. 在场标记与错误回执

Channel 侧的在场标记随 turn 已接收的消息走。`anchor_turn(session_id, anchor)` 目前每个 turn 只登记
一次，登记的是开启该 turn 的那条消息（`core/orchestration/turn.py:466`），标记随后由
`accept_turn_event(..., session_id=...)` 按对话线开合（Lark 的 `_stream_routes` 与 `_typing`，
`contrib/lark/channel.py:842`）。个体模式下一个 turn 可能接收来自多条对话线的消息，标记不能只停在
最先说话的那条线上。

每个 turn 持有一个它接收过消息的对话线集合：接收一条消息就把该线加进去，turn 到终态时按这个集合
逐条撤回标记。加入用集合本身的幂等性——同一条线的第二条消息被 steer 进同一个 turn 时自然不会重复。
集合的增删都在事件循环内一步完成、中间不跨 `await`，因此不需要任何锁。标记只对实现了该能力的
Channel 生效，当前为 Lark；集合中的每条对话线各自维护一个标记与一个 rotation task。

turn 失败时的错误回执按同一个集合发，且并发发出：现在 `_runtime_loop` 只把 `batch[0].message` 交给
`RuntimeErrorReporter.report`，个体模式下一个 batch 可以横跨多条线，只回第一条等于让其余几条的人
干等。改为对该 turn 接收过消息的每一条对话线各发一次，每条线一次、不按消息条数重复。没有接收过这个
turn 消息的线不发，错误回执不广播。各条之间没有依赖，而一次发送最长可以占满
`provider_call_seconds`，串行会让后面的对话线白等一个与它无关的超时，因此用 `asyncio.gather` 并发，
`return_exceptions=True`，每条自己记录失败，取消仍然向外抛。

正文输出仍归属开启该 turn 的对话线。

## 8. 审批

`session` 模式的审批不变：`ApprovalBinding` 由 (message, context, turn) 构造，审批卡回到发起该 turn
的对话线。

`dangerous_individual` 模式不做人工审批，运行期的许可请求一律自动放行。理由是这个模式下两件事都不
成立：一是投递地址无法确定——一个 turn 可以接收多条线的消息、也可以回复到任意 target，「发起该 turn
的线」并不等于 Agent 正在服务的对象，把卡片发过去就是向不相干的人征求许可；二是审批在 turn 内同步
等待，而个体模式只有一条 mailbox、一次一个 active turn，一张未决的卡片会让该 Agent 的全部对话线
一起停住。

判断与 audit 都放在 `_request_approval`（`core/orchestration/turn.py:363`）。它已经是全部许可
请求的唯一入口：两个 adapter 各自构造 `ApprovalRequest` 之后都调用同一个 `approval_handler`
（`contrib/claude/runtime.py:252`、`contrib/codex/events.py:637`），`approval.requested` 与
`approval.decided` 也都在这里落账，「无人可批则判 rejected」同样在此。模式分支与自动放行加在这一处，
adapter 侧不感知模式。

判断看的是 actor 而不是模式：actor 是一条对话线就照旧征求许可，actor 是 Agent 本身就自动放行。
两者表达的是同一件事的两面——「有没有唯一一条可以问的对话线」——而以后支持审批的个体模式会与
`dangerous_individual` 合并成一个模式、由 `sandbox_mode` 决定问不问，那时按模式写的分支要重写，
按 actor 写的不用。

该分支下 `_request_approval` 直接返回 approved，不构造 `ApprovalBinding`、不向任何对话线投递卡片。
每次自动放行写一条 `approval.decided` audit，带上 decision 与说明原因的 `reason`；不写
`approval.requested`，因为没有任何人被问过。

含义由取值本身承担：`dangerous_individual` 意味着该 Agent 没有人工许可闸门，runtime 沙箱中所有
「询问后放行」的策略对它等同于直接放行，只有沙箱本身无条件拒绝的动作仍然被拒。需要人工闸门的
Agent 留在 `session` 模式。

## 9. 把 session 改名为 thread

放在功能改动之后做：`actor_id` 落地后，指代对话线的 `bcn_session_id` 出现点已明显减少，改名的面最小。

`BcnSession` 改名为 `Thread`，`bcn_sessions` 表改名为 `threads`，各表中指代对话线的 `session_id`
列改名为 `thread_id`（`messages`、`consumer_cursors`，以及 `reminders.owner_session_id` →
`owner_thread_id`）；只改指代对话线的列，`runtime_attempts.session_id` 与 `RuntimeAttempt`、
`RuntimeTurn` 上同名的字段指代 runtime session，保持不动。受影响的索引一并改名重建。表与列的改动写
新 migration（v25），已发布的 migration 一个字节都不动。`ConsumerCursor.session_id` 与
`CorrelationContext.bcn_session_id` 改为 `thread_id`，`SessionContext` 改为 `TurnContext`，
`ISessionConcurrency`/`SessionLockRegistry` 改为 `IThreadConcurrency`/`ThreadLockRegistry`，锁入口
成为 `for_thread`。`SessionOrchestrator` 改名为 `AgentOrchestrator`（它按 `agent_id` 构造，是
Agent 级的编排器），所在模块随之由 `orchestration/session.py` 改为 `orchestration/orchestrator.py`；
`SessionCommandService`、`SessionTurnCoordinator`、`SessionAuditRecorder` 去掉不再成立的 `Session`
前缀。`SessionNotFoundError` 改为 `ThreadNotFoundError`。

不改的两处，理由都是「那里的 session 不是对话线」：`IChannel` 的 `accept_turn_event(session_id=)`、
`anchor_turn(session_id=)` 与 `ChannelSendRequest.session_id` 位于 Channel 边界，`Channel` 会把本地
对话线 id 换成 provider 的 session id 再交给 contrib 实现，两端同名参数指的是不同的东西，统一改名
反而会指错；developer instructions 里两句「bcn session」描述的是同一件事，但改提示词会改变模型行为，
不属于机械改名，留到需要时单独处理。

`channel_sessions` 本轮不改名，两者的关系在文档中写明：`thread` 是对话线的本地身份，
`channel_session` 是这条对话线在 provider 上的地址，一一对应。改名不改变任何行为，测试只做同步
更名，不新增语义断言。

## 10. 串行 Tasks

### Task 1：`agent.mode` 与 `Actors`

- 在 `AgentConfiguration` 与 `_parse_v3_agent` 加入 `mode`，缺省 `session`，非法值报配置错误；
- 按第 4 节新增 `Actors` 与 `Agent | Thread` 两个变体，提供 `for_thread` 与 `resolve`，`mode` 只在此处
  被读；注入 orchestrator 与命令层，`session` 模式下 `for_thread` 返回携带该对话线 id 的 `Thread`；
- 本 task 不改变任何行为；
- 补充测试覆盖：两种模式下 `for_thread` 与 `resolve` 的取值、个体模式下未知 actor 报错、非法 mode 的
  配置错误；
- 运行 focused tests、Ruff、format、`uv run scripts/pyright_lsp_check.py --outputjson .`，停下等待 review。

### Task 2：可达范围收敛与 handoff 删除

- 按第 2 节用调用方的 actor 约束 `message send` 与 `message read` 的可达范围，越界按既有解析失败
  路径拒绝；
- 删除 handoff 全套：`finalize_outbound_delivery` 的 handoff 分支、`cross_session` metadata、
  `render_handoff_message_body`、`SystemMessageKind.HANDOFF`、命令层 `publish_wake` 接线与
  `publish_inbox_wake` 中为它存在的部分；`FinalizeOutboundResult` 不再带 handoff message；
- 删除 `bcc inbox list` 命令与 `CommandService.list_inbox`，不留别名；存储侧目录实现保留，第 4 节的
  `bcc inbox check` 在其上按 `pending_count > 0` 过滤；
- developer instructions 撤销第 2 节点名的那两处，原样撤回；`Runtime Notifications` 那一句按模式取值；
- 补充测试覆盖：`session` 模式下发往另一条线被拒、读另一条线被拒、发送不再产生系统消息、同一条线
  的收发与 hold 行为不变；删除只为 handoff 存在的用例；
- 运行 focused tests 与全部静态门禁，停下等待 review。

### Task 3：actor 分片

- 按第 4 节把 orchestrator 内部的对话线分片改为 actor 分片，`RuntimeSession.bcn_session_id` 与
  `_RuntimeExpiry.bcn_session_id` 改名为 `actor_id`、删除 `RuntimeSession.channel_session_id`；
- `Runtime` 绑定与失败转移、idle timeout、context expire、升级提示改按 actor；
- 对话线锁保持按对话线 id；ingress 队列保持按 `(channel, provider_thread_id)`；
- 补充测试覆盖：两种模式下 runtime 归属与队列数量、个体模式下多对话线串行消费与单 active turn、
  Reminder 到期后回到 owner 对话线的 target；
- 运行 focused tests 与全部静态门禁，停下等待 review。

### Task 4：环境、命令作用域与个体模式命令面

- 以 `BCN_ACTOR_ID` 取代 `BCN_SESSION_ID`，两种模式注入同一组变量；`cmd/bcc/_client.py` 只读
  `BCN_ACTOR_ID`；服务端经 `Actors.resolve` 还原 actor 并据此决定作用域，capability 校验路径不变；
- wrapper 写入模式，`bcc` 命令表与 `--help` 按模式生成，help 文案照第 5 节逐字落地，包括 `bcc inbox`
  group 与 `bcc thread unfollow` 两处与模式无关的文案更正；
- `dangerous_individual` 模式提供 `bcc inbox check`，`bcc message check` 聚合全部对话线的未读并逐个
  推进各自游标、记录各自快照；
- 按第 6 节把 fresh-check 锚点与 `_drafts` 取自发送目标那条线；
- `bcc reminder` 的 anchor 改为按 message id 在 actor 作用域内定位；
- 补充测试覆盖：两种模式下同一组注入变量、`actor_id` 为对话线与为 Agent 时各自的作用域解析、缺少
  `BCN_ACTOR_ID` 时的既有错误、聚合 drain 后各对话线游标各自推进、聚合后对某个 target 的发送不被
  其他 target 的未读拦下、reminder anchor 解析；
- 运行 focused tests 与全部静态门禁，停下等待 review。

### Task 5：在场标记与错误回执

- 按第 7 节让每个 turn 持有它接收过消息的对话线集合，标记按集合挂起、终态按集合撤回；
- 错误回执改为按同一个集合各发一次，并发发出；
- 按第 2 节把未读汇总的计数与窗口都改为覆盖 actor 作用域内的全部对话线，新增存储查询
  `list_unread_messages`，`_steer_active_turn` 的前置判断改为「这条到达是否仍未读」；
- 补充测试覆盖：steer 后两条对话线各自出现标记且终态全部撤回、同一条线被 steer 两次时仍只有一个
  标记且能撤回、turn 失败时集合内每条线各收到一次回执且未参与的线不收到、个体模式下未参与唤起的
  对话线也出现在通知里、一个 batch 横跨两条对话线时两行都在、`list_unread_messages` 跨对话线取值
  且不越过 Agent 边界、不含已排空与不通知 runtime 的消息；
- 运行 focused tests 与全部静态门禁，停下等待 review。

### Task 6：审批

- 按第 8 节在 `_request_approval` 加入按 actor 的分支：actor 是 Agent 本身时直接返回 approved 并写入
  带 reason 的 `approval.decided` audit，不构造绑定也不投递卡片；actor 是一条对话线时审批路径原样保留；
- 补充测试覆盖：两种 actor 各自的审批行为与 audit；
- 运行 focused tests 与全部静态门禁，停下等待 review。

### Task 7：把 session 改名为 thread

- 按第 9 节完成改名，表与列的改动写新 migration（v25），已发布的 migration 不动；
- 改名不改变任何行为，测试只做同步更名；
- 运行 focused tests、全量 non-E2E 与全部静态门禁，停下等待 review。

### Task 8：真实 E2E 与 0.2 发版

- 使用 TestChannel 作为控制面，注入分属两条对话线的 inbound，观察同一 runtime 进程串行处理并各自
  回到来源 target；覆盖 turn 运行期间另一条线的消息以 inbox notice steer 进当前 turn、许可请求自动
  放行且留下 audit、Reminder 到期回到 owner 对话线、runtime 重启后 mailbox 恢复；
- 使用测试专用配置、临时数据库与隔离进程，不触碰正式 `bcn.service`、`~/.bcn` 数据库与 socket；
- 更新 CHANGELOG，说明 `session` 模式移除的跨对话线能力与新增的 `agent.mode`，发布 0.2；
- 运行 focused tests、全量 non-E2E 与全部静态门禁，停下等待最终 review。

## 11. 完成标准

1. `agent.mode` 缺省为 `session`；该模式下 Agent 只能读写自己那一条对话线，没有 inbox 一族命令，
   发送不产生任何系统消息；
2. `dangerous_individual` 模式下一个 Agent 只有一个 runtime session 与一条 mailbox，一次只有一个
   active turn，全部对话线在同一个 actor 作用域内；
3. 两种模式共用同一套队列与 runtime 生命周期代码；`agent.mode` 的引用点只有 `Actors` 一处，由 id
   反推身份的地方只有 `Actors.resolve` 一处，其余代码一路带着类型化的 actor；行为分支只剩 `bcc`
   命令面与审批两处；
4. 模式切换不迁移任何存储行，对话线与 `channel_sessions` 仍按 channel session 建立；
5. fresh-check 与草稿都锚在发送目标那条对话线；
6. `dangerous_individual` 模式下未读游标仍按对话线保存，只是在 `message check` 时跨线聚合推进；
7. 两种模式注入同一组环境变量，`bcc` 只读 `BCN_ACTOR_ID`，作用域由 `Actors.resolve` 还原的 actor
   得出，capability 校验不变；
8. `session` 模式的审批路径不变；`dangerous_individual` 模式不投递审批卡、一律自动放行并留下 audit；
   Reminder 回到 owner 对话线的 target；
9. turn 接收的每一条消息所在对话线都出现在场标记，终态时按集合全部撤回、不遗留；turn 失败时这些线
   各收到一次错误回执，未参与的线不收到；
10. 指代对话线的类型、字段与列统一为 `thread`，`bcn_` 前缀随之消失，改名不改变任何行为；
11. 全部仓库门禁通过，每个 Task 分别 review 后才进入下一 Task。
