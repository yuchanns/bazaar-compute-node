# 企业微信频道与帖子会话

## 目标

- 基于 `main` 的 `feature/20261014-wecom-topic` 分支开发。
- 原有 `wecom` 同时支持普通会话与频道，同一个实例持有同一个 bot 身份和长连接，按入站消息类型自动分发。
- 用户沿用现有配置；agent 沿用现有消息格式、target 与 bcc 命令。不同入站类型的获取和投影由 WeCom 适配层处理。
- 频道事件到达后主动查询帖子、回复正文，构造现有 `Message` 入站；每个帖子拥有独立会话，回复回到对应帖子。
- 机器人可以主动发帖子、回复帖子，并通过平台原生字段真实 @ 频道成员。
- 原生 topic 场景自动批准新会话，普通会话使用现有审核策略。

## 已核实的协议

本次调研直接读取企业微信智能机器人开发文档。频道功能正在灰度；本次对接条件是现有长连接能够收到频道事件，事件不携带正文，正文通过频道 OpenAPI 获取。

| 事项 | 结论与来源 |
| --- | --- |
| 身份层级 | `topic_id` 是频道 ID，`post_id` 是帖子 ID，`reply_id` 是帖子下的回复 ID。[事件文档](https://developer.work.weixin.qq.com/document/path/101918) |
| 事件 | `topic_at_bot` 表示被 @；`topic_event`、`topic_reply_event` 的 `type=1/2/3` 分别表示新增、修改、删除。事件包含 `msgid`，作者可能是 `from.userid` 或 `from.aibotid`。[事件文档](https://developer.work.weixin.qq.com/document/path/101918) |
| 调用凭证 | `POST /cgi-bin/aibot/get_token`，签名为 `sha256(secret + bot_id + time + nonce)` 的小写十六进制摘要。时间戳为秒，token 有效期一天，需要缓存。[凭证文档](https://developer.work.weixin.qq.com/document/path/101910) |
| 正文 | `/cgi-bin/aibot/topic/get_post_list`、`get_reply_list` 支持按 ID 获取；有全部内容权限时也支持游标分页。正文为 Markdown，附内嵌文件、成员提及和图片信息。[指定内容](https://developer.work.weixin.qq.com/document/path/101913)、[全部内容](https://developer.work.weixin.qq.com/document/path/101922) |
| 发送 | `send_post` 返回 `post_id`；`send_reply` 返回 `reply_id`，用 `parent_reply_id` 指定回复某条评论。正文上限 20480 字节。[发送文档](https://developer.work.weixin.qq.com/document/path/101924) |
| 真实 @ | 正文中的 `${inline_mention:placeholder}` 配合 `inline_mention_list`，成员为 `{type: 0, userid: ...}`。单次最多八个；当前发送文档允许 @ 成员，读取结构另外支持机器人身份。[发送文档](https://developer.work.weixin.qq.com/document/path/101924) |
| 素材 | HTTP multipart 上传临时素材，文件、图片以 `media_id` 写入发送请求，各最多八项。[素材文档](https://developer.work.weixin.qq.com/document/path/101917) |
| 权限与频道信息 | 频道权限区分全部内容与仅被 @ 内容，以及能否主动发送；仅被 @ 的访问和回复窗口为 24 小时。频道名称、成员由 `get_topic_list`、`get_topic_member_list` 获取。[权限说明](https://developer.work.weixin.qq.com/document/path/101906)、[频道信息](https://developer.work.weixin.qq.com/document/path/101925) |
| 重复入口 | 平台会把频道内容兼容同步到群聊通道，回调来源由 `compat_info.source=channel` 标识。此来源的内容交由原生频道事件路径处理，防止同一内容处理两次。[兼容说明](https://developer.work.weixin.qq.com/document/path/101906)、[现有长连接协议](https://developer.work.weixin.qq.com/document/path/101463) |

## 实现设计

### 同一个 wecom 按入站类型自动分发

`[[agent.channel]]` 沿用现有 `kind = "wecom"`、`bot_id`、`secret_env`、`websocket_url` 配置。同一个实例同时接收普通消息与频道事件，两种处理路径根据入站类型自动选择：

- 普通私聊、群聊消息按当前消息处理流程构造 inbound。
- 频道事件按 `topic_id`、事件类型和帖子/回复 ID 主动查询正文，再构造同样格式的 inbound。

两条路径最终统一生成现有 `Message`，交给相同的编排和存储流程。`WeComChannel` 负责唯一长连接的鉴权、心跳、重连、消息回执及退出。频道 REST 客户端使用相同 bot 凭据，token 缓存和 HTTP session 归该实例生命周期管理。

频道事件分发从当前 `WeComChannel._receive_event()` 扩展。事件进入由 channel 持有的异步处理任务，再查询正文；WebSocket reader 继续处理心跳、普通消息与发送回执。`compat_info.source=channel` 的兼容投影交由频道事件路径处理。关闭 channel 时取消并等待查询任务，再释放 HTTP session。

### 频道与帖子映射

普通会话继续使用现有 provider 地址。原生频道及帖子分别有地址，ID 段按明确编码规则保存与解析：

```text
wecom:<bot_id>:topic:<topic_id>
wecom:<bot_id>:topic:<topic_id>:post:<post_id>
```

频道根地址用于主动新建帖子，在机器人加入频道或首次频道事件时通过安静的入站上下文建立。每个帖子另有一个 `ChannelSession` 和 `Thread`，帖子下所有回复进入这个会话；不同帖子、频道和 bot 的身份独立。`provider_chat_id` 为频道 ID，`channel` 为 `wecom`，会话名称来自频道名称和帖子信息。

帖子和回复的 provider 消息身份由实际 `post_id/reply_id` 构造；普通新增和被 @ 事件指向相同内容时共用内容身份，防止重复入站。修改与删除事件使用事件身份追加相应变更记录。回调 `msgid` 同时用于事件排重。

收到 `topic_at_bot` 或新建、变更事件时，按事件中的 ID 查询正文。回复入站前取得帖子正文和需要的父回复，作为 `notifies_runtime=false` 的上下文入站；当前内容按作者、原生提及字段和事件语义设置 `SenderKind`、`mentions_agent` 与通知标志。获取失败保留可观察错误，成功获取后才确认内容已处理。

正文投影把成员占位符展开为稳定的 `@userid`，机器人身份保留 `aibotid`；附件经过现有 `IAttachmentMaterializer` 落到所属 workspace。下载所需 cookie 只留在 HTTP 下载请求中。

### 原生发送与真实 @

发送路径按 provider 地址选择普通会话发送或频道 REST 发送。已建立的帖子会话调用 `send_reply`；频道级主动发内容调用 `send_post`，并以返回的 `post_id` 建立该帖子会话。`--reply-to` 通过已有消息身份映射到帖子或回复 ID，发送评论时填写相应 `parent_reply_id`。

出站正文中的明确成员提及 `@userid` 对照当前频道成员身份处理，转成占位符及 `inline_mention_list`。代码片段中的文字保持正文含义。文件上传后以 `inline_file_list` 发送，图片以 `image_list` 发送，使用现有附件文件校验和读取边界。

HTTP 请求结果沿用 `ProviderCallResult` 与 `ChannelDeliveryReceipt`：平台确认后才报告 sent；已确认部分发送后失败报告 partial；发送请求结果未知时报告 unknown。回执保存实际帖子/回复 ID，供后续引用使用。

### topic 场景自动批准新会话

WeCom 频道投影在构造入站时标记其可信准入决定。该标记由 adapter 根据已验证的频道事件生成，使用通用入站 metadata 的 `review = "approved"`，和帖子身份一同进入编排器。

编排器在 `record_inbound()` 时，将可信准入决定转换成已有的 `opening=Review.APPROVED`；其余消息继续调用现有 `ReviewPolicy.opening()`。`ChannelSession.review`、现有审核状态和存储接口沿用当前实现。自动批准发生在新会话建立时；已保存的会话审核决定仍由现有审核入口管理。

## Tasks

### Task 1：协议调研与接入计划

- [x] 从最新 `main` 建立独立分支。
- [x] 读取官方频道协议、凭证、内容获取、发送、提及和素材文档，明确身份映射和调用方式。
- [x] 按本次讨论形成同一 bot 自动分发、事件查询正文与 topic 自动准入方案。
- [x] 写入本计划；本 task 完成后停下来 review。

### Task 2：同一 wecom 的频道事件与正文入站

- 增加频道 REST 客户端、凭证缓存、事件与内容边界模型。
- 完成频道事件分发、后台查询、帖子会话身份、上下文、作者、提及和附件投影，以及事件与内容排重。
- 频道入站提供可信准入决定，复用已有 `opening` 自动批准新 topic 会话。
- 同一实例按入站类型自动处理普通消息与原生频道事件；任务和连接退出完整回收。
- 用真实 SQLite 验证帖子会话及上下文行为，普通 WeCom 回归通过；真实企微事件与读取归类为 e2e，使用独立测试 bot 和隔离配置。
- 执行 Ruff 和根目录 LSP，完成后提交本 task，停下来 review。

### Task 3：频道发送、真实 @ 与完整验收

- 实现原生帖子/回复发送、回执及引用映射，成员真实 @、素材上传和平台限制。
- 文档说明现有 wecom 配置如何同时处理普通会话、原生频道与 topic 自动批准。
- 以独立测试 bot 验证普通会话与频道同实例收发、新建帖子、帖子回复、成员 @、附件，以及开启 control 后 topic 自动准入；结果和平台权限条件写入此计划。
- 相关回归、Ruff 和根目录 LSP 通过后提交，停下来 review。

## 验证约定

业务验证使用真实 SQLite、真实文件和已有 TestChannel 控制面；外部企业微信收发归类为 e2e，记录实际执行结果。测试资源使用临时配置、数据库与隔离进程。接口边界模型按官方字段验证，测试以现有配置和 bcc 命令可用、统一入站格式、会话隔离、消息可读、实际发送回执和资源释放行为为依据。
