# server 框架、登录、两端通信、agent 模块与会话和活动（第 11 至 14 阶段，2026-11-04）

依据消息 `063a69a7-4466-5989-987d-72340ff87d15`、`4a3df703-d8ba-52fd-8386-a80636c7ebd3`、`4a1410e9-fdd9-5e6d-9258-2b6524c85030`、`54a06153-2c7a-50d8-be38-d83215bb119f`、`a71294e0-d7b2-55ad-b070-0d39c6615bfc`、`361bd816-6861-5387-9de3-db06a0221bff`、`4a61b278-dbba-5110-b22a-1a48be84ee85`、`35a6f778-8313-5345-b815-dd56aadb82f4`、`5b10044f-5d13-593f-bdc1-f702f38d28c1`、`61a8fb0b-6faf-5b41-9845-5ce2b6255f50`、`0152f366-79a3-58a2-a4b8-097b74292762`、`048927d2-bcdb-5149-b3cd-74ba46652bd6`、`46bf2276-bd23-5186-a0e7-2e7fe36c32f6`、`6c355ba6-0bad-5525-92d7-0763f87ba60b`、`1f12a86a-5b7a-5cd1-8ab0-ab30cd64cbd4`、`75056a17-d242-53e0-b3d3-5541c3f8c2d0`、`c51fe75f-080c-5bbb-a5d4-73ebf2b05257`、`680370b8-e577-5ff8-86f0-8e7711ff16a2`、`34dd6558-b928-5db1-8744-dc150f1bcb1c`、`9fbda575-6578-574f-a885-eca2e6781f14`、`9b4c789d-27b9-52c0-a480-da6f675b957f`、`7392d984-82ef-5b71-a2dd-0f82fc8404bd`、`ae4a2fb3-637c-51f1-8661-9b00d43896c0`、`74cca34d-90c1-550a-ac3b-a75da7596efa`、`dec88203-3335-53ad-8f32-f599838099b5`、`732ad76b-eb60-5441-8c98-3c101345f20d`、`2377ea79-daee-5aff-8af4-efd3335a0a63`、`1ca34615-3618-57f4-a74c-a5de417fce7d`、`a00b3959-3fb4-5bd1-b080-ad718285cf71`、`96787fb9-cffc-545b-9637-cb35c238d7fc`、`72759245-29a2-5f1c-8420-f586190ac434`、`227a718a-65ce-55b2-bd6b-718bc585370f`、`9c4b2829-5eab-51e6-be65-ffacd7230ac2`、`8fe23c62-55a0-5ad2-9c8f-486f3459136d`、`60b4a535-b2cc-5ee2-954d-42cdd48ada8b`、`0848f322-562e-52d0-be22-8b2a5dbcc317`、`6d7ef808-9bbb-54d2-ba03-ab2ecfba8b79`、`11e6a63e-2572-5129-8f62-42c46e3844ca`、`4a309095-83e0-505e-be68-c6a0f6fe1c2b`、`c78439a5-8fcb-5c72-99b5-3fb718c03e55`、`ee83ba8e-da47-5385-b23a-bc34e7a6f58f`、`a088f3ea-c6c2-5f14-8831-3f1844592172`、`62072b45-7a4e-5a3a-b33b-ada5f46fa02a`、`2128b685-1165-5ffc-ae59-1bf377c0d729`、`a9707a85-61c1-5b2f-8212-6501f4a27597`、`89409f44-6c7f-5559-98dd-3c92e023980c`、`9a8a560e-ea1a-5b92-af21-27c315705f4f`、`04ebe6bb-3fde-5699-9478-cd4f92b06d0f`、`069a6203-552c-58c5-b57c-53b3dd5f055f`、`fe92f9bd-d2a3-5ee9-98ac-d05dfb4de85b`、`c829259a-d24e-5658-8862-8340fa564e6a`、`d929e3b7-1560-5a59-9d89-bb9bb0638eab`、`df181db4-2fb1-5bf6-9f35-13804366c141`、`fe7e2369-5a98-5338-b704-cc100dee30c9`、`145bdfe5-a2ba-5d54-98ee-b5a4c7fcf064`、`ea154f6b-e6ab-5e37-b761-21b8d3ba417d`、`0f163715-de78-5dda-ae47-86f1122c45d8`、`cca4a947-a5ad-51d8-8a61-8e890c84e2f8`、`c343e7fe-6a21-58a4-ba78-e4e9b031145d`、`2777ee0e-7dc2-5078-b8c8-26b36785fa88`、`c4993a58-dad1-5fb5-91a0-49252e01399c`、`2e906727-a570-51b8-b30d-dc5badd3b7b0`、`713d4cb5-ce3d-57a5-b325-ed96cc9d9b48`、`3cae9028-3f2d-552b-b954-adde6281dc51`、`e3d0a24f-d713-56f8-ad56-54af4e76f72f`、`eb67ecd4-cfc7-539b-8e4b-53608b0f134f`、`f9b97a53-3853-52dc-819c-9e669fdfeba1`、`617a0fd1-b2a3-50e9-86a0-edaacc3d31bd`、`0f086c35-1137-5163-81fc-8b175395b42e`、`ad1081e6-5140-5fe4-a0f6-0ec0cb58afb4`、`ccc3383f-6d1f-578e-a87f-36fd93a35d58`、`9dd2dc7d-666f-5ce3-9d64-d17cc57f2987`、`09872486-2607-5d05-bee4-a6a1387b1bd4`、`4ad7059a-2d64-5d5e-ae0d-5a3deb9a9366`、`2a8906da-4f07-5bb5-9bfd-1baab7146b33`、`13ecfc45-fbf6-583f-90ca-883d5aac1b6a`、`e1be9e30-31be-593a-afc4-056a711625f5`、`e0f47cec-5443-514b-b6d2-0a1c645071c7`、`18bee733-8431-54f9-945c-51158293a018`、`cbcdf14c-ef83-5766-b74b-17b8184f723b`、`1c8d85e7-863f-56d5-8664-2a2fb75270fa`、`7d53ab4b-35f8-56d7-8fa1-b60303bfa780`、`8212bc19-143e-5d78-97e5-70c3c74a43fc`、`3863d98c-c549-54eb-8edf-8ee607015e18`、`1f1f3c09-9828-51b3-8841-6e66f0645a09`、`cd0d485e-e55d-55c4-8ecf-e8b99a10b355`、`b85f0b10-310b-5770-8568-fecb79b3d4cf`、`4e0c6800-19d0-55de-927b-c8fc865c5cc7`、`1c2fc42d-da82-50b7-8c80-532008f78dd8`、`d7bd5618-3bfb-5721-9b2b-076b5657d172`、`b4fe9a9a-edd1-545d-a99d-c1fd30535d81`、`e2c30e82-dfea-5ac9-963f-581b093bfe12`、`be8a0129-a813-545d-861a-caf9e65171e7`、`3635b11e-1ada-5ae2-a6aa-4e5831a67d24`、`329cbbe6-0081-56f5-a671-d55992044274`、`5e52ede8-6af3-5c97-87a7-893aee7ae924`、`5e16fc9a-dd9d-54a4-8c5e-268f5b27dd36`、`078e3fe1-9b1c-50ef-93db-ec15faef03fa`、`e7f4fc3e-9052-5fbc-9dd9-133b398c89a2`、`df3c0868-77bf-52fe-8c70-0cdd81388ab5`、`dfd204ee-c750-5286-9b61-1e6101260e74`、`e87141a6-52f0-5170-9e2b-7f3fc394956a`、`bf63081c-17a8-5756-b418-7ce8ad07cdf8`、`b6e85bac-d38c-53fc-a8fc-3a5011f2a668`、`4ed365e2-198e-5816-bc16-05d2db22a5cf`、`3c6c1309-9a04-5665-9af3-dc4794c82b86`、`94db16e3-38c8-526a-b333-a0f7380b4ba3`、`7ff6f598-b4e6-5482-8adf-df771cf6dac2`、`1c7fc7a7-1fd5-575a-bf28-336acbf4bc5b`、`6d421b15-70d6-5fd4-96cf-acd279f90d2d`、`5e80679b-4a7e-51e7-9a7a-62e9d96aac36`、`c5a00df4-3e69-55d2-b0a4-159ed4260436`、`0a7b25d8-336e-5423-ab32-e780b72a9568`、`405e66f2-141c-51b4-a6d3-5a97a5b64a72` 与 `1529f668-8abe-5aab-816d-76a2dfe08990`：

- 服务端产品名称统一为 Bazaar Server；界面和说明文案中的扩展模块统一称为 Contrib。
- 节点协议不兼容 0.2；server 的功能与数据结构保持一致，实现可以重新设计。
- server 按功能模块拆成 contrib：每个模块提供自己的页面，accept 所依赖的模块；server 本体提供页面框架与基础 API 服务。
- server 的 contrib 与节点的 contrib 是同一种插件，机制相同：都在配置顶层启用，由 contrib manager 安装、启用与停用，需要哪些服务就 accept 哪些；任何插件都可以 accept 它需要的服务。
- 私有绑定只用于同一接口的第二个实例：server 的数据库是 STORAGE、DB 与 SCHEMA 的第二个实例，以私有绑定装配后以 SERVER_DB 与 SERVER_SCHEMA 提供，与 harness 的多个实现同理。node 与 server 的数据库各自独立，可以选用不同的后端。
- server 的设置写在各自所属条目的 config 里。
- server 的结构与节点对应，遇到问题先参考节点的做法：节点由 storage 的 database 条目提供 DB、legacy-node-migrations 接续 0.2 的历史；server 由 server-database 提供 SERVER_DB 与 SERVER_SCHEMA、server-migrations 接续 0.2 的历史。
- 侧边栏与权限点的登记入口由 server 本体提供，概览内容的登记由 server-overview 提供。模块登记带名称与图标的页面后，页面出现在侧边栏；概览在问候之下展示各模块登记的内容，顺序按账户调整。
- 模块页面运行在同源 iframe 中，前端框架由模块选择，主题、语言与导航经框架的嵌入工具取得。iframe 用于前端框架的独立；服务端以只安装可信的 contrib 为边界。
- 主题经 THEME 接口由主题插件提供，在设置页的外观部分选择；语言由框架统一给出，是否提供多语言由模块决定。
- 页面以 [design](design/README.md) 中的效果图为准：桌面与窄屏、夜市与晨市，包括登录页插画与摊位卡片棚子的画法。
- 电脑的登记、身份、在线状态与对电脑的调用归 server-computer 模块，节点协议的三个接口由它注册。
- 电脑详情所需的机器信息由节点的 node-management 经 RemoteEntry 提供，server-computer 经调用取得并缓存，页面经统一总线刷新；电脑可以改名。
- 0.2 的表结构由 server-migrations 按原历史接续，之后各模块持有自己的表，并按需新增。
- 节点经三个相互独立的 HTTP 接口接收调用、回传结果、上报事件，结果不混入事件上报；server 对节点的调用经节点 sidecar 分发。
- 副本之间的协作由可替换的中转 contrib 实现。
- 未确认的上报事件只留在有界的内存队列中，server 不可达时按退避重试。
- 页面写入的凭据进入 0.3 配置；server 的数据全部位于 ~/.bcn，首次启动从 ~/.bcs 导入。
- 缺少的节点能力由它所属的 contrib 补齐，没有所属时由新的 contrib 补齐。
- 所有计时经 TIMER。
- server 的功能都由 contrib 实现，server-management 也在自己的仓库中维护；第 11、12 阶段中，BCN 仓库只有 config-versions 的 V4→V5 转换，以及 node-management 为电脑提供的机器信息，其他逻辑不变。
- 宿主读取各已安装包在自身 pyproject.toml 中声明的 `bazaar_compute_node.defaults` 入口并合并默认配置，主仓库只按包名引用 server-management；其他 management 包同样在各自的仓库发行，与 bazaar-compute 一起安装即可使用，不需要改动主仓库。

## 分支

BCN 仓库的改动在 `0.3-server` 上进行，该分支从 `0.3` 的 cd8470d 开始；第 11 至 14 阶段完成整体验收后合入 `0.3`。
新仓库在各自的仓库中按 Task review。BCN 分支记录它们的固定版本，并验证跨仓库组合。

## 包与仓库

| 发行包 | 仓库 | 入口 | 职责 |
| --- | --- | --- | --- |
| bazaar-contrib-http | open-bazaar/contrib-http | http | HTTP 契约（路由、中间件与 ASGI 应用的登记、监听地址、请求生命周期）及 Starlette／uvicorn 实现 |
| bazaar-contrib-server-database | open-bazaar/contrib-server-database | server-database | 以私有绑定装配 server 的数据库（storage、所选后端、database 与 schema），以 SERVER_DB 与 SERVER_SCHEMA 提供；打开数据库前从 ~/.bcs 复制数据库与 session.key |
| bazaar-contrib-server-migrations | open-bazaar/contrib-server-migrations | server-migrations | 以原 v01–v07 为历史接续 0.2 的 Bazaar Server 表结构，以 upgrade_only 登记，完成后提供 SERVER_MIGRATIONS |
| bazaar-contrib-server | open-bazaar/contrib-server | server、server-theme-night、server-theme-day | server 是 server 本体，提供外壳、嵌入工具、侧边栏、设置页、统一总线、权限点登记、主题接口、语言、refs 与 API 挂载；server-theme-night 与 server-theme-day 是默认带上的两个主题插件 |
| bazaar-contrib-server-overview | open-bazaar/contrib-server-overview | server-overview | 概览页与问候、概览内容的登记与每个账户的排序 |
| bazaar-contrib-server-access | open-bazaar/contrib-server-access | server-access | 会话、登录页、账户、角色、分享与权限检查，并定义第三方登录方式的接口 |
| bazaar-contrib-server-github | open-bazaar/contrib-server-github | server-github | 以 GitHub 账户登录，实现第三方登录方式的接口 |
| bazaar-contrib-server-computer | open-bazaar/contrib-server-computer | server-computer、server-relay-memory | server-computer 持有电脑的登记、身份与在线状态，注册节点协议的三个接口，定义中转契约，提供对电脑的调用以及电脑的页面；server-relay-memory 是中转的单进程内存实现 |
| bazaar-contrib-server-management | open-bazaar/contrib-server-management | 无 contrib 入口，只有 defaults 入口 server | server 的默认配置；生成配置时读取 ~/.bcs 中 0.2 的设置 |
| bazaar-contrib-server-agent | open-bazaar/contrib-server-agent | server-agent | agent 模块：经 call 管理各电脑上的 agent，提供列表、详情、新建、配置与删除的页面，以及侧边栏的「正在工作」（第 13 阶段）；以 AGENTS 提供 agent 的读取与详情页 tab、动态区块的登记（第 14 阶段） |
| bazaar-contrib-server-activity | open-bazaar/contrib-server-activity | server-activity | 活动模块：接管 events，持久化节点上报的事件并去重，投影回合与用量，以 ACTIVITY 提供给其他模块；动态中的回合与用量、完整活动记录、摊位中的智能体与概览的集市动态（第 14 阶段） |
| bazaar-contrib-server-conversation | open-bazaar/contrib-server-conversation | server-conversation | 会话模块：经 call 读取会话、消息、成员、提醒与附件，审批、撤销与自动回复；会话页、智能体的会话 tab、动态中的提醒与概览的需要你处理；登记 agents.approve（第 14 阶段） |
| bazaar-contrib-server-usage | open-bazaar/contrib-server-usage | server-usage | 用量插件：概览中 agent 的概况与所选时间范围的回合和用量（第 14 阶段） |
| bazaar-contrib-starlink-conversation | open-bazaar/contrib-starlink-conversation | starlink-conversation | 节点端：经 DB 直接读取节点上会话、消息、附件与提醒的表，以 remote `conversation` 供 server 经 starlink 调用；由会话模块按需安装到节点上（第 14 阶段） |
| bazaar-contrib-starlink | open-bazaar/contrib-starlink | starlink | 节点侧长轮询、经 sidecar 分发调用、回传结果、上报审计事件、退避重试 |

- contrib-server-migrations 由原 contrib-server 改名而来；contrib-server、contrib-server-overview、contrib-server-access、contrib-server-github、contrib-server-computer、contrib-server-database、contrib-server-management 与 contrib-starlink 是新仓库。这些仓库都按现有 contrib 仓库建立 CI 与发版流程，十个新包各自登记到 contrib-index。
- server 的数据库由 server-database 条目选择后端，缺省为通用 sqlite 的私有实例。通用 contrib 只负责读写，表结构与查询归各业务 contrib。
- server-management 像 node-management 一样，在 `bazaar_compute_node.defaults` 入口组中登记 server 的默认配置，并与其他插件一样安装：
  - 与 bazaar-compute 一起新装（`uv tool install bazaar-compute --with bazaar-contrib-server-management`）的 host 首次运行即成为 server；
  - 同时装有 node 的包时，host 同时运行两者；
  - 已有的宿主经 contrib manager 加入 server 的包与条目。

  本条取代 framework.md「按 extra 选择初始依赖」中 `[server]` 随 server 交付的安排；宿主升级时，manager 把配置中的 contrib 与新宿主一起解析安装。
- 节点默认组合不包含 server 的包；节点填写 server 地址与 token 后才启用 starlink。

## server 的条目与数据

- server 的数据都在 ~/.bcn 下，登录签名密钥为 ~/.bcn/session.key。
- server 的条目在配置顶层启用，与节点的条目共享 TIMER 等服务。第 11 阶段的默认配置启用 http、server-database、server-migrations、server、server-theme-night、server-theme-day、server-overview 与 server-access；第 12 阶段加入 server-computer 与 server-relay-memory；第 13 阶段加入 server-agent；第 14 阶段加入 server-activity、server-conversation 与 server-usage。需要 GitHub 登录时，经 contrib manager 加入 bazaar-contrib-server-github 并启用 server-github。
- server-database 按自己条目里的存储选择，在私有绑定中启用 storage、所选后端、database 与 schema，再以 SERVER_DB 与 SERVER_SCHEMA 把这份数据库提供到顶层；节点照旧使用 DB 与 SCHEMA。
  - 存储选择与参数沿用原 Bazaar Server 的 storage 及同名参数表，缺省为通用 sqlite，以现有的 name 参数打开 ~/.bcn/bcs.sqlite3，与节点的 bcn.sqlite3 分开。两边旧的 schema_migrations 账本列与版本各不相同，不合并。
  - SERVER_DB 与 SERVER_SCHEMA 由 bazaar-contrib-server-database 导出；用到 server 数据库的模块 accept 它们并依赖该包，例如 server-migrations accept SERVER_SCHEMA。server-management 不依赖任何 server 的包，发版顺序为 server-database、server-migrations、server-management。
- 设置写在各自所属条目的 config 里，缺省值沿用原 Bazaar Server 模型：
  - listen 在 http 条目；
  - 存储选择与参数在 server-database 条目；
  - session_minutes 在 server-access 条目，retention_days 在活动模块的条目，随各自的 Task 交付。
- 从 0.2 Bazaar Server 升级：
  - 还没有 ~/.bcn/config.toml 时，server-management 的默认配置按 0.2 的规则读取 ~/.bcs/config.toml，把设置写进对应条目；
  - 已有 0.2 节点配置时，config-versions 的 V4→V5 转换在 ~/.bcs 存在时声明 server 的包、启用其条目并写入 0.2 的设置，缺少的包由 contrib manager 补装；
  - session_minutes 与 retention_days 在 server-access 与活动模块交付时加入上面两处；
  - server-database 打开数据库之前，若 ~/.bcs 存在且 server 的数据库还不存在，先复制 session.key，最后复制 bcs.sqlite3 及其预写日志，中断后下次启动重新导入；之后由 server-migrations 按原账本继续迁移。

  ~/.bcs 只读，原样保留。

### 表与迁移

- server-migrations accept SERVER_SCHEMA，以原 schema_migrations 的 v01–v07 为起点，接入统一的 contrib_schema_migrations 账本，并以 upgrade_only 登记：空库时不建表，旧库接续到 v07；已有表与数据保持不变，完成后提供 SERVER_MIGRATIONS。
- 各模块在 SERVER_SCHEMA 上以 ModuleSchema 的 predecessor 声明 bazaar-contrib-server-migrations 的 v07 为前身，并带自己的基线：空库时执行基线建表；旧库接续完成后，schema 把原表归属转给该模块并登记它的基线，不重放建表。做法与节点的 legacy-node-migrations 相同。
- 0.2 的表按模块归属：
  - server 本体：refs；
  - server-access：accounts、roles、account_roles、auth_settings、oidc_providers、oidc_identities，以及记录归属与分享的 relations；
  - server-computer：computers；
  - events：由第 14 阶段的活动模块接管，此前留在 bazaar-contrib-server-migrations。
- 模块新增的表登记在各自的版本链上，例如概览的排序表在 server-overview 的版本链上。server 本体的第 3 版删除它第 2 版建的 overview_order。
- 表之间以 id 关联，关联的行由代码显式删除。0.2 的历史中带有外键，server-migrations 照原历史重放；接管表的模块在自己的下一个版本中重建这些表，去掉外键，数据原样保留。
- server-access 的第 2 版：
  - 删除 oidc_providers 与 oidc_identities；
  - 把 account_roles 与 auth_settings 重建为不带外键的表，auth_settings 只保留 id 与 default_role_id；
  - 为 accounts 加入 display_name、avatar_url、day_theme 与 night_theme：display_name 与 avatar_url 是第三方登录给出的显示名与头像地址，day_theme 与 night_theme 保存白天主题与黑夜主题。
- server-github 在自己的版本链上建 github_settings 与 github_accounts：前者是单行的 client_id、client_secret、回调地址、默认角色与会话时长；后者以 GitHub 用户 id 记录对应的账户、login、显示名、邮箱与访问令牌。

## 第 11 阶段：server 框架与登录

### 外壳与嵌入

- 地址分为四类：
  - 外壳在 `/<模块>/<路径>`，由侧边栏与一个 iframe 组成，iframe 加载 `/ui/<模块>/<路径>`；顶栏由页面自己画；
  - 模块界面挂在 `/ui/<模块>` 下，是模块自己的 ASGI 应用，经 http 的 mount 挂载，前端框架由模块选择；
  - API 挂在 `/api/<模块>` 下，例如统一总线的 `/api/server/poll`；
  - 静态资源在 `/static/<模块>/` 下。
- `<模块>` 是模块登记时给出的路径名，例如 computers、agents，与入口名无关。`ui`、`api`、`static`、`login` 与 `starlink` 是保留名，模块的路径名不能使用。
- 外壳与模块界面同源，登录 cookie 对两者同样有效，写操作的同源检查不变。
- 登录检查由 server-access 登记到 server 本体，对请求与 WebSocket 一视同仁；没有登记时，非公开的页面与 API 一律返回 503。
- server 本体对它挂载的页面与 API 执行请求限制：请求体上限 1 MiB，超出返回 413；通过登录检查的写操作与 WebSocket 要求 Sec-Fetch-Site 为 same-origin 或 none，没有该头时 Origin 的主机须与 Host 相同，两者都没有时放行。
- 嵌入工具由 server 本体提供：
  - 主题样式表：全部已登记主题的 CSS 变量与基础组件样式，每套主题限定在 `[data-theme="<名称>"]` 下；
  - 嵌入脚本：设置当前主题与语言，把 iframe 内的跳转同步到外壳的地址栏与标题，把页面高度报告给外壳，把页面的订阅交给外壳的统一总线，并把浏览器的时区写进 `bcs_tz` cookie；
  - Jinja 基础模板：引入上述样式表与脚本，提供 0.2 的共用过滤器与模板全局（ago、clock、day、time、markdown、asset、ref、t），以及按名字给出定稿设计头像色块的 tint；模板带页面的顶栏：返回、标题、面包屑与右侧的操作区，由页面填写，例如总览的「新建智能体」与智能体详情的「共享」。其他前端框架的页面以同样的样式自己画顶栏。自家模块在 iframe 中以它渲染 Jinja 与 htmx 页面，沿用 0.2 的 StrictUndefined 与自动转义。
- 外壳与模块界面的响应带 CSP `frame-ancestors 'self'`。页面响应带 `Cache-Control: private, no-store`；页面按请求的 X-Timezone、其次 `bcs_tz` cookie 的时区渲染时间，都没有时按服务器本地时间逐个时间点换算；静态资源版本变化时经 `HX-Trigger: stale` 提示刷新，与 0.2 的 Stale 相同。
- 浏览器自带的界面一律由 server 本体在主题中画出，各模块不使用浏览器原生的样子：select 展开的列表（宽屏为挂在框下的浮层，窄屏从底部弹出，画在最外层窗口、盖过各层 iframe，键盘与读屏照原生 select 的方式操作）、表单校验的提示（字段变红，下方一行页面语言的说明）、悬停提示（`data-tip`，不用 title）、复选框与焦点环；数字框不带步进箭头，搜索框不带清除按钮，密码框不带浏览器的显示按钮，自动填充不改底色，文本框随内容长高、不带拖拽角，依据消息 `a9ec5167-0822-5198-bde9-d1439b673db2` 与 `80374e1c-5a0a-5658-bbc3-db61f6bd3f2b`。
- 列表与消息的翻页一律在滚动到末尾时载入，载入中显示 server 本体的「正在加载」行（转圈与说明），依据消息 `5fffe772-1496-58fc-83f6-25da7e54f504`。
- 外壳、登录页、favicon 与 PWA 图标使用同一个品牌标识：一组条纹帐篷。
- 页面上交给外部系统填写的地址，例如第三方登录的回调地址与电脑接入用的 server 地址，由浏览器按当前页面的 origin 生成。

### 窄屏与 PWA

- 宽度小于 960px 时外壳与页面改为单栏，与 0.2 的手机版相同：
  - 侧边栏的登记项改为底部导航，图标加文字；多于五项时第五项为「更多」，展开其余各项；
  - 页面的顶栏显示标题，详情页带返回上一层的入口；设置页先显示目录，目录末尾是退出；
  - 输入框文字不小于 16px，常用触摸目标约 44px；外壳使用动态视口高度，并为安全区留出边距。
- server 本体提供 PWA 的 manifest、192 与 512 像素的图标及 apple-touch-icon，外壳与登录页引用它们并给出 theme-color。
- 从主屏幕打开（standalone）时不能缩放：视口不缩放，并在每个页面拦下双指与双击缩放；浏览器标签页中照常缩放，依据消息 `f526cd28-c37a-5696-9f96-326a8a987efb`。

### 侧边栏、概览、设置页与权限点

- 侧边栏登记项包括名称的文案键、图标、外壳路径、分组的文案键、查看所需的权限点、顺序，以及可选的徽标主题。当前账户具备该权限点时才显示。同一分组的链接排在同一个标题下，组内按顺序，各组按组内最靠前的链接排列；server 本体自带工作台与扩展两个分组。徽标是链接右侧的数字、未读标记或「即将上线」，由插件经统一总线给出，手机上画在底部导航图标的右上角。
- 首页跳到当前账户能看到的第一个侧边栏链接。
- 设置固定在侧边栏底部。底部的账户卡片显示头像与名称，并带白天与黑夜的切换，点选即为总是白天或总是黑夜；退出在设置页目录的末尾。
- 侧边栏还可以登记组件，包括位置（某个分组之下，或侧边栏最上方）、内容地址、查看所需的权限点与顺序。宽屏时内容在分组下以 iframe 载入，窄屏时放在底部导航上方的一条中；插件按嵌入页面的 data-layout 画两种样子，内容经统一总线更新。例如 agent 模块的「正在工作」在宽屏列出工作中的 agent；在手机上轮播，每次显示一个 agent 的灯笼、名字、会话与时长，点按进入该 agent。
- 概览由 server-overview 提供：它以 OVERVIEW 提供概览内容的登记，想在概览上显示内容的插件 accept 它。登记项包括键、标题的文案键、在 iframe 中加载的内容地址、查看所需的权限点，以及占用的列数（1–3，缺省 1）。概览页按当前账户保存的顺序展示，没有调整过的按登记顺序；它把总览登记在侧边栏的工作台分组中。
  - 页面顶部是问候：按浏览器的当地时间，5–10 点为早上好，11–12 点为中午好，13–17 点为下午好，其余为晚上好，后接账户名。
  - 内容排在宽屏三列、窄屏一列的网格中，各占登记的列数。server-overview 不给内容加卡片与标题，分区标题与外框由登记的模块按 design 自己画。
  - 顶栏的「调整顺序」打开后，每块内容显示前移与后移按钮，点「完成」收起。
  - 排序表记录账户、登记键与位置，登记在 server-overview 的版本链上。
- 设置页由 server 本体提供，外观部分由它自己登记；其他模块登记设置页中的部分，登记项包括键、标题的文案键、在 iframe 中加载的内容地址，以及查看所需的权限点或仅 admin 可见。
- 权限点登记项包括权限点与说明。权限点写作「模块.动作」，同时是文案键。server-access 读取登记生成角色页的选项并执行检查；0.2 已有的权限点名字保持不变。

### 统一总线

- 页面与外壳中需要随时更新的部分经统一总线订阅，沿用 0.2 的 /poll：插件向 server 本体登记主题，给出名字、范围字段、取当前状态的函数、状态模型与权限检查。
- 订阅包括主题、范围（短编号）与已见状态（摘要或游标）。订阅管理器只在最外层的外壳中有一个，各层 iframe 中的页面同源，把订阅交给它；每轮最多每秒一次，把整个窗口的订阅去重后以一次 POST 发给 server 本体。
- server 本体逐个订阅检查权限并取当前状态，只返回变化的状态；目标不可见或主题没有登记时返回不可用。订阅的部分据此自行刷新。
- 请求限时 15 秒；页面隐藏时暂停；构建版本变化时提示刷新，与 Stale 相同。
- 侧边栏的徽标与组件同样经它更新。

### 主题与语言

- THEME 接口：主题插件向 server 本体登记主题，每套包括名称、显示名的文案、全部 token 的取值，以及只在该主题下生效的附加样式。token 沿用 design/src/tokens.css 中的名称，另加登录页插画使用的配色，即 design/src/scene.css 中的 `--scene-*`。
- 主题与基础组件只用纯色：定稿设计中的渐变与发光一律改为纯色，条纹与扇贝边等硬边图案保留。
- 夜市（night）与晨市（day）是两个主题插件 server-theme-night 与 server-theme-day，随 bazaar-contrib-server 默认带上，各自登记一套主题。
- 账户的 theme 列沿用 0.2，保存主题模式：light 为总是白天，dark 为总是黑夜，留空时跟随系统。账户另存白天主题与黑夜主题，各为一个已登记主题的名字，两套主题都可以选在任一项；留空时白天用晨市，黑夜用夜市，所选主题被撤销时同样回到这两套。跟随系统时，系统为浅色用白天主题，深色用黑夜主题。
- 设置页的外观部分包括语言、主题模式，以及白天主题与黑夜主题的选择。两项选择各列出全部已登记的主题，每套以一个矩形色板表示，色板由该主题的主要配色组成，名称在下方。
- 当前请求的语言依次取账户的 language 列、Accept-Language、英文，支持 en 与 zh-CN，与 0.2 的 LANGUAGES 相同。
- 模块自带文案目录（locales/en.toml、locales/zh-CN.toml），键位于模块自己的命名空间。只提供一种语言的模块，缺少的语言回退到它提供的那种。

### refs 与 API

- refs 表归 server 本体，提供值与短编号之间的转换；模板全局 ref 给出链接中的短编号，与 0.2 相同。
- 自家模块的页面由服务端直接渲染为 HTML，局部更新经 htmx 与统一总线完成；模块之间经进程内的服务互相调用，例如 agent 模块经 COMPUTERS 调用电脑。
- server 本体在 `/api/<模块>` 下提供 FastAPI 应用的挂载，统一总线挂在这里。模块有脚本、原生应用或远程管理等外部调用方时，经它挂载自己的 FastAPI 应用，输入输出由 Pydantic 校验并生成 OpenAPI 文档；server 本体提供取得当前账户与检查权限点的依赖，登录检查与写操作的同源检查与页面一致。

### 登录与权限

server-access 接受 server 本体，登记登录页，在设置页登记安全、角色、账户与登录方式四个部分，登记权限点 settings.roles、settings.accounts 与 settings.oidc，并提供当前账户与权限检查；第三方登录方式经它定义的接口接入。行为与数据沿用 0.2：

- 会话：
  - cookie `bcs_session` 包含账户、签发时间、session_version 与到期时间，以 session.key 做 HMAC-SHA256 签名，校验使用恒定时间比较。
  - 账号密码登录的时长取 session_minutes；第三方登录取登录方式的会话时长，没有设置时同样取 session_minutes；会话只保存在签名的 cookie 中。
  - 改密码使 session_version 加一，旧 cookie 随之失效；退出删除会话与续期的 cookie，返回 204 与 `HX-Redirect: /login`。
  - 全部 cookie 为 HttpOnly、SameSite=Lax，经 https 访问时带 Secure。
- 请求检查：
  - 登录页、第三方登录的入口与回调以及静态资源公开；其余经 server 本体登记的页面与 API 需要有效会话，账户及其角色在每个请求中读取。
  - 未登录时，htmx 请求得到 401 与 HX-Redirect，其他请求以 303 跳到登录页；会话过期而带有续期 cookie 的请求先按第三方登录的续期处理。
- 登录页：
  - 宽屏时左右两栏，左侧是集市街景插画，配色取主题的插画 token；右侧是表单，账号与密码的标签在输入框上方，登录按钮与输入框同宽。
  - 窄屏时表单在上，街景在下，两者之间以向下的圆弧分隔。
- admin：
  - 启动时没有名为 admin 的账户就创建，密码为 24 字节随机数的 base64url，以 WARNING 写入日志一次。
  - 本地的 admin 拥有全部权限点，可见全部电脑与 agent，可以分享任何对象；只有它能在设置页的安全部分修改密码。新密码至少 8 个字符，须提供当前密码。
  - 密码以 scrypt（N=2^15、r=8、p=1、32 字节输出、16 字节盐）保存，格式为 `scrypt$N$r$p$salt$digest`；同时计算的数量不超过 CPU 数与 4 中较小者；不存在的账户也按同样代价比对。
  - 本地账户只有 admin；第三方登录的账户在首次登录时创建，设置页的账户部分只分配角色。
  - 页面上的账户名取 display_name，没有时取 name；有 avatar_url 时以它为头像，图片带 `referrerpolicy="no-referrer"`，没有时显示按名字着色的色块。
- 角色：
  - roles.permissions 是排序后的权限点 JSON 数组，账户的权限是其全部角色的并集，admin 除外。
  - 角色名唯一，长度 1–80。全局默认角色保存在 auth_settings.default_role_id；删除默认角色须指定替代，被登录方式用作默认角色的角色不能删除，删除角色时一并删除它的分享与账户的分配。
  - 角色表单按模块分组列出已登记的权限点及说明；保存时保留角色中当前没有登记的权限点。
  - 缺少权限点返回 403，目标不可见返回 404。
- 分享：
  - relations 记录 computer_owner、agent_owner、computer_share_role 与 agent_share_role 四种关系，分享的权限点放在 ext 中。
  - 持有目标的模块经 ACCESS 登记一类可分享的目标：类别（如 computer）、分享所需的权限点、可分享的权限点，以及按 id 取目标名称的函数。归属由模块在建立目标的事务中经 ACCESS 记下；删除目标时，在同一事务中删除它的归属与分享。
  - 账户可操作的目标，是它拥有的目标，加上分享给它某个角色、且带有该权限点的目标；前提是账户的角色具备这个权限点。admin 可操作全部目标。
  - 可分享的权限点：电脑为 computers.view、computers.update、computers.delete，agents.create 随第 13 阶段的 agent 模块加入；agent 为 agents.view、agents.update、agents.delete、agents.approve。分享所需的权限点（如 computers.share）只作用于自己拥有的目标，admin 可以分享任何目标。
  - 分享页由 server-access 提供，地址为 `/ui/access/shares/<类别>/<目标的短编号>`，模块在自己的页面中以 iframe 载入。页面按角色列出可分享的权限点，可以搜索角色，标出全局默认角色，提示未保存的修改。编辑时只能授予自己在该目标上具备的权限点；保存时只替换这些权限点，其余保持不变，权限点全部取消的角色删除分享。保存或取消后，页面向所在的 iframe 派发 `bazaar:done`。
- 设置页：外观（主题与语言，所有账户可用）由 server 本体登记，经登录模块写入账户，保存后外壳整页重新载入；安全部分只对 admin 显示；角色、账户与登录方式部分分别由 settings.roles、settings.accounts 与 settings.oidc 控制。
- session.key 归 server-access，它同时以带标签的 HMAC 为其他模块签名，例如 0.2 中以 `image:` 为标签的图片地址。

#### 第三方登录

- server-access 定义第三方登录方式的接口，以 ACCESS 提供登记。插件登记一种登录方式，给出：
  - 键：插件的路由挂在 `/login/<键>/` 下，其后的路径由插件决定，`/login/<键>/` 是发起登录的地址；以 logout 为键的登记被拒绝；
  - 名称与简介的文案键，以及图标，都随插件自己的 resources 提供；
  - 插件自己的配置页地址、是否已配置，以及它用作默认角色的角色；
  - 续期：按账户在后台向外部服务确认授权仍然有效，有效时给出最新的显示名与头像。
- 登录流程的公共部分由 server-access 经 ACCESS 提供，插件在自己的路由中调用：
  - 开始授权事务：state、PKCE verifier 与浏览器令牌各为 32 字节随机数，记下登录方式与 return_to，600 秒过期，最多保留 1024 个；浏览器令牌放在 path 为 `/login` 的 cookie `bcs_login` 中；
  - 在回调中取回并作废事务：state 未知、已用过、已过期、浏览器令牌不符或登录方式不同时失败；
  - 为首次登录的人建账户：在插件自己的事务中写入，auth_type 为登录方式的键，name 为账户 id，角色为登录方式的默认角色，没有设置或该角色已不存在时为全局默认角色；都没有时失败；
  - 以确认的账户完成登录：更新账户的 display_name 与 avatar_url，按登录方式的会话时长签发会话，写入续期 cookie，跳回 return_to；
  - 以失败原因回到登录页：登录失败、事务过期、登录方式不可用与缺少默认角色各有提示。
- 每种登录方式在自己的版本链上持有自己的表，保存配置与外部用户和账户的对应；对外部服务的请求限时 15 秒，经 TIMER。
- 续期：第三方登录成功后设置 30 天的续期 cookie `bcs_reauth`，以 session.key 带标签签名，记录账户、session_version 与登录方式。会话过期后，带着它的请求由 server-access 请该登录方式在后台确认授权仍然有效，限时 15 秒，经 TIMER：有效时更新显示名与头像，在同一请求中签发新会话并继续处理；授权已失效、登录方式没有登记或外部服务不可用时，删除续期 cookie，按未登录处理。续期 cookie 在授权有效期间等同于凭据，退出时一并删除。
- 有已配置的第三方登录方式时，登录页先显示第三方登录：第一种以大图标显示，图标本身是按钮，下方是名称与简介；其余以小图标居中排在下方，点击后换为大图标；先后按登记顺序。账号密码收在卡片右上角叠加的三角中，点击切换到账号密码表单，表单的右上角同样可以切回。登录失败、事务过期、登录方式不可用与缺少默认角色在登录页上提示。
- 设置页的登录方式部分：首行是账号密码，内置且始终可用；其后每种已登记的登录方式一行，带图标、名称、简介与是否已配置，展开后在 iframe 中载入它自己的配置页；末行说明首次登录的人得到的全局默认角色，并链接到角色部分。
- return_to 只接受站内绝对路径，长度不超过 8192，否则回到默认页面。
- 回调以 `@redact("code", "state")` 标记，access log 中隐藏这两个值；第三方登录的响应带 `Cache-Control: no-store` 与 `Referrer-Policy: no-referrer`。

#### GitHub 登录

server-github 实现第三方登录方式的接口，以 GitHub OAuth App 的授权码流程登录：

- 配置页必填 client_id 与 client_secret，client_secret 留空时保持原值；可选默认角色与会话时长，默认角色不填时用全局默认角色，会话时长不填时与账号密码登录相同；回调地址由页面按浏览器当前地址的 origin 生成为 `<origin>/login/github/callback`，只读显示并可复制，用于 OAuth App 的 Authorization callback URL，保存时随表单存下，授权时使用存下的值。名称为 GitHub，简介为「用 GitHub 账号登录」，图标为 Simple Icons 的 GitHub 标志（CC0-1.0）。
- 授权地址为 `https://github.com/login/oauth/authorize`，带 client_id、redirect_uri、scope `read:user user:email`、state 与 PKCE，code_challenge_method 为 S256。
- 以 code、redirect_uri 与 code_verifier 向 `https://github.com/login/oauth/access_token` 换取访问令牌，请求带 `Accept: application/json`；再以 `GET https://api.github.com/user` 取 id、login、name 与 avatar_url，以 `GET https://api.github.com/user/emails` 取已验证的主邮箱。
- github_accounts 以 GitHub 用户 id 对应账户，并保存该用户最近一次登录得到的访问令牌：首次登录在同一事务中经 enroll 建账户并记下对应，之后的登录更新 login、显示名、邮箱与令牌。显示名取 name，没有时取 login；头像取 avatar_url。
- 续期以保存的令牌请求 `GET https://api.github.com/user`：200 且 id 相同为有效，同时更新显示名与头像；401 为授权已失效。GitHub 上取消授权后，最迟在一个会话时长内被发现。
- 网络错误、超时与 GitHub 的 5xx 应答为登录方式不可用；拒绝授权以及无效或过期的 code 为登录失败。
- 访问 GitHub 的部分以站点地址、API 地址与 TLS 上下文构造：入口使用 `https://github.com`、`https://api.github.com` 与系统的证书，测试使用本地 TLS 服务的地址与测试证书。

## 第 12 阶段：server-computer 与两端通信

### 两端协议

三个 HTTP 接口按节点一侧的动作命名，节点主动连接：

| 接口 | 作用 |
| --- | --- |
| `GET /starlink/v1/listen` | 接收调用 |
| `PUT /starlink/v1/announce/{编号}` | 回传结果 |
| `POST /starlink/v1/report` | 上报事件 |

- 路径中的版本号标识协议版本。
- 鉴权使用 `Authorization: Bearer <token>`。token 与 server 中的电脑一一对应，校验沿用 computers 表的 secret_hash。
- 每次请求相互独立，由任意副本处理的结果相同。

#### listen

- 长轮询，server 最多保持 25 秒，每次最多返回 20 个调用。
- 调用在交回结果或过期前一直保留在中转中。节点以 `after` 参数带上已处理的最大调用编号作为下次的起点，响应在途丢失时下一次轮询会重新拿到。
- 每个调用由调用编号和 sidecar 的 Request（service、method、args、kwargs）组成。
- 节点端的请求时限为保持时间加 5 秒。

#### announce

- 节点逐个回传 sidecar 的 Result 或 Failure，与事件分开。同一调用重复回传只算一次；回传失败时按退避重试，直到该调用过期。
- server 等待单个结果 60 秒，超时按无响应处理。
- 同一电脑上内容相同的在途调用只下发一次。

#### report

- 节点按 run_id 与递增 seq 批量上报审计事件。单批不超过 512 KiB，即 server 单个请求上限 1 MiB 的一半；响应带回已确认的最大 seq。
- 节点在确认前把事件留在内存，失败后按退避重发未确认的部分；进程退出时未确认的事件随之丢弃。
- 持久化与去重以事件存储中电脑、run_id 与 seq 的唯一约束为准，由第 14 阶段的活动模块承担。本阶段 server-computer 在进程内跳过已确认的重放。
- 内存队列有界：未确认的事件最多保留一个上报批次的大小（512 KiB），与 0.2 的上限相同，恢复后一个请求即可重放完。队列已满时，新事件在入队时丢弃，丢弃数量写入日志。

#### 重试

退避从 1 秒开始逐次加倍，上限 60 秒，即 0.2 的节点启动时限；任一请求成功后重新从 1 秒开始。

### 中转

- 副本之间需要协作的部分集中在 server-computer 定义的中转契约：
  - 提交调用并等待结果；
  - 读取发往某台电脑、编号大于给定起点的调用；
  - 交回结果；
  - 记录与查询电脑最后一次出现的时间。

  调用编号由中转生成，同一台电脑内递增。server-computer 与 starlink 只依赖这个契约，不依赖部署方式。
- 默认中转为 server-relay-memory。SQLite 部署只有一个副本，内存中转即可。
- 多副本部署把 server-relay-memory 换成实现同一契约的新 contrib。例如 server-relay-redis：调用与结果放在带过期时间的键中，长轮询以阻塞读取等待；或者在 PG 上用调用表加 LISTEN／NOTIFY。中转只保存存活期内的临时状态，重启后从零开始。
- 事件不经过中转，由收到事件的副本处理。
- 单机利用多核即在一台机器上运行多个 server 进程，每个进程是一个副本，配合跨进程的中转（server-relay-redis）与可多进程共享的数据库。届时 http 增加 reuse_port：Linux 上以 SO_REUSEPORT 共用端口，其他平台各用端口并由反向代理分发。uvicorn 的 workers 在子进程中重新导入应用，取不到各 contrib 登记的路由，不采用。

### 时间

http、server-computer、server-relay-memory 与 starlink 的计时都经 TIMER：长轮询的保持、等待结果、调用过期、退避重试以及退出时等待在途请求。server 的条目 accept 宿主共享的 TIMER，不另起时间轮。uvicorn 内部的计时除外。

### server-computer

- server-computer 持有 computers 表的身份与登记。登记电脑返回一次性 token，节点以它鉴权；登记由 server-computer 的页面发起，并经 server-access 把登记者记为该电脑的 computer_owner，与 0.2 相同。token 为 `<电脑 id>:<secret>`，secret 是 32 字节随机数的 base64url，表中只存它的 scrypt 摘要。
- server-computer 的页面包括电脑列表、登记、改名、删除与分享，在侧边栏登记，并登记自己的概览内容；权限点为 0.2 的 computers.view、computers.create、computers.delete 与 computers.share，以及改名所需的 computers.update。电脑经 server-access 登记为可分享的目标。
- 名称去掉首尾空白后不能为空，超过 100 个字符截断，不要求唯一；登记与改名使用同一规则。
- 删除电脑时，在同一事务中删除它的归属与分享、0.2 的 events 表中它的事件（该表存在时）以及电脑本身，token 随之失效。
- 供其他 contrib 调用节点的接口为 COMPUTERS 的 `call(电脑, service, method, 参数)`，与 sidecar Client 的 call 同名同参；节点上失败时抛 RemoteError，60 秒内没有结果时抛 TimeoutError。
- 调用与在线状态经中转契约完成，server-computer 不在进程内假定节点连在哪个副本。三个接口中任一请求鉴权成功即记为电脑出现；最后一次出现在 60 秒以内的电脑为在线：长轮询每次最多保持 25 秒，失败后的重试间隔不超过 60 秒。
- 节点 report 上来的事件以 REPORTED 在 Hub 内 announce，带电脑 id 与审计事件，供后续的 agent 与活动模块以 listen 订阅；本阶段不把事件写入 events 表。
- 节点协议的三个接口由 server-computer 经 http 直接注册，以 token 鉴权，不经过登录检查；token 无效返回 401，请求体超过 1 MiB 返回 413，内容不合法返回 422。

#### 机器信息

- 节点的 node-management 在 NODE 上提供 `info()`，加入它的 RemoteEntry。info 返回运行版本、系统（platform.system）、系统名称与版本号、release（Windows 取 platform.version，其他取 platform.release）、架构、节点进程的启动时间、CPU 核数与一秒内的占用、内存总量与已用（总量减可用），以及各磁盘的挂载点、总量与已用。系统名称在 Linux 取 os-release 的 NAME 与 VERSION_ID，读不到时为 platform.system；macOS 为 macOS 加 mac_ver；Windows 为 Windows 加 platform.release。
- 磁盘逐个读取 psutil 列出的分区，跳过 squashfs、iso9660、udf 与 cdfs 以及读取失败的分区；总量与可用量都相同的分区视为同一块磁盘，只列第一个挂载点，例如 btrfs 子卷与 APFS 容器。node-management 因此依赖 psutil。
- server-computer 经 call 取得 info，缓存在处理请求的 server 进程中：电脑出现时，若此前没有出现过或已离线超过 60 秒，下发一次；页面渲染或经统一总线刷新时读到在线的电脑，缓存满 60 秒就再下发一次，同一台电脑同时只有一次在途。没有人看页面时不再下发。节点没有回应或回应不合法时，保留上次的结果。

#### 页面

页面以 design 中电脑的效果图为准：

- 侧边栏在工作台分组登记「电脑」，徽标为账户可见的电脑数量。
- 列表的列为电脑（系统图标、名称、系统名称与架构）、状态、内存、硬盘、版本与最后在线。内存与硬盘为 btop 式的 16 格进度条，硬盘取占用最高的一块；格子按所在位置着色，前 60% 为 jade，到 85% 为 saffron，其余为 crimson，即灯笼的三种状态色。版本只写版本号；离线的电脑以横线代替进度条。列表按 id 分页，每页 50 台，滚动到底时载入下一页，载入中显示「正在加载更多」；列表与徽标经统一总线随电脑的登记、改名、删除、上下线与机器信息更新。
- 窄屏时每台电脑一张卡片：系统图标、名称与状态，在线时名称下方是 8 格的内存与硬盘。
- 行末的菜单按账户在该电脑上的权限点给出重命名、分享与删除；点击行打开详情。
- 详情在宽屏为右侧抽屉，窄屏为整页：标题为名称，旁边的铅笔原地改名，回车保存，Esc 取消；其下为系统名称、版本号与架构；事实为状态、已运行（按启动时间计算）与版本；资源为 CPU、内存与每块磁盘各一条进度条，磁盘以挂载点标注，并注明何时更新；底部为分享与删除。离线时说明重新连上后显示系统与资源。
- 登记在抽屉中分三步：起名；给出在电脑上运行的命令；等待电脑连上，连上后抽屉转为它的详情。命令为 `uv tool install "bazaar-compute[node]"` 与 `bcn contrib add bazaar-contrib-starlink --enable starlink --option server=<页面的 origin> --option token=<token>`，按 Linux／macOS 与 Windows 的写法分行；token 的中段以圆点遮住，复制按钮复制完整命令，token 只显示这一次。
- 分享在抽屉中以 iframe 载入 server-access 的分享页；删除前确认，并说明 token 立即失效、分享一并删除。
- 概览内容占两列：分区标题为「摊位」，副标题为电脑数量，有离线的电脑时加上离线数量，右侧链接到全部电脑；其下每台电脑一张摊位卡片，两张一行，顶部是与插画同一画法的棚子，颜色取名称的 tint；标题行为系统图标、名称、系统名称与版本，右侧为在线，或「离线」加离线时长的最大单位（分钟、小时或天）。

### 节点侧

- starlink 的配置条目包含 server 地址，以及 token 或 token_env。token 直接写值，token_env 写环境变量名；两者都填时以 token 为准，与渠道凭据的规则一致。
- starlink accept 节点的 sidecar CLIENT，把 server 发来的调用原样经本机的 sidecar 分发，与 bcc 的调用走同一条路，只能调用 contrib 以 RemoteEntry 声明的成员。每个调用限时 60 秒，即 server 等待结果的时长；结果超过 server 的请求上限 1 MiB 时，回传一个 ResultTooLarge 的 Failure。
- starlink 监听 AUDIT_EVENT：回调只负责入队，网络发送由它自己的后台任务完成。
- listen、announce 或 report 开始失败时，starlink 发布一条带请求类别与原因的审计事件 starlink.failed，再次成功时发布 starlink.recovered，两者与其他事件一样经 report 上报；server 拒绝 token（401）时 starlink 以失败退出，原因见它的 contrib 状态。
- config-versions 的 V4→V5 转换在 0.2 节点配置的 audit 或 control 为 "server"、且 [node.server] 有 url 时，把 url 与 token_env 转成启用的 starlink 条目的 server 与 token_env，并加入 starlink 包声明。迁移后的节点使用 token_env，值仍在原 runtime.env 中。0.2 的审计只能写到一个去处；0.3 的 logging 与 starlink 并列监听审计事件，转换因此总是启用 logging。

## 第 13 阶段：agent 模块（2026-11-07）

依据消息 `c4255280-08f2-53c7-a952-9aaff525579d`、`3d4a3e17-845b-5eed-af86-0118e81532ec`、`f925759e-3289-540b-8be2-a28a7a57b135`、`4b038821-7027-57dd-9894-4ee946d39d4f`、`7d71c92c-ea84-509c-83f6-25fde90248d7`、`b55c9d83-6c15-5f9a-b2b5-392361c087d8`、`f4ed67ad-625d-5f06-9cae-dccd6a9b9f06`、`49d7bdea-9823-5dad-acfd-75335bea8051`、`9e13fd5f-ae4e-520c-b1a9-d859c35f97ea`、`c06a284c-3504-50ac-8359-245bc3256c38`、`08751e73-c3e7-57ad-8e3b-c74c9d4b2e14`、`f645f386-d67d-5aad-a3b0-157f3be38bd5`、`39a799ac-7941-576d-ab82-64de8eb4d547`、`0df3ea7d-d13c-5aae-9112-232a1c3daecc`、`9535f26a-0628-591c-b016-38e7230bf227`、`f676a63d-4596-5991-a9a6-76268311b26d`、`69847bbb-231c-58a1-96b5-ab7c578ccd72`、`8a5bd254-802a-5fde-b047-73e965574009`、`48fabaa4-ec68-5e58-acc8-3ddaa7fbccb0`、`111e8265-b331-5709-aeff-9aec171613d5`、`3fb79e6a-0d0b-54b0-9019-8170f8591063`、`d1be68ad-f6c5-5996-9819-18874bc9fba9`、`f00556bd-29ff-5775-b4b3-b6e0af1bb510`、`d1d37f7f-5d2f-579f-b03d-392ae7d376e5` 与 `0d4f5eda-1232-54e1-8353-2595183c5d5b`：

- server 管理 agent 与 `bcn agent` 走同一条路：`bcn agent` 所用的 agent 管理服务 AGENT_CONFIGURATION 由节点上的 agent-command 提供，登记为 remote 后由 server-agent 经 server-computer 的 call 调用，配置文档由它在节点上读写。
- 渠道与运行时的表单由 server-agent 按插件实体配置模型的 JSON schema 统一绘制；插件在自己的配置模型上标注，不依赖 server。
- 凭据按 `a71294e0-d7b2-55ad-b070-0d39c6615bfc` 写进 0.3 配置里对应的凭据字段；读出时只给出是否已设置，修改时没有提交的凭据沿用原值。`*_env` 字段照常出现在表单中，填写的是节点上的环境变量名，值由用户预先在那台节点上设好；0.2 迁移来的配置在环境变量中已有值，照样可用。
- 工作区由节点按 agent 分配。新建与配置的内容为名称、模式、会话沙箱、空闲回收、渠道与运行时；工作区 tab 列出工作区的内容，并写出它在那台电脑上的绝对路径。浏览工作区是 agent 管理服务的成员。
- 一个 agent 可以有多个渠道与多个运行时：渠道同时在线；运行时按顺序使用，新会话用运行中、未被限流且排在最前的一个，已有会话在它的运行时可用时继续使用，与节点上 agent 的选择规则相同。
- 页面以 [design](design/README.md) 中智能体的效果图为准，交互与电脑模块一致：列表为表格，行末菜单，分享在抽屉中，删除前确认；渠道与运行时以插件的 logo 表示，旁边只写机器人名或模型。
- 中文页面文字称「插件」，英文称 contrib。
- 第 13 阶段的 BCN 改动同样在 `0.3-server` 上进行。

### 节点侧

#### agent 管理的 remote

- agent-command 在节点中提供的 AGENT_CONFIGURATION 以 RemoteEntry 登记为 remote `agent-configuration`，成员如下：
  - `list()`：配置的修订号，以及每个 agent 的 id、名称、模式、会话沙箱策略、空闲回收秒数，与按顺序排列的渠道和运行时。每个渠道与运行时给出 kind、连接名、凭据以外各字段的值，以及已设置的凭据字段名。
  - `providers()`：节点配置中启用的渠道 builder 与运行时 builder，各给出 kind 与实体配置模型（`entity_model`）的 JSON schema。
  - `inspect(kind, agent_id=None)`：以该运行时 builder 的 inspect 查询，返回 HarnessInspection，限时 10 秒，即 0.2 的命令时限。给出 agent 时在它的工作区中查询；新建向导中还没有 agent，在一个临时目录中查询，结束后删除该目录。
  - `add`、`edit` 与 `remove`：`bcn agent` 使用的同名成员。渠道与运行时的条目可带 `connection`，即 `list()` 给出的连接名：带它的条目中没有提交的凭据字段取该连接原有的值，提交 null 时清除，`connection` 本身不写入配置。三者的返回值与 `list()` 中的单个 agent 相同，在配置写入、开始应用后返回；agent 按下文的配置变更切换到新配置。
  - `workspace(agent_id, path="", offset=0)`：工作区的绝对路径、`path` 所指目录这一层的总项数，以及从 `offset` 起的 50 项，每页的项数与列表的分页相同。每项带名称、是否为目录、大小与修改时间；排列为目录在前、再按名称，与效果图相同；符号链接不列出，与 0.2 相同。总项数不设上限：单次调用的结果受 server 单个请求 1 MiB 的上限约束，超过时 starlink 回传 ResultTooLarge，分页让每次的结果远在上限之内。
  - `read(agent_id, path)`：工作区中一个文件的大小与修改时间，以及它的内容。内容只在文件为 UTF-8 文本、且不超过 1 MiB 时给出，即 server 单个请求的上限，更大的结果无法经 starlink 传回；其余文件标明无法显示。
  - `workspace` 与 `read` 的 `path` 为相对工作区的路径，解析后位于工作区之外时拒绝。
- 连接名是渠道或运行时在 agent 中的键 `<kind>:<序号>`，序号为同一 kind 内的顺序，与快照位置 `agents[<id>].channel[<kind>:<序号>]`、`agents[<id>].harness[<kind>:<序号>]` 中的相同。运行时调换顺序时，提交的条目带着它在 `list()` 中的连接名，凭据因此随条目保留；保存后连接名按新的顺序重新给出。
- 凭据字段是实体配置模型的 JSON schema 中标有 `writeOnly: true` 的字段。
- 工作区的位置与 agent 运行时所用的相同：配置中没有 workspace 时为 `<BCN_HOME 或 home>/.bcn/workspaces/<agent id>`，两处使用同一段解析。

#### 配置变更

agent 处理配置变更与文件更新时相同，`bcn agent` 与 server 的保存走同一条路，各 agent 的变更互不等待：

- 运行时的配置变化（模型、档位、凭据等字段）时，该运行时上的会话标为过期，即 harness expire 的状态：进行中的回合与后台任务照常完成，会话空闲后关闭；它上面的会话都关闭后，运行时按新配置重启，此后的会话使用新配置。重启之前到达的消息仍在原运行时上开会话，空闲后同样关闭。
- 只调整运行时的顺序时，各运行时照常运行，新会话按新的顺序选择运行时。
- 渠道的配置变化时，等 agent 空闲后按新配置重新连接。

现在的 bazaar-contrib-agent 在 configure 中先排空再替换：渠道变化时等整个 agent 空闲，运行时变化时等它上面的会话做完，再替换条目，各 agent 依次处理。第 13 阶段改为上述做法。

#### 状态

agent-activity 的 `agents()` 在现有的状态、原因、今日用量与最后事件之外，为每个 agent 增加下列内容。agent-activity accept AGENTS，由运行中的 agent 实例、条目的快照与统一的连接事件得出：

- `started_at_ms`：agent 开始运行的时间，由 agent 启动时记下。
- `working`：进行中的回合里最早开始的一个，给出开始时间与所在会话的显示名，由 agent 在回合开始时记下；没有进行中的回合时为空。
- `turns` 与 `said_at_ms`：进行中的回合的 id，以及给出这份状态时节点的时间。回合从 `harness.turn.requested` 发出之前到结束事件发出之后都在其中。
- `channels`：按配置顺序列出每个渠道连接的 kind、连接名、状态、机器人身份（`Channel.get_identity()`）、连接时间、最后收到消息的时间、连接后连续失败的次数与最近错误。
  - 状态取自该连接条目的快照；运行中而最后一个连接事件为失败时，插件还会重试的为重新连接中，不再重试的为已断开。
  - 连接时间、失败次数与最近错误取自统一的连接事件，按事件上下文中的 connection 对应到连接；条目失败时，最近错误取快照中的错误。
  - 最后收到由 agent 在该连接收到消息时记下。
- `runtimes`：按配置顺序列出每个运行时的 kind、连接名、状态、是否在用，以及被限流时恢复可用的时间。状态取自该运行时条目的快照；在用的是 agent 此时为新会话选择的那一个。

#### 渠道的连接事件

- bazaar-contrib-channel 定义两个连接事件，三个渠道插件都改发这一组：
  - `channel.connection.connected`：连接建立或恢复，载荷为机器人身份；
  - `channel.connection.failed`：连接断开或建立失败，并带错误；载荷为下次重试前等待的毫秒数，插件随后自行重试。依据消息 `beb133fa-5a23-56d3-b225-f18f5a665270`，插件放弃重连时这一项为空，渠道条目照旧运行，例如 lark 连上后认证失败或超过服务端给出的重连次数，wecom 连上后连续三次认证失败或被同一机器人的另一个连接接管。
- 改动前各插件的事件为：telegram 发 `channel.poll.failed` 与 `channel.poll.recovered`，lark 发 `channel.connection.connected`（载荷为 service_id）与 `channel.connection.failed`，wecom 发 `channel.connection.subscribed` 与 `channel.connection.failed`；载荷都不带机器人身份。
- 依据消息 `d11bc763-6e20-588d-9e83-8255a09f2986` 与 `d456448b-232b-5911-a8a4-eaa3c9d46f13`，bazaar-contrib-audit 的 `AuditContext` 增加 `connection`。agent 启用每个渠道与运行时的组合时，以 local 绑定在组合中放入一份审计服务：组合中的插件发布的事件缺少连接名时由它补上，再交给节点的审计服务，插件不经手。agent 自己发布的渠道事件与限流事件同样带上连接名。

### 配置模型的标注

渠道与运行时插件为实体配置模型给出表单所需的标注：

- 凭据字段经 `json_schema_extra` 标 `writeOnly: true`：telegram 的 token、lark 的 app_secret、wecom 的 secret、codex 的 api_key、claudecode 的 api_key 与 bedrock_api_key。
- 依据消息 `25fe9d74-6015-5615-b26a-1021b11d8792` 与 `7ce989b2-cd0f-577c-9648-97f4153dc2c5`，文字与 server 的做法相同，放在插件包的 `resources/locales/en.toml` 与 `zh-CN.toml` 中，键以字段名开头：`<字段>.title` 为名称，`<字段>.description` 为说明，`<字段>.empty` 为留空时的含义，例如 codex 与 claudecode 的 api_key 留空时使用电脑上已登录的账号；枚举的每个取值为 `<字段>.<取值>`，例如 lark 的 `region.feishu`。模型在 schema 顶层以 `x-locales` 按语言给出这两份文字，字段上不再写 title 与 description。
- 运行时的 model 标 `x-inspect: "models"`，effort 标 `x-inspect: "efforts"`：表单从 inspect 返回的模型中选择，档位取所选模型的 efforts，缺省为标为 default 的模型与它的 default_effort。
- `*_env` 字段是普通的文本字段，说明写明填写的是节点上的环境变量名；与凭据字段都有值时以凭据为准，与现有规则相同。

server-agent 对所有插件使用同一套画法：字符串为输入框，枚举为分段选择，凭据为只可替换的输入框，留空即保持不变。配置中的概要与新建的确认按 schema 列出有值的字段，凭据写已填写或已保存，留空而有 `<字段>.empty` 的字段写它的含义。文字按账户的语言取 `x-locales`，没有该语言或该键时取 en；每台电脑上的插件版本可能不同，server 使用每份 schema 自带的文字，不并入 server 的 catalog。

### server-agent

- 新仓库 open-bazaar/contrib-server-agent 的 bazaar-contrib-server-agent 提供 `server-agent`，accept SERVER、ACCESS、COMPUTERS、SERVER_DB 与 SERVER_SCHEMA，listen REPORTED 与 COMPUTER_REMOVED，以 server 本体的 `module("agents", …)` 提供页面；页面由服务端渲染，局部更新经 htmx 与统一总线完成。仓库按现有 contrib 仓库建立 CI 与发版流程，包登记到 contrib-index。
- server-management 的默认配置与 V4→V5 转换中的 server 条目加入 server-agent。

#### 数据

- 每台电脑的 agent 经 call 取得：`agent-configuration.list` 给出配置，`agent-activity.agents` 给出状态与今日用量。结果缓存在处理请求的 server 进程中，做法与电脑模块的机器信息相同：页面或侧边栏读到在线电脑的 agent 时，结果满 60 秒就再下发；同一台电脑同时只有一次在途；节点没有回应时保留上次的结果。
- inspect 的结果按电脑、agent 与运行时的 kind 缓存 60 秒，与 0.2 节点上的缓存时长相同，详情的动态与技能 tab 用到时才下发。
- 每取得一台电脑的 `agent-activity.agents`，announce `AGENTS_SAID`，带电脑 id 与这份状态。
- 收到 REPORTED 的 `harness.started`、`harness.completed`、`harness.failed`、`harness.cancelled`、`channel.connection.connected` 或 `channel.connection.failed` 时，立即重新下发该电脑的 `agent-activity.agents`，侧边栏与页面经统一总线随之刷新。
- 每台电脑最后一次取得的 agent 列表存入 server-agent 自己的表 `agents`：电脑 id、agent id、名称、`list()` 给出的配置与取得时间，以电脑 id 与 agent id 为键，表结构经 SERVER_SCHEMA 登记为第 1 版。电脑离线时页面照这份列表列出它的 agent，状态为离线。
- 新取得的列表中不再有的 agent，删除对应的行；一个 agent id 在所有电脑上都不再出现时，经 ACCESS 的 `forget` 删除它的归属与分享。
- server-computer 删除电脑后 announce `COMPUTER_REMOVED`，带电脑 id；server-agent 收到后删除该电脑的行，并按上一条删除这些 agent 的归属与分享。
- COMPUTERS 增加 `computers(ids)`：给出电脑的名称、是否在线与最后出现时间；ids 为 None 时给出全部电脑。

#### 权限点与分享

- 权限点沿用 0.2：agents.view、agents.create、agents.update 与 agents.delete，经 server 本体的 `permission` 登记；agents.approve 随第 14 阶段的会话模块登记。
- agent 经 ACCESS 登记为可分享的类型 `agent`，以 agent id 为目标，与 0.2 相同，导入的 0.2 分享照常生效；分享点为 agents.view、agents.update 与 agents.delete，分享所需的权限点为 agents.view，即拥有者即可分享，与 0.2 相同。
- ACCESS 增加 `points(kind, points)`：在已登记的分享类型上追加分享点，退出时撤销。server-agent 以它在 `computer` 上追加 agents.create，与 0.2 的电脑分享点相同；第 14 阶段的会话模块以它在 `agent` 上追加 agents.approve。
- 从页面新建的 agent 在保存成功后，经 ACCESS 的 `own` 把新建者记为拥有者，与写入 `agents` 表在同一事务中；`own` 取代目标原有的拥有者。
- 电脑的列表带来的 agent 尚无拥有者时，记为该电脑的拥有者，与写入 `agents` 表在同一事务中，与 0.2 相同。ACCESS 增加 `owners(kind, ids)`：一次给出一批目标各自的拥有者。
- 列表只列出账户可见（agents.view）的 agent；新建向导的电脑只列出账户可以新建 agent（agents.create）的在线电脑；行末菜单与详情中的操作按 agents.update、agents.delete 与能否分享给出。

#### 页面

- 侧边栏在工作台分组登记「智能体」，顺序在总览与电脑之间，徽标为账户可见的 agent 数量；并登记侧边栏组件「正在工作」：宽屏列出账户可见、正在工作的 agent 与已工作的时长，窄屏在底部导航上方轮播，每次显示一个 agent 的灯笼、名字、会话与时长。
- 列表：标题旁为 agent 总数与正在工作的数量；上方为名称搜索、按状态（全部、正在工作、出错、离线）与按电脑的筛选。列为智能体（头像、名称与模式）、状态、渠道、运行时、电脑、今日用量与操作。
  - 状态为带底色的灯笼块：正在工作只写时长，空闲写「空闲」，出错在块内写原因，离线只有灯笼。
  - 渠道与运行时每个一行，为插件的 logo 加机器人名或模型；第一个之后的运行时标「备用」。
  - 按电脑 id 与 agent id 的顺序分页，每页 50 个，与电脑列表相同，滚动到底时载入，载入中显示「正在加载更多」；带状态筛选时依次往后读，直到凑满一页或读完。
- 详情为整页：头部为头像、名称、状态与事实标签（各渠道的机器人名，按顺序的运行时模型与强度，所在电脑，模式），标签按整块换行；tab 为动态、工作区、技能、配置与共享。
  - 动态：智能体的状态、已运行时长、启动时间与最后一次活动；各渠道的连接状态（重新连接中时带第几次）、连接于、最后收到与最近错误；各运行时的顺序、是否在用、是否可用、版本（取自 inspect）、档位与凭据的状态；今日用量的总量，以及输入、缓存命中、输出与费用。状态卡片可以手动刷新。离线时显示最后一次取得的样子。
  - 工作区：标题注明所在电脑与绝对路径；目录逐层展开，点开时才读取下一层，每次 50 项，滚动到末尾时读取下一页，读取中显示「正在加载更多」与已列出、剩余的项数；可以重新读取。点开文件在右侧抽屉中查看：Markdown 文件经 server 本体的 markdown 过滤器渲染；其他文本按文件名由 Pygments 识别语言（`get_lexer_for_filename`，例如 toml、py、json 与 yaml），以代码块显示并带行号，着色与 server 本体 Markdown 中的代码块相同，使用 `tok-` 前缀的类名，颜色随主题，识别不了的按纯文本显示；无法显示的文件只显示大小，并说明暂不支持查看该文件。抽屉标题旁为识别出的语言、大小与修改时间，下方为绝对路径，可以复制。查看文件与列目录一样需要 agents.view。
  - 技能：在用运行时的 inspect 返回的 skills，按来源（工作区、用户、系统）分组，并注明运行时的版本与读取时间；另列 inspect 返回的 MCP 服务与工具，服务的连接与登录状态是 harness 契约中的公共枚举，运行时不知道的留空，连接失败时带原因。
  - 配置：基本信息（名称、空闲回收、模式与会话沙箱）直接在页面上编辑；渠道与运行时为可增删的列表，运行时可拖动排序；点「编辑」在右侧抽屉中按实体配置模型的 schema 编辑一个条目，凭据只显示已保存，留空即保持不变。页脚的保存经 `agent-configuration.edit` 提交整份配置，带 `list()` 的修订号与各条目的连接名。侧栏说明保存之后的情形，并有删除。
  - 共享：抽屉中以 iframe 载入 server-access 的分享页。
- 新建向导分四步：基本信息（名称、电脑、模式、会话沙箱、空闲回收）；渠道（所选电脑上已启用的渠道插件，可选多个）；运行时（已启用的运行时插件，可选多个并排定顺序，模型与档位取自 inspect）；确认（按 schema 列出有值的字段）。创建经 `agent-configuration.add`。
- 删除前确认，说明 agent 会在那台电脑上停下、配置移除，工作区的文件与会话记录留在电脑上，分享一并删除。
- 窄屏的列表为每个 agent 一行卡片；详情、配置、新建与查看文件为整页；分享、删除与编辑条目从底部弹出，与 design 中的手机效果图相同。

## 第 14 阶段：会话与活动（2026-11-08）

依据消息 `c533cdfa-7b30-52f8-8383-f0dc5ba85667`、`dd55b063-0595-5f6c-8f8b-c2811cb34ed9`、`42dc6b1d-66a5-5d18-879f-b16e8c6c4b00`、`c05987a7-a88b-5ee1-8775-b48a719804fb`、`228520dc-6230-5bd9-8138-10bfff1ddff9`、`9df9efdc-cb1a-549d-a77c-6bd8389f45de`、`99fd628e-c0ee-5da4-bc12-e0af94299550`、`8746c197-721b-5893-96d8-8fbd664352f5`、`28d6c888-0dce-5bbf-8b15-9dcfb04f0dd3`、`9db89375-84f8-5cb6-a587-8c0669a0f528`、`aaae1ce8-cad8-571d-b04e-b52b6a84512c`、`5ad680ad-4a73-52de-8e96-e7b30b79293b`、`94e9eff5-1cb6-513d-af47-fc0e64aaf632`、`6052b0a4-730f-5bad-bf10-aa06abf97a0d`、`0d9124d3-ef6c-56c8-9584-0519a1133149`、`7222091c-de2f-51f2-9a10-29aab489f1e1`、`ef0e8271-2df2-59f2-97a5-57e5feaa439b` 、`4d9c842a-a040-5652-8984-ff1bc4d28ae0`、`f5165cac-a8b2-5f53-88ae-6062f6c92d2a` 与 `1f2a869b-8796-5074-ae5d-a1f3cfb3efaf`：

- 页面以 [design](design/README.md) 中第 14 阶段的效果图为准：效果图上的每个字段都有真实的数据来源，建成的页面与效果图一致，验收以真实浏览器的截图与效果图对照，与第 13 阶段相同。页面只给出面向人的写法，不显示原始 JSON。效果图的对齐修正作为单独的改动，同时应用到 design 与已建的页面。
- 第 14 阶段新增四个插件，各在自己的新仓库中维护：server 默认组合中的会话模块 server-conversation、活动模块 server-activity 与用量插件 server-usage，以及节点端的 starlink-conversation，由会话模块按需安装到节点上。
- 会话、消息与提醒留在节点上，server 经 call 读取，不保存副本，与 0.2 相同；节点上报的事件由活动模块持久化，回合、用量与动态由它投影。
- server 的功能在节点上需要的读取由该功能自己的插件提供，消息与提醒插件不为这些读取改动：节点端的读取由独立的包 bazaar-contrib-starlink-conversation 提供，名字表明它只服务 server 经 starlink 的调用；它经 accept 的 DB 直接读取这些插件在节点数据库中的表。电脑连上 server 即表示使用这些功能：节点没有这个入口时，server 直接经节点 manager 的 contrib add 安装并启用它。
- 工具调用按种类完整显示：命令写出命令文本、工作目录、退出码、时长与可展开的输出；文件的读取、列出与搜索写出路径或查询；网页写出地址或查询；MCP 写作「服务 / 工具」并附参数摘要；每次调用写出结果。这些字段由 harness 契约承载，codex 与 claudecode 按各自协议中的真实字段给出；运行时没有给出的字段不显示，例如 Claude Code 不报告退出码与工作目录。
- 等待审批的联系人收到的自动回复沿用 0.2 的 `review.reply`，显示在审批卡片旁，并在那里编辑。
- 移除群聊或私聊即撤销审批：会话信息中「审批 · 已允许」一行的「撤销」打开就地的确认，说明 agent 会停止回复、之后的消息照常保存、对方下次发消息时会话回到新联系人等待审批、设了自动回复的先收到自动回复；确认按钮为「不再回复」，提交 denied，与 0.2 移除会话时提交 review=denied 相同。私聊与群聊相同。
- 会话的成员是其中出现过的不同发送者，与 0.2 相同：入站消息的各个发送者，加上在其中发过消息的本 agent；成员数即这些发送者的个数。
- 下列内容是效果图要求而 0.2 没有的：跨 agent 的会话页与搜索，0.2 只在单个 agent 的页面中列出会话；消息中直接显示的附件图片，0.2 只代取正文中以 Markdown 写出的 http(s) 图片，附件只写文件名；完整的工具调用，0.2 只记录工具名；按回合与按小时的用量，以及概览中近 7 天与近 30 天的数字。
- 第 14 阶段的 BCN 改动同样在 `0.3-server` 上进行。

### 节点侧

#### 会话的 remote

新仓库 open-bazaar/contrib-starlink-conversation 的 bazaar-contrib-starlink-conversation 提供节点端的入口 `starlink-conversation`。它 accept MESSAGES 与 REMINDER 声明对 messages-node 与 reminder 的依赖，使它们先于本入口启动、表已建好，节点缺少其一时本入口不启动，依据消息 `9447a9b9-b1d3-52b6-a9f6-596160a31644`：MESSAGES 取自接口包 bazaar-contrib-messages，REMINDER 取自只有一种实现的 bazaar-contrib-reminder，两者只用于声明依赖。读取只经 accept 的 DB，以 storage 的查询对象直接读取节点上会话、消息、入站附件与提醒的表，不依赖、也不导入 messages-node 与 agent-command 的包，依据消息 `8b0409a9-a639-5fcd-ab8c-fb673a583ef5`：插件只能依赖接口包；只会有一种实现的插件，接口与实现可以同在一个包中供依赖，另有接口包、会有多种实现的插件（如 messages 之下的 messages-node）只能经接口 accept，依据消息 `38060550-f430-5d58-8b4f-1df88ceb2645` 与 `fcf21a4c-e740-5255-a525-20c63513d751`。它 offer CONVERSATION，并以 RemoteEntry 登记 remote `conversation`，成员如下。读取不经 agent 的运行中绑定，agent 停止时同样可用。会话以 thread id 指定，每页 50 个，与 0.2 相同。这些成员与其他 remote 成员一样经 starlink 调用，每次限时 60 秒，节点退出时随 starlink 的在途调用一起结束。

- `conversations(agent_id, review, offset=0, limit=50)`：review 为 approved 或 pending。会话按最后活动从新到旧排列，即节点现有目录的顺序。结果给出该审批状态的会话总数、等待审批的会话数与是否还有下一页，以及每个会话的：
  - thread id、名称与私聊对方的 handle：群聊的名称为 target_display_name；私聊为对方最近一条消息的发送者显示名，没有时为 handle；
  - 类型（私聊或群聊）、渠道、审批状态、最后活动时间，以及 agent 尚未读到的消息数（pending_count，即 0.2 的未读数）；
  - 群聊的成员数；
  - 第一条与最新一条消息：message id、发送者显示名、发送者类型、时间与正文的前 240 个字符，240 与节点搜索摘要的长度相同。
- `conversation(agent_id, thread_id)`：一个会话的上述内容，加上首次联系的时间（channel_sessions 的 created_at_ms）、消息数（读取范围与 history 相同）与成员。成员按最近一次发言从新到旧排列，各给出发送者 id、名称、显示名、发送者类型（人类、智能体或系统）与是否为本 agent。
- `history(agent_id, thread_id, around=None, limit=50)`：任意审批状态的会话都可以读取，读取范围与现有的 read_messages 相同，即入站消息与 queued、sent 的出站消息。给出 around 时以该消息为中心，没有时从会话开头读起；结果带 has_before 与 has_after。每条消息给出 message id、seq、方向、发送者（id、名称与显示名）、发送者类型、时间（provider_time，没有时为 received_at）、正文、system_message_kind，以及附件的 attachment id、名称、kind、状态、media_type 与大小。
- `search(agent_id, query, sort, thread_id=None, sender=None, after_ms=None, before_ms=None, offset=0, limit=20)`：检索在本入口中以消息表及其全文索引实现，查询任意审批状态的会话，筛选与 0.2 相同，为会话、发送者（self、handle 或 id）与时间，sort 为 relevance 或 time。结果在现有内容之外给出匹配的总数与每条命中的相关度分值，即节点排序所用表达式的值；命中所在会话的名称按 `conversations` 的规则给出。
- `attachment(agent_id, attachment_id, offset=0)`：一个状态为 ready 的入站附件从 offset 起的一段，给出 media_type、总大小与这一段的字节（base64）。文件按附件记录的相对路径在该 agent 的工作区中读取，工作区为 BCN_HOME 下的 .bcn/workspaces/<agent id>，没有 BCN_HOME 时在用户主目录下，与 0.2 相同。每段 512 KiB，base64 编码后约 683 KiB，留在 starlink 回传结果的 1 MiB 上限之内。
- `reminders(agent_id, thread_id=None)`：按下次触发时间列出该 agent 状态为 scheduled 的提醒，给出 thread_id 时只列该会话的；每个提醒给出 reminder id、所属会话的 thread id、名称（命名规则与 `conversations` 相同）与类型、标题、下次触发时间、repeat_rule 与时区。repeat_rule 为节点的规范写法：`every:<N><m|h|d>`、`daily@HH:MM` 或 `weekly:<星期>@HH:MM`。

#### 审批与自动回复

- 审批、撤销与自动回复使用 collaboration 现有的成员：`review_contact(thread_id, decision)` 提交 approved 或 denied；`setting(agent_id, "review.reply")` 读取自动回复，`set_setting(agent_id, "review.reply", value)` 保存，空字符串表示不发自动回复，与 0.2 相同。
- collaboration 发布的 `channel.inbound.persisted` 在审计上下文中带上所记录消息的 message_id。

#### 提醒

- 提醒由节点端入口的 `reminders` 读取，见上。
- `reminder.fired` 在审计上下文中带上它写入会话的系统消息的 message_id。

#### 在节点上启用

- manager 以 RemoteEntry 把 CONTRIB_MANAGER 登记为 remote `contrib-manager`，成员为 `add(name, source=None, version="", enable=None, config=None)` 与 `operation(id)`。add 与 `bcn contrib add` 走同一个方法：经目录选包，选不到时直接报错；选到后返回进行中的操作，安装完成后按 enable 启用条目（None 不启用，空字符串启用全部条目，名称只启用该条目），config 为条目配置；operation 给出操作的状态与结果。CLI 调用 add 后等待操作完成。
- sidecar 监听 CONTRIB_CHANGED：条目启动、停止或移除时，它在线程中重新读取已装的包以 `bazaar_compute_node.remotes` 声明的 remote，此后的调用按新的表分派。manager 装好并启用的入口因此不经重启即可调用；停用的入口在调用时立即失败，卸载的包的 remote 随之去掉。依据消息 `df1a9853-5b67-563e-b9a5-d2cde6b4e0aa` 与 `b64bb9bc-70a9-5953-8bc4-7b36c32f460d`。
- 会话模块调用节点的 `conversation` 时，节点没有这个服务，call 以 LookupError 失败；会话模块随即按自己声明的兼容版本范围调用该电脑 manager 的 add，安装 bazaar-contrib-starlink-conversation 并启用 `starlink-conversation`，轮询 operation 直到完成，再重新读取。处理请求的进程为每台电脑记下进行中的安装，同一台电脑同时只有一次；add 报错或 operation 失败时记下错误，之后读取该电脑时仍没有这个服务，就再装一次。

#### 回合与发送的关联

- agent 在回合的审计上下文中带上运行时的连接名；被限流而改用下一个运行时重试时，之后的事件带新的连接名。
- `harness.pool.banned` 的载荷增加 `fallback`：agent 接着改用的运行时的连接名，没有可用的运行时时为空。
- 会话绑定的校验通过时给出该运行时会话的 session id 与它此刻进行中的 turn id。messages-node 把两者写入这次发送的出站事件（`channel.outbound.pending` 与 `channel.outbound.<状态>`）的审计上下文，出站事件的 metadata 增加 reply_to_message_id。

#### 审计事件

- agent 发布的 `harness.*` 审计事件不再带 raw：运行时的原生协议行已经由 `harness.source.*` 记录，同一个包不再在两处出现。
- 审计上下文增加 call_id，`harness.tool.*` 带上这次工具调用的 call id。
- starlink 遇到一个单独就放不进一批（512 KiB）的事件时，不再丢弃它，而是把载荷换成 `{"omitted": 原载荷的字节数}` 后照常排入，信封与上下文不变；server 因此照常收到这个事件，序号也是连续的。

#### harness 契约

- `ToolCall` 增加 `duration_ms` 与 `detail`，原有的 `input` 与 `output` 由 detail 取代。detail 是按种类区分的变体，消费者按种类穷尽处理：
  - command：command（运行时给出的完整命令）、cwd、description、exit_code、output，以及 actions，即运行时对命令的逐段解析，每段为这一段的命令与种类：读取（path）、列出（path）、搜索（query 与 path）或其他；
  - file：action（read、list 或 search）、path 与 query；
  - change：被修改的文件，每项为 path 与修改种类（add、delete 或 update）；
  - web：action（open、search 或 find）、url 与 query；
  - mcp：server、tool、arguments 与 result（结果中的文字）；
  - agent：description；
  - other：name、input 与 output。

  `tool.started` 给出调用开始时已知的字段；`tool.completed` 与 `tool.failed` 给出整次调用，包括结果与时长。
- 新增载荷 `ModelUpdated`（type 为 `model.updated`，字段 model），由运行时报告当前回合所用的模型；agent 照常把它发布为 `harness.model.updated`。

#### provider 的映射

codex 依据 app-server 协议的 ThreadItem 与通知：

- commandExecution 为 command：command、cwd、exitCode、durationMs 与 aggregatedOutput 分别给出 command、cwd、exit_code、duration_ms 与 output；commandActions 照录为 actions，read、listFiles、search 与 unknown 各给出这一段的 command 以及 path 或 query。codex 给出的 command 带外层的 shell，例如 `/bin/bash -lc '…'`，各段的 command 是其中的命令。
- mcpToolCall 为 mcp：server、tool 与 arguments 照录，result 取结果 content 中的文字，durationMs 为时长；失败照旧取 error.message。
- fileChange 为 change，取 changes 中的 path 与 kind；webSearch 为 web，action 的 search、openPage 与 findInPage 分别给出 search（query）、open（url）与 find（url，pattern 作为 query），action 为空（开始时）或为 other 时为 search，query 取项上的 query；imageView 为 file 的 read（path）；collabAgentToolCall 为 agent，description 取 prompt；其余的项为 other。没有 durationMs 的项，时长为 completedAtMs 减 startedAtMs。
- 模型：每个回合开始时给出 ModelUpdated，turn/start 带 model 时取它，否则取 thread/start 或 thread/resume 应答中的 model；收到 model/rerouted 时以 toModel 再给出一次，该通知不再落入丢弃的分支。

claudecode 依据 stream-json 中 tool_use 的 name 与 input：

- Bash 为 command：command 与 description 取 input 中的同名字段，output 取 tool_result 的文字；Claude Code 不报告退出码与工作目录，两者为空。
- Read 为 file 的 read，path 取 file_path；Write、Edit 与 NotebookEdit 为 change，path 取 file_path，NotebookEdit 取 notebook_path；WebFetch 为 web 的 open，取 url；WebSearch 为 web 的 search，取 query。
- `mcp__<服务>__<工具>` 为 mcp：以 `__` 分出服务与工具，arguments 取 input，result 取 tool_result 的文字。
- Task 与 system 的 task_started 为 agent，description 取 description；其余工具为 other。
- tool_result 不带时长，时长为 tool_use 与 tool_result 两个信封到达的时间之差。provider 记住每个 tool_use，直到它的 tool_result 到达，tool.completed 与 tool.failed 因此带上整次调用。
- 模型：每个回合有一条 system/init，provider 以其中的 model 给出 ModelUpdated。

### server 侧的公共改动

- server 本体的 Jinja 过滤器加入第 14 阶段各页面共用的写法，英文按同样的规则：
  - 列表时间：今天为「14:53」，昨天为「昨天」，更早为「11 月 6 日」，不是今年的带年份；
  - 日期标题：「今天 11 月 8 日」「昨天 11 月 7 日」，更早只写日期；
  - 日子与时间：「今天 14:56」「明天 09:00」，其余日子只写日期，例如首次联系的「10 月 8 日」；
  - 时长：文字为「0.2 秒」「22 秒」「1 分 48 秒」，紧凑为「1.2s」「22s」「3m10s」，计时为「03:12」；
  - 不带「前」的时间距离：「刚刚」「2 分钟」「2 小时」；
  - token 数：不足 100 时写原数；不足 100k 时以 k 为单位保留一位小数，小数为 0 时省略，例如「0.3k」「38.2k」「12k」；100k 起取整，例如「412k」；1M 起以 M 为单位保留两位小数，例如「1.24M」。server-agent 现有的 tokens 过滤器移入并改用这一写法。
- server-access 的 ACCESS 增加带标签的签名：`sign(label, value)` 以 session.key 对「标签:值」做 HMAC-SHA256；`verify(label, value, signature)` 以恒定时间比较。0.2 的图片地址即以 `image` 为标签。
- server-overview 的登记项增加顺序（order）与占用的行数（rows，1–2，缺省 1）。没有调整过顺序的账户按登记项的顺序看到概览，概览的网格按列数与行数排布，例如两行高的摊位右侧依次是需要你处理与集市动态；窄屏为一列，全部内容按顺序竖排。
- server-computer：
  - 处理请求的进程在电脑每次出现时，经 TIMER 把对它的检查推迟到 60 秒之后；到期时中转记录的最后出现时间没有变化，就 announce `COMPUTER_LEFT`，带电脑 id 与最后出现的时间。60 秒即在线的判定时长；多个副本各自 announce 时，活动模块按电脑与最后出现的时间去重。退出时撤销尚未到期的检查。
  - COMPUTERS 增加摊位内容的登记 `stall(src)`：摊位卡片在标题行之下以 iframe 载入登记的地址，地址带电脑的短编号；同时只有一个登记。
  - 摊位的概览登记项改为占两行，顺序在用量之后。
  - 删除电脑时不再删除 events 中它的行，改由活动模块在 COMPUTER_REMOVED 时删除；这一改动在 server-migrations 去掉 events 的外键、活动模块接管 events 之后发布。
- server-agent 提供公共服务 `AGENTS`：
  - 详情 tab 的登记 `tab(AgentTab)`：键、名称的文案键、图标、顺序、在 iframe 中载入的地址、查看所需的权限点、可选的计数主题，以及可选的上级 tab。地址带 agent 的短编号，与详情页的相同；带上级 tab 的登记不出现在 tab 栏中，打开时高亮它的上级，例如完整活动记录在动态之下。详情页地址中 tab 与 kind 之外的查询参数原样带给所开 tab 的 iframe，例如完整活动记录的 turn。server-agent 自己的工作区、技能与配置照旧，tab 按顺序排列，共享照旧为按钮。
  - 动态区块的登记 `section(AgentSection)`：键、在 iframe 中载入的地址、所在的栏（主栏或侧栏）、顺序、查看所需的权限点，以及窄屏是否显示。动态 tab 由登记的区块组成，宽屏为主栏与侧栏两栏，窄屏为一栏，主栏在前。
  - 读取 `agents(account)` 与 `agent(account, agent_id)`：账户可见（agents.view）的 agent，各带电脑 id、名称、电脑是否在线、`agent-configuration.list` 给出的配置，以及最后一次取得的 `agent-activity.agents`；取得与缓存沿用第 13 阶段的规则。
  - server-agent 自己登记动态的状态区块（侧栏，顺序 10，窄屏不显示）：每个渠道一组，写出连接状态（正常、重新连接中与第几次、已断开）、机器人、连接于、最后活动（最后收到消息的时间）与最近错误，可以手动刷新。第 13 阶段动态中的智能体状态、运行时与今日用量卡片由第 14 阶段的各区块取代。

### 活动模块 server-activity

- 新仓库 open-bazaar/contrib-server-activity 的 bazaar-contrib-server-activity 提供 `server-activity`，accept SERVER、ACCESS、OVERVIEW、COMPUTERS、AGENTS、SERVER_DB、SERVER_SCHEMA、SERVER_MIGRATIONS 与 TIMER，listen REPORTED、COMPUTER_REMOVED 与 COMPUTER_LEFT，offer `ACTIVITY`，以 server 本体的 `module("activity", …)` 提供页面。仓库按现有 contrib 仓库建立 CI 与发版流程，包登记到 contrib-index。
- 条目的 config 为 retention_days，缺省 30，至少为 1，与 0.2 相同。

#### 表

- bazaar-contrib-server-migrations 增加第 8 版：从 0.2 升级时把 events 重建为不带外键的表，数据与索引原样保留，与是否启用 server-activity 无关。events 的外键不等接管它的模块去掉，因为删除电脑的 server-computer 不接管 events。
- server-activity 以它为前身：第 1 版以不带外键的 events 为基线，旧库接续时接管该表；第 2 版：
  - 把 events 重建，数据原样保留，并加入 envelope、session_id、turn_id、message_id 与 contact 列。contact 标出开出新会话且等待审批的入站消息，即 channel_session_mapping 为 created、review 为 pending 的 `channel.inbound.persisted`：0.3 的行在写入时设置，0.2 的行在建立时按同样的条件设置。0.3 的行在 payload 中保存审计事件的 payload，在 envelope 中保存其余的信封字段（id、level、error、context 与 observed_at_ms）；0.2 的行 envelope 为空，session_id 与 turn_id 取自 0.2 关联中的 runtime_session_id 与 turn_id。唯一约束照旧为电脑、run_id 与 seq，另按 turn 与 message_id 建索引。
  - 新建 turns：电脑、agent、turn id、session id、会话的 thread id、渠道、会话类型、运行时连接名、模型、触发消息的 message id、开始与结束时间、结果与失败原因、本轮的输入、缓存命中与输出 token 数和费用、按种类计的工具调用次数，以及当前的步骤（回复、思考或工具）与它开始的时间；以电脑、agent 与 turn id 为键。
  - 新建 usage：每条用量事件一行，记电脑、agent、运行时会话、turn、时间、相对同一会话上一行的增量（输入、缓存命中、输出与费用）与该会话的累计值。
  - 新建 departures：电脑与最后出现的时间，两者为键。
  - 由 0.2 的行建立 turns 与 usage：0.2 的 `runtime.request.turn.started` 与 `runtime.turn.*` 给出回合的开始、结束与结果，`tool_call.started` 计入其他种类的调用，`usage.updated` 按增量的规则写入 usage；0.2 的回合以 inbound_seq 关联触发它的入站消息。
- 各表之间以 id 关联，没有外键，关联的行由活动模块显式删除。

#### 写入与投影

- 收到 REPORTED 时，在一个事务中插入 events，以电脑、run_id 与 seq 的唯一约束去重，冲突时什么也不做；只有真正插入了一行时，才在同一事务中更新投影。逐段的流式文字（`harness.content.delta` 与 `harness.tool.text.delta`）与运行时的原生协议行（`harness.source.*`）不写入 events，只在步骤的种类变化时更新所在回合的当前步骤：agent_message 为回复，plan、reasoning_text 与 reasoning_summary 为思考，`harness.tool.started` 为工具。载荷被 starlink 去掉的事件照常写入，投影只取其中的事件名、信封与上下文。同一台电脑的批次由 starlink 依次发送，事件因此按 seq 依次写入。created_at_ms 为事件的 occurred_at_ms，received_at_ms 为 server 收到的时间；agent_id、thread_id、session_id、turn_id 与 message_id 取自审计上下文。
- 投影：
  - `harness.turn.requested` 建立回合，记下开始时间、会话、运行时连接名与触发消息的 message id；`harness.model.updated` 记下模型；`harness.tool.completed` 与 `harness.tool.failed` 按其中的种类计数，codex 的网页搜索开始时还没有动作，结束时才知道种类；`harness.completed`、`harness.failed` 与 `harness.cancelled` 记下结束时间、结果与失败原因。
  - starlink 的待发队列满时会丢掉事件，回合的结束事件可能因此到不了。收到 server-agent 的 `AGENTS_SAID` 时，状态中带 `turns` 的 agent，它在这台电脑上还没结束、不在 `turns` 中、且开始早于 `said_at_ms` 的回合就此结束：结束时间为这个回合最后一条事件的时间，结果为未知。之后到达的结束事件照常改写结束时间与结果。
  - `harness.usage.updated` 以 total 相对同一运行时会话上一行的累计值计算增量：输入为 input_tokens 与 cache_write_input_tokens 之和，缓存命中为 cached_input_tokens，输出为 output_tokens，费用为 cost_usd，与 0.2 的口径相同；某一项小于上一行时，视为运行时重新计数，以新值为增量。增量同时计入所在的回合。
  - 触发回合的消息以 message id 在 events 中找到：入站消息为 `channel.inbound.persisted`，给出发送者与正文；提醒为 `reminder.fired`，给出提醒的标题。
  - `node.health` 每台电脑只保留最新的一条，与 0.2 相同。
  - COMPUTER_LEFT 写入 departures，重复的忽略。
- 会话的名称：群聊取事件中的 target_name；私聊为该会话最近一条入站消息的发送者显示名，写作「与 X 的私聊」。
- 保留期：启动时与之后每 24 小时经 TIMER 清理一次，与 0.2 相同，退出时撤销。received_at_ms 早于 retention_days 天前的 events、开始时间早于它的 turns、时间早于它的 usage 与 departures 删除；一个运行时会话沉寂超过保留期后，它的下一条用量以累计值为增量，与 0.2 失去基线时相同。
- COMPUTER_REMOVED：在一个事务中删除该电脑在四张表中的行。

#### ACTIVITY

其他模块经 `ACTIVITY` 读取，按账户可见的 agent 过滤：

- 游标：给定 agent（以及会话）中，某一类事件最新一行的 id，例如消息事件（`channel.inbound.persisted`、`channel.outbound.*` 与 `channel.session.reviewed`），供统一总线比较；
- 回合：进行中的回合，以及一段时间内的回合，可以限于一个会话；
- 回执：给定出站消息的 message id，按出站事件中的 turn id 给出该回合在这条回复之前、上一条回复之后的工具调用按种类的次数与经过的秒数；
- 统计：一段时间内按小时或按天的回合数与用量（tokens、费用与三项的构成）。

#### 写法

- 用量的三项为未命中缓存的输入、缓存命中与输出，即 usage 中的三个增量；tokens 为三项之和。回合与动态中写出的「输入」为未命中缓存的输入与缓存命中之和，「缓存命中」的百分比为缓存命中占这一输入的比例；概览用量条的三段「输入」「缓存命中」「输出」为三项各占 tokens 的比例，相加为 100%。费用只在有运行时报告时显示，Codex 不报告费用。
- 「今天」从查看者所在时区的零点起算，时区取自请求，与 0.2 相同。
- 工具调用按种类写出：
  - 命令：「运行命令」加命令文本，命令文本为 actions 中各段的命令，多段时逐段写出，没有 actions 时为 command；actions 全部为读取、列出或搜索的命令写作对应的文件操作，命令文本作为说明；说明为 description，工作目录写最后一段，退出码与时长在已知时写出，有输出时带「输出」，点开后在下方以等宽文字显示；
  - 文件：「查看」「列出」或「搜索」加路径或查询；修改：「修改」加路径；
  - 网页：「打开」加地址，「搜索网页」加查询；
  - MCP：「调用」加「服务 / 工具」，说明为参数摘要：顶层参数写作 key=value，字符串带引号，嵌套的值写作「…」，过长时截断；
  - 子任务：「派出子任务」加 description；其他：「调用」加名称与参数摘要；
  - 失败的调用标为失败，有错误信息时写出。
- 回执的写法按种类依次为「运行了 N 条命令」「查看了 N 个文件」「修改了 N 个文件」「打开了 N 个网页」「搜索了 N 次网页」「调用了 N 次工具」「派出了 N 个子任务」，次数为 0 的省略，最后为「共 N 秒」。

#### 页面

- 动态的区块，经 AGENTS 登记：
  - 正在处理（主栏，顺序 10）：每个进行中的回合一张卡片，标题为「正在处理」加会话名称，旁边是渠道的 logo 与会话类型，以及从开始起算的计时；其下是触发它的消息（发送者与正文，提醒为标题）；步骤为本轮的工具调用，完成的打勾并写时长；当前步骤为回复时写「正在写回复…」，为思考时写「正在思考…」，并写这一步的计时；底部为本轮的输入、输出、缓存命中与已调用工具的次数。窄屏按效果图省去渠道、动词与时长。
  - 今天（主栏，顺序 20）：标题旁为今天的回合数与失败数，取消的回合不算失败，右侧链接到完整活动记录；今天开始的回合从新到旧，每行为开始时间、来源（提醒到期，或渠道的 logo 加会话名称）、触发的文字、结果（完成、失败、已取消、未知或进行中）、时长与 tokens。宽屏按卡片的高度列出，窄屏列出最新的 3 个。
  - 今日用量（侧栏，顺序 20）：今天的 tokens、费用与缓存命中，以及 24 根按查看者时区逐小时的柱子，高度相对当天最大的一小时，没有用量的小时为短桩，当前小时着色。
- 完整活动记录经 AGENTS 登记为动态之下的 tab：事件从新到旧、按天分组，每页 50 条，与 0.2 相同，滚动到末尾时载入更早的一页，载入中显示「正在加载更早的记录」；新的事件经统一总线加在顶部。以 `turn` 参数打开时，从该回合最新的一行起往更早载入一页，该回合的各行底色标出，顶上带「回到最新」；回执即以此链到它的回合，ACTIVITY 给出的回执因此带上回合的 turn id。筛选为全部、回合、渠道、运行时、提醒与失败。各行的来源与写法：
  - 智能体：`harness.turn.requested` 为「开始处理」加会话名称，说明为模型；`harness.completed` 为「本轮完成」，右侧为时长、输入、输出、缓存命中与工具次数；`harness.failed` 为本轮失败与原因；`harness.cancelled` 为「本轮取消」；
  - 渠道：`channel.inbound.persisted` 为「收到 X 在 Y 的消息」，说明为正文；`channel.outbound.sent`、`partial` 与 `queued` 为「回复了」加会话名称，说明为正文，`failed` 与 `unknown` 为发送失败；`channel.connection.connected` 在同一连接失败之后为「重新连上」，否则为「连上」，说明为机器人；`channel.connection.failed` 为连接失败、错误与下次重试；
  - 运行时：`harness.tool.completed` 与 `harness.tool.failed` 按上面的种类写出，载荷被 starlink 去掉时，按同一 call id 的 `harness.tool.started` 写出，并注明「输出过大，未上报」；`harness.pool.banned` 为「A 被限流，改用 B」，没有 fallback 时为「A 被限流」，说明为会话名称；连接的模型取该连接最近一次报告的模型，还没有报告时取配置中的 model；
  - 提醒：`reminder.fired` 为「提醒到期」加标题，说明为会话名称；
  - 失败筛选列出上面标为失败的行，取消的回合不在其中。0.2 的行按 0.2 的事件名列入同一类，0.2 没有记录的模型与工具字段不写。
- 摊位中的智能体，经 COMPUTERS 的 `stall` 登记：该电脑上账户可见的 agent，每个一行，为灯笼、名字、渠道与运行时的 logo，以及一句近况：进行中的回合写「正在处理」加会话名称与计时；空闲时写「空闲」与最后一次活动的时间，最后一次活动是回复时写成「在 Y 回复了 X」；电脑离线时写「电脑离线」。行末为近七天的走势：每 6 小时的回合数，共 28 个点，按查看者所在时区的零点对齐。
- 集市动态是概览内容（键 feed，一列，顺序 40），标题为「集市动态」：账户可见的 agent 与电脑的近况，从新到旧，取最新的 50 条，按卡片的高度显示；每条为图标、加粗的主语与事件，以及不带「前」的时间距离：
  - 开始处理：`harness.turn.requested`，主语为 agent；
  - 回复：sent、partial 与 queued 的出站事件，写作「在 Y 回复了 X」，X 为 reply_to_message_id 所指消息的发送者，没有时省去；
  - 新联系人：events 中 contact 标出的 `channel.inbound.persisted`，私聊写作「X 请求私聊 A」，群聊写作「Y 想让 A 加入群聊」；
  - 回合的工具调用：有工具调用的回合结束时，按回执的写法汇总本轮全部调用，例如「运行了 6 条命令」，主语为 agent；
  - 提醒到期：`reminder.fired`，写作「A 的提醒到期：标题」；
  - 断开连接：departures，主语为电脑。
- 统一总线的主题：`activity.agent`（范围为 agent）的状态为该 agent 最新一行事件的 id 与进行中的回合，动态的区块与完整活动记录据此刷新；`activity.feed` 与 `activity.stall`（范围为电脑）同理。
- 查看动态、完整活动记录与摊位中的智能体需要该 agent 的 agents.view，集市动态只列出账户可见的 agent 与电脑。

### 会话模块 server-conversation

- 新仓库 open-bazaar/contrib-server-conversation 的 bazaar-contrib-server-conversation 提供 server 端的 `server-conversation`，并声明它兼容的 bazaar-contrib-starlink-conversation 版本范围，在节点上按这个范围安装。`server-conversation` accept SERVER、ACCESS、OVERVIEW、COMPUTERS、AGENTS 与 ACTIVITY，offer `CONVERSATIONS`，以 server 本体的 `module("chats", …)` 提供页面。仓库按现有 contrib 仓库建立 CI 与发版流程，包登记到 contrib-index。
- 权限点 agents.approve 由它经 server 本体的 `permission` 登记，并以 ACCESS 的 `points` 在 `agent` 上追加，导入的 0.2 分享照常生效。允许、拒绝与撤销需要该 agent 的 agents.approve；编辑自动回复需要 agents.update，与 0.2 在 agent 配置中编辑它时相同；其余页面需要 agents.view。
- CONVERSATIONS 给出账户可见的、等待审批的会话数，供用量插件的概况使用。

#### 数据

- 会话模块没有自己的表。每个 agent 的会话经 call 取得：`conversation.conversations` 给出列表，`conversation.conversation` 与 `conversation.history` 给出会话的信息与消息，`conversation.search` 给出搜索结果，`conversation.reminders` 给出提醒，`collaboration` 的成员完成审批与自动回复。
- 等待审批的会话与会话总数缓存在处理请求的进程中：该 agent 的消息事件游标没有变化、且结果不满 60 秒时沿用，否则在电脑在线时重新下发；同一个 agent 同时只有一次在途，节点没有回应时保留上次的结果，与第 13 阶段相同。
- 跨 agent 的列表与搜索合并各 agent 的结果：列表按最后活动排列，搜索按所选的顺序排列，最相关时按节点给出的分值；翻页时记下每个 agent 已读到的位置。电脑离线或会话功能正在它上面安装时，它的 agent 的会话暂时不在列表中，列表上方写明暂时看不到的 agent 及原因：电脑离线、正在这台电脑上启用会话功能，或安装失败时的错误。
- 正在处理与回执取自 ACTIVITY：会话中有进行中的回合时，消息末尾写「正在处理」与计时；agent 的回复下方写这条回复的回执，点开后转到完整活动记录中的这个回合，即 `/agents/<短编号>?tab=log&turn=<turn id>`。动态的提醒区块写出的会话名称取自节点 `reminders` 给出的名称。

#### 图片

- 附件中的图片在消息中直接显示，宽度与效果图相同，下方写文件名与大小；其他附件只写文件名与大小。图片地址为 `/ui/chats/files/<agent 的短编号>/<attachment id>` 加 ACCESS 以 `attachment` 为标签对 agent id 与 attachment id 的签名；请求时校验签名与账户在该 agent 上的 agents.view，再按段经 `conversation.attachment` 读取并依次写出，每段是一次限时 60 秒的 call。响应带 `Cache-Control: private, max-age=86400`、`Content-Security-Policy: sandbox` 与 `X-Content-Type-Options: nosniff`，附件按 id 不会改变。
- 正文中以 Markdown 写出的 http(s) 图片沿用 0.2 的代取：地址为 `/ui/chats/images?u=<地址>&s=<签名>`，签名以 `image` 为标签；只接受 http 与 https，主机须解析为公网地址，最多跟随 3 次重定向且每次重新检查，整体限时 20 秒并经 TIMER，上游须为 image/* 且声明的长度不超过 10 MiB，按 64 KiB 读取并在超过 10 MiB 时停止，响应头与上面相同；签名不符为 403，其余失败为 502。

#### 页面

- 侧边栏在工作台分组登记「会话」，位置在智能体与电脑之间，徽标为账户可见的、等待审批的会话数；窄屏在底部导航中。
- 会话页（宽屏为列表、消息与会话信息三栏，窄屏逐层打开）：
  - 顶栏为标题、智能体筛选（全部智能体或其中一个）与搜索消息的搜索框；
  - 列表的 tab 为会话（总数）与新联系人（等待审批的数量），各以图标开头（messages-square、user-plus）；窄屏只显示图标与数字，文字留给读屏。会话的每行为头像（群聊为群组图标，私聊为名字首字）与角上的渠道 logo、名称、所属 agent 的灯笼与名字、最新一条消息（「发送者：正文」）与时间；滚动到底时载入下一页，载入中显示「正在加载更多」。宽屏打开会话页时打开最新的会话。
  - 消息区：标题为会话名称，副标题为渠道与会话类型、成员数与负责回复的 agent；消息按天分隔，每条为头像、名字、身份标签（人类、本会话的智能体为琥珀色、其他智能体为紫色）、时间与正文，正文经 server 本体的 markdown 过滤器渲染，提醒等系统消息单独成行、不按 Markdown 渲染，与 0.2 相同；首次载入最新的 50 条，向上滚动时载入更早的，与 0.2 相同，载入中在顶部显示「正在加载更早的消息」，正在读的那条位置不动；最新一条移出视野时，消息区底部显示「回到最新」，点了滚回最新一条；标题栏的搜索按钮在本会话中搜索，窄屏在会话的顶栏，与 0.2 相同，提醒按钮打开会话信息中的提醒；底部写明这里只读：消息由该 agent 在该渠道中收发，回复请直接在群里或私聊中进行。
  - 会话信息：成员与人数，每人为头像、名字与身份（人类、本会话、智能体）；提醒与个数，每个为触发时间与标题，重复的提醒按 repeat_rule 写作「每天 09:00」「每周一 10:00」或「每 2 小时」；会话一节为审批（已允许与「撤销」）、首次联系与消息数。
  - 撤销：宽屏在该行旁弹出确认，窄屏从底部弹出，标题为「不再回复」加会话名称，正文写明撤销的后果，按钮为「取消」与红色的「不再回复」；确认后经 `review_contact` 提交 denied，会话离开列表。
- 新联系人：列表为等待审批的会话，从新到旧，每行为头像与渠道 logo、名称、被联系的 agent，以及「私聊」或「群聊 · N 位成员」与第一条消息；打开后中间为审批卡片：待审批、首次联系、「X 想私聊 A」或「Y 想让 A 加入群聊」、渠道、handle、第一条消息距今多久与全文，按钮为允许与拒绝，下方写明允许之前 agent 只会发自动回复，没有设自动回复时写明它不会回复；右栏为联系人、首次联系、消息数与该 agent 的自动回复，铅笔就地编辑，保存经 `set_setting`。允许之后会话进入会话 tab 并打开；电脑离线时按钮不可用，与 0.2 相同。第一条消息以不带中心、只读一条的 history 取得，与 0.2 相同。
- 搜索：结果代替列表，为找到的条数、会话、发送者与时间的筛选和排序（最相关或最新）；每条命中为会话、所属 agent、「发送者：摘要」与时间，匹配的词按节点给出的片段标出。结果滚动到底时载入下一页，载入中显示「正在加载更多」。打开命中时，消息区以它为中心载入，命中的一条底色标出，并带「回到最新」。会话筛选列出范围内 agent 的会话，发送者筛选列出所选会话的成员与 agent 自己，时间为今天、近 7 天、近 30 天与不限。
- 按消息 `69aacd72-4950-51e8-8276-eab4f7247fc0` 与后续接手消息 `585327d4-686f-5b50-a600-8330098de071`，会话消息、会话列表和搜索结果使用共同的虚拟列表。参考原 server `src/bazaar_compute_server/resources/static/components.js` 的 `messageSearch`（保留于 `08b23161e2f0aaabba51069292c2010bd32a234a`）：已加载条目保存 HTML 与实测行高，累加行尾位置，二分查找滚动位置上方一屏到下方两屏的范围；上下占位保留总高度，`ResizeObserver` 更新行高，记住正在读的条目与视口偏移并在测量后复位。0.2 只有搜索结果采用这一做法。
  - 公共实现放在 contrib-server 的 `window-list.js`，列表以 `data-window-list` 指定条目选择器。组件保留已加载内容，离开窗口的行从 DOM 移除，返回时从保存的 HTML 恢复并初始化 Alpine 与 htmx。桌面使用列表自身的滚动容器，手机使用当前页面实际滚动的祖先容器；宽度变化重新测量并保持阅读锚点。
  - htmx 继续处理原分页地址、游标、加载提示和下一页触发元素。组件在片段追加后接收新条目；替换整份列表时结束原组件的观察并使用新结果。页面恢复条目后按当前会话重新标出选择。
  - 先交付搜索结果的公共组件与单页效果，review 后接入会话列表与消息区。消息接入时以已加载消息的首尾记录决定分页和新消息游标，日期分隔随相邻消息处理；顶部追加与图片尺寸变化保持正在读的消息，在底部时新消息和正在处理的变化继续跟随最新，在上方阅读时保持位置。
- 智能体详情的会话 tab，经 AGENTS 登记在动态之后，计数为该 agent 的会话总数：左侧为会话与新联系人两个子 tab，每行为头像与渠道 logo、名称、「群聊 · N 位成员」或「私聊」、最新一条消息、时间与 agent 未读的消息数；右侧为新联系人的卡片（允许与拒绝）与自动回复。点开一行时转到会话页中的这个会话。
- 动态的提醒区块（侧栏，顺序 30，窄屏不显示），经 AGENTS 登记：待触发的提醒数，每个为触发时间与「标题 · 会话名称」。
- 需要你处理是概览内容（键 attention，一列，顺序 30）：标题旁为条数，右侧「全部」打开会话页的新联系人；各条从新到旧，为账户可见的新联系人（私聊附第一条消息，群聊附成员数与第一条消息距今多久，都带允许与拒绝），以及 AGENTS 给出的状态为出错的 agent，写作「A 在 <电脑> 上启动失败」，下方为原因，即智能体列表中「出错」所写的原因。
- 统一总线的主题：`chats.count` 为徽标；`chats.list`（范围为智能体筛选）的状态为范围内 agent 的消息事件游标与离线的电脑；`chats.thread`（范围为 agent 与会话）为该会话的消息事件游标与进行中的回合；`chats.agent`（范围为 agent）为会话 tab 的计数。页面随之重新取得变化的部分，与 0.2 相同。

### 用量插件 server-usage

- 新仓库 open-bazaar/contrib-server-usage 的 bazaar-contrib-server-usage 提供 `server-usage`，accept SERVER、ACCESS、OVERVIEW、COMPUTERS、AGENTS、ACTIVITY 与 CONVERSATIONS，只登记概览内容（键 usage，三列，顺序 10）。仓库按现有 contrib 仓库建立 CI 与发版流程，包登记到 contrib-index。
- 第一行为概况与时间范围：概况写「N 位智能体正在工作，N 个新会话等待审批，N 台电脑离线。」，数字为 0 的一句省去，窄屏省去离线的电脑；右侧在今天、近 7 天与近 30 天之间切换，窄屏只有今天。近 7 天从 6 天前的零点起，近 30 天从 29 天前的零点起，保留期较短时只统计保留下来的部分。
- 其下四张卡片：
  - 运行中的智能体：运行中的数量与账户可见的总数，以及每个 agent 一个灯笼；
  - 正在工作：数量与名字；
  - 处理轮次：所选范围内开始的回合数、与前一个同样长的范围相比的升降（今天为较昨日），以及走势：今天为从零点到当前小时的逐小时回合数，近 7 天与近 30 天为逐日；
  - 用量：tokens 与费用，以及输入、缓存命中与输出的构成条与图例。
- 窄屏按效果图使用短标签，省去走势与构成条。
- 统一总线的主题 `usage.overview` 的状态为可见 agent 的事件游标、agent 的状态摘要、等待审批的会话数、离线的电脑数与按 15 分钟取整的当前时刻，有变化时卡片重新计算。各时区的整点都落在 15 分钟的整数倍上，查看者所在时区每过一个整点卡片随之重算，没有其他变化时今天与近 7 天、近 30 天的范围也在零点前移。

### 默认配置与 V4→V5

- server-management 的默认配置加入 server-activity（retention_days = 30）、server-conversation 与 server-usage；~/.bcs 存在时，按 0.2 的规则把 ~/.bcs/config.toml 顶层的 retention_days 写入 server-activity 条目。
- config-versions 的 V4→V5 转换在 ~/.bcs 存在时声明三者的包并启用其条目，server-activity 条目写入 0.2 的 retention_days。

### 插件索引的刷新

依据消息 `04270749-b887-599d-ba53-1442c8251f44`、`31bc4375-58da-50c6-9906-09c67b98254b`、`803e86d0-5507-590e-9946-ec655e11e78c`、`feed60b1-7b78-5ede-bb60-c9243f4bbc04`、`b11cd361-6bee-526d-a4fa-2ae401619a7a`、`d27e238b-d3d4-55ca-be34-223c68836774`、`83ead17b-be9d-56da-831f-2512ce081d5d`、`53091712-6854-5ef4-adf6-7d6a842d5dbb`、`9c25bc0c-42e5-5fc0-9a61-dcfff69c3e57`、`e76bba68-bdf3-54ca-afa7-f01c6d2c5502`、`20d45d49-1cf5-56a8-a549-60e8645d9bf2`、`e9af99c9-287d-5351-863a-280c20d9bb6c` 与 `1cdf8c35-fc70-5c5e-94d4-eb0f234db53b`，随 Task 65 交付：

- node-management 与 server-management 的默认配置在两份内置索引之后加入 contrib-index 的在线地址 `https://raw.githubusercontent.com/open-bazaar/contrib-index/main/index.json`，并启用 contrib-indexes。config-versions 的 V4→V5 转换此前不写 contrib_indexes，0.2 升级上来的节点没有任何索引；转换改为写入同样的三份索引，并在 contrib-manager 旁启用 contrib-indexes。内置快照照旧保留，在线索引尚未读到时由它提供 contrib-index 的内容；同一个包与来源出现在多份索引中时照旧合为一条。
- 读取索引不访问网络：http/https 索引只读 manager 数据目录（`<BCN_HOME 或 ~>/.bcn/contrib/indexes`）中刷新写下的缓存文件，没有时为空；内置资源与本地文件照旧在读取时直接读。运行中的节点与各个 bcn 命令是不同的进程，读同一份文件，读取不经 sidecar。
- ContribCatalog 提供统一的刷新：并发读取配置中全部 http/https 索引，下载后校验再原子写入缓存文件；读取失败时保留原有条目，并把错误写入同一缓存文件，任何进程的 `bcn contrib index list` 都能看到。每个 bcn 命令都会启动 contrib-manager，后台刷新不放在它的 lifespan 中：bazaar-contrib-manager 新增入口 contrib-indexes，没有插件 accept 它，只在 bcn run 激活整个组合时启动，启动后在后台刷新一次，此后经 TIMER 每小时一次。`contrib index add` 加入索引后刷新一次；新增命令 `bcn contrib index refresh` 手动刷新并输出各索引的状态。server 端的手动刷新留给第 15 阶段的市场模块。
- catalog 在本进程内记下正在失败的索引，一份索引开始失败时发布事件 `CONTRIB_INDEX_FAILED`，载荷为该索引的状态；持续失败不重复发布，恢复后再失败时再发布。node-health 监听该事件并发布审计事件 `contrib.index.refresh_failed`，载荷为索引地址与错误，审计事件经 starlink 送到 server。server 主机没有审计，失败记在 `bcn contrib index list` 的状态中。

### 验收

- 两端验收沿用第 12 与 13 阶段的做法：server 与节点经真实 HTTP 连接，节点的 agent 以 TestChannel 收发消息，运行时为测试目录中按 harness 契约实现的运行时；codex 与 claudecode 的字段由各自的 e2e 以真实 CLI 验证。
- 页面以真实浏览器在 1440px 与 390px 宽度、夜市与晨市下截图，与 design 中第 14 阶段的效果图对照：会话页的四种视图、智能体的会话 tab、动态与完整活动记录、手机的九个画面，以及概览。
- 原 Bazaar Server 的历史、refs 与升级后的新事件在同一个数据源中查询：0.2 的回合、用量与消息出现在动态、完整活动记录与概览中。

## Tasks

每个 Task 在所涉及的每个仓库各一个提交，完成后 review。

- Task 43：本计划，并同步计划入口、路线图与 framework。
- Task 44：bazaar-contrib-http。
  - 验证：真实监听，路由、中间件与 ASGI 应用的登记，请求生命周期，退出时关闭监听并结束在途请求，Windows／Linux／macOS CI。
- Task 45：server 的基础。包括 contrib-server-migrations（由 contrib-server 改名，改为 upgrade_only 并 accept SERVER_SCHEMA）的 server-migrations；新仓库 contrib-server-database 的 server-database（私有绑定的数据库，SERVER_DB 与 SERVER_SCHEMA，打开数据库前从 ~/.bcs 复制数据库与 session.key）；新仓库 contrib-server-management 的默认配置与 0.2 设置的读取；以及 config-versions 的 V4→V5 转换中 server 的包、条目与 0.2 设置。server-management 的安装测试从 PyPI 安装已发布的 bazaar-compute 与 `bazaar-compute[node]`，再加入本地构建的包。
  - 验证：空库时 server-migrations 不建表；0.2 的 v3 与 v7 数据库接续到 v07 并保留原账本，再次启动不重复执行。
  - 验证：用 v0.2.3 Bazaar Server 生成的真实 ~/.bcs 启动，首次启动后 server 数据库的表结构与行数、session.key、http 条目的 listen 与 server-database 条目的存储选择与原值一致，~/.bcs 不变。
  - 验证：再次启动不重复导入；没有 ~/.bcs 时生成原默认设置与当前基线。
  - 验证：只装 bazaar-compute 与 server-management 的 host 首次运行成为 server；同时装有 node 的包时，节点的配置与数据库不受影响，server 的条目都在顶层，contrib manager 可以管理。
  - 验证：已有 0.2 节点配置与 ~/.bcs 时，V4→V5 转换声明 server 的包并启用其条目，写入的设置与 0.2 一致。
- Task 46：新建 contrib-server 与 contrib-server-access，登录与 server 本体一起交付：
  - contrib-server 的 server 本体与主题插件 server-theme-night、server-theme-day：外壳与嵌入工具、侧边栏、概览、设置页、权限点登记、主题接口、语言、refs、API 挂载、请求限制，以及窄屏布局与 PWA；
  - contrib-server-access 的 server-access：会话、登录页、admin、退出与登录检查；它的表按 0.2 归属一次接管；
  - config-versions 的 V4→V5 转换与 server-management 的默认配置加入 server、server-theme-night、server-theme-day 与 server-access，server-access 条目写入 0.2 的 session_minutes。

  发版顺序为 bazaar-contrib-server、bazaar-contrib-server-access、bazaar-contrib-server-management。外壳与嵌入工具的浏览器验证以登录为前提，放在 contrib-server-access 的测试中。
  - 验证：经真实 HTTP 取得公开页面与嵌入工具；主题登记与撤销后主题样式表随之变化；保留名被拒绝；响应带 frame-ancestors 与缓存头；没有登录模块时非公开页面与 API 一律拒绝。
  - 验证：以启动时创建的 admin 登录后，Jinja 基础模板与嵌入脚本在真实浏览器中设置主题与语言，同步地址栏、标题与高度；退出后回到登录页；未登录时页面与 API 的跳转和拒绝；超过 1 MiB 的请求与跨站的写操作被拒绝。
  - 验证：390px 与 1440px 宽度下，外壳、概览、设置页与登录页在真实浏览器中的布局与 design 中的效果图一致；底部导航多于五项时出现「更多」；manifest 与图标可以取得。
  - 验证：V4→V5 转换与新装 host 的 server-access 条目带有 0.2 的 session_minutes。
- Task 47：server-access 的账户、角色、权限检查与第 2 版迁移，设置页中的安全、角色、账户与登录方式四个部分，第三方登录方式的接口、登录页中第三方登录的展示与切换以及续期；server 本体中账户的显示名与头像，侧边栏的分组、徽标与组件，账户卡片的白天、黑夜切换，页面绘制的顶栏，统一总线，以及个人设置中的语言、主题模式与白天、黑夜主题；概览移到新仓库 contrib-server-overview 的 server-overview；新仓库 contrib-server-github 的 server-github；config-versions 的 V4→V5 转换与 server-management 的默认配置加入 server-overview。发版顺序为 bazaar-contrib-server、bazaar-contrib-server-access、bazaar-contrib-server-overview、bazaar-contrib-server-github、bazaar-contrib-server-management；server-overview 的测试以 server-access 登录。
  - 验证：权限点检查与 admin；侧边栏、概览与设置页按权限点显示，登记撤销后随之消失；角色的新建、修改、设为默认与删除，账户的角色分配；admin 改密码后旧会话失效。
  - 验证：按账户的主题模式、白天与黑夜主题、语言与概览顺序，在真实浏览器中保存后外壳与页面随之变化；侧边栏按分组显示，账户卡片可以切换总是白天与总是黑夜。
  - 验证：第 2 版迁移在空库以及 0.2 的 v3 与 v7 库上完成，外键去掉，账户、角色与默认角色的数据不变。
  - 验证：统一总线在一个窗口中每轮只有一次请求，变化的订阅随之刷新，无权查看的返回不可用；侧边栏的徽标与组件经它更新，手机上组件在底部导航上方；页面绘制顶栏；概览插件的登记、排序与首页跳转。
  - 验证：第三方登录以测试中按接口实现的一种登录方式验证：授权事务只能使用一次，过期或浏览器令牌不符时被拒；首次登录建账户并带登录方式或全局的默认角色，都没有时失败；会话过期后续期在授权有效时延续登录，失效时回到登录页；被登录方式用作默认角色的角色不能删除；有第三方登录时账号密码收在右上角，可以往返切换。
  - 验证：server-github 的测试对本地 TLS 服务进行，做法与 Dex 的 GitHub connector 相同：该服务以测试证书提供插件用到的 GitHub 接口（授权后带 code 回调、换取令牌、/user 与 /user/emails），覆盖首次登录建账户、再次登录、续期、授权失效后续期失败、拒绝授权与 GitHub 不可用；配置页保存配置并按页面 origin 给出回调地址。
  - 验证：以维护者的 GitHub 账户与在其名下建立的 OAuth App 手工验收一次：测试实例运行在浏览器可以访问的机器上，以真实浏览器完成登录与续期，在 GitHub 上取消授权后续期失败并回到登录页。
- Task 48：新仓库 contrib-server-computer 的 server-computer 与 server-relay-memory：身份与登记、三个接口、中转契约、调用与在线状态、机器信息，以及电脑的页面、权限点、改名与概览内容；server-access 的分享随它的第一个使用者一起交付，包括分享的登记、可见与可操作范围的检查和分享页；node-management 的 `info()` 与 psutil 依赖；server-overview 的问候、内容的列数与调整顺序；config-versions 的 V4→V5 转换与 server-management 的默认配置加入 server-computer 与 server-relay-memory。发版顺序为 bazaar-contrib-server-access、bazaar-contrib-server-overview、bazaar-contrib-server-computer、bazaar-contrib-server-management。
  - 验证：登记电脑后得到一次性 token，错误 token 被拒。
  - 验证：长轮询的保持、返回与退出时释放；调用与结果的关联、在途去重与 60 秒超时。
  - 验证：事件确认与重放跳过；在线时间的记录与查询；电脑页面按权限点与分享显示。
  - 验证：只分享了查看的角色不能改名，分享了修改后可以改名，空名称被拒；分享页只能授予自己具备的权限点，保存后可见与可操作的范围随之变化。
  - 验证：node-management 的 info 在三平台返回系统、CPU、内存与磁盘；server 经 call 取得后显示在列表、详情与概览中。
  - 验证：以真实浏览器在 1440px 与 390px 宽度下完成登记、改名、分享与删除，与 design 中电脑的效果图一致。
- Task 49：新仓库 contrib-starlink 的 bazaar-contrib-starlink。测试在同一进程中以 PyPI 上的 server-computer 为另一端经真实 HTTP 连接，被调用的成员由测试目录中安装的一个小包以 RemoteEntry 声明。
  - 验证：sidecar 分发只到声明成员，结果回传与失败重试，上报的确认与重放。
  - 验证：token 与 token_env 的选择，server 不可达时的退避重试，退出时结束在途调用与后台任务。
- Task 50：V4→V5 转换中的 starlink 迁移，并在 contrib-index 登记十个新包。
- Task 51：两端联合验收。按消息 `38087144-154a-5eaa-93c7-78c2de410d74`，测试放在 contrib-server-management，在第三个安装环境中从 PyPI
  安装已发布的 `bazaar-compute[node]`，加入本地构建的 server-management，两端经真实 HTTP 连接。节点按电脑登记页给出的命令
  `bcn contrib add bazaar-contrib-starlink --enable starlink --option server=… --option token=…` 从索引装上 starlink 并连上 server。
  测试目录中的小包 two-ends 提供 server 侧的 ends-server（一个模块，经 COMPUTERS 调用电脑的成员，列出收到的 REPORTED）与节点侧的
  ends-node（以 RemoteEntry 声明 speak，向节点的审计说一句话）。
  - 分进程：server 与节点各以一个 `bcn run` 运行，分别以 server-management 与 node-management 的默认配置作为首次写入的配置，
    各自使用自己的配置、数据库、端口与运行目录。
  - 按消息 `508d6600-4d1d-50d6-83ab-9216b5c98986`，同进程：一个 `bcn run` 以两者合并的默认配置同时运行 server 与节点，
    starlink 连到同一进程中的 server。
  - 验证：登记后电脑页显示在线与节点的版本；server 调用 node.version、node.info、agent-activity.agents 与 ends.speak 得到结果，
    agent-activity 未声明的 observe 被拒；节点说的话在 server 各收到一次。
  - 验证：server 停下后节点记下 starlink.failed，期间节点经 sidecar 说的两句在 server 以同一目录与端口重新启动后各到一次，
    并带有 starlink.failed 与 starlink.recovered；同进程时 server 的电脑在 bcs.sqlite3 中，不在 bcn.sqlite3 中。
  - 验证：按消息 `897f1d71-9e4f-5394-97e5-659d9f2387d1`，Linux 与 macOS 上两端收到 SIGINT 后退出，输出中没有异常回溯；
    Windows 上直接结束进程，只验证节点的恢复。
  - 三平台 CI。
- Task 52：第 13 阶段的计划与效果图，并同步计划入口、路线图与 design 的说明。
- Task 53：节点侧，BCN `0.3-server`：agent-command 的 remote `agent-configuration` 及其成员，agent-activity 新增的内容与它读取的 agent 状态，bazaar-contrib-channel 的连接事件，`AuditContext` 的 `connection`，以及 agent 为渠道与运行时的组合放入的审计服务；完成后发布 bazaar-compute 的 rc。
  - 验证：真实节点经 sidecar 调用 `agent-configuration`：`list` 只给出凭据的字段名；带连接名编辑时没有提交的凭据沿用，提交 null 时清除，两个运行时调换顺序后凭据随条目保留；`providers` 给出已启用 builder 的 schema；`inspect` 在 agent 的工作区与临时目录中各查询一次，临时目录随后不存在；`workspace` 给出绝对路径、总项数与一页内容，按 offset 读完各页与总项数一致；`read` 给出文本文件的内容，二进制文件标明无法显示；工作区之外的路径被拒绝。
  - 验证：配置变更：回合进行中修改运行时的模型并保存，保存在回合结束前返回，这一轮照常完成，空闲后该运行时按新配置重启，之后的会话使用新模型；只调整顺序时，已有会话继续在原运行时上，新会话按新的顺序选择运行时；修改渠道的配置后，agent 空闲时按新配置重新连接；一个 agent 正在等待时，另一个 agent 的变更照常生效。
  - 验证：agent-activity 在回合开始时给出 working，结束后清除；渠道连接的状态、身份与失败次数随连接事件变化；运行时被限流时在用者换为下一个。
- Task 54：先在 BCN `0.3-server` 让 `channel.connection.failed` 的重试时间可以为空、agent-activity 区分重新连接中与已断开，并发布 bazaar-compute 的 rc；再做渠道与运行时插件：contrib-telegram、contrib-lark 与 contrib-wecom 的配置模型标注并改发统一的连接事件，contrib-codex 与 contrib-claudecode 的配置模型标注。各自发布 rc。
  - 验证：BCN：渠道报告不再重连时 agent-activity 给出已断开，重新连上后恢复为运行中。
  - 验证：各插件的 schema 带凭据的 writeOnly、`x-locales` 中英文的名称与说明、枚举文字、留空含义与 inspect 标注；各渠道连上时发出带机器人身份的连接事件，事件上下文带有连接名，插件代码不涉及连接名；telegram 以同一机器人的另一次 getUpdates 打断长轮询，验证失败与重连的事件；wecom 以同一机器人的另一个连接接管，验证不再重试的失败事件。
- Task 55：server 侧：contrib-server-access 的 `points`，contrib-server-computer 的 `computers(ids)` 与 `COMPUTER_REMOVED`，新仓库 contrib-server-agent 的 server-agent，以及 contrib-server-management 的默认配置加入 server-agent；按消息 `c2615c37-cdfd-5709-a9f1-0e02a9aeea13` 与 `1afb7e1f-dfb8-579d-82d9-599821a29a1f`，harness 契约中 MCP 服务的状态改为公共枚举（连接：已连接、连接中、已停用，或带原因的失败；登录：不需要、已登录、未登录），contrib-codex 与 contrib-claudecode 的 inspect 按它补上 MCP 服务。发版顺序为 bazaar-compute、bazaar-contrib-codex、bazaar-contrib-claude-code、bazaar-contrib-server-access、bazaar-contrib-server-computer、bazaar-contrib-server-agent、bazaar-contrib-server-management。
  - 验证：只分享了查看的角色看得到 agent 但不能修改，分享了修改后可以保存配置；拥有者可以分享；电脑的分享页出现 agents.create，没有分享时新建向导不列出该电脑。
  - 验证：以真实浏览器在 1440px 与 390px 宽度下完成新建（两个渠道、两个运行时）、配置修改（凭据留空不变，运行时调换顺序）、工作区展开与滚动载入、查看 Markdown 文件与按类型着色的代码文件、技能查看、分享与删除，与 design 中智能体的效果图一致。
  - 验证：电脑离线后列表仍列出它的 agent 并标为离线；删除电脑后它的 agent 与分享随之删除；REPORTED 的回合事件到达时，侧边栏的「正在工作」随之出现与消失。
  - 验证：codex 与 claudecode 的 e2e 中，inspect 给出工作区的 MCP 服务、按枚举的状态与它的工具。
- Task 56：config-versions 的 V4→V5 转换在 ~/.bcs 存在时加入 server-agent，contrib-index 登记 bazaar-contrib-server-agent，然后发布 bazaar-compute 的 rc，内置索引随之带上它。
  - 验证：已有 0.2 节点配置与 ~/.bcs 时，V4→V5 转换声明 bazaar-contrib-server-agent 并启用 server-agent；contrib-index 的检查通过。
- Task 57：两端验收：contrib-server-management 的两端用例加入经页面新建、修改与删除 agent。
  - 验证：分进程的 server 与节点上，新建的 agent 写入节点配置并运行；修改后没有提交的凭据沿用；删除后配置移除，工作区保留。
  - 三平台 CI。
- Task 58：第 14 阶段的计划与效果图，并同步计划入口、路线图与 design 的说明。
- Task 59：节点侧，BCN `0.3-server`：messages-node 的 remote `messages` 新增的成员与出站事件的关联，collaboration 入站事件的 message_id，reminder 的 `reminders` 与 `reminder.fired` 的 message_id，agent 回合上下文中的运行时连接名、`harness.pool.banned` 的 fallback 与会话绑定校验给出的 session 和 turn，以及 harness 契约中工具调用的 detail、duration_ms 与 ModelUpdated，审计上下文的 call_id 与不再带 raw 的 `harness.*` 审计事件，完成后发布 bazaar-compute 的 rc；contrib-starlink 去掉放不进一批的事件的载荷后照常上报，随后发布 rc。发版顺序为 bazaar-compute、bazaar-contrib-starlink。
  - 验证：真实节点经 sidecar 调用 `messages`：conversations 分别列出已允许与等待审批的会话，按最后活动排列，给出总数、第一条与最新一条消息以及群聊的成员数；conversation 给出首次联系、消息数与成员，本 agent 发过言时在其中；history 读取等待审批与已拒绝的会话，以最新一条为中心与不带中心时各读一次，has_before 与 has_after 与实际一致；search 给出总数与分值并按分值排列；attachment 逐段读完一个图片附件，拼起的字节与工作区中的文件相同；agent 没有运行时这些成员失败。
  - 验证：agent 停下后 `reminders` 仍列出待触发的提醒，并可按会话筛选；提醒触发的回合与 `reminder.fired` 带同一个 message_id；`channel.inbound.persisted` 带所记录消息的 message_id。
  - 验证：回合中经 bcc 发出的回复，其出站事件带该回合的 session id、turn id 与 reply_to_message_id；回合的事件带运行时的连接名；运行时被限流后 `harness.pool.banned` 给出改用的连接名，没有可用的运行时时为空。
  - 验证：`harness.tool.*` 带 call_id，`harness.*` 审计事件中没有 raw；starlink 收到超过一批的事件时以 omitted 代替载荷发出，server 收到的序号连续。
- Task 60：contrib-codex 与 contrib-claudecode 按 Task 59 的契约给出工具调用的种类、字段与时长，以及每个回合的模型，各自发布 rc。发版顺序为 bazaar-compute（Task 59）、bazaar-contrib-codex、bazaar-contrib-claude-code。按消息 `1cb7ef16-04ad-5d1e-8977-d577db5efdc3` 与 `025a43ee-e09f-5a57-b85a-5be05acaf9df`，两者的 e2e 沿用已有的任务，在这些任务产生的工具调用上检查新字段。
  - 验证：codex 的 e2e 中，每个命令给出命令文本、cwd 与时长，退出码为 0 时为 tool.completed，否则为 tool.failed；MCP 的 test 服务给出 server、tool、arguments 与结果的文字；每个回合给出与 thread/start 应答相同的模型。
  - 验证：claudecode 的 e2e 中，Bash 给出命令、输出与时长，退出码与工作目录为空；`mcp__test__get_value` 分出 test 与 get_value 并给出结果的文字；每个回合给出 system/init 中的模型。
- Task 61：BCN `0.3-server`：按消息 `0d9124d3-ef6c-56c8-9584-0519a1133149` 与 `ef0e8271-2df2-59f2-97a5-57e5feaa439b`，去掉 Task 59 在 messages-node 与 reminder 上为 server 加的 remote 成员 `conversations`、`conversation`、`history`、`search`、`attachment` 与 `reminders`，以及只为它们写的查询：批量取会话、每个会话的第一条、每个发送者的最新一条、按 id 取附件、历史的 has_after、搜索的总数与分值；事件上的字段保留，即入站与提醒事件的 message_id、出站事件的 session、turn 与 reply_to_message_id、工具事件的 call_id。manager 把 CONTRIB_MANAGER 登记为 remote，成员为 add 与 operation。完成后发布 bazaar-compute 的 rc。
  - 验证：真实节点经 sidecar 调用 manager 的 add，从测试目录中的本地包源安装一个小包并启用它的条目，operation 依次给出进行中与完成，条目随后运行；再次 add 同一个包照常完成，条目仍只启用一次。
  - 验证：事件上保留的字段照旧出现。
- Task 62：server 侧的公共改动：contrib-server 的共用写法，tokens 过滤器由 server-agent 移入；contrib-server-access 带标签的签名；contrib-server-overview 登记项的顺序与行数；contrib-server-computer 的 COMPUTER_LEFT、摊位内容的登记与摊位的顺序和行数；contrib-server-agent 的 AGENTS（tab 与动态区块的登记、agent 的读取）与按渠道列出的状态区块。发版顺序为 bazaar-contrib-server、bazaar-contrib-server-access、bazaar-contrib-server-overview、bazaar-contrib-server-computer、bazaar-contrib-server-agent。
  - 验证：签名对同一标签与值成立，换标签、改值或改签名时不成立。
  - 验证：概览按登记的顺序、列数与行数排布，两行高的内容右侧依次排下两个一列的内容；390px 下全部内容按顺序竖排为一列。
  - 验证：电脑停止连接 60 秒后 announce 一次 COMPUTER_LEFT，其间再次出现时不 announce；摊位卡片载入登记的内容。
  - 验证：测试目录中的小包登记的 tab 按顺序出现在 tab 栏中并带计数，带上级的 tab 不在 tab 栏中、打开时高亮上级；动态由登记的区块组成，窄屏不显示的区块在 390px 下不出现；撤销登记后随之消失；AGENTS 的读取只给出账户可见的 agent；状态区块为每个渠道一组。
- Task 63：contrib-server 的 Pages 给出查看者的时区（zone）；contrib-server-migrations 的第 8 版去掉 events 的外键；新仓库 contrib-server-activity 的 server-activity；contrib-server-agent 中没有内容的动态区块不占位；随后 contrib-server-computer 删除电脑时不再删除 events。发版顺序为 bazaar-contrib-server、bazaar-contrib-server-migrations、bazaar-contrib-server-activity、bazaar-contrib-server-agent、bazaar-contrib-server-computer。测试经 server-computer 的 report 接口上报按契约构造的事件。
  - 验证：空库建立 events 与三张投影表；0.2 的 v7 库带 0.2 的事件升级后，events 不带外键、行数与内容不变，未启用 server-activity 时删除电脑也不因 events 失败；接续后 turns 与 usage 由 0.2 的行建立，0.2 的新联系人标出 contact；再次启动不重复执行。
  - 验证：同一批事件重复上报只写入一次、投影只更新一次；用量按增量写入，会话重新计数时以新值为增量；保留期按 retention_days 清理，node.health 每台电脑只留一条；删除电脑后四张表中它的行随之删除；重复的 COMPUTER_LEFT 只记一次。
  - 验证：以真实浏览器在 1440px 与 390px 宽度下，动态的正在处理（命令、文件、网页、MCP 与子任务的步骤，以及写回复）、今天与今日用量，完整活动记录的筛选、按天分组与滚动载入更早的记录，摊位中的智能体与集市动态，与 design 中的效果图一致；0.2 的回合与消息出现在完整活动记录中。
- Task 64：BCN `0.3-server` 的 sidecar 监听 CONTRIB_CHANGED 刷新 remote 表；agent 的配置去掉 0.3 新增的 workspace，工作区固定为 BCN_HOME 下的 .bcn/workspaces/<agent id>，与 0.2 相同，不做兼容，依据消息 `d5b8caab-38d9-5db5-920f-1d58a7b0fee6`；用到它的测试，包括 contrib-codex 与 contrib-claudecode 的 e2e，先改为以 BCN_HOME 隔离；新仓库 contrib-starlink-conversation 的节点端入口 `starlink-conversation`，新仓库 contrib-server-conversation 的 server-conversation，以及节点没有 `starlink-conversation` 时经 manager 的安装。测试以真实的节点进程为另一端，与 server-agent 的测试相同。发版顺序为 bazaar-compute、bazaar-contrib-starlink-conversation、bazaar-contrib-server-agent、bazaar-contrib-server-activity、bazaar-contrib-server-conversation；其中 server-agent 把查询参数带给 tab，server-activity 的完整活动记录可以打开到某个回合，依据消息 `c8cc6a62-3d09-5ad4-a156-9e8c562ad21b` 与 `3bd1ddc7-af96-55f8-b586-97617af045a3`。
  - 验证：真实节点经 manager 的 add 安装并启用一个声明了 remote 的小包后，不经重启即可经 sidecar 调用它的成员。
  - 验证：节点端：conversations 分别列出已允许与等待审批的会话，按最后活动排列，给出总数、第一条与最新一条消息以及群聊的成员数；conversation 给出首次联系、消息数与成员，本 agent 发过言时在其中；history 读取等待审批与已拒绝的会话，以最新一条为中心与不带中心时各读一次，has_before 与 has_after 与实际一致；search 给出总数与分值，最相关时按分值排列；attachment 逐段读完一个图片附件，拼起的字节与工作区中的文件相同；reminders 列出待触发的提醒并可按会话筛选；agent 停止后这些成员照常可用。
  - 验证：节点没有 `starlink-conversation` 时，会话模块读取该电脑即经 manager 在兼容的版本范围内装上 bazaar-contrib-starlink-conversation 并启用 `starlink-conversation`，其间会话页的列表上方写明正在这台电脑上启用会话功能，装好后列出该电脑的会话；包源中没有这个包时，列表上方写出 add 给出的错误。
  - 验证：只分享了查看的角色看得到会话，但不能允许、拒绝或撤销，分享了 agents.approve 后可以；agent 的分享页出现 agents.approve；编辑自动回复需要 agents.update。
  - 验证：跨 agent 的列表按最后活动合并并翻页；允许后会话进入会话 tab，拒绝后离开新联系人；撤销后会话离开列表、节点上为 denied，对方再发消息时回到新联系人并先收到自动回复；搜索合并两个 agent 的结果并给出总数，打开命中时以它为中心。
  - 验证：附件图片经签名地址按段取得并显示，签名不符为 403，没有该 agent 的 agents.view 时为 404；Markdown 图片的代取拒绝非公网地址与签名不符的地址。
  - 验证：以真实浏览器在 1440px 与 390px 宽度下完成会话、新联系人的审批与自动回复、撤销、搜索、会话信息、智能体的会话 tab 与需要你处理，与 design 中的效果图一致。
- Task 65：合并只会有一种实现的拆包，依据消息 `a66deef9-1176-5cba-b311-a56e9e7c7afc`、`1f53d261-18ef-58a7-84a5-bd855533e32d`、`a6c30251-43ba-5f9c-b148-ccbc28adb01f` 与 `57a23407-c84d-512c-b2f3-a461caece1e0`：保留接口包的包名，把实现移入，入口名不变，不做兼容。
  - BCN `0.3-server`：bazaar-contrib-messages 收入 `messages-node` 入口，删除 bazaar-contrib-messages-node；bazaar-contrib-agent 收入 `agent-command` 入口，删除 bazaar-contrib-agent-command；bazaar-contrib-timer 收入 `timer-wheel` 入口，open-bazaar/contrib-timer-wheel 不再发布。默认配置、自举与各包的依赖声明改用合并后的包名。manager 与 node-management 按[插件索引的刷新](#插件索引的刷新)读取与刷新索引。然后发布 bazaar-compute 的 rc。
  - 依赖这些包的独立仓库随之抬高依赖下限，测试依赖中的 bazaar-contrib-timer-wheel 去掉；contrib-server-management 的默认配置改由 bazaar-contrib-timer 提供 `timer-wheel`，加入 contrib-index 的在线地址并启用 contrib-indexes，随后发布它的 rc；contrib-index 去掉 bazaar-contrib-timer-wheel 的登记，在 bazaar-compute 的 rc 之前合并，内置快照随之不再含它。
  - 新的 rc 发布后，PyPI 上的 bazaar-contrib-timer-wheel、bazaar-contrib-messages-node 与 bazaar-contrib-agent-command 设为归档，open-bazaar/contrib-timer-wheel 在 GitHub 上设为 archived，依据消息 `748b4ec4-7929-5e52-9c8e-4bab9d8a8156` 与 `c77fb6ba-56bf-524c-a924-1d7e50edd637`。
  - 验证：节点与 server 照常启动，默认配置只声明合并后的包；原有各包的测试在合并后的包中照常通过；contrib-index 的检查通过。
  - 验证：在线索引不可达时，列出插件与 contrib add 不等待网络，内置快照中的插件照常可用；刷新成功后在线索引新增的包可以安装；刷新失败保留上一次的结果，节点只发布一次 contrib.index.refresh_failed，恢复后再失败时再发布；`bcn contrib index refresh` 输出各索引的状态；节点停止时后台刷新随之结束；0.2 升级上来的节点的配置含三份索引并启用 contrib-indexes。
- Task 66：新仓库 contrib-server-usage 的 server-usage。
  - 验证：今天、近 7 天与近 30 天的回合数、升降、走势与用量按事件计算，没有费用的回合不计入费用；概况中数字为 0 的一句省去；以真实浏览器在 1440px 与 390px 宽度下，与 design 中的概览效果图一致。
- Task 67：contrib-server-management 的默认配置加入 server-activity、server-conversation 与 server-usage，并写入 0.2 的 retention_days；config-versions 的 V4→V5 转换在 ~/.bcs 存在时加入三者并写入 retention_days；contrib-index 登记四个新包，然后发布 bazaar-compute 的 rc，内置索引随之带上它们。发版顺序为 bazaar-contrib-server-management、contrib-index、bazaar-compute。
  - 验证：只装 bazaar-compute 与 server-management 的 host 首次运行即启用三者，retention_days 为 30；以带 retention_days = 7 的 0.2 ~/.bcs 首次运行时，server-activity 条目为 7；已有 0.2 节点配置与 ~/.bcs 时，V4→V5 转换声明三者的包并启用其条目，retention_days 与 0.2 一致；contrib-index 的检查通过。
- Task 68：两端验收：contrib-server-management 的两端用例加入会话与活动。
  - 验证：分进程的 server 与节点上，节点的 agent 经 TestChannel 收到首次联系，会话页的新联系人与概览的需要你处理随之出现；允许后 agent 回复，撤销后节点上为 denied；回合、工具调用、用量与回复出现在动态、完整活动记录、集市动态与概览中；附件图片从节点取得并显示。
  - 验证：server 重启后节点重放的事件只记一次；节点停止 60 秒后集市动态出现断开连接。
  - 三平台 CI。
- Task 69：虚拟列表方案与搜索单页验证：contrib-server 提取上述公共组件，contrib-server-conversation 的搜索结果接入；交付真实浏览器效果后 review。
  - 验证：真实隔离节点产生跨多页的搜索结果，在 1440px 与 390px 下加载全部结果，页面只保留视口附近的条目；滚回已加载位置不重复查询，文字与命中链接恢复，打开后仍定位该消息。
  - 验证：页面尺寸变化时保留阅读锚点，切换搜索词、筛选或返回空结果时使用新列表；重复导航后观察与订阅正常释放。
  - 2026-11-12 单页实现：[server 3fb6d60](https://github.com/open-bazaar/contrib-server/commit/3fb6d60)、[conversation 1927410](https://github.com/open-bazaar/contrib-server-conversation/commit/1927410)。隔离环境以本地 server 组件运行会话页；180 条搜索结果在 1440×900、390×844 两种视口下完成全部分页，保留的结果 DOM 少于 50 条，返回顶部不再请求分页，桌面与手机布局互换保留阅读条目，打开命中后定位正确，改为空查询正常。两项长列表测试及三项原浏览器回归通过；server 七项测试、两仓 ruff/pyright 和 JavaScript 语法检查通过。单页效果已交付；继续完成同一范围的会话列表与消息区。
- Task 70：会话列表与会话消息接入同一组件。
  - 验证：会话列表深翻后恢复、跨会话导航与选择；消息向上追加保持当前消息位置、日期分隔正确、图片载入后锚点不动，底部跟随、上方阅读不跳转、搜索居中和回到最新保持。
  - 公共 server 组件 review 通过后先发布，再将 conversation 最低版本对齐该公开包、更新锁文件，完成 consumer review 和发布；本批前端包一起部署。

  - 2026-11-12 实现：[server 8a447ab](https://github.com/open-bazaar/contrib-server/commit/8a447ab)、[conversation c6fedbb](https://github.com/open-bazaar/contrib-server-conversation/commit/c6fedbb)。公共组件以稳定条目保存锚点和已加载顺序，接纳前后分页，提供已加载消息游标、搜索居中与底部跟随。消息与日期包在同一条目中，日期按前一个已加载消息判断；工作状态独立留在尾部。
  - 本地验收：真实隔离节点、真实 Chromium，在 1440×900 与 390×844 两种视口下，180 个群、180 条搜索结果和跨三天的 180 条消息均完成多页加载；页面条目少于 50，返回已加载位置不重新查询。八项浏览器用例覆盖阅读锚点、日期分隔、图片载入、搜索命中、选择与历史导航、底部新消息/工作状态跟随、旧消息阅读和尺寸变化；会话/搜索/共享七项回归、server 七项测试、两仓 ruff/pyright 和 JavaScript 语法检查通过。截图已交付。公共组件复审修正了追加/前插时误销毁列表、整页滚动坐标、卸载行的 htmx 全局订阅释放，以及单行替换后的旧行引用；每项改动 amend 回原 Task 69/70。独立真实 HTTP/Chromium 检查通过，53 个挂载行只触发 53 次唯一请求，八项实际页面回归全部重跑通过。server PR20 复审无新问题、三平台 CI 通过，已发布 0.3.0rc15；consumer 最低依赖与锁文件已 amend 回 Task69，公开包的共享资源与受审源码逐字节一致。移除浏览器用例中的辅助截图写盘后，使用公开依赖及 CI 的阻塞检测入口运行，完整 16 项真实业务测试全部通过；截图验收产物保留。

- Task 71：智能体列表的小操作菜单随列表按权限生成，点击立即展开。
  - 使用原有查看、配置、分享和删除权限，保留各业务操作本身的服务端鉴权；列表行直接包含菜单，移除原首次点击时请求的菜单片段路由。
  - 实现：[server-agent 163cd6f](https://github.com/open-bazaar/contrib-server-agent/commit/163cd6f)。真实浏览器在 1440px 与 390px 下离线打开/关闭菜单，再恢复网络完成原配置、共享和删除流程；仅查看权限的账号只得到对应菜单操作。列表与共享四项回归通过。
- Task 72：MCP 服务卡片中的长工具名保持在卡片内，完整显示并按可用宽度换行。
  - 服务网格项允许收缩，工具标签最大宽度为可用宽度并允许单词内换行。真实测试 MCP 提供长工具名，宽屏和手机页面验证标签不溢出卡片，实际截图均已检查；读取和两种视口浏览器用例通过。
  - 实现：[server-agent e47aa48](https://github.com/open-bazaar/contrib-server-agent/commit/e47aa48)。菜单与 MCP 修复通过同一 server-agent PR15 审查，分别保留原所属提交。CI 的阻塞检测发现截图辅助写盘后，移除测试内的截图保存，保留既有视觉验收产物和布局断言；该修订 amend 回本任务。按 CI 入口启用阻塞检测，完整 13 项真实业务测试通过。与 server、conversation 一起发布部署并核对原登录和数据关系。

- Task 73：同一第三方群的多个 bot 在会话页聚合，已知 bot 使用其智能体身份显示。授权消息 `c3857cc2-f24a-5182-8975-cfc0ba0eda85`，分支更正 `dc290b78-6581-5552-9432-c3b7a864d005`；两个会话插件使用 `feature/20261114-group-conversations`。2026-11-15 用户 `4d85b849-b077-586e-8b29-4ccbecf4b1f7` 批准渠道 builder 解析方案 `01a1d99d-1c27-7413-a2c6-119c09bcb6b3`，范围增加 channel 契约和对应渠道实现。
  - 分析：节点 `channel_sessions` 已有 `provider_thread_id`、`channel_identity`，`messages` 已有 `provider_message_id`、`sender_id` 和编辑版本。Telegram 地址是 `telegram:<bot>:<chat>:<topic>`，飞书是 `lark:bot:<bot>:chat:<chat>:thread:<topic>`，企微是 `wecom:<bot>:<chat>` 或 topic/post 地址；群和话题标识可从这些已有地址取得。server-agent 的渠道活动状态已上报 bot 身份。当前缺口在 starlink-conversation 的读取模型和 server-conversation 的视图。
  - starlink-conversation 返回已有渠道地址、账号身份、第三方消息 ID 和编辑版本；支持按消息时间及平台消息身份定位历史窗口，供不同节点的消息合并分页。
  - `ChannelBuilder.conversation_key(target, configs)` 委托现有 builder 定义离线解析已存 `ChannelTarget`，configs 为该 agent 同类渠道配置的完整元组。返回包含必要作用域、群及话题的不透明字符串，无法确定时返回 None；无需运行渠道实例。Telegram 使用数值群与话题；Lark 仅在全部候选配置区域一致时解析群与话题；WeCom 按地址的 bot 匹配连接端点并区分群、话题和帖子；QQ 的群 OpenID 无跨应用身份时返回 None，频道使用频道 ID。
  - 节点读取插件通过公开 AGENT_CONFIGURATION 与 CHANNEL_BUILDERS 取得停止 agent 的配置和已注册 builder，在目录及详情读取时派生 conversation_key。Server 仅按渠道与该键聚合，空键或私聊保留各 agent 原会话；附件和审批仍走原来源。以使用 JSON 地址、不同 bot 地址不同的自制渠道验证自动聚合，并验证缺少作用域时独立展示。
  - server-conversation 在现有读取缓存与更新总线中取得会话目录，按渠道、群和话题聚合后分页；每个群保留各智能体的原会话来源，按已有渠道账号 ID 统一 bot 名称，按平台消息身份合并重复观察，并通过原来源读取附件、活动和执行审批。
  - 验证：真实隔离节点和 HTTP 页面覆盖跨 bot 同群、同名异群、不同话题、重复入站、独立回复、群列表跨页、合并历史跨页和搜索定位；共享账号仅聚合其可见来源，审批保持各 agent 的原归属。真实浏览器在桌面和手机检查群列表、身份、历史与管理入口；两仓 Ruff/Pyright、根 LSP、原业务回归及发行包独立安装检查通过后提交 review。
  - 2026-11-14 实现：[starlink-conversation 9012bff](https://github.com/open-bazaar/contrib-starlink-conversation/commit/9012bff6994f5f7a46c2e6ce3f29b74e27606a80)、[server-conversation fc51890](https://github.com/open-bazaar/contrib-server-conversation/commit/fc518906456612ccbe0495dab711ba762e99066d)，均已签名，分别推送同名 feature 分支。节点自包版本预备 rc3，依赖保持原锁定版本；consumer 安装读取端的下限为 rc3。
  - 本地验收：节点 3 项业务测试通过；server 的 21 项 HTTP/浏览器用例分批通过（手机隐藏列表不挂载行，选择高亮断言限定宽屏后两种视口重跑通过），另以本次构建的节点 rc3 wheel 和隔离包源完成原自动安装场景，1 项通过。两仓 Ruff、Pyright、根 LSP 和 JavaScript 语法检查通过；wheel、sdist 各自与提交中的 5/58 个包源码资源逐字匹配，均完成干净环境安装及入口加载。
  - 桌面 1440×960、手机 390×844 的消息与信息页截图已检查，包含从另一个 bot 的原来源读取的图片、统一姓名和独立管理入口。证据位于工作区 `artifacts/server-group-20261114/`。本轮交付分支 review；节点 rc3 公开发行后更新 consumer 开发锁文件并运行公开依赖 CI，再按节点端、server 端顺序发行升级。
  - 2026-11-15 渠道契约修订：ChannelBuilder 离线派生聚合键，Telegram/Lark/WeCom/QQ 各自解释作用域，节点通过 AGENT_CONFIGURATION 读取停止 agent 的配置，Server 通用比较。自制 JSON 地址渠道覆盖跨 bot 同群、话题和空键相同地址独立展示；节点 3 项、Server 22 项回归及 1 项实际构建包自动安装场景通过；四渠道 9/11/8/16 项本地测试通过。七仓 Ruff、Pyright（standalone）与根 LSP 通过，14 个 wheel/sdist 安装及源码资源逐字校验通过。依赖 channel 预备 rc33、节点会话预备 rc3，公开锁在 producer 发布后更新。 节点修订 a1203c69696b0ef618bf79b4f617e862c1d55069，Server 修订 7a7ee4188d20dd6b913a55c887f16d6a052eff5d，QQ 修订 c41cd1fec718ef8480ada96b7d791513c805487b；均 amend 原所属提交，README 保持原文。

- Task 75：在 Server 的智能体列表和详情中查看运行时的剩余额度。依据消息 `96628726-8628-5811-b8ca-93a04a1cb4d3`、`10c01c75-9cc6-522d-bc9e-55fe45001ff8`、`992de549-a217-51eb-8d7c-75a4fda59aa1`。四仓各用 `feature/20261119-agent-quota`，主仓以 `0.3` 为基线，Codex、Claude Code、Server Agent 以各自的 `main` 为基线。
  - `bazaar-contrib-harness` 提供可选的 `QuotaReader.read_quota()`，按窗口返回剩余百分比、窗口时长、重置和观测时间。节点在现有 `agent-activity.agents` 批量读取各已运行适配器的额度，按运行时连接放入 `RuntimeStatus.quota`，其余运行时的额度为空。
  - Codex 用原生 App Server `account/rateLimits/read`，将窗口的已用百分比转成剩余、秒级时间戳转为毫秒。[App Server 额度查询](https://github.com/openai/codex/blob/28327355b861ab6cc76b01c7248663eb1be440cf/codex-rs/app-server/README.md#7-rate-limits-chatgpt)
  - Claude Code 用原生 `stream-json` `get_usage` 并传 `skip_behaviors: true`，将可用的账户窗口转成同一结构。[Claude Agent SDK 变更记录](https://github.com/anthropics/claude-agent-sdk-typescript/blob/main/CHANGELOG.md#03169)
  - Server Agent 以原活动状态读取与页面更新机制消费快照。列表和详情按运行时为各额度窗口画充电环，环中心显示剩余百分比。列表中模型在左，周期和环在同一行靠右，窄屏将各运行时置于同一个分组。依据消息 `e8095c98-e8a8-5a89-a817-d33348a9e503`、`4184fc32-6d98-598c-ae1a-a97ad0ca3e86`。详情的「剩余额度」区块在宽屏和窄屏均显示，并给出额度重置时间。当前没有读数时显示「额度未知」，离线保留最近一次读数。依据消息 `1c14eeac-3aa8-584a-827f-257a39c896ee`、`223a371c-e0d7-5437-a719-08f03f4e26aa`。
  - 验证：以真实登录配置验证原生查询及数值转换，验证真实节点响应与 Server 页面、缺少额度的旧节点响应、原 Agent 列表和详情。适配器以同批主仓源码联验，公共契约发行后对齐适配器的发布依赖和锁文件。
  - 2026-11-19 本地验收：已登录的 Codex 0.169.0 返回周额度与重置时间，实际节点通过 Starlink 送达 Server。1440px 和 390px Chromium 上核对列表和详情的充电环、环内百分比及详情重置时间并截图。旧节点缺字段和 Claude 返回空额度时显示「额度未知」。节点离线后，列表和详情保留最新读数。另用有马当前 Devin 配置实测 Claude Code 2.1.327，适配器返回空额度。两次联验均只查询额度，原生 Claude 订阅有读数的路径尚未实测。
  - BCN 三项独立安装与命令回归、Server Agent 13 项回归、两个适配器各 11 项进程回归通过。受影响源码的 Ruff、主仓根目录 Pyright LSP 和插件 Pyright 通过。适配器提交为 Codex `f6323b4`、Claude Code `25ad5e3`，Server Agent 提交为 `c6353b2`。新版 Server Agent 的 wheel 和 sdist 均通过独立安装及页面资源核对。
  - 2026-11-19 BCN #185 [Review 修订](https://github.com/yuchanns/bazaar-compute-node/pull/185#discussion_r4623308276)，依据消息 `e9843e55-d100-5476-aff5-81b0e51f930c`：每个运行时独立读取额度，查询异常记录到该 Agent 和运行时连接，仅该项额度为空，保留并发读取与取消语义。隔离节点通过自制插件订阅 Audit 等待渠道就绪；同一真实 Sidecar 调用在原提交因额度文件缺失而整批失败，修订后返回两个 Agent 的状态、用量、渠道和正常读数，仅故障运行时的额度未知。
  - 2026-11-19 发行修订：Agent 依赖的最低 Harness 版本为 `0.3.0rc34`（首个公开提供额度契约的版本），避免部分更新时解析到缺少 `QuotaReader` 的旧 Harness。Server Agent rc16 发行检查的锁定环境复现旧 Harness 下无法初始化节点；同一环境仅换 Harness 至 rc34 后正常生成配置。
