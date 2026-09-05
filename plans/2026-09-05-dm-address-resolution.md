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

- Telegram：取 `SenderIdentity.name`，即 `@username`。Bot API 10.0（2026-05-08）起，两个机器人在双方于
  BotFather 打开开关后可以按 `@username` 互发私聊，`sendMessage` 的 `chat_id` 接受目标机器人的
  `@username`。该开关无法探测：`getMe` 返回的 `User` 没有对应字段。因此 Telegram 的解析是乐观的——
  地址总能构造出来，成立与否由发送结果决定。历史发送者没有 username 时无法解析。
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

同名多人时不猜测：handle 查询返回多于一个不同的 `sender_id` 即视为未命中，与
`resolve_inbox_target` 现有的「不是恰好一行就报错」保持一致。

## Tasks

### Task 1：按 handle 查历史发送者

在存储端口增加一个查询：给定 handle 与 actor 可达范围，返回历史入站消息中匹配的发送者身份与其 channel。
匹配使用 `sender` 列并按 casefold 比较，与 `target_handle_key` 的比较方式一致。返回多于一个不同
`sender_id` 时视为无结果。

### Task 2：channel 的私聊地址解析能力

`IChannel`（`core/channel.py:121`）增加一个方法，接受 `SenderIdentity`，返回私聊的 provider 地址或 `None`。
`SenderIdentity` 同时带 `id` 与 `name`（`core/models/entities.py:220`），足以覆盖三个 channel 的取值差异，
不需要为此扩展参数。`None` 表示该 channel 无法解析，调用方据此返回「找不到」。

### Task 3：未命中时建立映射

在 `command.py` 的两个解析点接入 Task 1 与 Task 2，按设计一节的流程建立 `channel_sessions` 与 `threads`
两行后重新解析。新建的 dm 行写入 `target_handle` 与 `target_handle_key`，使后续解析直接命中第一步。

### Task 4：端到端测试

用 TestChannel 注入一条群消息，再以该发送者的 handle 执行 `bcc message send --target dm:@name`，
验证映射被建立且消息送达。TestChannel 需要具备解析能力以覆盖成功路径，并能返回 `None` 以覆盖「找不到」路径。
