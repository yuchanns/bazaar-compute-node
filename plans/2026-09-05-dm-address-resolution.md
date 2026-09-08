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

- Telegram：按对方是不是 bot 分流。`sendMessage` 的 `chat_id` 文档写的是「target **bot**, supergroup or
  channel 的 @username」，普通用户不在其列，因此**只有 bot 才用 `@username`，人一律用数字 id**；
  私聊的 chat_id 与对方的用户 id 相同。bot 没有 username 时退回数字 id，不构造飞书会拒绝的地址。
  Bot API 10.0（2026-05-08）起，两个机器人在双方于 BotFather 打开开关后可以按 `@username` 互发私聊；
  该开关无法探测（`getMe` 的 `User` 没有对应字段），因此这条是乐观的，成立与否由发送结果决定。
  为承载 username，`TelegramThreadIdentity.chat_id` 由 `int` 放宽为 `int | str`：数字仍按原样解析，
  历史身份串的 uuid5 因此不变，已有会话不会被重新编号。
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
4. 换到地址则建立 `channel_sessions` 与 `threads` 两行并重新解析；换不到则抛
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

`sender_kind` 供 Telegram 区分 bot 与人，其余 channel 忽略。该参数不进入 agent 的视野：agent 始终只写
`dm:@name`，人与 bot 的差别只存在于内部解析。

### Task 3：未命中时建立映射

在 `command.py` 的 `send` 解析点接入 Task 1 与 Task 2，按设计一节的流程建立 `channel_sessions` 与
`threads` 两行后重新解析。新建的 dm 行写入 `target_handle` 与 `target_handle_key`，使后续解析直接命中
第一步。

只接 `send`，不接 `unfollow`：为一个尚不存在的会话建立映射只为了取消关注没有意义。

`materialize_outbound_if_fresh` 原本要求目标会话至少有一条入站消息，否则报「target is not replyable」。
冷启动私聊的目标会话必然零入站，与该校验直接冲突。该校验没有任何测试覆盖，也不来自任何既定要求，
因此删除；配套的 `ErrorKind.TARGET_NOT_REPLYABLE` 随之成为死代码，一并删除。

### Task 4：端到端测试

用 TestChannel 注入一条群消息，再以该发送者的 handle 执行 `bcc message send --target dm:@name`，
验证映射被建立且消息送达。TestChannel 需要具备解析能力以覆盖成功路径，并能返回 `None` 以覆盖「找不到」路径。
