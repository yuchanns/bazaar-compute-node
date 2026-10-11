# BCN 0.3 模块化路线图

本路线图列明能力归属、替换顺序、阶段验收和正式发行要求。
版本目标、分支及仓库组织见 [总计划](README.md)，首阶段执行细节见 [contrib 框架计划](framework.md)。

## 顺序与能力归属

以下按依赖排列。首个特性分支交付第 1–7 项中的默认基础能力及默认 agent 的配置管理，按 [首阶段 Tasks](framework.md#本特性分支的串行-tasks) 分项实现并 review。
具体渠道和 harness provider、其余平台管理及 server／web 等组合由后续特性分支交付，合入按各自验证结果推进。
每一阶段在新成员中实现所属能力、调用者和包内测试，后台协程归属任务组，通知和队列使用 Event 与 Queue，取消和时限通过任务取消与 timeout 管理，线程与子进程使用标准库接口。
新包统一要求 Python 3.15。每一阶段都完成其新模块的包结构与安装验证，发布版本在最终发行阶段统一收齐。
各能力阶段交付独立的新包组合，旧 src 与旧测试保持原样。第 16 阶段在全部新能力齐备、整体候选验收通过后统一切换入口，并一次清理旧代码、测试、依赖与装配。
必要包按 [本地 workspace 开发](README.md#本地-workspace-开发) 加入 BCN 成员与共享锁，可选包按 [跨仓库开发工作区](README.md#跨仓库开发工作区) 聚合各自 checkout，
以 editable 安装联调，再在独立环境使用各包正式依赖和 wheel 完成交付验收。

| 顺序 | 能力主题 | 模块化的能力 | 前置能力与验收 |
| --- | --- | --- | --- |
| 1 | 框架与日志 | core 运行内核、统一启动接口、本地绑定与发现，app 普通命令入口与默认资源，公共配置载体与版本服务契约、filesystem 实现、五版转换模块、自举、V5 配置、安装管理和日志 | 组合明确声明 ContribEntry 及服务绑定，ContribHub 按服务依赖和父实例关系启动、更新及释放实例，用户统一在业务文档顶层 contribs 启用，登记 lifespan / listen / offers 和静态或按配置计算的 accepts；Context 提供 offer / accept / announce；启动输入选择提供 CONFIG_STORE 的已安装入口；版本包提供 VERSIONS 组合并装配 CONFIG_CURRENT 依赖链，V1→V2→V3→V4→V5 服务生成候选和所需包，宿主校验后经载体条件提交；通过包元数据关联入口；节点升级保留模块依赖与来源 |
| 2 | 命令、注册入口与调用 | COMMANDS、插件命令、sidecar；channel / harness builder、bcc 命令 | 共享 Click 命令与 invoke 的入口扩展；sidecar 定义 SERVER／CLIENT 及 ctx handler，Unix／Windows 包提供传输服务，消费者依赖接口并自行编排调用；core 按通用插件根与绑定选择依赖，回调决定退出时间；builder 登记实体关系 |
| 3 | 数据库与迁移 | storage 公共数据操作契约、后端注册表、DB 选择层、SQLite 实现、schema 与迁移服务 | storage → 所选后端 → database → 消费者；按配置展开后端依赖，实际多个后端验证并存与选择；业务声明查询、provider 翻译；BCN／BCS 历史 ledger 分别识别，已发布迁移及 checksum 原样保存 |
| 4 | 消息与会话 | agent 消息数据、会话 target、历史、未读 cursor、发送与回执、附件、草稿、搜索和 thread follow | 消息模块使用公共查询接口并持有业务模型与索引要求；同一消息服务供 CLI / RPC 使用；新调用者使用消息 / 会话 / 搜索服务，旧 repository 在最终发行阶段统一清理 |
| 5 | 身份与协作 | sender / contact 身份、引用 refs、会话登记和审批、消息引用及关联 | 消息模块公共契约就绪；状态变化和读取遵循现有协作语义 |
| 6 | 定时与提醒 | timer 公共契约、时间轮实现模块、Reminder 的登记、到期、snooze、更新、取消、持久化与唤醒 | 节点启用时间轮并提供共享 timer；消费者直接依赖 timer 契约；提醒模块使用 db 自持业务，新调用者直接依赖其契约，旧实现最终统一清理 |
| 7 | agent 配置、执行与编排 | agent 组合入口拥有配置模型、实例构造与管理命令；执行实例处理 turn、会话绑定、调度、输出、审批和任务故障 | 默认 agent 依赖 channel／harness 公共接口及消息、协作、提醒、timer、审计；组合明确创建本地实例，可整体替换 agent 实现 |
| 8 | 渠道 provider | Telegram、Lark、WeCom 的连接、收发、附件和 provider 特有配置 | 实现公共 channel 接口并通过 builder 登记；新增渠道由自己的实现包及 UI 模块接入 |
| 9 | harness provider | Codex、Claude Code 的启动、模型、能力检查、事件解析与审批对接 | 通过 harness builder 与执行契约接入；真实 TestChannel 验证端到端行为 |
| 10 | 节点与平台管理 | 节点配置、workspace / 文件、模型与 skills 查询、版本检查、升级、系统服务和健康；agent 配置由第 7 项的 agent 插件拥有 | RPC / 命令注册可用；各管理能力自行提供服务和入口；安装与升级共用包管理机制 |
| 11 | server 两端通信 | HTTP、server／starlink、独立 BCS 配置载体与 storage、节点身份、重连、事件上报、查询及操作结果 | server-api 接受 HTTP；starlink 独立 getUpdate／send 收取请求和返回结果、accept 功能服务，按配置 listen 审计事件；真实隔离两端验证 |
| 12 | 多节点服务与数据 | fleet、历史投影、联系人、图片与 refs 服务 | 接收两端协议提供的节点事件并持久化，通过私有 HTTP 调用目标 client；直接读取原 BCS 数据源，纯 API 及带 agent 的组合均可运行 |
| 13 | 身份与访问 | 登录、OIDC、账户、角色、分享、权限、重新认证与会话策略 | HTTP / 数据 / sessions 服务就绪；身份和访问策略作为可组合服务登记；节点操作与业务 API 接入相同授权契约 |
| 14 | web 页面与配置 UI | computer / agent / 联系人 / 历史 / 搜索 / 设置页面、channel editor、模板、静态资源和页面订阅 | bazaar-contrib-web 依赖 server 与通用 GUI；页面模块自持文案；登记页面、配置 schema 和交互组件，渠道表单由渠道 UI 模块携带 |
| 15 | [contrib 市场页面](bazaar.md) | 官方与额外模块索引、包条目、详情与安装、入口配置、启停、升级和卸载 | 复用索引与管理服务；明确当前节点 / 远端节点安装目标；真实包源安装、多入口配置、后端选择与下次启动生效状态可验证 |
| 16 | 分包与版本发行 | BCN 必要包的 0.3.0 发行、独立可选仓库的兼容产物、旧实现与依赖清理、索引、发版工具与文档 | 全部能力映射有对应模块；集中式业务 repository、旧业务接口与适配层全部删除；干净安装与三平台集成通过；按包依赖顺序统一发布 |

## 全新安装的基础服务

`uv tool install bazaar-compute-node` 安装兼容范围内的 core 与下列默认组合；首次本地管理命令或 `bcn run` 经所选配置载体创建默认文档，初始 agent 插件的 `config.agents` 为空。
公共契约包随消费者依赖安装，运行入口按默认资源启用：

| 用途 | 默认运行入口或自举能力 |
| --- | --- |
| 配置与启动 | 所选配置载体、五版转换及配置加载器，默认载体为 config-filesystem |
| 状态与日志 | status、logging |
| 模块与节点管理 | contrib-manager、commands、sidecar、按平台选择的 sidecar 实现、node-management |
| 存储 | storage、sqlite、database、schema，默认 database.config.backend="sqlite" |
| agent 公共业务 | messages、collaboration、timer-wheel、reminder |
| provider 公共接口与 agent | channel-builders、harness-builders；agent 组合持有配置，明确构造执行及 provider 实例 |

具体 channel／harness provider 由用户通过 `bcn contrib add <name> --enable ENTRY` 安装并选择入口；单独 `--enable` 启用该包导出的全部入口，例如 codex、telegram。
启用保存该包所提供组合中的 builder 与运行定义；`bcn agent add` 选择已启用的 provider 并填写实体参数，`bcn run` 按该配置创建实际连接和进程。
安装与启用分别记录状态，启用配置经模型与服务依赖校验后写入顶层 contribs；provider 的连接／执行参数保存在 agent 条目的 `config.agents[].channel`／`harness`，由所选 provider 模型校验。
本地添加模块、编辑配置和添加 agent 通过短生命周期管理组合完成，沿用相同配置载体及版本服务。
完整首次使用命令见 [总计划](README.md#全新安装与默认运行组合)。首阶段收齐默认基础服务与运行前管理，用真实示例包验证配置及生命周期；具体 provider 阶段以实际安装的 Codex、Telegram 和 TestChannel 控制面完成隔离验收，最终发行从真实包源复核。

## 发行包、模块入口与内置组合

以下清单确定 `0.3` 的包边界、发行名、模块入口及内置归属。BCN 必要包首次正式发行版本为 `0.3.0`。
公共接口与默认节点模块留在 BCN：app/、core/ 与 contrib/<功能>/；下方可选能力表中的包各自在独立仓库维护版本和发行。
Python 模块名由发行名中的连字符改为下划线，例如 `bazaar-contrib-logging` 对应 `bazaar_contrib_logging`。
app 通过普通 Python 入口调用 core 的统一运行接口，提供启动输入、退出信号与业务默认资源，消费版本包的 VERSIONS。
core 作为框架运行内核，由应用直接 import，执行发现、自举装配、校验、本地绑定与完整生命周期；
配置包以普通加载器接入文档读写与版本服务，具体功能通过 contrib 定义装配。
运行模块统一登记在 `bazaar_compute_node.contribs`；表中的业务入口名同时是配置使用的 `entry` 值，配置载体入口由 --configer 选择，版本入口由发行自举组合选择；全部入口在该 group 内唯一。
一个发行包可以提供多个入口，组合声明每个入口的参数及绑定，Hub 按条目启动实例。业务页面入口在第 14 阶段接入。
跨阶段的发行包按阶段增加入口与依赖：渠道连接在第 8 阶段、编辑器在第 14 阶段，
server 两端及私有 HTTP 在第 11 阶段、多节点业务在第 12 阶段、访问保护与业务 API 在第 13 阶段、页面在第 14 阶段。每阶段的 wheel 只登记已交付入口与所需依赖；
第 16 阶段以本表的完整入口集合和依赖组合发行。
清单共 39 个官方发行包、53 个唯一模块入口，其中 `bazaar-contrib-storage` 提供两个公共运行入口。

“默认”表示由 app 的 `bazaar-compute-node` 直接声明依赖并随整体安装和升级；“server／web 依赖”表示选装对应包时安装。
业务模块定义与参数统一保存在顶层 contribs，配置载体使用启动输入、版本链使用发行自举组合先行装配；组合按已校验业务输入声明具体实例和服务绑定；包依赖安装代码，服务依赖决定实际装配。
provider 的 builder 用于发现、配置和创建实例，连接或进程由选中的 channel / harness 实例管理。
公共契约通过普通 Python 包依赖安装，消费者和实现者导入同一包里的类型与服务 key。

### 公共能力包

| 发行包 | 公共内容 | 引用者 | 阶段 | 组合归属 |
| --- | --- | --- | --- | --- |
| `bazaar-contrib-config` | ConfigStore、ConfigSnapshot、CONFIG_STORE、ConfigConverter、ConfigCandidate、CONFIG_CURRENT、CONFIG_V1…CONFIG_V5、修订条件提交与错误契约 | 公共配置加载器、filesystem／自定义配置中心载体、app 参数绑定、CLI 和管理入口 | 1 | 默认、应用组合的依赖 |
| `bazaar-contrib-audit` | 审计记录、typed 事件 key、审计发布与脱敏规则 | 活动记录、starlink 的事件上报及事件投影，按业务需要接入 | 11 | 所需业务的依赖 |
| `bazaar-contrib-storage` | `Database`、`BackendRegistry`、`STORAGE`、`DB`、`backend_key(name)`，数据／schema／显式查询／事务契约；`storage` 和 `database` 两入口，默认共享 | 全部数据库实现和持久化业务模块 | 3 | 默认、server |
| `bazaar-contrib-timer` | timer 接口、`TIMER`、定时句柄和到期／取消结果 | 时间轮、Reminder、agent、channel / harness、管理和远程模块 | 6 | 默认、server 的依赖 |
| `bazaar-contrib-channel` | `IChannel`、`ChannelBuilder`、输入／发送／回执／审批模型与 `CHANNEL_BUILDERS`；附带 `channel-builders` 共享注册入口 | agent、渠道 provider、渠道编辑器 | 2 | 默认；web 的编辑器依赖 |
| `bazaar-contrib-harness` | `IHarness`、`HarnessBuilder`、会话／turn／输出／审批／能力模型与 `HARNESS_BUILDERS`；附带 `harness-builders` 共享注册入口 | agent、Codex／Claude Code 等执行 provider、模型查询 | 2 | 默认 |

这六个包导出公共契约和可直接调用的公共逻辑，通过 `import` 使用。
`bazaar-contrib-storage` 还声明 `storage`、`database`：前者提供后端注册表，后者按配置提供 DB。
channel 与 harness 的接口和登记服务分别归上述公共包；具体 provider 依赖这些包。
其他能力的公共类型与 key 由对应功能包导出：命令注册归 invoke，跨进程 SERVER／CLIENT 与 ctx handler 归 sidecar，
消息与协作归各自业务包，HTTP 与 API 注册契约归 http，UI 注册契约归 gui；starlink 的 HTTP 请求格式和请求关联由通信包公开定义，server-api 按同一格式提供对端。
具体 provider 依赖相应公共能力包，例如 PG 与 SQLite 共同依赖 `bazaar-contrib-storage`，业务同样依赖它。

### 默认节点模块包

| 发行包 | 模块入口与运行范围 | 拥有的能力与主要依赖 | 阶段 | 组合归属 |
| --- | --- | --- | --- | --- |
| `bazaar-contrib-config-filesystem` | `config-filesystem`：共享，自举选择 | 依赖 bazaar-contrib-config，提供 CONFIG_STORE；TOML、修订复核、锁及原子替换 | 1 | 默认；BCN 主配置的默认载体 |
| `bazaar-contrib-config-versions` | `config-v1`、`config-v2`、`config-v3`、`config-v4`、`config-v5`：共享，VERSIONS 组合 | 依赖 bazaar-contrib-config，逐版提供转换服务；Vn 依赖 V(n-1)，V5 生成参数映射及所需包，配置加载器消费 CONFIG_CURRENT | 1 | 默认；BCN 主配置的版本链 |
| `bazaar-contrib-status` | `status`：共享 | IContribStatus、CONTRIB_STATUS 与 snapshot()；依赖 core，读取宿主记录的实例状态与故障来源 | 1 | 默认、server |
| `bazaar-contrib-logging` | `logging`：共享 | 标准 logging 输出、实例来源、异常堆栈、线程队列与文件轮转；依赖 core | 1 | 默认、server |
| `bazaar-contrib-manager` | `contrib-manager`：共享 | 模块索引、包管理、入口与组合配置导入、依赖与来源清单、配置迁移所需包解析及整体升级；服务端页面经节点接口访问 | 1 | 默认；节点管理 |
| `bazaar-contrib-invoke` | `commands`：共享 | Click 命令登记与命令入口扩展；功能包持有命令与文案 | 2 | 默认、server |
| `bazaar-contrib-sidecar` | `sidecar`：共享 | SERVER／CLIENT 公共契约；接受 SERVER 并暴露 ctx／返回对象的公开 handler，消费方自行编排 | 2 | 节点默认；消费插件的接口依赖 |
| `bazaar-contrib-sidecar-unix` | `sidecar-unix`：共享 | offer SERVER／CLIENT，以 Unix socket 实现会话与请求；依赖 sidecar 接口 | 2 | Linux/macOS 默认 |
| `bazaar-contrib-sidecar-windows` | `sidecar-windows`：共享 | offer SERVER／CLIENT，以 Windows named pipe 实现会话与请求；依赖 sidecar 接口 | 2 | Windows 默认 |
| `bazaar-contrib-sqlite` | `sqlite`：共享 | 依赖 STORAGE、登记 sqlite 后端、提供 backend_key("sqlite")；持有节点连接、公共操作翻译和已发布节点迁移资源 | 3 | 默认；节点初始存储后端 |
| `bazaar-contrib-schema` | `schema`：共享 | 模型／索引注册、迁移顺序、版本和 ledger；通过 DB 执行业务 schema 变更 | 3 | 默认、server |
| `bazaar-contrib-messages` | `messages`：共享 | 消息／会话、历史、未读、搜索、附件、草稿、收发回执和 thread follow；依赖 storage、schema、调用与审计契约 | 4 | 默认；server 投影的契约依赖 |
| `bazaar-contrib-collaboration` | `collaboration`：共享 | sender／contact、refs、会话登记、审批及可见范围；依赖消息、storage 和调用契约 | 5 | 默认；server 联系人的契约依赖 |
| `bazaar-contrib-timer-wheel` | `timer-wheel`：共享 | 提供 TIMER，持有时间轮与驱动任务 | 6 | 默认、server |
| `bazaar-contrib-reminder` | `reminder`：共享 | Reminder 的模型、持久化、到期、snooze、更新、取消；依赖 storage、schema、timer 和消息 | 6 | 默认 |
| `bazaar-contrib-agent` | `agent`：共享 组合；内部执行定义为 AGENT | 拥有 agent 配置及管理命令、实体展开、默认 BCN 指令、bcc 工具环境、上下文与通讯编排；依赖 channel／harness 接口、消息、协作、Reminder、timer | 7 | 默认、可替换；配置与行为整体由所选 agent 实现拥有 |
| `bazaar-contrib-node-management` | `node-management`：共享 | 首阶段接收已实现的 run 命令、配置激活与退出等待；后续节点配置、workspace／文件、模型／skills、健康、系统服务与升级按阶段交付；agent 配置及命令由 agent 包拥有 | 2、10 | 默认 |

### server、web 与可选能力模块包

以下入口由拥有它们的组合明确创建，默认共享服务；provider 的运行输入由 agent 组合提供。

| 发行包 | 模块入口与运行范围 | 拥有的能力与主要依赖 | 阶段 | 组合归属 |
| --- | --- | --- | --- | --- |
| `bazaar-contrib-telegram` | `telegram-builder`：共享；`telegram`：接受 channel 输入；`telegram-ui`：共享 | Telegram 收发、附件、配置与编辑器；运行入口实现 IChannel，依赖 channel、timer，页面入口依赖 GUI 与访问契约 | 8、14 | 可选；web 的编辑器依赖 |
| `bazaar-contrib-lark` | `lark-builder`：共享；`lark`：接受 channel 输入；`lark-ui`：共享 | Lark 连接、收发、附件、配置与编辑器；运行入口实现 IChannel，依赖 channel、timer，页面入口依赖 GUI 与访问契约 | 8、14 | 可选；web 的编辑器依赖 |
| `bazaar-contrib-wecom` | `wecom-builder`：共享；`wecom`：接受 channel 输入；`wecom-ui`：共享 | WeCom 连接、收发、附件、配置与编辑器；运行入口实现 IChannel，依赖 channel、timer，页面入口依赖 GUI 与访问契约 | 8、14 | 可选；web 的编辑器依赖 |
| `bazaar-contrib-codex` | `codex-builder`：共享；`codex`：接受 harness 输入 | Codex 进程、事件解析、模型／能力与审批；实现 IHarness，依赖 harness、timer | 9 | 可选；实际实例由 agent 配置选择 |
| `bazaar-contrib-claude-code` | `claudecode-builder`：共享；`claudecode`：接受 harness 输入 | Claude Code 进程、事件解析、模型／能力与审批；实现 IHarness，依赖 harness、timer | 9 | 可选；实际实例由 agent 配置选择 |
| `bazaar-contrib-http` | `http` | HTTP route／middleware／静态资源登记、监听能力与请求生命周期；提供 HTTP，导出共用 HTTP 与 API 注册契约 | 11 | server 依赖 |
| `bazaar-contrib-server-config-filesystem` | `server-config-filesystem` | 依赖 bazaar-contrib-config，在 server 子组合的私有绑定中提供 CONFIG_STORE；直接读写 ~/.bcs/config.toml，持有自己的锁、修订和原子保存 | 11 | server 的默认配置载体，可替换为额外配置中心 |
| `bazaar-contrib-server-sqlite` | `server-sqlite` | 依赖 bazaar-contrib-storage 与服务端 CONFIG_STORE；在服务端注册表登记 sqlite，连接原 ~/.bcs/bcs.sqlite3，持有 BCS 历史迁移资源 | 11 | server 的默认存储，可替换为额外存储实现 |
| `bazaar-contrib-server` | `server`、`server-api` | server 用本地绑定的子组合装配独立 BCS 配置与业务实例；server-api 接受 HTTP、提供 API 与 starlink 的协议对端；按配置依赖 DB／schema／timer 及目标功能契约；保留原 BCS 协议与数据源 | 11、12、13 | 可选服务端组合；多节点业务随阶段加入 |
| `bazaar-contrib-starlink` | `starlink` | 节点主动连接目标 server，自持 getUpdate 请求循环与 send 结果发送、身份、请求关联和重连；accept 节点功能服务，按配置 listen 审计事件上报 | 11 | 可选；默认停用，配置 server 后启用 |
| `bazaar-contrib-fleet` | `fleet`、`fleet-ui` | computer／agent 登记、模型、事件投影、状态与配置；依赖 storage、schema、审计事件及 API，通过 server 私有 HTTP 请求目标节点；API 登记依赖访问服务，页面依赖 GUI 与访问服务 | 12、13、14 | server 的服务依赖；web 的页面依赖 |
| `bazaar-contrib-history` | `history`、`history-ui` | 聚合消息／会话投影、历史、搜索、图片、refs；依赖 storage、schema、fleet、消息、API 与访问服务，页面依赖 GUI 与访问服务 | 12、13、14 | server 的服务依赖；web 的页面依赖 |
| `bazaar-contrib-contacts` | `contacts`、`contacts-ui` | 聚合联系人、登记／审批与消息关联；依赖 storage、schema、fleet、history、协作、API 与访问服务，页面依赖 GUI 与访问服务 | 12、13、14 | server 的服务依赖；web 的页面依赖 |
| `bazaar-contrib-access` | `access`、`access-ui` | 账户、角色、权限、分享、登录、OIDC、重新认证和 sessions；依赖 storage、schema、HTTP／API，提供访问策略，页面依赖 GUI | 13、14 | server 的服务依赖；web 的页面依赖 |
| `bazaar-contrib-gui` | `gui` | 页面／导航／编辑器登记、布局、模板、静态资源、订阅和配置表单；依赖 HTTP，提供 UI | 14 | web 依赖；可供其他页面模块复用 |
| `bazaar-contrib-web` | `web` | 在 server 子组合中接受 API、UI 与访问服务，提供管理界面、业务页面、渠道编辑器和 contrib 市场；发行依赖包含对应页面包 | 14、15 | 独立选装页面组合 |
| `bazaar-contrib-bazaar` | `bazaar` | /bazaar、索引、详情、目标选择、包安装与配置管理；依赖 API、UI、access、fleet、状态查询与模块管理 | 15 | web 的页面依赖 |

业务包持有自己的模型、schema、查询、服务、API 方法及页面；业务页面依赖所属业务服务、UI 与访问服务。
server 导出 API，业务模块按公共契约登记各自方法与输入模型。新 API 和页面由功能包登记，应用入口按登记结果提供服务。
HTTP／GUI 的公共协议供功能模块导入，功能包通过这些协议引用 API／UI key；其发行依赖与 server／web 组合形成单向关系。

### 应用、核心与组合资源

| 发行包 | 目录与职责 | 入口 | 阶段 |
| --- | --- | --- | --- |
| `bazaar-core` | core/src/bazaar_core/；框架运行内核、ContribHub.run、发现与自举装配、通用校验、服务／事件、本地绑定、依赖与完整生命周期 API | 由 Python import 使用 | 1 |
| `bazaar-compute-node` | app/src/bazaar_compute_node/；业务输入服务与明确的实例构造、通用命令分派、启动输入、兼容范围内的默认依赖、defaults.toml，消费版本包的 VERSIONS | bcn 调用 core 运行接口、bcc 调用 | 1 |

app 按兼容范围直接依赖 bazaar-core 与默认 contrib；core 的包依赖保持通用。
应用基础先作为独立任务交付：run(bootstrap, load) 启动自举条目及提供 COMPOSITION 的加载器，bcn run 启用已交付的 status；
随后在相同应用入口接入配置发现、载体、版本链与默认配置资源。
server／starlink／web 与具体 provider 等可选包在各自独立仓库开发，可选安装并各自持有依赖与配置资源。
BCN 必要包的 project.version 使用共同版本，首版为 0.3.0；这些包随 BCN 发行升版。
仓库内正式依赖按实际所需的最低版本声明三段式兼容范围，首版为 ~=0.3.0；仅使用更新接口时提高对应依赖下限。
独立官方可选包和第三方包维护自己的版本，并声明 core 最低版本／上限和所需公共接口包的兼容范围。
旧 bazaar_compute_server 的业务与资源按所属能力移入 contrib 后，bcn run 通过这些模块提供服务端组合。

app 直接安装 config-filesystem 和 config-versions，后者以 VERSIONS 保存版本入口与服务清单，配置契约通过 CONFIG_CURRENT 指定当前目标；
宿主按启动输入选定载体、按依赖装配版本链，再读取完整配置，根实例贯穿业务与配置操作。
默认顶层 contribs 启用状态查询、日志、模块管理、调用、channel／harness 注册、storage、SQLite、database、schema、消息、协作、时间轮、
Reminder、agent 及节点管理。agent 组合拥有 `config.agents`，明确构造执行及 provider 实例；
用户选装 provider 后，启用该包组合中的 builder／运行定义，具体 channel／harness 由 agent 的 provider 选择及 builder 登记关系匹配创建。
服务端 API 与页面包作为可选组合安装，普通节点按需安装 starlink 包，配置目标 server 并启用 starlink；默认节点组合不启用远端连接。
用户在节点主配置中启用包导出的 server 入口；server 自己的组合资源包含独立配置载体、
状态查询、日志、调用、storage、server-sqlite、database、schema、timer、http、server-api、fleet、history、contacts、access。
用户选择包导出的 starlink 入口并填写连接参数；事件上报与远端请求分别选择，按启用能力声明功能服务依赖。
用户在节点主配置中启用包导出的 web 入口；web 在 server 子组合中启用 gui、业务页面、渠道编辑器与 bazaar。
纯 API 或 web 部署可使用空的 `agent.config.agents`；新增 agent 后，组合展开其实体，宿主为内部 AGENT 定义及选定 provider 创建所属实例。

首次创建节点配置使用 app 提供的 bazaar_compute_node/defaults.toml 资源，由配置包普通加载器经 core 校验后通过所选 CONFIG_STORE 条件创建 version="5"、storage、sqlite、database，明确 database.config.backend="sqlite"，
并写入 agent 的 `config.agents=[]` 及内置官方索引提取后的本地路径。
历史配置的转换与提交规则见 [V5 配置与版本转换](framework.md#v5-配置与版本转换)。
可选组合资源是普通 TOML，CLI／页面导入后形成完整候选配置，经相同 Pydantic 参数校验、服务绑定和依赖图检查后保存。
节点组合导入复用已有共用条目，保留用户参数、DB 选择及停用选择；同一入口已有多个条目时由用户选择更新目标。
server／web 的节点启用条目与服务端独立配置分别保存，BCS 原字段直接由服务端读取。
所需服务仍被停用时，导入结果列出缺失依赖，由用户调整候选配置后提交。
安装包展示资源和入口，用户选择并保存启用配置；后续启动始终读取保存结果。
`bcn run` 对全部组合使用相同的载体自举和业务装配流程，filesystem 支持 --config <path>，配置中心从启动参数取得连接信息；实例创建关系及服务依赖决定生命周期，节点能力由已启用模块决定。

必要包显示 BCN 发行版本及实际安装版本，所有包显示各自的兼容范围。
整体升级将目标 app、其 core／必要依赖的兼容范围及保存的全部包要求交给自身 uv 一次解析，保留用户指定的兼容旧依赖、包源、版本要求、配置和后端选择。
必要包随 BCN 升版；独立可选包按兼容范围解析，单包修复由所属仓库发行。卸载同时检查包依赖与启用配置。

### channel 与 harness 公共接口

`bazaar-contrib-channel` 定义 channel 接口，`bazaar-contrib-telegram` 实现它；`bazaar-contrib-harness` 定义 harness 接口，`bazaar-contrib-codex` 实现它。
Lark／WeCom 实现同一 channel 接口，Claude Code 实现同一 harness 接口。默认或自定义 agent 依赖两个公共接口包，通过所选实现收发消息和执行任务。
接口包导出协议、模型与共享 service key；具体实现包声明正式依赖，在 lifespan 中提供相应接口对象。

`bazaar-contrib-channel` 拥有 provider 无关的 `IChannel`、`CHANNEL`、`ChannelBuilder`、输入消息、发送请求、交付回执、附件、身份和渠道审批载荷。
`bazaar-contrib-harness` 拥有 `IHarness`、`HARNESS`、`HarnessBuilder`、会话与 turn 输入、指令和工具环境、可取消的输出流、审批事件、能力／模型／skills 查询及关闭结果。
输入与结果在公共接口边界由 Pydantic 校验；双方通过公共审批标识关联请求和结果。
审批请求／决定／结果由 harness 公共包拥有，channel 使用同一载荷展示审批并收集结果。
agent 将 harness 输出映射为 channel 请求；公共 harness 包依赖 core，公共 channel 包依赖 core 与 harness 的共用审批契约，具体 SDK 类型在 provider 内部转换。

两个公共包分别提供 共享 `channel-builders`／`harness-builders`，导出 `CHANNEL_BUILDERS`／`HARNESS_BUILDERS` 服务 key。
builder 登记 provider 标识、配置模型、运行入口及能力查询；默认 agent 根据自己的配置选择已启用定义，通过登记契约校验实体参数并构造实例条目与 provider 服务绑定。
公共 `ChannelBuilderConfig`／`HarnessBuilderConfig` 基类以 `ClassVar` 导出 kind、entity_model 与运行入口；具体 builder 的 Pydantic 配置类声明这些元信息，候选从已安装入口定义取得模型，在资源启动前校验实体参数。
公共包还导出按 kind 缓存的 `channel_builder_key(kind)`／`harness_builder_key(kind)`，具体 builder 提供对应 key，agent 按配置声明所选 builder 依赖，确认其已启用后装配。
具体 channel 实例在 lifespan 中持有连接，具体 harness 实例在 lifespan 中持有进程／会话，运行实例通过公共登记服务发布所属 agent 的绑定与状态。
agent 的内部执行任务通过这些服务等待所选实例就绪，再消费输入、执行 turn 和交付输出；自身 lifespan 的服务登记与 yield 先完成，资源在所属实例的 lifespan 退出时释放。

调用与公共接口任务将运行输入服务、runtime builder／接口名称统一改为 harness。
已交付 Task 3／4 的检查项记录当时名称；V4 `agent.runtime` 数组在 V5 转换为默认 agent 配置中的 `harness`，原有身份、顺序和参数逐项保留。
研究依据为现有 `src/bazaar_compute_node/core/channel.py`、`core/runtime.py` 的 channel、builder、session、turn、输出流和审批契约；新公共包按上述归属拆出类型和依赖。

### 默认 agent 行为与替换

`bazaar-contrib-agent` 的公开 `agent` 入口拥有完整配置模型、实体装配、管理命令及默认 BCN agent 行为。
配置统一保存在该入口的 `contribs[].enabled[].config`，包含 `agents` 列表及各 agent 的 channel／harness 选择、参数和凭据引用：

```toml
[[contribs]]
[[contribs.enabled]]
entry = "agent"
[contribs.enabled.config]
agents = []
```

app 与公共 ConfigDocument 只处理统一 contrib 列表；默认 agent 模型和实体规则由所选 agent 插件解释。
agent 组合用普通 Python 函数从已校验输入构造全部已知实例及稳定服务绑定，配置候选预检显式调用同一构造逻辑。lifespan 通过 `ctx.enable` 管理执行与 provider 子资源；指定私有服务和消息事件使用 local，外部 DB 和公共审计可共享。动态子组合在启用前单独校验，框架接口见 [本地绑定与实例声明](framework.md#本地绑定与实例声明)。
`bcn agent add`、列表、编辑、删除由该插件登记，修改自己条目的 `config.agents`，经 CONFIGURATION 校验完整候选并条件提交。

该包拥有 developer instruction、bcc 工具环境及身份／会话绑定、模型上下文、消息与提醒处理、审批及输出交付的编排，依赖公共 channel／harness、消息、协作、存储、timer 和 Reminder。
现有 `src/bazaar_compute_node/core/instruction.py`、`resources/developer_instructions.md` 的渲染与资源，以及旧 runtime 中构造 BCN 会话的业务逻辑，移入新 agent 实现；旧源码按统一清理阶段保留。
功能插件提供 typed 服务、事件、配置模型和能力说明，由所选 agent 明确读取并组织成其模型输入；提示词的语义与组合由 agent 拥有。
harness provider 接收 agent 已组织的指令、运行输入与工具环境，管理模型协议、进程和事件映射。

app 的 defaults.toml 选择默认 agent 入口；用户替换该启用条目并导入自定义组合后，配置模型、命令和行为由新插件拥有，宿主按其展开结果、服务绑定、依赖和生命周期装配。
消息、存储与定时等小插件可由多个组合共享；自定义 agent 自行选择实际使用的接口、指令、工具与通讯方式。
以真实自定义 agent 的独立 wheel 验证替换配置模型、命令、实体装配、指令、工具和通讯行为；真实外部 harness 的 e2e 使用 TestChannel 控制面。

### 插件文案

功能包持有自己的命令帮助、提示、通知与页面文案，默认使用英语。
文案与功能实现一同交付，命令通过 COMMANDS 登记，页面通过 UI 契约登记。

### 默认 provider 的替换规则

`bazaar-contrib-storage` 提供注册表和选择层，SQLite、PG 等实现者使用同一 `Database` 契约：

| 入口 | 所属包 | 依赖 | 提供 | 资源职责 |
| --- | --- | --- | --- | --- |
| storage | bazaar-contrib-storage | — | STORAGE | 每个存储组合的后端注册表 |
| sqlite | bazaar-contrib-sqlite | STORAGE | backend_key("sqlite") | 创建连接、登记 sqlite 后端，退出撤销登记并关闭连接 |
| pg（第三方示例） | bazaar-contrib-pg（假定包名） | STORAGE | backend_key("pg") | 创建连接、登记 pg 后端，退出撤销登记并关闭连接 |
| database | bazaar-contrib-storage | STORAGE、backend_key(config.backend) | DB | 根据选择提供同一个后端对象，管理 DB 服务登记 |

`backend_key(name)` 由公共包按名称缓存，供实现者和选择层引用同一 typed key。
BackendRegistry 按后端名保存 `Database` 对象，注册上下文管理器将登记存续期绑定到后端的 lifespan。
同一组合的注册表、后端和选择层处于同一组合，各宿主进程持有自己的注册表。
消息、Reminder、聚合业务等消费者的包依赖是 `bazaar-contrib-storage`，声明 `accepts=[DB]` 并通过 `ctx.accept(DB)` 使用服务。
业务模型与查询使用公共数据操作契约，新增后端的翻译实现集中在后端模块。

PG 后端入口的生命周期写法为：

```python
from contextlib import asynccontextmanager

from bazaar_contrib_storage import STORAGE, backend_key
from bazaar_core import Context, Contrib
from pydantic import BaseModel


class PGConfig(BaseModel):
    dsn: str


PG = backend_key("pg")


@asynccontextmanager
async def pg_lifespan(ctx: Context, config: PGConfig):
    db = await open_pg(config.dsn)
    try:
        with ctx.accept(STORAGE).register("pg", db):
            ctx.offer(PG, db)
            yield
    finally:
        await db.close()


pg = Contrib(
    config=PGConfig,
    accepts=[STORAGE],
    offers=[PG],
    lifespan=pg_lifespan,
)
```

安装 PG 后从发行元数据选择并启用其入口、填写连接参数，再更新已有 database 的 `backend="pg"`。
filesystem 用户手写 `config.toml`，CLI／页面经当前 CONFIG_STORE 保存，使用相同配置模型。SQLite 条目可以保留，使两连接并存；
用户停用 SQLite 时从启用列表移除该条目，包继续作为官方依赖安装。
入口名由发行元数据取得，后端名由模块的注册代码和服务声明确定，两者可以不同。
具体初始配置、PG 配置和索引对应关系见 [框架的配置流程](framework.md#默认配置生成与后端切换)。

database 声明 `accepts=lambda config: [STORAGE, backend_key(config.backend)]`，同步计算实际依赖集合。
宿主先校验配置并计算依赖，后建立服务图；所选后端完成登记并到达 yield 后，再启用 database、schema 和业务消费者。
停机按依赖逆序执行：先结束业务和 schema，再撤销 DB，随后后端撤销登记、关闭连接，最后结束注册表。
同名后端重复提供相同 backend key、重复提供 DB 选择入口、所选后端缺失或依赖成环时，
启动前诊断包含具体入口、配置位置、服务 key 与相关实例。
配置选中的后端连接失败时，本次启动回收已创建资源并报告该后端故障。
整体升级保留 PG 的额外包来源、版本要求、连接参数及 database 的选择。

timer 的替代 provider 直接提供共享 `TIMER`；在目标组合停用原实现、启用新实现，消费者继续引用同一 key。
同一 key 和绑定身份只能有一个提供者；子组合继承父实例的其他绑定，通过 accepts 使用共享 DB 和 TIMER。

### 数据库阶段的交付与验收

第 3 阶段交付 `bazaar-contrib-storage` 的公共查询契约、BackendRegistry、共享 key 和两个入口，
以及 `bazaar-contrib-sqlite`、`bazaar-contrib-schema` 的独立 wheel、默认存储配置和对应文档。
公共包依赖图、schema 执行和实际业务数据操作按以下流程验证：

1. 从构建 wheel 的元数据加载 storage、sqlite、database、schema，使用隔离配置及临时数据库启动存储组合。
2. 业务模块通过公共 DB 声明模型、显式查询及批量关联读取，验证查询、schema、事务与持久化结果。
3. 替代后端验收使用额外 PG 模块包和隔离 PG 数据库；模块依赖 bazaar-contrib-storage，实现相同操作及后端语义。
4. 启用两后端并更新已有 database 条目的选择，重启后由相同消费者执行相同业务流程，验证模型与查询结果。
5. 分别保存两后端并存和停用 SQLite 的配置，观察实际连接启动、消费者结束、登记撤销和连接关闭的顺序。
6. 通过完整组合升级验证额外包来源、版本要求、连接参数及后端选择继续有效，已发布迁移和 checksum 保持原样。

加载与配置接口提供所选后端缺失、重复后端名、重复 DB 服务、模块类型与目标运行上下文不匹配的诊断。
普通节点、纯 API 节点及混合节点分别验证各自注册表，agent 消费与节点相同绑定的 DB。

## 统一宿主与 server／web 组合

### 服务契约与启动依赖

所有进程由 `bcn run --config <path>` 启动相同应用入口。模块统一在顶层 contribs 中声明，运行实例由组合明确创建并选择 local 绑定；agent 是可选子树。
普通节点、仅 API、带 web 的管理节点、同时运行 server 与 agent 的节点都通过同一发现、校验、装配和退出流程。
能力按模块及其配置表达，原 BCS 的集中管理能力由可选 API／页面组合承接。
0.2 BCS 升级继续直接读取独立的 ~/.bcs 配置、数据库及登录密钥，原结构和底层查询保持历史数据的含义。
同一 ContribHub 用 server 的本地组合装配服务端配置载体、storage、server 与 web，各自实例按服务依赖启动和退出；节点与服务端分别选择数据库后端和连接。
完整路径、读取规则、历史 ledger 与协议要求见 [BCS 连续升级](framework.md#02-bcs-配置与数据源连续升级)。

| 提供者 | 提供 | 消费者与职责 |
| --- | --- | --- |
| http | HTTP | 提供路由与监听能力；server-api 启动 API 监听与两端私有 HTTP 入口，gui 登记页面及静态资源 |
| server-api | API、上报事件 | 在 server 组合管理私有 HTTP、节点请求及结果，提供业务方法登记和分发；收到上报后按公共审计契约 announce，fleet／history 等消费 |
| 节点功能模块 | 功能服务、审计事件 | offer 消息、管理等服务，由 client accept 后调用；announce 业务审计事件，由 logging 与 client 并列 listen |
| starlink | 两端协议的节点实现 | 在节点 主动 getUpdate 收取请求、accept 功能服务并执行、send 返回结果；按配置 listen 审计事件上报，需要广播的通知在节点内 announce |
| access | 访问服务 | 校验可信请求身份、目标与权限；业务 API、页面和节点操作消费同一策略 |
| gui | UI | web、业务页面、渠道编辑器及 bazaar 登记布局、页面、组件和订阅 |

HTTP 与 API 的类型和 key 由 `bazaar-contrib-http` 导出，server-api 在本地组合提供 API 的实现。
UI 的类型和 key 归 `bazaar-contrib-gui`；starlink 公开两端 HTTP 请求格式并持有节点身份、请求关联及 getUpdate／send 生命周期，server-api 提供对应 HTTP 入口。
业务包依赖这些契约及自身业务依赖，server／web 发行包再依赖功能包；业务包导入公共契约即可登记扩展。

节点与 server 分别按具体实例的绑定和服务依赖装配，配置条目顺序可以任意：

```text
storage → 所选后端 → database → schema → 业务持久化／client 上报队列
节点功能服务 + timer → starlink
http → server-api(API) → access／业务 API
http → gui(UI)
API + UI + 访问服务 → 业务页面／渠道编辑器／bazaar
共享服务 + builder → agent → channel／harness
server 组合：server-api(API) + gui(UI) + access → web／页面
```

图中为主要关系，各模块的完整 accepts 包含表中业务前置。access 接受 API 登记身份入口并提供访问服务；
其他受保护入口在访问服务可用后登记。server-api 接受 HTTP，在 yield 前启动监听并提供 API，退出时关闭监听；web 接受本地 API、UI、访问服务，页面按同一依赖图装配。
server 的发行依赖和配置资源选择业务模块，业务模块消费 API，形成先提供注册服务、再登记业务的运行关系。
完整组合的入口、授权、投影和页面就绪后，应用才发布 ready 并开放外部请求。

server 与 client 的私有 HTTP 承担节点登记、请求下发、事件上报及调用结果回传。
节点业务 announce 审计事件 → client listen 并入队 → 私有 HTTP → server announce 已接收事件 → fleet／history 投影。
业务 API 查询或下发 → 私有 HTTP → 目标 client accept 功能服务并调用 → 私有 HTTP 返回结果；通知可由 client 在节点内 announce。
client 负责重连、上报缓冲和确认，server 负责接收去重；请求使用独立的关联 ID、超时及错误结果，业务另行发布审计事件。
事件保持原节点、agent、事件标识和顺序，client 只上报自己节点的来源，server 的投影消费保持来源身份。
client 的 listen 回调只完成入队，网络发送由所属后台任务执行；其接受的服务按配置声明依赖，查询插件状态使用 status 插件 offer 的 CONTRIB_STATUS。
每个模块在自己的 lifespan 中登记方法、监听器与连接句柄，退出撤销所属登记、完成在途请求并释放资源。
停止按依赖逆序结束页面与业务入口、client 请求及上报任务、server-api 的 API 监听和 HTTP 服务；持久化消费者结束后才退出所属组合的 DB、后端与配置载体。

### 并行运行与分开部署

bcn run 按声明装配并启动模块，lifespan 完成初始化、到达 yield 后继续装配其余模块。
HTTP、连接与 agent 的持续执行由所属模块管理后台任务或子进程，具体方式由实现选择。
任务故障接入既定通知契约，退出时完成在途操作、结束并等待所属任务／进程，再释放资源。
生命周期要求见 [并行运行与资源清理](framework.md#并行运行与资源清理)。

独立部署使用多份配置分别执行 bcn run，各进程使用自己的数据库、端口、socket 和运行目录，server 与 client 通过私有 HTTP 连接。
混合组合验收在 agent 等待长任务时继续请求 API、读取页面，在连接或页面订阅等待时继续执行 agent turn；
分进程组合验收实际连接与结果传递。两种组合都验证健康、故障通知和全部资源退出。

### 安装与配置的用户流程

1. 普通节点安装宿主，首次生成默认配置，执行 bcn run；按配置创建 agent。
2. 需要 API 的节点从索引安装 bazaar-contrib-server，其发行依赖安装 HTTP、独立配置及存储和多节点业务包。
3. 用户在节点主配置启用包导出的 server 入口；server 启动后从自己的配置载体读取 BCS 监听、访问和存储设置。
4. 使用相同 bcn run 启动；agent 列表为空时运行纯 API，有 agent 配置时同时提供 API 与执行能力。
5. 需要页面时安装 bazaar-contrib-web，在节点主配置启用 web；web 在 server 子组合中装配 GUI 和页面，保存后在下一次启动生效。
6. 其他节点按需安装 bazaar-contrib-starlink，填写目标 server 地址与 token 引用后启用 starlink，分别选择事件上报及远端请求能力。

既有 0.2 BCS 在安装并启用 server／web 后，启动时直接读取原独立配置和数据。
默认资源位置继续为 ~/.bcs/config.toml、~/.bcs/bcs.sqlite3、~/.bcs/session.key；
原 BCS 自定义配置位置由 server 的 config_options.path 保留，数据库和密钥继续按原数据目录解析。

模块管理服务从已安装发行元数据展示入口及参数模型，用户选择后提交完整候选配置并校验。
配置资源可以用于新建独立配置，也可以合并已有配置；每个宿主进程拥有独立服务实例，各组合管理自己的本地绑定和生命周期。
包依赖图交给 uv，服务依赖图交给 ContribHub，安装状态、保存配置和实际运行分别展示。

### 各阶段交付与验收

- 第 11 阶段交付 http、server／server-api／starlink 及独立 server-config-filesystem／server-sqlite，以隔离的真实两端验证公开请求格式、主动 getUpdate、独立 send、身份、事件上报、服务查询、请求及结果、确认、重放、去重和退出；query 直接调用真实 offer 服务，业务操作独立产生审计事件；默认节点在配置 server 后启用 starlink，并补充 V4 的 audit／control／server 字段迁移。
- 第 12 阶段交付 fleet、history、contacts，直接读取 BCS 原配置并查询原数据库，验证配置中心与存储替换；同一 bcn run 验证空 agent 的 API 组合、server 与 agent 各自独立配置／DB 的同进程并行及分进程通信，同时验证 0.2 节点原地址／token／协议的上报与控制。
- 第 13 阶段接入 access 与受保护的节点操作 API，保留 BCS 原账户、密码哈希、角色、分享、OIDC 设置及 session.key；验证旧账户与有效 cookie、请求身份、登录、分享、节点操作与数据权限；各受保护入口声明访问服务依赖。
- 第 14 阶段交付 gui、web 与业务页面，验证原 BCS 历史、refs、账户主题及升级后的新事件在同一数据源可查询，完成独立 API 运行、页面组合和功能模块新增接口／页面；注册撤销及生命周期可观察。
- 第 15 阶段将 contrib 市场接入 web 组合，验证当前节点和远端节点的真实模块操作。
- 第 16 阶段删除原 bcs CLI、独立应用、server sink 和中心 Control 装配，收齐 bazaar-contrib-server／bazaar-contrib-web 正式产物、组合配置与真实 0.2 BCS 原数据源升级证据；原目录、数据及不可变迁移资源按所属模块继续保留。

完整组合验收使用实际 wheel、隔离配置、数据库、端口和进程。分别验证普通节点、纯 API、web 和混合组合，
停止 web 后重启仍可提供 API；停用 server-api 的候选配置列出依赖 API 的 web 与业务条目，需要同步调整后保存。
整套升级保留可选包来源、版本要求、配置、后端选择及 agent 子树。各阶段按其详细计划的 Tasks 串行交付和 review。

## 公共扩展入口的实现原则

### 模块声明与生命周期

功能包导出 Contrib 定义，登记独立的 lifespan 与固定 listen。offers 声明服务，accepts 是静态序列或从已校验配置计算的依赖；每个 ContribEntry 对应一个具体实例。
Context 提供 id、name、快照以及 offer／accept／announce；组合用 ctx.enable 管理子资源，local 为选定服务和事件建立私有绑定，其他绑定继承创建者。
同一 key 和相同绑定的提供者才能满足依赖。ContribHub 在启动资源前检查整个已声明图，按实际提供者与创建者关系启动、传播故障并逆序退出；动态组合在启用边界校验。
普通加载器提供 COMPOSITION，配置和服务就绪后启用监听。退出先等待或取消回调，再释放子实例及依赖资源。

### 默认组合的实例归属

| 能力 | 创建与绑定 | 资源职责 |
| --- | --- | --- |
| 配置载体与版本转换 | 自举组合，默认共享服务 | 先提供 CONFIG_STORE 与 CONFIG_CURRENT；业务和配置操作结束后退出 |
| 存储、timer、消息、协作和 Reminder | 启用一个共享提供者 | 功能包管理注册表、连接、驱动和业务资源，消费者声明相应依赖 |
| channel／harness builder | 共享登记服务 | agent 从业务输入选择实现，明确创建实际运行实例 |
| agent 执行编排 | 每个 agent 一个明确实例，私有输入和消息事件 | 管理调度、会话、审批、输出与空闲状态 |
| channel／harness 运行实例 | 所属 agent 的本地子组合 | 管理自己的连接、进程和任务，先于创建者退出 |
| 节点日志与管理 | 共享生命周期与服务提供者 | 读取实例身份、输出标准日志和提供节点服务 |
| server／web | server lifespan 的本地组合 | 私有 CONFIG_STORE、STORAGE、DB、HTTP 和 API；管理 ~/.bcs 与业务数据 |

日志接收标准 logging 记录；私有消息事件由创建组合选择 local，发布者与消费者使用相同绑定。

### 配置载体与版本转换

bazaar-contrib-config 导出中立的文档快照、读取、按修订写入、版本服务及候选与错误契约；filesystem 默认载体与第三方配置中心导入同一 CONFIG_STORE。
--configer 选择已安装入口，--configer-options 可重复提供原始参数，--config 的帮助说明标明其为同组选项的别名；两种写法混用时保留传入顺序。
未指定时入口默认为 config-filesystem，参数默认为空序列；格式解析、Pydantic 校验和默认值归所选载体，filesystem 使用最后一个路径或原默认配置位置。
入口发现来自发行元数据，装配边界校验 API、offers、参数和实际服务供给，诊断错误入口与缺少自举依赖。
bazaar-contrib-config-versions 导出五个共享入口，Vn accepts 前一版、offers 本版转换服务；版本识别与历史转换由各版持有。
宿主从版本包的 VERSIONS 组合装配到 CONFIG_CURRENT，调用它生成候选、执行模型和服务校验，再经已选载体提交；完整业务文档启用其他模块。
服务端、普通节点和 Pod 使用相同启动 flags 与装配流程；无本地 config.toml 的配置中心组合以真实远端读写、并发提交与部署重建验收。
完整接口、冲突处理、filesystem 原子保存和生命周期见 [配置载体与启动自举](framework.md#配置载体与启动自举)。

### 定时器契约与时间轮实现

定时能力拆成公共 timer 契约和提供该契约的实现模块。时间轮模块承接分层时间轮实现，
在 lifespan 中创建并启动驱动任务，通过 `ctx.offer(TIMER, timer_service)` 提供服务，退出时关闭驱动与未完成定时器。
默认 BCN 组合启用一个使用共享 TIMER 绑定的时间轮实例，agent 和 channel / harness 实例声明 TIMER 依赖并使用同一绑定；
纯 server 组合在默认组合启用同一实现。

timer 契约由功能契约包持有，定义定时器创建、重置、取消、等待到期、定时精度与最大延迟能力，
以及取消和服务关闭的统一结果语义。各消费者声明 `accepts=[TIMER]`，通过 `ctx.accept(TIMER)` 使用服务。
定时器句柄由创建它的消费者在自己的生命周期内管理，消费者退出时取消尚未完成的定时器；
时间轮 provider 在其消费者结束后关闭。更换 timer provider 时，消费者继续使用同一契约。

Reminder 模块依赖 DB 与 TIMER，自行持有持久化、调度、snooze、更新、取消、到期与 agent 唤醒规则。
agent 空闲管理、消息合并等待、渠道限流与重试、版本检查、健康上报和远程连接重连使用通用 timer 服务。
本阶段在新包组合中实现这些调用者及定时器异常契约，业务依赖 TIMER 公共服务；旧时间轮及中心装配在最终发行阶段统一清理。
以真实定时器、隔离资源及业务流程验证到期、重置、取消、驱动故障、消费者退出和宿主关闭的行为。

### 新功能与调用

每个功能模块自行定义服务接口和结果，注册自己的 CLI / RPC 方法。
调用模块在入口完成 Pydantic 校验和身份绑定，业务服务负责其规则；传输不持有全部业务请求的联合类型。
server API 与 bcc 进入同一功能服务。可信身份与实例目标分开，模块实例可以在 channel 建立之前被配置服务寻址。

搜索与其他业务方法由功能模块 offer 服务并登记本地命令；server client 通过 accept 获取相同服务执行私有协议请求；
渠道配置可以注册自己的交互能力。UI 注册入口同时允许普通 schema 表单和 provider 自己的交互面板。

### 数据与查询

`bazaar-contrib-storage` 的 DB 契约统一显式数据操作及其语义，包含模型／schema 描述、过滤、排序、分页、
批量读写、索引与全文检索要求、事务边界和错误结果。读取返回普通数据记录，关联读取由消费者显式发起批量查询。
SQLite 及其他 provider 将公共操作翻译成各自后端的查询与存储操作。
消息搜索、联系人、提醒和多节点管理查询归各自的功能模块。
数据模型和共享表的读取契约有明确拥有者，其他模块依赖对应契约包。
功能模块通过 `ctx.accept(DB)` 取得实现，使用公共接口表达自己的查询与事务，provider 负责后端执行。
新增业务在所属模块增加模型、schema 与业务服务；通用操作覆盖其需求时复用现有 storage 契约。
新增通用存储能力时同步更新 `bazaar-contrib-storage` 契约、使用它的 provider 和组合验收。

`src/bazaar_compute_node/core/storage.py` 的业务存储契约、`src/bazaar_compute_node/contrib/sqlite/storage.py` 的业务转发、
`src/bazaar_compute_node/contrib/sqlite/repository/` 的业务查询与 `SqliteRepository` 聚合类按功能拆入对应模块。
新消息、会话、搜索、提醒和设置调用者逐阶段接入所属功能服务；旧 repository 目录在最终发行阶段整体清理。
原服务端 `storage.py` 的业务模型与查询接口归入 fleet、history、contacts、access 等功能模块，
数据库 provider 持有通用数据操作与后端翻译实现。
核心和数据库模块的最终依赖图仅包含通用契约，各功能通过 db 或其他功能拥有者的服务协作。

SQLite 的 BCN／BCS 已发布迁移链分别保留原内容、版本及 checksum。schema 从原 schema_migrations 的
initial_node_schema／initial_server_schema 身份识别历史链，复用原记录并执行缺少的历史步骤；BCS 保留原 1…7 及校验算法。
后续模块迁移使用独立 contrib_schema_migrations ledger，按发行包／迁移版本记录唯一身份并校验 checksum；
混合组合按模块注册所需 schema，新库按启用组合建立 schema，表名冲突由注册边界诊断。
数据库迁移服务负责执行与 ledger，功能模块通过注册入口提供后续迁移和依赖顺序。新索引和 schema 改动使用新增迁移。
数据库验收从新模块组合建立隔离环境，验证各模块的 schema、查询、事务和跨功能业务流程。

### provider 与运行实例

channel / harness 的公共协议属于对应契约包。builder 在节点组合中注册，agent 根据已校验配置创建 channel / harness 的实例条目及私有绑定。
新组合通过注册表装配 provider；原有类型枚举和公共装配分支在最终发行阶段统一清理。
provider 的第三方依赖留在自己的发行包，app 发行包通过明确的默认依赖安装需要的模块。

### 应用与界面

`bazaar-contrib-server` 用本地绑定的子组合管理服务端实例；server-api 提供 API，bazaar-contrib-web、GUI 和管理页面按相同绑定声明功能服务依赖。
server 与节点 client 通过私有 HTTP 通信；client 通过 listen 和 accept 接入节点插件，API／页面与 client 分别拥有生命周期。
业务模块登记方法与资源，渠道模块提供配置 schema，渠道 UI 模块登记编辑器和交互组件。
布局、订阅、历史状态和页面权限由对应 Web 契约执行。server 可独立运行，web 按配置启用。
全部组合由 app 的普通 bcn run 入口调用 core 的同一运行接口，core 宿主依据明确实例及服务依赖装配。
完整依赖图、配置资源、部署与清理验收见 [统一宿主与 server／web 组合](#统一宿主与-serverweb-组合)。

### 安装、发现和升级

`bcn contrib` 与节点升级共用自身 uv 定位、安装执行和依赖清单。
默认用户执行一次宿主升级，目标 `bazaar-compute-node` 与 core、默认官方组合和已安装的 server／web 等可选包按各自依赖范围一起解析；用户指定的兼容旧版本可以保留。
升级请求将目标宿主与完整额外模块清单一起交给 uv，候选输入通过具名 index 和 sources 将额外包绑定到保存的来源。
uv 解析出完整 pylock 后，tool 部署使用固定宿主版本及 `--with-requirements` 消费解析产物，
专用 venv 使用 `uv pip sync --python` 安装同一结果。版本依赖由各包的 `pyproject.toml` 声明，
运行服务依赖由静态或按配置计算的 `accepts` 声明，具体流程见 [安装方案](framework.md#模块管理与自身-uv)。
安装前解析整个候选组合，版本冲突报告目标宿主、具体模块及不满足的依赖范围；
解析失败保留当前组合与配置，用户可以选择保留当前版本、使用兼容模块版本或移除额外模块。
解析成功后执行安装与入口／API 校验，成功再提交完整依赖清单，按监督进程规则在下一次启动生效。
安装过程失败时保留原声明，报告实际环境变更并提供恢复原组合的操作。
用户显式固定的额外模块版本和安装源在升级中继续生效。官方组合与额外模块一并参加真实安装和运行验证。
模块启用配置与包依赖清单分别保存，修改配置在下一次宿主启动生效。
V5 转换沿已有 V1→V2→V3→V4 链追加 V4→V5，旧能力参数移入所属模块，生成所需包清单。
整体升级保留载体包、来源和自举参数，预先解析迁移所需包，目标环境校验完整候选配置后经载体保留旧内容、条件提交；各能力阶段同步完成其映射。
详见 [V5 配置与版本转换](framework.md#v5-配置与版本转换)。
用户统一配置顶层 contribs 中的入口与参数；组合按业务输入和 builder 明确创建实例，local 决定私有服务与事件，宿主生成内部实例标识。
管理列表分别展示统一配置条目和它派生的运行实例，配置编辑更新同一来源条目。
`contrib_indexes` 保存官方及额外模块索引的本地文件路径、HTTP(S) 或 resource:// 包内资源地址。官方索引在 manager 构建时从 uv.lock 中的全部 contrib/ 工作区包自动生成，随 manager 发行；默认配置声明 resource://bazaar_contrib_manager/catalog.json，读取无需联网，未安装的包同样可见。
索引条目提供 package 和 source，管理服务读取并缓存登记的安装清单，远端失败显示原因及缓存，节点继续启动。
选择的包与来源交给 uv，安装后通过发行元数据关联到入口，用户填写参数经该宿主的 CONFIG_STORE 保存配置文档。
contrib 市场页面与 CLI 共用索引及模块管理服务；当前节点和选中的远端节点分别安装，由目标宿主运行自己的 uv。
市场通过普通 页面模块登记，安装进度、已装状态、配置和下次启动生效状态见 [市场计划](bazaar.md)。

## 各阶段统一验收

1. 本能力的实现、查询、配置、入口和第三方依赖均有对应模块包及公共契约。
2. 通过统一 entry point 和配置加载 Contrib，根据已校验业务输入声明实例条目、校验服务绑定与依赖后，使用 lifespan / listen 启动服务与事件消费，其他模块可以使用其服务和扩展入口。
3. 相关业务行为和权限、状态、故障、生命周期语义由真实组合验收。
4. 本阶段的新实现、调用者和测试在独立成员中直接使用新模块契约，公共宿主代码保持通用；旧代码与旧测试作为原始实现保留，最终发行阶段统一清理。
5. 新增相同类别的功能只需注册自身能力；真实示例证明宿主无需追加业务方法。
6. workspace 的成员、来源覆盖、源码提交与锁同步，editable 联调通过；各包独立 wheel 安装、包内新测试、包内 Ruff、Pyright/LSP 和相应平台 CI 通过。
7. API、配置、包依赖、版本及模块索引条目同步更新，并合入 `0.3`。
8. 本阶段对应的 V4→V5 参数映射、迁移所需包和资源完成同步，以隔离配置验证旧设置对应的实际行为。

普通节点、纯 API、web 和混合组合验收使用测试专用配置、临时数据库、socket、包环境和隔离进程。
真实 provider 使用 TestChannel 控制面；native service 测试在各平台独立隔离 CI runner 执行。
已发布迁移保持不可变，模块迁移和查询验证使用隔离数据库。

## 0.3 发行清单与发布顺序

完整能力集合包含 app、core、6 个公共能力 contrib 和 31 个实现／组合 contrib，共 39 包、53 入口。
22 个必要包在 BCN 共同发行，17 个可选包在独立仓库发行；各阶段记录所属仓库、发行名、入口和集成证据：

| 记录字段 | 必须保存的内容 |
| --- | --- |
| 能力 | 对应本路线图的业务或 provider 范围 |
| 源码 | 所属仓库 URL、固定提交与成员目录 |
| 发行包 | Python 发行名、import 名、entry points 与公共契约依赖 |
| 版本 | 必要包的 BCN 共同版本，首版 0.3.0；独立可选包的实际版本 |
| 产物 | wheel／sdist 文件名、发布地址与校验值 |
| 兼容范围 | core／功能契约与 Python 约束、Contrib API 范围 |
| 集成证据 | 干净安装、组合运行、升级与三平台 CI 结果 |
| 发布状态 | 每包上传结果及所属仓库固定发行提交下的缺失产物 |

第 16 阶段按串行 Tasks 交付并 review：

1. 收齐 39 个正式包和完整入口，验证包依赖闭包、core 的通用边界与整体候选组合；验收通过后统一切换入口，一次清理旧 src、旧测试、依赖、repository、装配与适配层。
2. 交付 scripts/release.py：从 BCN workspace 发现必要成员，单个目标 version 同步这些成员的 project.version、资源版本引用和共享锁，保留各包仍适用的依赖下限与兼容范围；生成本仓库发行清单，并固定独立可选包的版本／源码／产物及集成依赖顺序。
3. 接入保留单 version 输入的 release workflow，形成已验证签名的共同发行提交；逐包构建和验收 wheel／sdist，按依赖顺序上传，app 在默认依赖上传后发布。
4. 使用同一发行提交和产物验证部分发布后的核对与补缺，全部成功后发布 annotated v<版本> 标签及整体 release；独立可选包由所属仓库按兼容范围发布；从真实包源完成三平台组合安装、安装文档和官方索引更新。

最终发行顺序如下：

1. 冻结 0.3 core／功能契约、配置载体与五版自举、V5 配置和历史转换，确认官方成员、包依赖与发行清单。
2. 在 0.3 集成分支以全部官方候选 wheel 完成 workspace 外的干净安装与最终组合验收。
   从 V1／V2／V3／V4 隔离配置升级到 V5，验证存储与数据位置、agent／provider、远端连接、凭据引用、
   version_check 和额外包来源。
   从实际 0.2 BCS 发行环境建立隔离配置、数据库、密钥及账户／权限／事件，server 启动时直接读取原独立配置，
   核对原数据库路径、历史 ledger、业务身份和原密钥，验证登录、页面查询、旧节点原协议及升级后新数据写入；
   同时覆盖原 BCS 自定义配置位置、缺省值、既有迁移版本、重复启动，以及节点与服务端各自配置／数据的同进程隔离。
   以默认 filesystem 和独立真实配置中心载体验证条件创建、并发修订、原内容保留、无本地配置启动和部署重建；
   完成普通节点、纯 API、web 及 server 与 agent 混合组合的统一 bcn run 启动、多节点连接、模块索引与本地 / 远端模块管理、核心用户流程、
   模块 schema 与查询、官方组合与额外依赖整体升级和三平台生命周期验收。
   从默认 SQLite 组合启用实际替代后端、更新已有 database 选择，验证两后端并存及停用、业务流程、子组合的服务查询与升级后选择保留；
   以实际不兼容的包依赖验证解析冲突诊断及原组合保留，安装失败验证声明与环境的恢复流程。
3. 全部能力和候选验收通过后，统一切换入口并清理旧 src、旧测试与旧依赖；以清理后的新包组合复核安装和关键流程，再将 0.3 合入 main；Release workflow 以一个 version 输入准备共同版本、依赖和锁，创建已验证签名的发行提交，所有后续作业固定此提交。
4. 从 BCN 发行提交及各可选仓库固定提交构建完整 wheel／sdist 集合，复核元数据、资源、checksum、三平台安装与升级结果；按跨仓库正式依赖顺序发布 core、公共接口、必要及独立可选包，最后发布 app。
5. 每包保存发行名、实际版本、所属仓库源码提交、产物校验值与上传状态；失败重跑沿用同一提交和已构建产物，核对已发布文件后补齐缺失项。
6. 全部包可从真实地址安装并通过最终 smoke 后，推送同一提交上的 annotated v<版本> 标签和整体 release，文档与官方索引记录 BCN 版本及集成验收过的独立可选包版本。

工具与 CI 的版本同步、源码预期值、发布排序及重跑规则见 [发版工具与 CI](README.md#发版工具与-ci)。
最终验收以真实安装组合为准：用户安装 BCN 后，可以发现、安装、配置并使用各项能力；
功能模块自行增加查询、调用入口和配置界面，core 继续按具体实例、服务绑定与依赖装配。
