# Outbound Message Attachments

## 目标

让 agent 通过一次 `bcc message send` 同时提交可选正文和一个或多个本地附件：

```bash
bcc message send --target "<target>" \
  --attachment "<path>" \
  --attachment "<path>"
```

正文继续从 stdin 读取；`--attachment` 可重复，命令行顺序就是附件顺序。输入校验只维护一个
`is_empty`：初始为 `True`，正文含有非空白内容或存在附件时设为 `False`，最后只判断一次 `is_empty`。

`IChannel.send` 是唯一的 channel-neutral 交付端口。core 只表达“一次 logical message 包含正文和有序
附件”，每个 channel 自行决定单次还是多次 provider call、正文与附件顺序、上传协议以及交付结果。

## Channel-neutral 契约

新增不可变 `OutboundAttachment`，只包含 core 能稳定理解的字段：

- `name`：展示文件名；
- `relative_path`：相对于当前 BCN workspace 的内部路径；
- `media_type`：由本地文件名推断的可选 MIME hint；
- `size_bytes`：command 接受请求时观察到的大小。
- `sha256`：command 接受请求时计算的内容指纹，用于锁定本次 logical message 的准确字节。

`OutboundMessage.attachments` 保存有序 tuple，`ChannelSendRequest` 通过现有 `outbound` 携带它们。core 不定义
`file/image/voice/video`，不校验 WeCom 扩展名或大小上限，也不保存 `upload_id` / `media_id`。

CLI 可以接收绝对或相对路径；相对路径由 `bcc` 在调用进程当前目录下解析。daemon 在 fresh-check 前执行统一
安全边界：路径必须解析到当前 workspace 内的现存普通文件，拒绝符号链接、目录、越界路径和重复路径，再把
workspace-relative path 持久化。provider 调用前再次核对文件仍在 workspace 内且大小与 SHA-256 均未变化，
防止命令进程与 channel 读取之间的路径替换。字节不会进入本地 JSON IPC，也不会复制到第二个附件仓库。

SQLite schema 升至 v10，在 `outbound_messages` 增加 `attachments_json`，保存有序的 neutral descriptors。
这既保留 fresh-check 拒绝时的完整 draft，也让 partial/unknown 交付具有可审计输入；不新增独立附件表和
生命周期。现有 SQLite migration 按当前版本直接增加该列，不编写旧格式兼容分支。

## WeCom 主动发送

WeCom adapter 在持有现有 `_send_lock` 后，先对全部附件做本地二次预检；任一附件无效时，在任何 provider
消息发出前整体失败。随后按固定顺序发送：

1. 非空正文按现有 4096-byte markdown 分片依次主动发送；
2. 附件按 CLI 顺序逐个执行 upload init、chunk、finish，再以一次 `aibot_send_msg` 主动发送对应媒体。

正文先发送可以让后续附件保留上下文；全部附件预检先于正文，避免确定性的本地错误造成 partial delivery。
未来 channel 若原生支持正文与多附件一次发送，可在自身 `IChannel.send` 内改变 provider 映射，core 契约
无需变化。

WeCom adapter 独占文件类型映射、格式/大小/文件名校验、分片规则、MD5、`chat_type` 以及 upload/media id
生命周期。core 不感知也不复刻这些 provider 限制。

上传本身不会向用户产生可见消息，因此 init/chunk/finish 的失败或 ack unknown 在尚未发送任何 provider
消息时均可报告为 failed；一旦某个 `aibot_send_msg` 已确认，后续 upload/send 失败统一映射 partial。任何
实际 send frame 发出后 ack 无法确认，则整体结果为 unknown，禁止自动重试完整 logical message。

receipt 保存每个可见 part 的类型、序号和 provider request id，并保存上传阶段的 request ids 用于诊断；
不持久化 `upload_id` 或 `media_id`。`ChannelDeliveryReceipt.provider_receipt_ref` 指向最后一个已尝试的可见
send request。

## Developer instruction

只在 `DEVELOPER_INSTRUCTIONS` 现有 Sending messages 段落补充 `bcc message send` 参数说明：

- `bcc message send --attachment "<path>"` 可重复；
- 可以同时发送 stdin 正文和多个附件；
- attachment-only send 可以没有正文；正文和附件统一参与 logical message empty 判定；
- path 必须指向当前 workspace 内的普通文件；
- 一次 CLI 调用是一条 logical message，channel 可以映射成多个 provider messages；若返回 partial/unknown，
  不得盲目重试整条消息。

## 实施任务

1. 在 `feat/outbound-message-attachments` 扩展 neutral domain、SQLite v10、local command transport 与
   `bcc message send --attachment`；实现
   workspace path 边界、重复路径拒绝、attachment-only 输入和完整 draft 持久化。补齐 domain、migration、
   repository、dispatcher、CLI 与 developer instruction 的 focused tests，完成后停在 review。
2. 扩展 command orchestration 与 `IChannel.send` 输入：把有序附件交给 channel，保持现有 fresh-check、
   reply target、provider outcome 与 audit 状态机；补齐正文-only、附件-only、mixed、local preflight failure
   和 partial/unknown 的 focused tests，完成后停在 review。
3. 实现 WeCom 主动附件交付：全量预检、类型映射、MD5、三阶段分片上传、显式 `chat_type`、逐附件主动发送
   及多 part receipt。复用现有通用 ack correlation，不增加只使用一次的薄 helper；补齐纯 frame/codec、
   边界和状态归并测试，完成后停在 review。
4. 运行完整 test suite、Ruff、Pyright、compileall、lock verification、`git diff --check`，并确认
   Neovim LSP 实际 attach 后检查全部改动 Python 文件。随后在真实测试 WeCom 会话验证正文 + 多附件、
   attachment-only、DM/group `chat_type` 以及接收端内容；完成后提供业务 diff 和 live evidence，停在 review。

当前分支为 `feat/outbound-message-attachments`，base 是
`origin/main@9e4e7ce760b14beb292309af842d4c1b2bd47d48`。本计划不授权 commit、push、PR、发布或部署；这些动作
需要单独授权。
