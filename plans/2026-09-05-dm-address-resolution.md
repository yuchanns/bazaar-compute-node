# DM 地址解析

## 背景

`bcc message send --target dm:@name` 目前只能发给已经存在的会话。`resolve_inbox_target`
（`contrib/sqlite/repository/messages.py:300`）把 `dm:@name` 解析成
`channel_sessions.target_kind = 'dm' AND target_handle_key = ?` 的查询，命中不是恰好一行就抛
`InboxTargetResolutionError`。而 `channel_sessions` 的 dm 行只在收到对方私聊时才建立，因此从未私聊过的对象
无法寻址，机器人之间尤其如此——它们通常只在群里见过面。

## 目标

`dm:@name` 未命中时，用已经收到过的入站消息补出这个地址：先查已有会话，查不到就按 channel 各自的规则
把历史发送者身份换成一个私聊地址并建立映射，换不出来就把「找不到」返回给 `bcc` 调用方。

解析过程不区分对方是人还是机器人。channel 要么能把一个历史发送者变成私聊地址，要么不能，对方的性质不进入模型。

## 地址来源

入站消息本身携带对方的可寻址身份，不需要任何目录查询接口。`messages` 表已经存了三列：`sender`
（handle）、`sender_id`（provider 身份）、`sender_display_name`，由 `contrib/sqlite/codec.py:102-116`
写入和读出。因此「见过谁说话」就等于「持有给谁发私聊所需的一切」。

各 channel 的对应关系如下，均为调研结论：

- Telegram：取 `SenderIdentity.id`，即对方的用户 id，私聊的 chat_id 与之相同。人与 bot 同样对待。
  `@username` 只用于**打开一个尚不存在的私聊**：Bot API 10.0（2026-05-08）的 changelog 写的是
  「Added the ability to send messages to other bots via username if both bots enabled bot-to-bot
  communication」，文档中没有任何一处说 bot 可以用数字 id 作为尚未建立的私聊目标；而私聊一旦存在，
  数字 chat_id 即可送达（本节点向 `telegram:<bot>:7181589532:0` 发出的四条出站消息均为 `sent`）。
  该开关无法探测（`getMe` 的 `User` 没有对应字段），因此首次发送是乐观的，成立与否由发送结果决定。
  `TelegramThreadIdentity.chat_id` 保持 `int | str`：历史身份串仍按原样解析，其 uuid5 不变，
  已有会话不会被重新编号。
- 飞书：取 `SenderIdentity.id`，即 `open_id`，而 `open_id` 是 `im/v1/messages` 的 `receive_id_type` 合法取值，
  可直接作为 `receive_id`。`im.message.receive_v1` 的 `event_sender` 结构为
  `sender_id{union_id, user_id, open_id}` 加 `sender_type`（取值 `user` 或 `bot`），两种发送者共用同一结构，
  因此机器人发言同样带 `open_id`。接收其他机器人的群消息需要 `im:message.group_msg.include_bot:read`
  权限，该权限仅自建应用可申请。
- 企业微信：取 `SenderIdentity.id`，即 `userid`（`contrib/wecom/channel.py:1161`），而长连接的 `aibot_send_msg`
  在单聊场景要求 `body.chatid` 填用户的 `userid` 并置 `body.chat_type = 1`。
  `contrib/wecom/outbound.py:95` 已经是这个形状。

## 设计

解析失败后的补救发生在 core 的发送路径，不在存储层：存储层无法调用 channel。
`command.py:477` 与 `command.py:561` 是仅有的两个 `resolve_inbox_target` 调用点。

流程：

1. 按现有逻辑解析 `dm:@name`，命中即结束。
2. 未命中且目标形如 `dm:@name` 时，在当前 actor 可达范围内按 handle 查历史入站发送者，
   取其 `sender_id` 与所属 channel。
3. 把该身份交给 channel 换取私聊的 provider 地址。
4. 按 `(channel, provider_thread_id)` 找现有会话——会话行的 id 由建立它的那一刻决定，不必等于此刻
   由身份算出的那个；找不到才建立 `channel_sessions` 与 `threads` 两行，然后重新解析；换不到地址则抛
   `InboxTargetResolutionError`，`bcc` 调用方看到的仍是「找不到」。

发送失败不回滚已建立的映射。地址换不出来时没有别的补救手段，保留映射与删除映射对调用方没有区别。

`dm:@name` 中的 `name` 就是 agent 在消息头 `@` 位置上看见的那一段。该位置由
`resources/command/sender.tpl` 渲染：有 handle 时是 handle，没有时是 `sender_id`；显示名只出现在括号里，
不参与寻址。因此匹配只用两级，与 `@` 位置的取值规则一致：

1. `messages.sender`（handle）
2. `messages.sender_id`

**这两级的取值在三个 channel 上都是唯一的**，所以重名不会发生，不需要歧义处理：

- Telegram：`@` 位置是 username，Telegram 内唯一（`contrib/telegram/channel.py:1012`）。
- 飞书：没有 handle，`@` 位置是 open_id（`contrib/lark/channel.py:596-606`），唯一。
- 企业微信：没有 handle 也没有显示名，`@` 位置是 userid（`contrib/wecom/channel.py:1434`），唯一。

显示名不作为匹配键，正是重名不可能出现的原因——一旦按显示名匹配，重名就会成为常态。真正的名字在
消息括号里看得见，用于人读；寻址用 `@` 那一段，用于唯一定位。两者分工不同，不必合并。

### 飞书私聊保持现状

飞书私聊的 target 继续显示为 `dm:<channel_session_id>`，说话人继续渲染为 `@<open_id>(<显示名>)`，
本方案不改这两处展示。`plans/2026-08-25-readable-targets.md` §5.2 的结论予以保留。

需要成立的只有一件事：**`dm:@<open_id>` 能解析到人**。`@` 位置上的 open_id 正是 agent 在飞书消息里
看到并且可以照抄的那一段（`resources/command/sender.tpl` 在没有 handle 时渲染 `@<sender_id>`），
因此按 `messages.sender_id` 匹配这一级足以覆盖飞书，不需要为飞书补 handle。

## 会话身份由 provider 认定

会话行的键是对方的 provider 身份，不是任何可被改名或转让的名字。Telegram 上首次发往一个尚不存在的
私聊必须用 `@username`，这是投递地址，与会话身份是两件事：混用会让「我方发出的」与「对方回来的」
落在两行上，同一段对话被劈成两半。

`sendMessage` 成功时返回发出的那条 Message，其中必然带 `chat`，因此每一次投递都附带 provider 自己认定
的会话 id，与本次用什么形式寻址无关。据此：

- 出站回执 `ChannelDeliveryReceipt` 与 `OutboundDeliveryResult` 携带 `provider_thread_id`，由 channel 从
  回执中取出并按自己的身份串格式拼出。
- 一次投递之后，若回执给出的身份与会话行不同，则以回执为准。目标身份已经有行时不改，两行同一个身份
  比一个滞后的名字更糟；`channel_sessions` 上的 provider 身份索引不是唯一索引，重复不会被数据库挡下。
- 身份变更是一次窄操作 `rebind_channel_session`，不走 `save_channel_session`——后者明确禁止改动
  provider 身份，该限制在其余路径上继续成立。

## 首次投递携带 handle

`DmAddress` 携带 `delivery_handle`：打开一个尚不存在的私聊所需的名字，由 channel 自己给出——需不需要
一个名字、什么样的名字算数，只有 channel 知道。telegram 对 bot 给出其 username，对人不给，其余 channel
不给。mint 把 channel 给出的值写入会话行的 `provider_identity_ref_json`，`ChannelSendRequest` 再把它带回
channel；telegram 出站在有该值时用 `@handle` 作为 `chat_id`，否则用身份中的数字 id。打字状态的路由仍按
数字身份注册，`@handle` 只进入本次发送的 payload。

投递一旦有任何一段被确认，该私聊就已经存在，数字 id 足以送达，会话行上的 `delivery_handle` 随即清除；
多段发送中前面几段成功而后面失败时，整体结果可能是 `unknown`，此时回执里仍带着被确认那段所属的会话 id，
按它同样清除。清除前重新读取该行：清一个键不该带上发送前的快照，否则会把期间到达的入站回执盖掉。
清除与身份变更同时发生时，先清除后变更：变更之后再保存整行会带上该行已经没有的身份。

## Tasks

### Task 1：按 `@` 位置的取值查历史发送者

在存储端口增加一个查询：给定 `dm:@` 后的那一段与 actor 可达范围，返回历史入站消息中匹配的发送者身份
与其 channel。先按 `sender` 列匹配，未命中再按 `sender_id` 匹配，与 `@` 位置的渲染规则一致。
`sender` 的比较按 casefold，与 `target_handle_key` 的比较方式一致；`sender_id` 精确比较。
显示名不参与匹配。

### Task 2：channel 的私聊地址解析能力

`IChannel` 增加 `dm_address(sender, *, sender_kind) -> DmAddress | None`。`None` 表示该 channel 换不出
地址，调用方据此返回「找不到」。

返回的不是单个地址字符串，而是 `DmAddress(channel_session_id, thread_id, provider_thread_id)` 三元组：
两个 id 由各 channel 用**自己的身份串**做 uuid5 得出，core 无法从 `provider_thread_id` 推导。若由 core
自行编号，等对方之后真的发消息进来，channel 会算出另一组 id，同一个人会出现两条会话。

`sender_kind` 用于排除来路不明的发送者，并决定 `delivery_handle`：以频道或匿名管理员身份发到群里的消息带的是该群的 id 而没有
`from`，按它建立的私聊会把内容发回原群。该参数不进入 agent 的视野：agent 始终只写 `dm:@name`。

### Task 3：未命中时建立映射

在 `command.py` 的 `send` 解析点接入 Task 1 与 Task 2，按设计一节的流程建立 `channel_sessions` 与
`threads` 两行后重新解析。新建的 dm 行写入 `target_handle` 与 `target_handle_key`，使后续解析直接命中
第一步。

只接 `send`，不接 `unfollow`：为一个尚不存在的会话建立映射只为了取消关注没有意义。

`materialize_outbound_if_fresh` 原本要求目标会话至少有一条入站消息，否则报「target is not replyable」。
冷启动私聊的目标会话必然零入站，与该校验直接冲突。该校验没有任何测试覆盖，也不来自任何既定要求，
因此删除；配套的 `ErrorKind.TARGET_NOT_REPLYABLE` 随之成为死代码，一并删除。

### Task 4：迁移既有的按 handle 命名的会话

按 handle 命名的 dm 行的正身可从入站消息推出：入站同时带着对方说话时用的 handle 与其 `sender_id`，
取该 handle 下 seq 最大的一条，与 `_resolve_or_mint` 的取法一致。据此：

- 对方已经有按其 id 命名的会话时，把按 handle 命名的那行的 messages、thread、reminder 与 cursor
  并入该会话后删除该行。消息上的 `provider_thread_id` 不改写——它记录的是该条消息当时如何被寻址；
  `target` 改写为留存会话的 `canonical_target`。
- 对方还没有这样的会话时，就地把该行改名为按 id 命名，并把原 handle 写入 `delivery_handle`，
  使下一次发送仍能打开尚未建立的私聊。

cursor 合并取「两边未读中最小的 seq 减一」：`delivered_through_seq` 比较的是全局 seq，取两者较大值会把
另一侧尚未读到的消息判为已送达。

配对只在同一 channel 内进行，provider 身份的定义是 `(agent_id, channel, provider_thread_id)`。

### Task 5：端到端测试

用 TestChannel 注入一条群消息，再以该发送者的 handle 执行 `bcc message send --target dm:@name`，
验证映射被建立且消息送达。TestChannel 需要具备解析能力以覆盖成功路径，并能返回 `None` 以覆盖「找不到」路径。
