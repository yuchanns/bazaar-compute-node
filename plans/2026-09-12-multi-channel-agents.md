# 多 Channel Agent

## 背景

config v3 已经把 runtime 数组化，`[[agent.runtime]]` 解析为 `AgentConfiguration.runtimes`
（`app/config.py:296`、`app/config.py:342`），channel 仍是单表，解析为单个 `ChannelConfiguration`
（`app/config.py:294`、`app/config.py:336`）。

运行期同样是一对一：`Agent` 构造一个 provider channel 并包进 `Channel`（`app/agent.py:114`、
`app/agent.py:125`），`AgentOrchestrator` 持有该实例（`core/orchestration/orchestrator.py:173`）。
入站只有一条流（`core/orchestration/orchestrator.py:927`），出站的 `OutboundDeliveryService`
在构造时绑定同一个实例（`core/orchestration/orchestrator.py:195`、`core/orchestration/delivery.py:30`）。

一个 agent 因此只能接入一个 channel。需要同时出现在多处时只能另配一个 agent，代价是工作区与记忆不共享。

## 目标

一个 agent 同时接入多个 channel 实例，包含同一 kind 的多个实例。入站多路合流为一条流，出站按会话所属实例路由，
target 命中多个会话时全部投递。

agent 侧的契约不变：`target` 与 `thread_id` 已经是 channel 无关的 uuid5，提示词中复用原 target 的约定不受影响。

## 设计

### 与多 runtime 的区别

runtime 是可互换的池。`Runtime` 提供 `select(exclude=)`、`bind`、`record_failure`
（`core/orchestration/orchestrator.py:337`、`core/orchestration/orchestrator.py:348`、
`core/orchestration/orchestrator.py:898`），会话上记录位置下标 `runtime_index`
（`core/orchestration/orchestrator.py:396`）。任一 runtime 都能承接同一会话，失败可以换一个继续。

channel 不可互换。会话属于哪个实例由入站决定，出站必须回到同一实例，不存在选择与失败转移。因此不复用池结构。

一条会话恰好属于一个实例。同一个群被同一 agent 的两个实例接入时，算作两条会话，各自归属其实例；该群的消息在两个实例
上分别入站，出站也各自从所属实例发出。telegram 与 lark 现有的 id 算法已经是这个结果，因为 `provider_thread_id`
中含 bot 身份。

位置下标尤其不能用于 channel：`[[agent.channel]]` 增删或调序会使已有会话指向另一个实例，且不会报错，
表现为消息投递到错误的 channel。

### 实例身份

出站需要知道一条会话属于哪个实例，否则 target 解析之后无法决定从哪个 bot 发出。该标识必须与配置顺序无关。

标识取 `(kind, ChannelIdentity.id)`，不新增配置项。三个 channel 的 `get_identity()` 均已返回 provider 的 bot 身份：
telegram 为 `str(bot_id)`（`contrib/telegram/channel.py:188`），lark 由 provider 身份换算
（`contrib/lark/channel.py:202`），wecom 为应用自身的 bot id（`contrib/wecom/channel.py:234`）。
该值由 provider 决定，配置增删或调序都不改变它。

`get_identity()` 在 startup 之后才有值，telegram 需要先取得自身账号信息。会话由入站产生，入站必然发生在 startup 之后，
因此取值时机满足。启动未完成即取不到身份的实例不进入可用状态。

会话的归属应当由会话行自身携带。telegram 与 lark 已经把 bot 身份写进 `provider_thread_id`
（`telegram:bot:{bot_id}:chat:{chat_id}:topic:{topic_id}`），发送时用 `parse_provider_thread_id`
（`contrib/telegram/identity.py:63`）解析回来。wecom 的 `provider_thread_id` 是裸的 `conversation`，
发送时直接当作地址使用（`contrib/wecom/channel.py:504`），因此 wecom 的会话行不携带所属 bot。

wecom 对齐另外两者：`provider_thread_id` 加入 bot 段，发送路径增加对应的解析。对齐之后所有新写入的会话都自描述。

`channel_sessions` 增加一列记录所属实例，写入时由 contrib 提供。`channel` 列保持存 kind 不变，
既有行的查找键 `(agent_id, channel, provider_thread_id)`（`contrib/sqlite/repository/sessions.py:40`）不受影响。

存量行的所属实例为空，分两步填上。telegram 与 lark 的 `provider_thread_id` 本身带 bot id
（`telegram:{bot_id}:{chat_id}:{topic_id}`、`lark:bot:{open_id}:chat:…`），由一条 SQL migration 一次性截出来写入。
wecom 的行只有 `conversation`，bot id 来自配置，构造时即可得（`contrib/wecom/channel.py:234`），
因此由 `Agent` 在 orchestrator 启动前回填（backfill）：某 kind 的空值行一律分配给配置中该 kind 的第一个实例，
同时把 `provider_thread_id` 改写为带 bot 段的新格式；行的 id 不变，后续入站按改写后的键找到原行继续使用。
回填以 `channel_identity IS NULL` 为谓词，跑过一次之后自然无事可做，不另记标记。
分配错误的情形可自愈：另一实例的下一条入站不匹配该行，按自身身份新建会话行，后续流量归属正确。

### id 命名空间

`Channel._local_id` 以 `bcn:{agent_id}:{kind}:{provider_local_id}` 构造命名空间
（`core/channel.py:295`），其中不含 channel 身份。现有实现不相撞依赖各 contrib 自带前缀：

- telegram：`telegram:bot:{bot_id}:chat:{chat_id}:topic:{topic_id}`（`contrib/telegram/identity.py:59`）
- lark：`lark:bot:{bot_open_id}:chat:{chat_id}:thread:{thread_id}`（`contrib/lark/identity.py:41`）

两者都含 bot 身份，同 kind 多实例天然不撞。wecom 不含应用身份：

- `wecom:dm:{sender.id}`（`contrib/wecom/channel.py:494`）
- `wecom:{target_prefix}:{conversation}`（`contrib/wecom/channel.py:1408`）

同一企业内挂两个企微应用时，同一会话在两个实例上算出相同的 `channel_session_id`，两个实例的对话并入同一 thread。
补入应用身份可消除。

`_local_id` 的命名空间保持不变。入站以 `(agent_id, channel, provider_thread_id)` 找到既有行后复用其 id
（`contrib/sqlite/repository/facade.py:38`），而 `Channel` 内 thread id 到 provider session id 的映射
（`core/channel.py:231`）以同一公式的结果为键；公式一变，存量会话的 `accept_turn_event` 与 `anchor_turn`
都拿不到 provider session id，telegram 与 lark 的进度投影随之失效。同 kind 多实例不相撞由两件事保证：
contrib 的身份串含 bot 身份（telegram、lark 已是，wecom 补入），以及 `channel_sessions` 的所属实例列参与判重。

### 组合体

实例集合以 `Channels`（`core/channel.py`）承载，它实现 `IChannel`，由各实例的 `Channel` 组成。
`Agent` 把配置中的全部实例组装成一个 `Channels` 交给 `AgentOrchestrator`，orchestrator、`OutboundDeliveryService`、
turn 与 command 服务继续只认一个 `IChannel`，合流与路由都在组合体内部完成。

`IChannel` 增加 `members`，默认返回自身；`Channels` 返回各成员。只有需要逐实例信息的地方使用它：
`Agent` 回填存量行时逐成员取 `get_identity()` 与 `backfill_provider_thread_id()`。

### 入站合流

`Channels.receive()` 为每个可用成员挂起一次 `anext()`，通过 `asyncio.wait(FIRST_COMPLETED)` 合流。
每次取得消息后，为该成员挂起下一次读取；流结束或出错的成员退出合流，其他成员继续读取。
完成回调及时记录读取错误，消费者退出时取消并收集仍挂起的读取任务。

### 出站路由

`Channels.send()` 按请求选择成员。会话到实例的映射取自 `channel_sessions`，该映射在入站落库时写入，
出站请求带上会话的 `channel_identity` 供组合体路由。

`accept_turn_event`、`anchor_turn`、`request_approval`
（`core/orchestration/turn.py:686`、`core/orchestration/turn.py:324`、`core/orchestration/turn.py:379`）
均带 session 维度，路由方式与出站一致。

`accept_turn_event` 的签名是同步返回 `None`（`core/channel.py:159`），实例选择不引入 I/O，保持同步。

### 同名广播

target 解析命中不同 bot 上的同名会话时全部投递；同一 bot 上命中多个会话仍报歧义。
广播前统一检查所有目标的 freshness，任一目标需要 hold 时整体暂缓发送。

每条会话仍只从其所属实例发出一次，因此投递条数等于命中的会话数。同 kind 多实例接入同一个群时命中两条会话，
发出两条消息，语义是两个 bot 各自发言。

投递结果按会话分别记录，部分成功不改写其余会话的状态。

草稿挂在 runtime 所见的那一个会话上：`_drafts` 以解析出的 thread 集合为键，一次发送一份草稿，任一会话
hold 则整体 hold，`--send-draft` 一次发全部；对调用方的结果仍是一条会话的一行。

### 一次调用一次发送

`bcc message send` 曾带 `command_id` 作幂等键，配合客户端 10 秒超时：超时后再来一次时靠它拒绝重复处理，
`messages.command_id` 上的唯一索引是库内保证。一次发送落多条 outbound 与该唯一索引冲突。根因是调用方先于
发送放弃：客户端 10 秒之外，`CommandDispatcher` 还给每条命令套 `command_seconds`（10 秒），而 provider 调用允许
`provider_call_seconds`（600 秒），任一先到都会在消息可能已出去时取消。去掉这两道之后一次调用只会被处理一次，
幂等键无事可守。因此去掉 `command_id`（CLI 请求、`CommandService`、消息模型、审计关联）、bcc 客户端超时与命令分发
期限（`command_seconds` 仅余审计写入与版本探测使用），新 migration 重建 `messages` 表去掉该列与索引（列在表级
CHECK 内，不能直接 DROP COLUMN）。channel 内部的 HTTP 超时与 WeCom 的回执等待是传输层故障判定，保留。

### 身份渲染

`DeveloperInstructionContext.bot_name` 为单值，且校验非空与不含换行（`core/instruction.py:16`、
`core/instruction.py:35`）。改为序列，逐个施加相同校验，`resources/developer_instructions.md` 同步渲染多个名字。

`Agent._bot_name`（`app/agent.py:240`）由取单实例身份改为汇总全部实例。

### 生命周期与状态

`Channels.start` 与 `Channels.stop` 对全部成员执行。启动时单成员失败不阻断其余成员，失败成员进入不可用状态
并在状态中体现；全部成员失败才算启动失败。同 kind 下出现相同 bot 身份时拒绝启动。

`app/agent.py:294` 的 `channel` 与 `channel_health` 由单值改为按成员列出。

## Tasks

### Task 1：配置数组化与实例身份

`[agent.channel]` 改为 `[[agent.channel]]`，`AgentConfiguration.channel` 改为 `channels` 元组，至少一项。
不新增配置项，实例标识在运行期由 `(kind, ChannelIdentity.id)` 得到；同一 kind 下出现相同 bot 身份时拒绝启动。
配置升级为 version 4；version 3 的单个 channel 表转换为单元素数组，其余字段沿用。

### Task 2：实例身份落库与 wecom 对齐

wecom 的 `provider_thread_id` 与两处身份串加入 bot 段，发送路径增加解析。
`channel_sessions` 增加所属实例列，`channel` 列保持存 kind。
telegram 与 lark 的存量行由 SQL migration 从 `provider_thread_id` 截出 bot id 填入；
wecom 的存量行由 `Agent` 启动时回填，分配给配置中该 kind 的第一个实例并改写 `provider_thread_id` 为新格式。
`save_channel_session` 现有的唯一性检查按 `(channel, provider_thread_id)` 判重
（`contrib/sqlite/repository/sessions.py:141`），需一并带上实例，否则第二个实例无法建立自己的会话行。

### Task 3：组合体与生命周期

`Agent` 构造全部实例并组装成 `Channels`。`Channels.start`/`stop` 覆盖全部成员，单成员启动失败不阻断其余，
同 kind 同 bot 身份拒绝启动。状态输出改为按成员列出。

### Task 4：入站合流

`Channels.receive()` 并行等待各成员的 `anext()`，单成员阻塞不影响其余成员与整体推进。

### Task 5：出站路由与同名广播

`Channels.send` 按会话选择成员。`accept_turn_event`、`anchor_turn`、`request_approval` 同步改造。
target 命中多个会话时全部投递，结果按会话分别记录。

### Task 6：身份渲染

`bot_name` 改为序列，校验与模板同步。

### Task 7：一次调用一次发送

去掉 `command_id`、bcc 客户端超时与命令分发期限；migration 重建 `messages` 表去掉 `command_id` 列与其唯一索引。

### Task 8：真实运行验证

同一 agent 同时接入两个不同 kind 的实例与同一 kind 的两个实例，验证：入站分别落在各自的会话；出站回到来源实例；
同名 target 广播到全部会话；单实例停止后其余实例继续工作。

## 验收标准

- 配置为单个 channel 时行为与改造前一致。
- 同一 kind 的两个实例，入站会话互不合并。
- 会话的出站始终回到其来源实例。
- 一个实例阻塞或停止时，其余实例的入站与出站不受影响。
- 提示词中渲染出该 agent 在全部实例上的名字。
