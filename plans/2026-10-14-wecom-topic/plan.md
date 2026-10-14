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
| 素材 | HTTP JSON 初始化、分片和完成上传；分片使用 `base64_data`，编码前不超过 512KB。文件、图片以 `media_id` 写入发送请求，各最多八项。[素材文档](https://developer.work.weixin.qq.com/document/path/101917) |
| 权限与频道信息 | 频道权限区分全部内容与仅被 @ 内容，以及能否主动发送；仅被 @ 的访问和回复窗口为 24 小时。频道名称、成员由 `get_topic_list`、`get_topic_member_list` 获取。[权限说明](https://developer.work.weixin.qq.com/document/path/101906)、[频道信息](https://developer.work.weixin.qq.com/document/path/101925) |
| 重复入口 | 平台会把频道内容兼容同步到群聊通道，回调来源由 `compat_info.source=channel` 标识。此来源的内容交由原生频道事件路径处理，防止同一内容处理两次。[兼容说明](https://developer.work.weixin.qq.com/document/path/101906)、[现有长连接协议](https://developer.work.weixin.qq.com/document/path/101463) |

## 实现设计

### 同一个 wecom 按入站类型自动分发

`[[agent.channel]]` 沿用现有 `kind = "wecom"`、`bot_id`、`secret_env`、`websocket_url` 配置。同一个实例同时接收普通消息与频道事件，两种处理路径根据入站类型自动选择：

- 普通私聊、群聊消息按当前消息处理流程构造 inbound。
- 频道事件按 `topic_id`、事件类型和帖子/回复 ID 主动查询正文，再构造同样格式的 inbound。

两条路径最终统一生成现有 `Message`，交给相同的编排和存储流程。`WeComChannel` 负责唯一长连接的鉴权、心跳、重连、消息回执及退出。频道 REST 客户端使用相同 bot 凭据，token 缓存和 HTTP session 归该实例生命周期管理。

频道事件分发从当前 `WeComChannel._receive_event()` 扩展。事件进入由 channel 持有的异步处理任务，再查询正文；WebSocket reader 继续处理心跳、普通消息与发送回执。`compat_info.source=channel` 的兼容投影交由频道事件路径处理。连接失败、传输超时、HTTP 408/429/5xx 等暂时性读取错误最多重试三次，间隔为 1、2、4 秒；保持原事件和内容身份，成功后才确认，最终失败记录事件 ID 和已脱敏原因。

关闭 channel 时阻止新发送和排队发送，取消并等待查询任务，在关闭预算内等待已开始的原生发送，超时后取消并等待其退出，再释放 HTTP session。取消时依据是否已开始可见发送、是否已有确认回执返回失败、结果未知或部分成功。关闭后的 REST 客户端拒绝再次创建连接，显式启动 channel 时开启新一轮生命周期。

### 频道与帖子映射

普通会话继续使用现有 provider 地址。原生频道及帖子分别有地址，ID 段按明确编码规则保存与解析：

```text
wecom:<bot_id>:topic:<topic_id>
wecom:<bot_id>:topic:<topic_id>:post:<post_id>
```

频道根地址用于主动新建帖子，在机器人加入频道或首次频道事件时通过安静的入站上下文建立。每个帖子另有一个 `ChannelSession` 和 `Thread`，帖子下所有回复进入这个会话；不同帖子、频道和 bot 的身份独立。`provider_chat_id` 为频道 ID，`channel` 为 `wecom`，会话名称来自频道名称和帖子信息。

帖子和回复的 provider 消息身份由实际 `post_id/reply_id` 构造；普通新增和被 @ 事件指向相同内容时共用内容身份，由现有统一入库流程去重。修改与删除事件使用事件身份追加相应变更记录。回调 `msgid` 用于识别变更事件及查询错误。

收到 `topic_at_bot` 或新建、变更事件时，按推送中的帖子或回复 ID 查询当前这一条正文，再按作者、原生提及字段和事件语义设置 `SenderKind`、`mentions_agent` 与通知标志。回复包含 `parent_reply_id` 时，另按 ID 查询这一条引用消息，作为安静上下文入库。当前正文与引用查询的暂时性错误进入同一事件的限次重试。已有历史由本地数据库提供，入站引用携带原生父消息身份，编排器在同一会话中查库关联已保存的消息。

正文投影把成员占位符展开为稳定的 `@userid`，机器人身份保留 `aibotid`；附件经过现有 `IAttachmentMaterializer` 落到所属 workspace。下载所需 cookie 只留在 HTTP 下载请求中。共用附件保存器移除工作区累计容量上限及每次保存前的目录容量扫描。

### 原生发送与真实 @

发送路径按 provider 地址选择普通会话发送或频道 REST 发送。已建立的帖子会话调用 `send_reply`；频道级主动发内容调用 `send_post`，并以返回的 `post_id` 建立该帖子会话。`--reply-to` 通过已有消息身份映射到帖子或回复 ID，发送评论时填写相应 `parent_reply_id`。

出站正文中的明确成员提及 `@userid` 对照当前频道成员身份处理，转成占位符及 `inline_mention_list`。代码片段中的文字保持正文含义。文件上传后以 `inline_file_list` 发送，图片以 `image_list` 发送，使用现有附件文件校验和读取边界。新帖上下文的附件身份包含帖子消息身份、附件序号和内容摘要，内容相同的不同文件也能分别入库。

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

- [x] 增加频道 REST 客户端、凭证缓存、事件与内容边界模型。
- [x] 完成频道事件分发、当前消息查询、帖子会话身份、本地上下文引用、作者、提及和附件投影，复用统一入库去重。
- [x] 频道入站提供可信准入决定，复用已有 `opening` 自动批准新 topic 会话。
- [x] 同一实例按入站类型自动处理普通消息与原生频道事件；当前正文的暂时性读取错误有上限重试，关闭时协调发送任务并回收连接。
- [x] 用真实 SQLite 验证帖子会话及上下文行为，普通 WeCom 回归通过；真实企微事件与读取使用用户提供的独立测试 bot。
- [x] 执行 Ruff 和根目录 LSP；按用户“开发完再测试”的要求继续完成发送与验收。

### Task 3：频道发送、真实 @ 与完整验收

- [x] 实现原生帖子/回复发送、回执及引用映射，成员真实 @、素材上传和平台限制。
- [x] 本计划说明现有 wecom 配置如何同时处理普通会话、原生频道与 topic 自动批准。
- [x] 以独立测试 bot 验证频道事件收发、新建帖子、帖子回复、成员 @、附件；以真实 SQLite 和 TestChannel 控制面验证审核与上下文。
- [x] 相关回归、Ruff 和根目录 LSP 通过，提交后交付 review。

## 验证约定

业务验证使用真实 SQLite、真实文件和已有 TestChannel 控制面；外部企业微信收发归类为 e2e，记录实际执行结果。测试资源使用临时配置、数据库与隔离进程。接口边界模型按官方字段验证，测试以现有配置和 bcc 命令可用、统一入站格式、会话隔离、消息可读、实际发送回执和资源释放行为为依据。

## 实测结果与平台条件

- 用户授权的独立测试机器人已完成 API 鉴权、频道与成员查询。真实长连接收到新增帖子、被 @ 和帖子内部回复事件；推送对应的当前正文进入统一的 `Message`。平台同步的兼容群聊回调被过滤，当前回复保持实际作者、提及，引用关系从同一会话的本地历史关联。
- `WeComChannel.send()` 接收原来的正文和附件参数，内部自动把频道成员的 `@userid` 转成原生占位符与 `inline_mention_list`；无需 agent 填写结构化字段。代码、链接和邮件地址保留文字含义。成员接口只提供身份 ID，因此按 `userid` 解析提及。
- 真实 OpenAPI 已验证发帖、评论、回复某条评论、成员真实 @、图片和文件上传及下载。文件验证覆盖超过 512KB 的多分片上传，下载后核对原始字节；两个内容相同、路径不同的文件随新帖投影后，一同写入真实临时 SQLite。用户确认客户端看到彩色测试图片。
- 附件下载需要原样携带接口返回的 Cookie，通用 cookie 序列化会改变平台需要的值。链接和 cookie 只用于下载请求，不进入消息、回执或日志。图片扩展名按实际媒体类型保存。
- 单独发送图片时，平台拒绝空正文（40058）；channel 自动以图片文件名补正文，沿用原来的仅附件调用方式。当前原生发送接口支持文件、图片和成员提及；提及机器人、腾讯文档专用卡片和审批卡片未列入该发送协议。原生话题的卡片审批返回拒绝及原因；会话自动准入只涉及已有 `Review` 状态。
- 原生事件处理任务在单条事件失败后继续工作；暂时性读取错误最多重试三次。出站调用返回明确的失败、部分成功或结果未知状态。共用附件保存器不再以工作区累计附件容量拒绝新文件。普通 WeCom 仍使用原有收发路径。普通会话的真实发送需要其实际回调 chat id；频道成员 ID 无法直接作为已建立的普通会话地址，本次普通真实会话测试未执行。
- 相关本地回归 135 项通过，包含附件保存、真实 SQLite 的自动准入、本地引用关联、首次收到回复时的消息可读性、帖子和机器人隔离、重放去重。原生 provider e2e 使用独立进程及临时工作区，真实 HTTP 请求开始后关闭 channel，验证在途评论送达、排队发送被拒绝、连接释放、显式重新启动和无等待预算时取消在途发送。另以此前实际收到的回复 ID 和真实 API 内容构造入站审计，记录当前正文使用按 ID 获取的一条读取请求，重复投影后本地会话仍保留一条消息。普通 provider e2e 因未提供真实普通会话地址跳过。根目录 `uv run scripts/pyright_lsp_check.py --outputjson .` 和 Ruff 通过。

真实 provider 测试通过 `BCN_WECOM_BOT_ID`、`BCN_WECOM_SECRET`、`BCN_WECOM_TOPIC_ID` 提供独立测试资源，运行 `uv run pytest -m e2e tests/e2e/test_wecom_real_topics.py`。普通会话实测另外提供真实回调得到的 `BCN_WECOM_TEST_CHAT_ID`。这些变量仅用于测试，不改变生产配置。
