# BCN 0.3 模块化路线图

本路线图列明能力归属、替换顺序、阶段验收和正式发行要求。
版本目标、分支及仓库组织见 [总计划](README.md)，首阶段执行细节见 [contrib 框架计划](framework.md)。

所有阶段遵循[迁移实施与验收标准](framework.md#迁移实施与验收标准)：提示词、行为、Actor 和文案与 main 原实现保持一致，
只调整已讨论确认的包归属、依赖与公共接口边界；各 Task 对照原源码、资源及真实调用结果完成 review。
本次指令明确批准移除 Agent ID 显示行，将 Runtime session ID 显示改为 Session；按框架计划列出的两处调整比对模板及实际渲染。
Actor 是内部流通的身份原语，内部身份参数、业务调用与归属键统一使用 Actor，仅在边界与 `session_id`／`agent_id` 转换。
`session` 使用 `Thread(thread_id)`，`dangerous_individual` 使用 `Agent(agent_id)`；身份解析、运行会话与调度归属、
命令可见范围和跨进程会话绑定均按 main 保留，具体模式及验收见上述标准。

## 顺序与能力归属

以下列明能力依赖。首阶段先完成命令与调用，再单独交付节点共享 timer、数据库，随后完成 agent 相关能力；每个 Task 的顺序与 review 节点按 [首阶段 Tasks](framework.md#本特性分支的串行-tasks) 分项实现并 review。
具体渠道和 harness provider、其余平台管理及 server 与其功能模块等组合由后续特性分支交付，合入按各自验证结果推进。
每一阶段在新成员中实现所属能力、调用者和包内测试，后台协程归属任务组，通知和队列使用 Event 与 Queue，取消和时限通过任务取消与 timeout 管理，线程与子进程使用标准库接口。
新包统一要求 Python 3.15。每一阶段都完成其新模块的包结构与安装验证，发布版本在最终发行阶段统一收齐。
各能力阶段交付独立的新包组合，旧 src 与旧测试保持原样。第 16 阶段在全部新能力齐备、整体候选验收通过后统一切换入口，并一次清理旧代码、测试、依赖与装配。
必要包按 [本地 workspace 开发](README.md#本地-workspace-开发) 加入 BCN 成员与共享锁，可选包按 [跨仓库开发工作区](README.md#跨仓库开发工作区) 聚合各自 checkout，
以 editable 安装联调，再在独立环境使用各包正式依赖和 wheel 完成交付验收。

| 顺序 | 能力主题 | 模块化的能力 | 前置能力与验收 |
| --- | --- | --- | --- |
| 1 | 框架与日志 | core 运行内核、统一启动接口、本地绑定与发现，app 普通命令入口与默认资源，公共配置载体与版本服务契约、filesystem 实现、五版转换模块、自举、V5 配置、安装管理和日志 | 组合明确声明 ContribEntry 及服务绑定，ContribHub 按服务依赖和父实例关系启动、更新及释放实例，用户统一在业务文档顶层 contribs 启用，登记 lifespan / listen / offers 和静态或按配置计算的 accepts；Context 提供 offer / accept / announce；启动输入选择提供 CONFIG_STORE 的已安装入口；版本包提供 VERSIONS 组合并装配 CONFIG_CURRENT 依赖链，V1→V2→V3→V4→V5 服务生成候选和所需包，宿主校验后经载体条件提交；通过包元数据关联入口；节点升级保留模块依赖与来源 |
| 2 | 命令、调用与共享定时器 | COMMANDS、插件命令、sidecar；独立 timer 契约和时间轮实现 | 共享 Click 命令与 invoke 的入口扩展；sidecar 定义 SERVER／CLIENT 及 ctx handler，Unix／Windows 包提供传输服务，消费者依赖接口并自行编排调用；core 按通用插件根与绑定选择依赖，回调决定退出时间；定时器独立提供共享 TIMER，任意节点插件声明依赖后由组合选入所选实现，并在数据库之前单独交付 |
| 3 | 数据库与迁移 | storage 公共数据操作契约、后端注册表、DB 选择层、SQLite 实现、schema 与迁移服务 | storage → 所选后端 → database → 消费者；按配置展开后端依赖，实际多个后端验证并存与选择；业务声明查询、provider 翻译；业务迁移插件统一登记抽象迁移，按已有版本续迁，空库建立当前基线 |
| 4 | 消息与会话 | agent 消息数据、会话 target、历史、未读 cursor、发送与回执、附件、草稿、搜索和 thread follow | 消息模块使用公共查询接口并持有业务模型与索引要求；同一消息服务供 CLI / RPC 使用；新调用者使用消息 / 会话 / 搜索服务，旧 repository 在最终发行阶段统一清理 |
| 5 | 身份与协作 | sender / contact 身份、引用 refs、会话登记和审批、消息引用及关联 | 消息模块公共契约就绪；状态变化和读取遵循现有协作语义 |
| 6 | Reminder | Reminder 的登记、到期、snooze、更新、取消、持久化与唤醒 | 使用已交付的共享 timer、DB 和消息服务，自持提醒业务；新调用者直接依赖其契约，旧实现最终统一清理 |
| 7 | agent 公共接口、配置、执行与编排 | channel／harness 接口和 builder 登记、bcc 命令；agent 组合入口拥有配置模型、实例构造与管理命令；执行实例处理 turn、会话绑定、调度、输出、审批和任务故障 | 数据库后先交付 agent 配置、实例与局部生命周期；后续逐个 commit 为真实 agent 接入 channel／harness、消息、协作、提醒及所需公共接口；组合明确创建本地实例，可整体替换 agent 实现 |
| 8 | 渠道 provider | Telegram、Lark、WeCom 的连接、收发、附件和 provider 特有配置 | 实现公共 channel 接口并通过 builder 登记；新增渠道由自己的实现包及 UI 模块接入 |
| 9 | harness provider | Codex、Claude Code 的启动、模型、能力检查、事件解析与审批对接 | 通过 harness builder 与执行契约接入；真实 TestChannel 验证端到端行为 |
| 10 | 节点与平台管理 | 节点配置、workspace / 文件、模型与 skills 查询、版本检查、升级、系统服务和健康；agent 配置由第 7 项的 agent 插件拥有 | RPC / 命令注册可用；各管理能力自行提供服务和入口；安装与升级共用包管理机制 |
| 11 | server 框架与登录 | HTTP、server 本体（外壳与嵌入工具、侧边栏、概览、权限点登记、主题、语言、refs、API 挂载）、主题插件、登录与权限（会话、账户、角色、分享、OIDC 接口与实现）、server-migrations、server-management 与 ~/.bcs 导入 | 功能模块经 server 本体登记页面、权限点与概览内容，页面运行在同源 iframe 中；0.2 的账户、密码哈希、角色、分享、OIDC 设置、session.key 与有效 cookie 导入后照常可用，见 [server.md](server.md) |
| 12 | server-computer 与两端通信 | server-computer（登记、身份、在线状态、call、节点协议、中转契约、页面与 API）、server-relay-memory、starlink、重试、事件上报、调用及结果 | starlink 经 listen／announce／report 接收调用、回传结果和上报事件，调用经节点 sidecar 分发；真实隔离两端验证，见 [server.md](server.md) |
| 13 | agent 模块 | agent 的列表、详情、新建、配置、删除与近况，渠道配置表单 | accept server-computer，经 call 管理各电脑上的 agent；渠道表单由各渠道插件的 UI 入口提供；agents.* 权限点与分享生效 |
| 14 | 会话与活动 | 会话模块（会话列表、会话详情、审批、图片）与活动模块（events 持久化、近况与当日用量） | 会话模块 accept agent，经 call 从节点读取会话；活动模块 listen 节点事件，按电脑、run_id 与 seq 去重；各自登记页面、权限点与概览内容 |
| 15 | [contrib 市场](bazaar.md) | 每台电脑各自的模块索引、已装 contrib、详情与安装、入口配置、启停、升级和卸载 | 市场模块 accept server-computer，经 call 操作目标电脑；contribs.view、contribs.install、contribs.update、contribs.delete 权限点由它登记；真实包源安装、多入口配置、后端选择与下次启动生效状态可验证 |
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
| 节点通用定时 | timer-wheel 作为 TIMER 提供者，由消费者的依赖闭包选入 |
| agent 公共业务 | messages、collaboration、reminder |
| provider 公共接口与 agent | channel-builders、harness-builders；agent 持有配置，agent-execution 持有实体执行及 provider 组合 |

具体 channel／harness provider 由用户通过 `bcn contrib add <name> --enable ENTRY` 安装并选择入口；单独 `--enable` 启用该包导出的全部入口，例如 codex、telegram。
启用保存该包所提供组合中的 builder 与运行定义；`bcn agent add` 选择已启用的 provider 并填写实体参数，`bcn run` 按该配置创建实际连接和进程。
安装与启用分别记录状态，启用配置经模型与服务依赖校验后写入顶层 contribs；provider 的连接／执行参数保存在 agent 条目的 `config.agents[].channel`／`harness`，由所选 provider 模型校验。
本地添加模块、编辑配置和添加 agent 通过短生命周期管理组合完成，沿用相同配置载体及版本服务。
完整首次使用命令见 [总计划](README.md#全新安装与默认运行组合)。首阶段收齐默认基础服务与运行前管理，用真实示例包验证配置及生命周期；具体 provider 阶段以实际安装的 Codex、Telegram 和 TestChannel 控制面完成隔离验收，最终发行从真实包源复核。

## 发行包、模块入口与内置组合

以下清单确定 `0.3` 的包边界、发行名、模块入口及内置归属。BCN 必要包首次正式发行版本为 `0.3.0`。
应用、公共接口与节点业务模块留在 BCN：app/、core/ 与 contrib/<功能>/；SQLite、时间轮、文件配置、原生 sidecar 传输与日志默认实现，以及下方官方可选能力表中的包，在 open-bazaar 的独立仓库维护版本和发行。
Python 模块名由发行名中的连字符改为下划线，例如 `bazaar-contrib-logging` 对应 `bazaar_contrib_logging`。
app 通过普通 Python 入口调用 core 的统一运行接口，提供启动输入、退出信号与业务默认资源，消费版本包的 VERSIONS。
core 作为框架运行内核，由应用直接 import，执行发现、自举装配、校验、本地绑定与完整生命周期；
配置包以普通加载器接入文档读写与版本服务，具体功能通过 contrib 定义装配。
运行模块统一登记在 `bazaar_compute_node.contribs`；表中的业务入口名同时是配置使用的 `entry` 值，配置载体入口由 --configer 选择，版本入口由发行自举组合选择；全部入口在该 group 内唯一。
一个发行包可以提供多个入口，组合声明每个入口的参数及绑定，Hub 按条目启动实例。server 功能模块的页面随所属阶段接入。
跨阶段的发行包按阶段增加入口与依赖：渠道连接在第 8 阶段、渠道的 UI 入口在第 13 阶段，
server 框架与登录在第 11 阶段，server-computer 与两端通信在第 12 阶段，agent、会话与活动、市场模块分别在第 13、14、15 阶段。每阶段的 wheel 只登记已交付入口与所需依赖；
第 16 阶段以本表的完整入口集合和依赖组合发行。
发行包与入口以本表为准，其中 `bazaar-contrib-storage` 提供两个公共运行入口。

“默认”表示由 app 的默认配置选择，启动前通过包管理服务恢复所选插件；“server 依赖”表示选装对应包时安装。
Python 发行依赖由实际导入接口的包声明；app 直接声明自身入口、自举、配置和管理流程使用的包，agent 及其 audit／channel／harness 依赖由插件配置和所属包维护。
业务模块定义与参数统一保存在顶层 contribs，配置载体使用启动输入、版本链使用发行自举组合先行装配；组合按已校验业务输入声明具体实例和服务绑定；包依赖安装代码，服务依赖决定实际装配。
provider 的 builder 用于发现、配置和创建实例，连接或进程由选中的 channel / harness 实例管理。
公共契约通过普通 Python 包依赖安装，消费者和实现者导入同一包里的类型与服务 key。

### 公共能力包

| 发行包 | 公共内容 | 引用者 | 阶段 | 组合归属 |
| --- | --- | --- | --- | --- |
| `bazaar-contrib-config` | ConfigStore、ConfigSnapshot、CONFIG_STORE、ConfigConverter、ConfigCandidate、CONFIG_CURRENT、CONFIG_V1…CONFIG_V5、修订条件提交与错误契约 | 公共配置加载器、filesystem／自定义配置中心载体、app 参数绑定、CLI 和管理入口 | 1 | 默认、应用组合的依赖 |
| `bazaar-contrib-audit` | AuditContext、AuditEvent、AUDIT、AUDIT_EVENT 与 NodeAudit 发布服务 | agent 必需的事件发布依赖；记录、starlink 上报与 BCS 网关分析分别订阅 | 7、11 | agent 的包与服务依赖；消费入口由组合按需启用 |
| `bazaar-contrib-storage` | `Database`、`BackendRegistry`、`STORAGE`、`DB`、`backend_key(name)`，数据／schema／显式查询／事务契约；`storage` 和 `database` 两入口，默认共享 | 全部数据库实现和持久化业务模块 | 3 | 默认、server |
| `bazaar-contrib-timer` | timer 接口、`TIMER`、定时句柄和到期／取消结果 | 时间轮、Reminder、agent、channel / harness、管理和远程模块 | 2 | 默认、server 的依赖 |
| `bazaar-contrib-channel` | `Channel`、`ChannelBuilder`、输入／发送／回执／审批模型与 `CHANNEL_BUILDERS`；附带 `channel-builders` 共享注册入口 | agent、渠道 provider、渠道表单 | 7 | 默认；agent 模块的渠道表单依赖 |
| `bazaar-contrib-harness` | `Harness`、`HarnessBuilder`、会话／turn／输出／审批与发现模型、`HARNESS_BUILDERS`；附带 `harness-builders` 共享注册入口 | agent、Codex／Claude Code 等执行 provider、模型查询 | 7 | 默认 |

这些包导出公共契约和可直接调用的公共逻辑，通过 `import` 使用。
`bazaar-contrib-storage` 还声明 `storage`、`database`：前者提供后端注册表，后者按配置提供 DB。
channel 与 harness 的接口和登记服务分别归上述公共包；具体 provider 依赖这些包。
其他能力的公共类型与 key 由对应功能包导出：命令注册归 invoke，跨进程 SERVER／CLIENT 与 ctx handler 归 sidecar，
消息与协作归各自业务包，HTTP 契约归 http，页面框架与登记契约归 server；starlink 的 HTTP 请求格式和请求关联由通信包公开定义，server-computer 按同一格式提供对端。
具体 provider 依赖相应公共能力包，例如 PG 与 SQLite 共同依赖 `bazaar-contrib-storage`，业务同样依赖它。

### 默认节点模块包

| 发行包 | 模块入口与运行范围 | 拥有的能力与主要依赖 | 阶段 | 组合归属 |
| --- | --- | --- | --- | --- |
| `bazaar-contrib-config-filesystem` | `config-filesystem`：共享，自举选择 | 依赖 bazaar-contrib-config，提供 CONFIG_STORE；TOML、修订复核、锁及原子替换 | 1 | 默认；BCN 主配置的默认载体 |
| `bazaar-contrib-config-versions` | `config-v1`、`config-v2`、`config-v3`、`config-v4`、`config-v5`：共享，VERSIONS 组合 | 依赖 bazaar-contrib-config，逐版提供转换服务；Vn 依赖 V(n-1)，V5 生成参数映射及所需包，配置加载器消费 CONFIG_CURRENT | 1 | 默认；BCN 主配置的版本链 |
| `bazaar-contrib-status` | `status`：共享 | ContribStatusQuery、CONTRIB_STATUS 与 snapshot()；依赖 core，读取宿主记录的实例状态与故障来源 | 1 | 默认、server |
| `bazaar-contrib-logging` | `logging`：共享 | 标准 logging 输出、实例来源、异常堆栈、线程队列与文件轮转；依赖 core | 1 | 默认、server |
| `bazaar-contrib-manager` | `contrib-manager`：共享 | 模块索引、包管理、入口与组合配置导入、依赖与来源清单、配置迁移所需包解析及整体升级；服务端页面经节点接口访问 | 1 | 默认；节点管理 |
| `bazaar-contrib-invoke` | `commands`：共享 | Click 命令登记与命令入口扩展；功能包持有命令与文案 | 2 | 默认、server |
| `bazaar-contrib-sidecar` | `sidecar`：共享 | SERVER／CLIENT 公共契约；接受 SERVER 并暴露 ctx／返回对象的公开 handler，消费方自行编排 | 2 | 节点默认；消费插件的接口依赖 |
| `bazaar-contrib-sidecar-unix` | `sidecar-unix`：共享 | offer SERVER／CLIENT，以 Unix socket 实现会话与请求；依赖 sidecar 接口 | 2 | Linux/macOS 默认 |
| `bazaar-contrib-sidecar-windows` | `sidecar-windows`：共享 | offer SERVER／CLIENT，以 Windows named pipe 实现会话与请求；依赖 sidecar 接口 | 2 | Windows 默认 |
| `bazaar-contrib-sqlite` | `sqlite`：共享 | 依赖 STORAGE、登记 sqlite 后端、提供 backend_key("sqlite")；持有连接和公共操作翻译 | 3 | 默认；节点初始存储后端 |
| `bazaar-contrib-legacy-node-migrations` | `legacy-node-migrations`：共享 | 依赖 SCHEMA，仅接续已有 Node 1～32 历史，提供 LEGACY_NODE_MIGRATIONS；业务 contrib 自行登记自己的基线与后续版本 | 3 | Node 持久化业务插件的依赖 |
| `bazaar-contrib-schema` | `schema`：共享 | 模型／索引注册、模块版本、已有进度、表归属交接与 ledger；通过 DB 在事务内执行业务包拥有的迁移 | 3 | 默认、server |
| `bazaar-contrib-messages` | 公共契约 | 消息／会话、Actor、请求与结果、消息及通用会话读写 Protocol、共享 MESSAGES key 和纯回执转换；依赖 core 和 channel 公共契约 | 4 | 消息实现、协作及 server 投影的契约依赖 |
| `bazaar-contrib-messages-node` | `messages-node`：共享 | 历史、未读、搜索、附件、草稿、收发回执和 thread follow 的本机实现；持有目标解析、草稿、交付和数据作用域；业务请求经公共服务处理；bcc Click 命令及格式化归 Agent 工具 | 4 | 默认配置选择并由 manager 恢复 |
| `bazaar-contrib-collaboration` | `collaboration`：共享 | 复用消息的 sender／contact、Actor 和会话公共契约，迁入准入、登记、审核、消息引用关联及设置；消息数据经消息接口读写，审核决策和审计由协作持有，设置等自身数据通过 DB 管理；通过 agent 生命周期绑定并共享会话锁；页面短数字 refs 归 server 本体 | 5 | 默认配置选择并由 manager 恢复；会话模块的契约依赖 |
| `bazaar-contrib-timer-wheel` | `timer-wheel`：共享 | 提供 TIMER，持有时间轮与驱动任务 | 2 | 默认、server |
| `bazaar-contrib-reminder` | `reminder`：共享 | Reminder 的模型、持久化、到期、snooze、更新、取消；依赖 storage、schema、timer 和消息 | 6 | 默认 |
| `bazaar-contrib-agent` | `agent`：配置入口；`agent-execution`：共享 AGENTS；实体提供局部 AGENT 身份与执行服务 | 拥有 agent 配置及管理命令、实体展开、默认 BCN 指令、bcc 工具环境、上下文与通讯编排；依赖 channel／harness 接口、消息、协作、Reminder、timer | 7 | 默认、可替换；配置与行为整体由所选 agent 实现拥有 |
| `bazaar-contrib-node-management` | `node-management`：共享 | 首阶段接收已实现的 run 命令、配置激活与退出等待；后续节点配置、workspace／文件、模型／skills、健康、系统服务与升级按阶段交付；agent 配置及命令由 agent 包拥有 | 2、10 | 默认 |

### server 与可选能力模块包

以下入口由拥有它们的组合明确创建，默认共享服务；provider 的运行输入由 agent 组合提供。

| 发行包 | 模块入口与运行范围 | 拥有的能力与主要依赖 | 阶段 | 组合归属 |
| --- | --- | --- | --- | --- |
| `bazaar-contrib-postgres` | `postgres`：共享 | 在 open-bazaar/contrib-postgres 维护；实现公共 storage 操作，登记 PostgreSQL 后端并提供 backend_key("postgres") | 3 | 可选存储后端 |
| `bazaar-contrib-telegram` | `telegram-builder`：共享；`telegram`：接受 channel 输入；`telegram-ui`：共享 | Telegram 收发、附件、配置与编辑器；运行入口实现 Channel，依赖 channel、timer，UI 入口依赖 server 本体 | 8、13 | 可选；agent 模块的渠道表单依赖 |
| `bazaar-contrib-lark` | `lark-builder`：共享；`lark`：接受 channel 输入；`lark-ui`：共享 | Lark 连接、收发、附件、配置与编辑器；运行入口实现 Channel，依赖 channel、timer，UI 入口依赖 server 本体 | 8、13 | 可选；agent 模块的渠道表单依赖 |
| `bazaar-contrib-wecom` | `wecom-builder`：共享；`wecom`：接受 channel 输入；`wecom-ui`：共享 | WeCom 连接、收发、附件、配置与编辑器；运行入口实现 Channel，依赖 channel、timer，UI 入口依赖 server 本体 | 8、13 | 可选；agent 模块的渠道表单依赖 |

WeCom 从消息内的 quote 补录安静入站消息时，保留当前消息发送者；
发送者的补全由 WeCom provider 完成，通用 Channel 和 Agent 原样传递。
| `bazaar-contrib-codex` | `codex-builder`：共享；`codex`：接受 harness 输入 | Codex 进程、事件解析、模型／能力与审批；实现 Harness，依赖 harness、timer | 9 | 可选；实际实例由 agent 配置选择 |
| `bazaar-contrib-claude-code` | `claudecode-builder`：共享；`claudecode`：接受 harness 输入 | Claude Code 进程、事件解析、模型／能力与审批；实现 Harness，依赖 harness、timer | 9 | 可选；实际实例由 agent 配置选择 |
| `bazaar-contrib-http` | `http` | HTTP route／middleware／静态资源登记、监听能力与请求生命周期；提供 HTTP，导出 HTTP 契约 | 11 | server 依赖 |
| `bazaar-contrib-server-migrations` | `server-migrations` | 在 open-bazaar/contrib-server-migrations 维护；接受 SCHEMA，以原 v01–v07 为历史、以 upgrade_only 接续 0.2 的 BCS 表结构并提供 SERVER_MIGRATIONS；空库不建表，各模块以它的 v07 为前身登记自己的基线 | 3、11 | server 默认组合 |
| `bazaar-contrib-server` | `server`、`server-theme` | 在 open-bazaar/contrib-server 维护；server 是 server 本体：外壳与嵌入工具、侧边栏、概览、权限点登记、THEME 接口、语言、refs 与 API 挂载；server-theme 登记夜市与晨市两套主题；原 BCS 数据结构保留 | 11 | server 默认组合 |
| `bazaar-contrib-server-management` | `server-management` | 在 open-bazaar/contrib-server-management 维护；在 `bazaar_compute_node.defaults` 登记 server 的默认配置，以私有绑定装配 server 子组合（通用 sqlite 的私有实例打开 ~/.bcn/bcs.sqlite3），首次启动从 ~/.bcs 导入数据库、登录密钥与设置；bazaar-compute 的 `[server]` extra 安装它 | 11 | 可选；装 `[server]` 的 host 即为 server |
| `bazaar-contrib-starlink` | `starlink` | 节点主动连接目标 server：listen 长轮询接收调用，经私有 sidecar 实例分发到 RemoteEntry 声明的成员，announce 回传结果，listen 审计事件后经 report 上报；持有 token／token_env、请求关联与退避重试 | 12 | 可选；默认停用，配置 server 后启用 |
| `bazaar-contrib-server-access` | `server-access`、`server-oidc` | 在 open-bazaar/contrib-server-access 维护；server-access 提供会话、登录页、账户、角色、分享与权限检查，定义 OIDC 接口；server-oidc 是标准 OIDC 的实现；接管 0.2 的账户、角色、分享与 OIDC 表 | 11 | server 默认组合；登录方式由 OIDC 实现插件扩展 |
| `bazaar-contrib-server-computer` | `server-computer`、`server-relay-memory` | 在 open-bazaar/contrib-server-computer 维护；server-computer 持有电脑的登记、身份与在线状态，注册 listen／announce／report，定义中转契约并提供对电脑的 call，以及电脑的页面与 API；server-relay-memory 是中转的单进程实现；接管 computers 表 | 12 | server 默认组合 |
| `bazaar-contrib-server-agent` | `server-agent` | agent 模块：accept server-computer，经 call 管理各电脑上的 agent，提供列表、详情、新建、配置、删除与近况的页面和 API；渠道表单来自渠道插件的 UI 入口 | 13 | server 默认组合 |
| `bazaar-contrib-server-conversation` | `server-conversation` | 会话模块：accept agent，经 call 读取会话列表与会话详情，审批联系人，代取聊天中的图片；登记 agents.approve | 14 | server 默认组合 |
| `bazaar-contrib-server-activity` | `server-activity` | 活动模块：listen server-computer 发布的节点事件，持久化到 events 并按电脑、run_id 与 seq 去重，投影近况与当日用量；接管 events 表 | 14 | server 默认组合 |
| `bazaar-contrib-server-market` | `server-market` | 市场模块：accept server-computer，按电脑展示各自的索引与已装 contrib，经 call 安装、升级、配置、启停与卸载；登记 contribs.* 权限点 | 15 | server 默认组合 |

server 的功能模块持有自己的模型、schema、查询、服务、API 与页面，经 server 本体登记侧边栏、概览与权限点，页面运行在同源 iframe 中。
模块之间按业务需要 accept：agent 模块接受 server-computer，会话模块接受 agent 模块，活动与市场模块接受 server-computer。
HTTP 与 server 本体的公共契约供功能模块导入；功能模块的发行依赖指向这些契约包，与 server 组合形成单向关系。

### 应用、核心与组合资源

| 发行包 | 目录与职责 | 入口 | 阶段 |
| --- | --- | --- | --- |
| `bazaar-core` | core/src/bazaar_core/；框架运行内核、ContribHub.run、发现与自举装配、通用校验、服务／事件、本地绑定、依赖与完整生命周期 API | 由 Python import 使用 | 1 |
| `bazaar-compute-node` | app/src/bazaar_compute_node/；业务输入服务与明确的实例构造、通用命令分派、启动输入、兼容范围内的默认依赖、defaults.toml，消费版本包的 VERSIONS | bcn 调用 core 运行接口、bcc 调用 | 1 |

app 按兼容范围直接依赖 bazaar-core 与实际使用的自举、配置、命令和安装管理接口；业务插件按配置安装，各自声明消费的公共契约依赖。core 的包依赖保持通用。
应用基础先作为独立任务交付：run(bootstrap, load) 启动自举条目及提供 COMPOSITION 的加载器，bcn run 启用已交付的 status；
随后在相同应用入口接入配置发现、载体、版本链与默认配置资源。
server 的迁移、配置载体、存储组合、API、业务与页面，以及 starlink 与具体 provider 等可选包在 open-bazaar 的独立仓库开发，可选安装并各自持有依赖与配置资源。
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
安装 bazaar-compute[server] 后，server-management 提供 server 的默认配置并装配 server 子组合；子组合包含
状态查询、日志、调用、storage、通用 sqlite 的私有实例、database、schema、timer、http、server-migrations、server、server-theme、server-access、server-oidc，以及后续阶段的 server-computer、server-relay-memory、agent、会话、活动与市场模块。
用户选择包导出的 starlink 入口并填写 server 地址与 token 或 token_env；server 的调用经 starlink 的私有 sidecar 实例分发到 RemoteEntry 声明的成员。
第三方功能模块与主题插件安装后加入 server 子组合，各自登记页面或主题。
只运行 server 的部署可使用空的 `agent.config.agents`；新增 agent 后，组合展开其实体，宿主为内部 AGENT 定义及选定 provider 创建所属实例。

首次创建节点配置使用 app 提供的 bazaar_compute_node/defaults.toml 资源，由配置包普通加载器经 core 校验后通过所选 CONFIG_STORE 条件创建 version="5"、storage、sqlite、database，明确 database.config.backend="sqlite"，
并写入 agent 的 `config.agents=[]` 及内置官方索引提取后的本地路径。
历史配置的转换与提交规则见 [V5 配置与版本转换](framework.md#v5-配置与版本转换)。
可选组合资源是普通 TOML，CLI／页面导入后形成完整候选配置，经相同 Pydantic 参数校验、服务绑定和依赖图检查后保存。
节点组合导入复用已有共用条目，保留用户参数、DB 选择及停用选择；同一入口已有多个条目时由用户选择更新目标。
server 的节点启用条目与服务端独立配置分别保存，BCS 原字段直接由服务端读取。
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
公共契约与 agent 在 BCN 主仓库维护；具体 channel／harness 实现均归 open-bazaar 独立插件仓库，用户通过 contrib 管理命令安装，V4→V5 按历史选择声明对应插件。

`bazaar-contrib-channel` 拥有 provider 无关的 `Channel`、`CHANNEL`、`ChannelBuilder`、输入消息、发送请求、交付回执、附件、身份和渠道审批载荷。
`bazaar-contrib-harness` 拥有 `Harness`、`HARNESS`、`HarnessBuilder`、会话与 turn 输入、指令和工具环境、可取消的输出流、审批事件、能力／模型／skills 查询及关闭结果。
公共 `HarnessBuilder.inspect(workspace, timeout)` 统一返回可用性、CLI 版本、模型／推理档位与工作区 skills，
查询无需 Agent 或运行会话；运行实例不再提供重复的 describe 查询。
上游 Agent 按自己的 mode 和 session 配置选择共用的 `SandboxPolicy`，通过 SessionInput 向各 Harness 传递实际沙箱策略。
full access 由宿主自动审批，其余通过 Channel 审批卡处理；provider 固定开启网络并转发原生审批请求。
Codex／Claude 负责原生参数映射，Harness 输入不携带 Agent 的 mode，也不提供独立审批或网络开关。
输入与结果在公共接口边界由 Pydantic 校验；双方通过公共审批标识关联请求和结果。
审批请求／决定／结果由 harness 公共包拥有，channel 使用同一载荷展示审批并收集结果。
agent 校验审批与当前 session／turn 及原 request_id 的关联；不匹配时记录 warning 日志，并返回关联原请求、带 reason 的 rejected 决定，审计保留实际请求与返回决定。
agent 将 harness 输出映射为 channel 请求；公共 harness 包依赖 core，公共 channel 包依赖 core 与 harness 的共用审批契约，具体 SDK 类型在 provider 内部转换。

两个公共包分别提供 共享 `channel-builders`／`harness-builders`，导出 `CHANNEL_BUILDERS`／`HARNESS_BUILDERS` 服务 key。
builder 登记 provider 标识、配置模型与运行入口；HarnessBuilder 通过 inspect 查询模型和 skills。默认 agent 根据自己的配置选择已启用定义，通过登记契约校验实体参数并构造实例条目与 provider 服务绑定。
公共 `ChannelBuilderConfig`／`HarnessBuilderConfig` 基类以 `ClassVar` 导出 kind、entity_model 与运行入口；具体 builder 的 Pydantic 配置类声明这些元信息，候选从已安装入口定义取得模型，在资源启动前校验实体参数。
公共包还导出按 kind 缓存的 `channel_builder_key(kind)`／`harness_builder_key(kind)`，具体 builder 提供对应 key，agent 按配置声明所选 builder 依赖，确认其已启用后装配。
具体 channel 实例在 lifespan 中持有连接，具体 harness 实例在 lifespan 中持有进程／会话；agent 通过 local 启用各私有子组合，持有框架启用句柄，并以句柄的 typed 服务引用连接执行实例。框架维护内部绑定和生命周期，运行输入只包含业务上下文。
agent 的内部执行任务通过这些服务等待所选实例就绪，再消费输入、执行 turn 和交付输出；自身 lifespan 的服务登记与 yield 先完成，资源在所属实例的 lifespan 退出时释放。

调用与公共接口任务将运行输入服务、runtime builder／接口名称统一改为 harness。
已交付 Task 3／4 的检查项记录当时名称；V4 `agent.runtime` 数组在 V5 转换为默认 agent 配置中的 `harness`，原有身份、顺序和参数逐项保留。
研究依据为现有 `src/bazaar_compute_node/core/channel.py`、`core/runtime.py` 的 channel、builder、session、turn、输出流和审批契约；新公共包按上述归属拆出类型和依赖。

### 完整行为事件与 NodeAudit

事件发布和上报保留 agent／harness 产生的全部行为事件及内容，订阅方自行选择处理范围。
统一事件信封携带事件标识、来源、源顺序、发生与接收时间，以及 node、agent、session、turn、provider 会话和调用关联；
保留 provider 原始事件名、原始载荷和公共结构化字段，扩展事件继续通过同一契约发布。
agent 发布自己构造的指令、输入、模型请求及执行生命周期；harness provider 在事件映射入口保留接收到的源事件，
输出流携带这些明细。按公共事件 key announce，沿现有绑定表达公共或局部可见范围。

`bazaar-contrib-audit` 提供 NodeAudit 和 AUDIT 服务，agent 以包依赖和 accepts 明确消费，执行时始终发布。
NodeAudit 给每条事件附加发布运行标识、序号、接收时间与本地单调时间，通过 AUDIT_EVENT 发布；
AuditContext 携带 agent、channel／harness、目标会话、入站消息、session／turn 与 provider 关联，payload 保留实际请求、结果及源事件内容。
当前接入 agent 的输入、会话与 turn 请求／结果、harness 完整输出、审批和资源关闭。NodeAudit 的 lifespan 提供发布服务，消费方通过普通 listen 接入。
记录、日志、starlink 上报和 BCS 投影分别拥有自己的处理与资源；
starlink 保留完整事件信封和载荷，server-computer 接收后按同一契约发布，活动模块与网关分析各自订阅或查询记录。

| 分析需求 | 保留的数据及来源 |
| --- | --- |
| 工具与文件行为 | provider 的工具调用标识、名称、参数、输出、结果、进度、patch 和状态；文件访问携带来源调用、路径与操作证据 |
| 技能使用 | provider 的显式技能事件或技能加载工具调用及来源；可用技能清单与实际使用记录分别表达 |
| 提示词与用量 | agent 构造的指令／输入、provider 的模型请求信息与原始 usage；保留模型、input／output、cache read／write、reasoning、费用及计量范围 |
| 首文字耗时 | 执行端提交请求、收到首个可见文本 delta 和终态的本地单调时间；同时保留源时间，供订阅方计算等待、首文字和总耗时 |
| 轮次与关联 | agent turn、turn 内部模型响应／步骤、工具调用和重试的来源标识与开始／结束事件，按各自层级统计 |

研究依据为 main `core/orchestration/turn.py` 的 `_consume_events`／`_audit_stream_event`、
`contrib/server/audit.py`、BCS 的 `consumers.py`／`activity.py`：当前实现提取部分活动上报，活动页面读取保存的事件。
新的完整发布要求在源事件映射处保留明细。main `contrib/codex/events.py` 同时处理 tokenUsage 的 last／total，
`contrib/claude/events.py` 区分 turn usage、按模型累计用量及 cache read／write；公共事件保留原始口径和粒度，供消费方计算缓存命中与费用。
provider 提供的可选计量项和明确来源一同保留，技能与文件分析关联实际事件证据。

当前 agent 接入以真实 TestChannel 和文档子进程执行文件任务，独立安装消费者将审计事件写入真实文件并验证输入、源事件、文件结果、关联与时间。
各具体 provider 交付时通过相同控制面验证技能、模型用量和多轮事件来源；第 12 阶段接入完整载荷的两端上报，再由 BCS 消费方验证活动／用量／耗时分析。

### 默认 agent 行为与替换

Task 16 的 Agent 装配直接组合现有 messages、collaboration、Reminder、timer、Channel 和 Harness 契约；
由 Agent 持有 Actor 调度、活跃命令绑定及各 provider 子组合，使用现有框架管理生命周期。
Harness 抽象承载原有 turn steering、会话恢复、后台任务保护与故障切换所需的能力；
Agent 保留 inbox 通知、三秒静默合批、送达游标去重、两种 mode 的审批语义和错误反馈。
真实 TestChannel、独立子进程和原生 bcc 在 wheel／sdist 中验证完整调度、通知、恢复与资源退出；
原行为、插件归属及验收依据逐项记录在 framework 的 Task 16。
独立 Agent 验收使用单独的 editorial 发行包，公共 TestChannel／Harness workbench 供各能力包共享。


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
框架 Task17 同时交付 `bcn agent review` 下的 `list`、`approve` 和 `deny` 三个基础命令，
按 agent ID 查询待审会话，并按 agent／thread ID 通过或拒绝；通过本机 sidecar 消费 collaboration 服务，
会话准入使用 collaboration 持久化的审核状态：新会话默认待审，由调用方经 sidecar 查询、通过或拒绝。
本机命令、BCS 和自定义 contrib 使用同一公共服务及审核状态，准入与具体控制端及上报配置解耦。
审核通过并持久化后，按会话读取数据库中的入站消息，触发一次正常入站通知，经 inbox notice 唤醒响应。
现有入站、落库和审核拦截沿用原流程；节点在入站后、审批前重启也能触发该通知，本机命令与 BCS 经同一公共服务执行。
配置管理使用节点启动前的短生命周期组合，会话审核连接运行中的节点；各子命令按实际操作选择所需依赖。
后续 TUI／GUI 界面由独立 contrib 消费同一公共协作服务。

该包拥有 developer instruction、bcc 工具环境及身份／会话绑定、模型上下文、消息与提醒处理、审批及输出交付的编排，依赖公共 channel／harness、消息、协作、存储、timer 和 Reminder。
初始化的指令渲染、workspace bcc wrapper、PATH 与 session 环境先在框架 Task12.1 接入；bcc 沿 0.2 直接注册固定 Click 命令，Agent 接受 CONFIGURATION 并提供所选配置环境，bcc 工具在自己的 Hub 中接受 CLIENT、直接使用所选 sidecar，命令帮助与新增实现说明统一使用 contrib／contribs。消息接入前完成这一前置任务，完整的命令能力绑定和通讯编排继续在执行任务中交付。
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
6. 通过完整组合升级验证额外包来源、版本要求、连接参数及后端选择继续有效，原版本账本保留，统一迁移按版本接续并保留业务数据。

加载与配置接口提供所选后端缺失、重复后端名、重复 DB 服务、模块类型与目标运行上下文不匹配的诊断。
普通节点、纯 API 节点及混合节点分别验证各自注册表，agent 消费与节点相同绑定的 DB。

## 统一宿主与 server 组合

### 服务契约与启动依赖

所有进程由 `bcn run --config <path>` 启动相同应用入口。模块统一在顶层 contribs 中声明，运行实例由组合明确创建并选择 local 绑定；agent 是可选子树。
普通节点、只运行 server 的节点、同时运行 server 与 agent 的节点都通过同一发现、校验、装配和退出流程。
能力按模块及其配置表达，原 BCS 的集中管理能力由 server 的功能模块承接。
server 的数据位于 ~/.bcn；0.2 BCS 升级时，server-management 在首次启动把 ~/.bcs 的数据库、登录密钥与设置导入 ~/.bcn，原结构和底层查询保持历史数据的含义，~/.bcs 原样保留。
同一 ContribHub 用 server-management 的本地组合装配 storage、通用 sqlite 的私有实例、server 本体与各功能模块，各自实例按服务依赖启动和退出；节点与服务端分别选择数据库后端和连接。
完整路径、读取规则、历史 ledger 与协议要求见 [BCS 连续升级](framework.md#02-bcs-配置与数据源连续升级)。

| 提供者 | 提供 | 消费者与职责 |
| --- | --- | --- |
| http | HTTP | 提供路由、中间件、ASGI 应用挂载与监听；server 本体挂载外壳与嵌入工具，功能模块挂载各自的界面与 API，server-computer 注册节点协议的三个接口 |
| server 本体 | 页面框架与登记入口 | 外壳、嵌入工具、侧边栏、概览、权限点、THEME、语言、refs 与 API 挂载；功能模块与主题插件向它登记 |
| server-access | 登录与权限 | 会话、登录页、账户、角色、分享与权限检查；server 本体与功能模块经它取得当前账户 |
| server-computer | 电脑与对电脑的调用 | 登记、身份、在线状态、节点协议、中转契约与 call；收到 report 后按公共审计契约 announce，agent、会话与活动模块消费 |
| 节点功能模块 | 功能服务、审计事件 | offer 消息、管理等服务，由 starlink 经私有 sidecar 调用；announce 业务审计事件，由 logging 与 starlink 并列 listen |
| starlink | 两端协议的节点实现 | 在节点主动 listen 收取调用，经私有 sidecar 实例分发并执行，announce 返回结果；listen 审计事件并经 report 上报 |

HTTP 的类型和 key 由 `bazaar-contrib-http` 导出；页面框架、登记入口与 THEME 的类型和 key 归 `bazaar-contrib-server`；登录与权限、OIDC 接口归 `bazaar-contrib-server-access`；中转契约与 call 归 `bazaar-contrib-server-computer`。
starlink 公开两端 HTTP 请求格式并持有节点身份、请求关联及 listen／announce／report 生命周期，server-computer 提供对应 HTTP 入口，协议见 [server.md](server.md)。
功能模块依赖这些契约及自身业务依赖；模块导入公共契约即可登记页面、权限点与概览内容。

节点与 server 分别按具体实例的绑定和服务依赖装配，配置条目顺序可以任意：

```text
storage → 所选后端 → database → schema → 业务持久化／starlink 上报队列
节点功能服务 + timer → starlink
http → server 本体 → server-access → 功能模块
主题插件 → server 本体
server-computer → agent 模块 → 会话模块
server-computer → 活动模块、市场模块
共享服务 + builder → agent → channel／harness
```

图中为主要关系，各模块的完整 accepts 包含表中业务前置。server-access 接受 server 本体，登记登录页与权限检查；功能模块接受 server 本体与 server-access，登记页面、API、权限点与概览内容。
server 本体接受 HTTP，在 yield 前挂载外壳并提供登记入口，退出时撤销挂载；各模块退出时撤销各自的登记。
完整组合的入口、授权、投影和页面就绪后，应用才发布 ready 并开放外部请求。

server 与节点经两端协议承担节点登记、请求下发、事件上报及调用结果回传。
节点业务 announce 审计事件 → starlink listen 并入队 → report → server-computer announce 已接收事件 → 活动等模块投影。
完整载荷和时间／顺序关联按[完整行为事件与 NodeAudit](#完整行为事件与-nodeaudit)保留，活动页和网关分别选择所需事件及分析口径。
功能模块查询或下发 → server-computer 的 call → 中转 → 节点 listen → 节点 sidecar 调用功能服务 → announce 返回结果；通知可由节点内 announce。
starlink 负责重连、上报缓冲和确认，server-computer 负责接收去重；请求使用独立的调用编号、超时及错误结果，业务另行发布审计事件。
事件保持原节点、agent、事件标识和顺序，starlink 只上报自己节点的来源，server 的投影消费保持来源身份。
starlink 的 listen 回调只完成入队，网络发送由所属后台任务执行；其接受的服务按配置声明依赖，查询插件状态使用 status 插件 offer 的 CONTRIB_STATUS。
每个模块在自己的 lifespan 中登记方法、监听器与连接句柄，退出撤销所属登记、完成在途请求并释放资源。
停止按依赖逆序结束页面与业务入口、starlink 请求及上报任务、server 本体的挂载和 HTTP 服务；持久化消费者结束后才退出所属组合的 DB、后端与配置载体。

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
2. 需要 server 的 host 安装 bazaar-compute[server]；首次运行由 server-management 的默认配置装好 HTTP、server 与存储等包。
3. server 的监听、访问和存储设置写在节点主配置中 server-management 条目的 config 里。
4. 使用相同 bcn run 启动；agent 列表为空时运行纯 API，有 agent 配置时同时提供 API 与执行能力。
5. server 的默认组合包含 server 本体、server-theme、server-access 与已交付的功能模块；安装第三方功能模块或主题插件后，它们加入 server 子组合，页面出现在侧边栏，主题出现在主题管理中，保存后在下一次启动生效。
6. 其他节点按需安装 bazaar-contrib-starlink，填写目标 server 地址与 token 或 token_env 后启用 starlink。

既有 0.2 BCS 安装 bazaar-compute[server] 后，首次启动时 server-management 把 ~/.bcs/bcs.sqlite3 与 session.key 复制到 ~/.bcn，
把 ~/.bcs/config.toml 的设置写入 server-management 条目，再按原 schema_migrations 账本继续迁移；~/.bcs 原样保留。

模块管理服务从已安装发行元数据展示入口及参数模型，用户选择后提交完整候选配置并校验。
配置资源可以用于新建独立配置，也可以合并已有配置；每个宿主进程拥有独立服务实例，各组合管理自己的本地绑定和生命周期。
包依赖图交给 uv，服务依赖图交给 ContribHub，安装状态、保存配置和实际运行分别展示。

### 各阶段交付与验收

- 第 11 阶段交付 http、server-migrations、server 本体与 server-theme、server-access 与 server-oidc、server-management，详见 [server.md](server.md)；经真实 HTTP 与浏览器验证外壳与 iframe 嵌入、侧边栏、概览、权限点与主题的登记和撤销、语言选择与从 ~/.bcs 导入，并验证 0.2 的账户、密码哈希、角色、分享、OIDC 设置、session.key 与有效 cookie 在导入后照常可用。
- 第 12 阶段交付 server-computer、server-relay-memory 与 starlink，详见 [server.md](server.md)；以隔离的真实两端验证 listen／announce／report、身份、调用与结果、事件确认与重放和退出；server 的调用经节点 sidecar 到达 RemoteEntry 声明的成员；节点填写 server 地址与 token 后启用 starlink，并补充 V4 的 audit／control／server 字段迁移；同一 bcn run 验证 server 与 agent 各自独立 DB 的同进程并行及分进程通信。
- 第 13 阶段交付 agent 模块：经 server-computer 的 call 管理各电脑上的 agent，提供列表、详情、新建、配置、删除与近况的页面和 API，渠道表单由渠道插件的 UI 入口提供；agents.view、agents.create、agents.update 与 agents.delete 按角色与分享生效。
- 第 14 阶段交付会话模块与活动模块：会话列表、会话详情、审批与聊天图片经 call 从节点读取，agents.approve 由会话模块登记；活动模块持久化节点事件并投影近况与当日用量；验证原 BCS 历史、refs 及升级后的新事件在同一数据源可查询。
- 第 15 阶段交付市场模块：按电脑展示各自的索引与已装 contrib，经 call 在目标电脑上安装、升级、配置、启停与卸载；contribs.view、contribs.install、contribs.update 与 contribs.delete 由市场模块登记。
- 第 16 阶段删除原 bcs CLI、独立应用、server sink 和中心 Control 装配，收齐 bazaar-contrib-server 与各功能模块的正式产物、组合配置与真实 0.2 BCS 原数据源升级证据；原目录、数据及不可变迁移资源按所属模块继续保留。

完整组合验收使用实际 wheel、隔离配置、数据库、端口和进程。分别验证普通节点、只运行 server 和混合组合，
停用某个功能模块后重启，其余模块照常提供页面与 API；停用 server-access 的候选配置列出依赖它的功能模块，需要同步调整后保存。
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
| server | server-management lifespan 的本地组合 | 私有 STORAGE、DB 与 HTTP，server 本体与功能模块；管理 ~/.bcn 中的 server 数据与业务数据 |

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

定时能力在数据库之前单独用一个 commit 交付，完成后停下 review；数据库随后用另一个 commit 交付。
公共 `bazaar-contrib-timer` 与实现 `bazaar-contrib-timer-wheel` 是两个独立发行包，消费者和第三方实现共同依赖公共包。
定时能力拆成公共 timer 契约和提供该契约的实现模块。时间轮模块承接分层时间轮实现，
在 lifespan 中创建并启动驱动任务，通过 `ctx.offer(TIMER, timer_service)` 提供服务，退出时关闭驱动与未完成定时器。
BCN 组合声明使用共享 TIMER 绑定的时间轮条目，按所选消费者根的依赖闭包启用；
agent、channel／harness、管理及纯 server 组合均可声明 TIMER 依赖并使用所选实现。

timer 契约由功能契约包持有，定义定时器创建、重置、取消、等待到期、定时精度与最大延迟能力，
以及取消和服务关闭的统一结果语义。各消费者声明 `accepts=[TIMER]`，通过 `ctx.accept(TIMER)` 使用服务。
定时器句柄由创建它的消费者在自己的生命周期内管理，消费者退出时取消尚未完成的定时器；
时间轮 provider 在其消费者结束后关闭。更换 timer provider 时，消费者继续使用同一契约。

Reminder 模块依赖 DB 与 TIMER，自行持有持久化、调度、snooze、更新、取消、到期与 agent 唤醒规则。
其 main 模型、周期计算、业务命令和单全局 frontier scheduler 迁入独立 reminder 包，bcc 命令及文案归 Agent 工具。Reminder 自有状态经 DB 读写；消息经公共消息作用域访问，到期状态与系统消息共用一个 DB 事务。原 Node 迁移账本继续提供 reminders 表，执行组合完成 agent 绑定后启动共享 scheduler。
agent 空闲管理、消息合并等待、渠道限流与重试、版本检查、健康上报和远程连接重连使用通用 timer 服务。
定时器任务先交付公共服务与异常契约，各业务阶段再实现相应调用者并依赖 TIMER 公共服务；旧时间轮及中心装配在最终发行阶段统一清理。
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
原服务端 `storage.py` 的业务模型与查询接口归入 server 本体、server-access、server-computer、agent、会话与活动等功能模块，
数据库 provider 持有通用数据操作与后端翻译实现。
核心和数据库模块的最终依赖图仅包含通用契约，各功能通过 db 或其他功能拥有者的服务协作。

Node 的 agent、messages-node、collaboration、reminder 与 Server 各自拥有业务表的迁移和当前基线，
版本、表结构和数据变更统一通过 schema 的 Migration 和公共存储操作表达。
Legacy Node Migration 仅登记完整的已发布 Node 1～32 并接续旧库；Node 业务 contrib 直接接受 SCHEMA 并各自登记，Server 提供自己的登记入口。
登记包身份自动来自当前 contrib 的发行元数据，ModuleSchema 只声明表、版本及历史来源；旧账本记录保留原身份。
当前 0.3 发行包及独立 Server／PostgreSQL 使用 `0.3.0rc1`，内部依赖、默认组合和配置转换需求统一为 `~=0.3.0rc1`，接受后续 RC、正式版及 0.3.x；公共 API 契约版本继续为 0.3。
历史升级已覆盖最早发行包 0.1.3 和最早标签 v0.1.0 的真实独立安装；仅本次最早版本验收使用一次性容器。
经原公共存储生成数据后直接安装当前产物，配置转换、全业务表对照、公共消息查询及两次独立进程重启通过；
完整 CLI 依赖恢复仍需尚未发布的 0.3 索引产物，当前以真实本地包验收已交付的业务服务。
业务数据库服务依赖 DB 及对应迁移入口的就绪服务；登记入口依赖公共 schema 服务，后者使用同一个 DB。
具体 SQLite／PG 实现由组合配置选择。业务迁移的启用由显式依赖决定。
已有账本的版本用于跳过已执行步骤、补齐缺少的变更；统一迁移实现去掉 checksum 校验。
原 1…32／1…7 的实际变更转为公共操作，旧账本与 0.3 已有统一账本的进度接入同一迁移引擎。
Node 历史升级保持原版本顺序，完成后业务包按前身版本声明各自交接表归属与基线进度；交接与新版本处于各包登记事务，后续仅推进所属包的 ledger。
空库直接应用模块声明的当前基线并记录版本，之后使用同一引擎执行新版本。
新索引和 schema／数据变化追加迁移；历史 src 作为只读验收对照。
数据库验收使用真实旧版本数据库及新模块组合，验证记录、关联、游标、基线、事务和跨功能业务流程。

2026-10-18 将 contrib 存储权限及 Sidecar 自我授权风险记为后续独立设计待办，当前保留业务迁移各自登记。
讨论涵盖登记决定表权限、激活前同名冲突预检、storage 内部控制授权、结构化请求校验与同权限 Python 的执行边界；
现状、候选方案及暂缓结论见[后续安全待办](framework.md#后续安全待办)。

### provider 与运行实例

channel / harness 的公共协议属于对应契约包。builder 在节点组合中注册，agent 根据已校验配置创建 channel / harness 的实例条目及私有绑定。
新组合通过注册表装配 provider；原有类型枚举和公共装配分支在最终发行阶段统一清理。
provider 的第三方依赖留在自己的发行包，app 发行包通过明确的默认依赖安装需要的模块。

### 应用与界面

`bazaar-contrib-server` 用本地绑定的子组合管理服务端实例；server 本体提供页面框架与登记入口，功能模块按相同绑定声明所需服务。
server 与节点经两端协议通信；starlink 通过 listen 和 accept 接入节点插件，页面、API 与 starlink 分别拥有生命周期。
功能模块登记页面、API、权限点与概览内容，渠道模块提供配置 schema，渠道的 UI 入口提供 agent 模块使用的表单。
外壳、导航、主题、语言与页面权限由 server 本体与 server-access 执行；server 可以只运行登录与已安装的模块。
全部组合由 app 的普通 bcn run 入口调用 core 的同一运行接口，core 宿主依据明确实例及服务依赖装配。
完整依赖图、配置资源、部署与清理验收见 [统一宿主与 server 组合](#统一宿主与-server-组合)。

### 安装、发现和升级

`bcn contrib` 与节点升级共用自身 uv 定位、安装执行和依赖清单。
默认用户执行一次宿主升级，目标 `bazaar-compute-node` 与 core、默认官方组合和已安装的 server 与功能模块等可选包按各自依赖范围一起解析；用户指定的兼容旧版本可以保留。
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

普通节点、只运行 server 和混合组合验收使用测试专用配置、临时数据库、socket、包环境和隔离进程。
真实 provider 使用 TestChannel 控制面；native service 测试在各平台独立隔离 CI runner 执行。
已发布迁移保持不可变，模块迁移和查询验证使用隔离数据库。

## 0.3 发行清单与发布顺序

完整能力集合以本路线图的发行包及入口表为准。
app、core、公共接口和节点业务包在 BCN 共同发行，六个默认实现与可选包在 open-bazaar 的独立仓库发行；各阶段记录所属仓库、发行名、入口和集成证据：

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

1. 收齐清单中的正式包和完整入口，验证包依赖闭包、core 的通用边界与整体候选组合；验收通过后统一切换入口，一次清理旧 src、旧测试、依赖、repository、装配与适配层。
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
   完成普通节点、只运行 server 及 server 与 agent 混合组合的统一 bcn run 启动、多节点连接、模块索引与本地 / 远端模块管理、核心用户流程、
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
