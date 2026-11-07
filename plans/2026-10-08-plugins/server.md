# server 框架、登录、两端通信与 agent 模块（第 11 至 13 阶段，2026-11-04）

依据消息 `063a69a7-4466-5989-987d-72340ff87d15`、`4a3df703-d8ba-52fd-8386-a80636c7ebd3`、`4a1410e9-fdd9-5e6d-9258-2b6524c85030`、`54a06153-2c7a-50d8-be38-d83215bb119f`、`a71294e0-d7b2-55ad-b070-0d39c6615bfc`、`361bd816-6861-5387-9de3-db06a0221bff`、`4a61b278-dbba-5110-b22a-1a48be84ee85`、`35a6f778-8313-5345-b815-dd56aadb82f4`、`5b10044f-5d13-593f-bdc1-f702f38d28c1`、`61a8fb0b-6faf-5b41-9845-5ce2b6255f50`、`0152f366-79a3-58a2-a4b8-097b74292762`、`048927d2-bcdb-5149-b3cd-74ba46652bd6`、`46bf2276-bd23-5186-a0e7-2e7fe36c32f6`、`6c355ba6-0bad-5525-92d7-0763f87ba60b`、`1f12a86a-5b7a-5cd1-8ab0-ab30cd64cbd4`、`75056a17-d242-53e0-b3d3-5541c3f8c2d0`、`c51fe75f-080c-5bbb-a5d4-73ebf2b05257`、`680370b8-e577-5ff8-86f0-8e7711ff16a2`、`34dd6558-b928-5db1-8744-dc150f1bcb1c`、`9fbda575-6578-574f-a885-eca2e6781f14`、`9b4c789d-27b9-52c0-a480-da6f675b957f`、`7392d984-82ef-5b71-a2dd-0f82fc8404bd`、`ae4a2fb3-637c-51f1-8661-9b00d43896c0`、`74cca34d-90c1-550a-ac3b-a75da7596efa`、`dec88203-3335-53ad-8f32-f599838099b5`、`732ad76b-eb60-5441-8c98-3c101345f20d`、`2377ea79-daee-5aff-8af4-efd3335a0a63`、`1ca34615-3618-57f4-a74c-a5de417fce7d`、`a00b3959-3fb4-5bd1-b080-ad718285cf71`、`96787fb9-cffc-545b-9637-cb35c238d7fc`、`72759245-29a2-5f1c-8420-f586190ac434`、`227a718a-65ce-55b2-bd6b-718bc585370f`、`9c4b2829-5eab-51e6-be65-ffacd7230ac2`、`8fe23c62-55a0-5ad2-9c8f-486f3459136d`、`60b4a535-b2cc-5ee2-954d-42cdd48ada8b`、`0848f322-562e-52d0-be22-8b2a5dbcc317`、`6d7ef808-9bbb-54d2-ba03-ab2ecfba8b79`、`11e6a63e-2572-5129-8f62-42c46e3844ca`、`4a309095-83e0-505e-be68-c6a0f6fe1c2b`、`c78439a5-8fcb-5c72-99b5-3fb718c03e55`、`ee83ba8e-da47-5385-b23a-bc34e7a6f58f`、`a088f3ea-c6c2-5f14-8831-3f1844592172`、`62072b45-7a4e-5a3a-b33b-ada5f46fa02a`、`2128b685-1165-5ffc-ae59-1bf377c0d729`、`a9707a85-61c1-5b2f-8212-6501f4a27597`、`89409f44-6c7f-5559-98dd-3c92e023980c`、`9a8a560e-ea1a-5b92-af21-27c315705f4f`、`04ebe6bb-3fde-5699-9478-cd4f92b06d0f`、`069a6203-552c-58c5-b57c-53b3dd5f055f`、`fe92f9bd-d2a3-5ee9-98ac-d05dfb4de85b`、`c829259a-d24e-5658-8862-8340fa564e6a`、`d929e3b7-1560-5a59-9d89-bb9bb0638eab`、`df181db4-2fb1-5bf6-9f35-13804366c141`、`fe7e2369-5a98-5338-b704-cc100dee30c9`、`145bdfe5-a2ba-5d54-98ee-b5a4c7fcf064`、`ea154f6b-e6ab-5e37-b761-21b8d3ba417d`、`0f163715-de78-5dda-ae47-86f1122c45d8`、`cca4a947-a5ad-51d8-8a61-8e890c84e2f8`、`c343e7fe-6a21-58a4-ba78-e4e9b031145d`、`2777ee0e-7dc2-5078-b8c8-26b36785fa88`、`c4993a58-dad1-5fb5-91a0-49252e01399c`、`2e906727-a570-51b8-b30d-dc5badd3b7b0`、`713d4cb5-ce3d-57a5-b325-ed96cc9d9b48`、`3cae9028-3f2d-552b-b954-adde6281dc51`、`e3d0a24f-d713-56f8-ad56-54af4e76f72f`、`eb67ecd4-cfc7-539b-8e4b-53608b0f134f`、`f9b97a53-3853-52dc-819c-9e669fdfeba1`、`617a0fd1-b2a3-50e9-86a0-edaacc3d31bd`、`0f086c35-1137-5163-81fc-8b175395b42e`、`ad1081e6-5140-5fe4-a0f6-0ec0cb58afb4`、`ccc3383f-6d1f-578e-a87f-36fd93a35d58`、`9dd2dc7d-666f-5ce3-9d64-d17cc57f2987`、`09872486-2607-5d05-bee4-a6a1387b1bd4`、`4ad7059a-2d64-5d5e-ae0d-5a3deb9a9366`、`2a8906da-4f07-5bb5-9bfd-1baab7146b33`、`13ecfc45-fbf6-583f-90ca-883d5aac1b6a`、`e1be9e30-31be-593a-afc4-056a711625f5`、`e0f47cec-5443-514b-b6d2-0a1c645071c7`、`18bee733-8431-54f9-945c-51158293a018`、`cbcdf14c-ef83-5766-b74b-17b8184f723b`、`1c8d85e7-863f-56d5-8664-2a2fb75270fa`、`7d53ab4b-35f8-56d7-8fa1-b60303bfa780`、`8212bc19-143e-5d78-97e5-70c3c74a43fc`、`3863d98c-c549-54eb-8edf-8ee607015e18`、`1f1f3c09-9828-51b3-8841-6e66f0645a09`、`cd0d485e-e55d-55c4-8ecf-e8b99a10b355`、`b85f0b10-310b-5770-8568-fecb79b3d4cf`、`4e0c6800-19d0-55de-927b-c8fc865c5cc7`、`1c2fc42d-da82-50b7-8c80-532008f78dd8`、`d7bd5618-3bfb-5721-9b2b-076b5657d172`、`b4fe9a9a-edd1-545d-a99d-c1fd30535d81`、`e2c30e82-dfea-5ac9-963f-581b093bfe12`、`be8a0129-a813-545d-861a-caf9e65171e7`、`3635b11e-1ada-5ae2-a6aa-4e5831a67d24`、`329cbbe6-0081-56f5-a671-d55992044274`、`5e52ede8-6af3-5c97-87a7-893aee7ae924`、`5e16fc9a-dd9d-54a4-8c5e-268f5b27dd36`、`078e3fe1-9b1c-50ef-93db-ec15faef03fa`、`e7f4fc3e-9052-5fbc-9dd9-133b398c89a2`、`df3c0868-77bf-52fe-8c70-0cdd81388ab5`、`dfd204ee-c750-5286-9b61-1e6101260e74`、`e87141a6-52f0-5170-9e2b-7f3fc394956a`、`bf63081c-17a8-5756-b418-7ce8ad07cdf8`、`b6e85bac-d38c-53fc-a8fc-3a5011f2a668`、`4ed365e2-198e-5816-bc16-05d2db22a5cf`、`3c6c1309-9a04-5665-9af3-dc4794c82b86`、`94db16e3-38c8-526a-b333-a0f7380b4ba3`、`7ff6f598-b4e6-5482-8adf-df771cf6dac2`、`1c7fc7a7-1fd5-575a-bf28-336acbf4bc5b`、`6d421b15-70d6-5fd4-96cf-acd279f90d2d`、`5e80679b-4a7e-51e7-9a7a-62e9d96aac36`、`c5a00df4-3e69-55d2-b0a4-159ed4260436`、`0a7b25d8-336e-5423-ab32-e780b72a9568`、`405e66f2-141c-51b4-a6d3-5a97a5b64a72` 与 `1529f668-8abe-5aab-816d-76a2dfe08990`：

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

BCN 仓库的改动在 `0.3-server` 上进行，该分支从 `0.3` 的 cd8470d 开始；第 11 至 13 阶段完成整体验收后合入 `0.3`。
新仓库在各自的仓库中按 Task review。BCN 分支记录它们的固定版本，并验证跨仓库组合。

## 包与仓库

| 发行包 | 仓库 | 入口 | 职责 |
| --- | --- | --- | --- |
| bazaar-contrib-http | open-bazaar/contrib-http | http | HTTP 契约（路由、中间件与 ASGI 应用的登记、监听地址、请求生命周期）及 Starlette／uvicorn 实现 |
| bazaar-contrib-server-database | open-bazaar/contrib-server-database | server-database | 以私有绑定装配 server 的数据库（storage、所选后端、database 与 schema），以 SERVER_DB 与 SERVER_SCHEMA 提供；打开数据库前从 ~/.bcs 复制数据库与 session.key |
| bazaar-contrib-server-migrations | open-bazaar/contrib-server-migrations | server-migrations | 以原 v01–v07 为历史接续 0.2 的 BCS 表结构，以 upgrade_only 登记，完成后提供 SERVER_MIGRATIONS |
| bazaar-contrib-server | open-bazaar/contrib-server | server、server-theme-night、server-theme-day | server 是 server 本体，提供外壳、嵌入工具、侧边栏、设置页、统一总线、权限点登记、主题接口、语言、refs 与 API 挂载；server-theme-night 与 server-theme-day 是默认带上的两个主题插件 |
| bazaar-contrib-server-overview | open-bazaar/contrib-server-overview | server-overview | 概览页与问候、概览内容的登记与每个账户的排序 |
| bazaar-contrib-server-access | open-bazaar/contrib-server-access | server-access | 会话、登录页、账户、角色、分享与权限检查，并定义第三方登录方式的接口 |
| bazaar-contrib-server-github | open-bazaar/contrib-server-github | server-github | 以 GitHub 账户登录，实现第三方登录方式的接口 |
| bazaar-contrib-server-computer | open-bazaar/contrib-server-computer | server-computer、server-relay-memory | server-computer 持有电脑的登记、身份与在线状态，注册节点协议的三个接口，定义中转契约，提供对电脑的调用以及电脑的页面；server-relay-memory 是中转的单进程内存实现 |
| bazaar-contrib-server-management | open-bazaar/contrib-server-management | 无 contrib 入口，只有 defaults 入口 server | server 的默认配置；生成配置时读取 ~/.bcs 中 0.2 的设置 |
| bazaar-contrib-server-agent | open-bazaar/contrib-server-agent | server-agent | agent 模块：经 call 管理各电脑上的 agent，提供列表、详情、新建、配置与删除的页面，以及侧边栏的「正在工作」（第 13 阶段） |
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
- server 的条目在配置顶层启用，与节点的条目共享 TIMER 等服务。第 11 阶段的默认配置启用 http、server-database、server-migrations、server、server-theme-night、server-theme-day、server-overview 与 server-access；第 12 阶段加入 server-computer 与 server-relay-memory；第 13 阶段加入 server-agent。需要 GitHub 登录时，经 contrib manager 加入 bazaar-contrib-server-github 并启用 server-github。
- server-database 按自己条目里的存储选择，在私有绑定中启用 storage、所选后端、database 与 schema，再以 SERVER_DB 与 SERVER_SCHEMA 把这份数据库提供到顶层；节点照旧使用 DB 与 SCHEMA。
  - 存储选择与参数沿用原 BCS 的 storage 及同名参数表，缺省为通用 sqlite，以现有的 name 参数打开 ~/.bcn/bcs.sqlite3，与节点的 bcn.sqlite3 分开。两边旧的 schema_migrations 账本列与版本各不相同，不合并。
  - SERVER_DB 与 SERVER_SCHEMA 由 bazaar-contrib-server-database 导出；用到 server 数据库的模块 accept 它们并依赖该包，例如 server-migrations accept SERVER_SCHEMA。server-management 不依赖任何 server 的包，发版顺序为 server-database、server-migrations、server-management。
- 设置写在各自所属条目的 config 里，缺省值沿用原 BCS 模型：
  - listen 在 http 条目；
  - 存储选择与参数在 server-database 条目；
  - session_minutes 在 server-access 条目，retention_days 在活动模块的条目，随各自的 Task 交付。
- 从 0.2 BCS 升级：
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
- 外壳、登录页、favicon 与 PWA 图标使用同一个品牌标识：一组条纹帐篷。
- 页面上交给外部系统填写的地址，例如第三方登录的回调地址与电脑接入用的 server 地址，由浏览器按当前页面的 origin 生成。

### 窄屏与 PWA

- 宽度小于 960px 时外壳与页面改为单栏，与 0.2 的手机版相同：
  - 侧边栏的登记项改为底部导航，图标加文字；多于五项时第五项为「更多」，展开其余各项；
  - 页面的顶栏显示标题，详情页带返回上一层的入口；设置页先显示目录，目录末尾是退出；
  - 输入框文字不小于 16px，常用触摸目标约 44px；外壳使用动态视口高度，并为安全区留出边距。
- server 本体提供 PWA 的 manifest、192 与 512 像素的图标及 apple-touch-icon，外壳与登录页引用它们并给出 theme-color。

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
- 节点 report 上来的事件以 REPORTED 在 Hub 内 announce，带电脑 id 与审计事件，供后续的 agent、会话与活动模块以 listen 订阅；本阶段不把事件写入 events 表。
- 节点协议的三个接口由 server-computer 经 http 直接注册，以 token 鉴权，不经过登录检查；token 无效返回 401，请求体超过 1 MiB 返回 413，内容不合法返回 422。

#### 机器信息

- 节点的 node-management 在 NODE 上提供 `info()`，加入它的 RemoteEntry。info 返回运行版本、系统（platform.system）、系统名称与版本号、release（Windows 取 platform.version，其他取 platform.release）、架构、节点进程的启动时间、CPU 核数与一秒内的占用、内存总量与已用（总量减可用），以及各磁盘的挂载点、总量与已用。系统名称在 Linux 取 os-release 的 NAME 与 VERSION_ID，读不到时为 platform.system；macOS 为 macOS 加 mac_ver；Windows 为 Windows 加 platform.release。
- 磁盘逐个读取 psutil 列出的分区，跳过 squashfs、iso9660、udf 与 cdfs 以及读取失败的分区；总量与可用量都相同的分区视为同一块磁盘，只列第一个挂载点，例如 btrfs 子卷与 APFS 容器。node-management 因此依赖 psutil。
- server-computer 经 call 取得 info，缓存在处理请求的 server 进程中：电脑出现时，若此前没有出现过或已离线超过 60 秒，下发一次；页面渲染或经统一总线刷新时读到在线的电脑，缓存满 60 秒就再下发一次，同一台电脑同时只有一次在途。没有人看页面时不再下发。节点没有回应或回应不合法时，保留上次的结果。

#### 页面

页面以 design 中电脑的效果图为准：

- 侧边栏在工作台分组登记「电脑」，徽标为账户可见的电脑数量。
- 列表的列为电脑（系统图标、名称、系统名称与架构）、状态、内存、硬盘、版本与最后在线。内存与硬盘为 btop 式的 16 格进度条，硬盘取占用最高的一块；格子按所在位置着色，前 60% 为 jade，到 85% 为 saffron，其余为 crimson，即灯笼的三种状态色。版本只写版本号；离线的电脑以横线代替进度条。列表按 id 分页，每页 50 台，滚动到底时载入下一页；列表与徽标经统一总线随电脑的登记、改名、删除、上下线与机器信息更新。
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
- 收到 REPORTED 的 `harness.started`、`harness.completed`、`harness.failed`、`harness.cancelled`、`channel.connection.connected` 或 `channel.connection.failed` 时，立即重新下发该电脑的 `agent-activity.agents`，侧边栏与页面经统一总线随之刷新。
- 每台电脑最后一次取得的 agent 列表存入 server-agent 自己的表 `agents`：电脑 id、agent id、名称、`list()` 给出的配置与取得时间，以电脑 id 与 agent id 为键，表结构经 SERVER_SCHEMA 登记为第 1 版。电脑离线时页面照这份列表列出它的 agent，状态为离线。
- 新取得的列表中不再有的 agent，删除对应的行；一个 agent id 在所有电脑上都不再出现时，经 ACCESS 的 `forget` 删除它的归属与分享。
- server-computer 删除电脑后 announce `COMPUTER_REMOVED`，带电脑 id；server-agent 收到后删除该电脑的行，并按上一条删除这些 agent 的归属与分享。
- COMPUTERS 增加 `computers(ids)`：给出电脑的名称、是否在线与最后出现时间；ids 为 None 时给出全部电脑。

#### 权限点与分享

- 权限点沿用 0.2：agents.view、agents.create、agents.update 与 agents.delete，经 server 本体的 `permission` 登记；agents.approve 随第 14 阶段的会话模块登记。
- agent 经 ACCESS 登记为可分享的类型 `agent`，以 agent id 为目标，与 0.2 相同，导入的 0.2 分享照常生效；分享点为 agents.view、agents.update 与 agents.delete，分享所需的权限点为 agents.view，即拥有者即可分享，与 0.2 相同。
- ACCESS 增加 `points(kind, points)`：在已登记的分享类型上追加分享点，退出时撤销。server-agent 以它在 `computer` 上追加 agents.create，与 0.2 的电脑分享点相同；第 14 阶段的会话模块以它在 `agent` 上追加 agents.approve。
- 从页面新建的 agent 在保存成功后，经 ACCESS 的 `own` 把新建者记为拥有者，与写入 `agents` 表在同一事务中。节点上以其他方式创建的 agent 没有拥有者，admin 可见，由 admin 分享给角色。
- 列表只列出账户可见（agents.view）的 agent；新建向导的电脑只列出账户可以新建 agent（agents.create）的在线电脑；行末菜单与详情中的操作按 agents.update、agents.delete 与能否分享给出。

#### 页面

- 侧边栏在工作台分组登记「智能体」，顺序在总览与电脑之间，徽标为账户可见的 agent 数量；并登记侧边栏组件「正在工作」：宽屏列出账户可见、正在工作的 agent 与已工作的时长，窄屏在底部导航上方轮播，每次显示一个 agent 的灯笼、名字、会话与时长。
- 列表：标题旁为 agent 总数与正在工作的数量；上方为名称搜索、按状态（全部、正在工作、出错、离线）与按电脑的筛选。列为智能体（头像、名称与模式）、状态、渠道、运行时、电脑、今日用量与操作。
  - 状态为带底色的灯笼块：正在工作只写时长，空闲写「空闲」，出错在块内写原因，离线只有灯笼。
  - 渠道与运行时每个一行，为插件的 logo 加机器人名或模型；第一个之后的运行时标「备用」。
  - 按电脑 id 与 agent id 的顺序分页，每页 50 个，与电脑列表相同；带状态筛选时依次往后读，直到凑满一页或读完。
- 详情为整页：头部为头像、名称、状态与事实标签（各渠道的机器人名，按顺序的运行时模型与强度，所在电脑，模式），标签按整块换行；tab 为动态、工作区、技能、配置与共享。
  - 动态：智能体的状态、已运行时长、启动时间与最后一次活动；各渠道的连接状态（重新连接中时带第几次）、连接于、最后收到与最近错误；各运行时的顺序、是否在用、是否可用、版本（取自 inspect）、档位与凭据的状态；今日用量的总量，以及输入、缓存命中、输出与费用。状态卡片可以手动刷新。离线时显示最后一次取得的样子。
  - 工作区：标题注明所在电脑与绝对路径；目录逐层展开，点开时才读取下一层，每次 50 项，末尾的「显示更多」写明已列出与剩余的项数，点了读取下一页；可以重新读取。点开文件在右侧抽屉中查看：Markdown 文件经 server 本体的 markdown 过滤器渲染；其他文本按文件名由 Pygments 识别语言（`get_lexer_for_filename`，例如 toml、py、json 与 yaml），以代码块显示并带行号，着色与 server 本体 Markdown 中的代码块相同，使用 `tok-` 前缀的类名，颜色随主题，识别不了的按纯文本显示；无法显示的文件只显示大小，并说明暂不支持查看该文件。抽屉标题旁为识别出的语言、大小与修改时间，下方为绝对路径，可以复制。查看文件与列目录一样需要 agents.view。
  - 技能：在用运行时的 inspect 返回的 skills，按来源（工作区、用户、系统）分组，并注明运行时的版本与读取时间；另列 inspect 返回的 MCP 服务与工具，服务的连接与登录状态是 harness 契约中的公共枚举，运行时不知道的留空，连接失败时带原因。
  - 配置：基本信息（名称、空闲回收、模式与会话沙箱）直接在页面上编辑；渠道与运行时为可增删的列表，运行时可拖动排序；点「编辑」在右侧抽屉中按实体配置模型的 schema 编辑一个条目，凭据只显示已保存，留空即保持不变。页脚的保存经 `agent-configuration.edit` 提交整份配置，带 `list()` 的修订号与各条目的连接名。侧栏说明保存之后的情形，并有删除。
  - 共享：抽屉中以 iframe 载入 server-access 的分享页。
- 新建向导分四步：基本信息（名称、电脑、模式、会话沙箱、空闲回收）；渠道（所选电脑上已启用的渠道插件，可选多个）；运行时（已启用的运行时插件，可选多个并排定顺序，模型与档位取自 inspect）；确认（按 schema 列出有值的字段）。创建经 `agent-configuration.add`。
- 删除前确认，说明 agent 会在那台电脑上停下、配置移除，工作区的文件与会话记录留在电脑上，分享一并删除。
- 窄屏的列表为每个 agent 一行卡片；详情、配置、新建与查看文件为整页；分享、删除与编辑条目从底部弹出，与 design 中的手机效果图相同。

## Tasks

每个 Task 在所涉及的每个仓库各一个提交，完成后 review。

- Task 43：本计划，并同步计划入口、路线图与 framework。
- Task 44：bazaar-contrib-http。
  - 验证：真实监听，路由、中间件与 ASGI 应用的登记，请求生命周期，退出时关闭监听并结束在途请求，Windows／Linux／macOS CI。
- Task 45：server 的基础。包括 contrib-server-migrations（由 contrib-server 改名，改为 upgrade_only 并 accept SERVER_SCHEMA）的 server-migrations；新仓库 contrib-server-database 的 server-database（私有绑定的数据库，SERVER_DB 与 SERVER_SCHEMA，打开数据库前从 ~/.bcs 复制数据库与 session.key）；新仓库 contrib-server-management 的默认配置与 0.2 设置的读取；以及 config-versions 的 V4→V5 转换中 server 的包、条目与 0.2 设置。server-management 的安装测试从 PyPI 安装已发布的 bazaar-compute 与 `bazaar-compute[node]`，再加入本地构建的包。
  - 验证：空库时 server-migrations 不建表；0.2 的 v3 与 v7 数据库接续到 v07 并保留原账本，再次启动不重复执行。
  - 验证：用 v0.2.3 BCS 生成的真实 ~/.bcs 启动，首次启动后 server 数据库的表结构与行数、session.key、http 条目的 listen 与 server-database 条目的存储选择与原值一致，~/.bcs 不变。
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
  - 验证：以真实浏览器在 1440px 与 390px 宽度下完成新建（两个渠道、两个运行时）、配置修改（凭据留空不变，运行时调换顺序）、工作区展开与显示更多、查看 Markdown 文件与按类型着色的代码文件、技能查看、分享与删除，与 design 中智能体的效果图一致。
  - 验证：电脑离线后列表仍列出它的 agent 并标为离线；删除电脑后它的 agent 与分享随之删除；REPORTED 的回合事件到达时，侧边栏的「正在工作」随之出现与消失。
  - 验证：codex 与 claudecode 的 e2e 中，inspect 给出工作区的 MCP 服务、按枚举的状态与它的工具。
- Task 56：config-versions 的 V4→V5 转换在 ~/.bcs 存在时加入 server-agent，contrib-index 登记 bazaar-contrib-server-agent，然后发布 bazaar-compute 的 rc，内置索引随之带上它。
  - 验证：已有 0.2 节点配置与 ~/.bcs 时，V4→V5 转换声明 bazaar-contrib-server-agent 并启用 server-agent；contrib-index 的检查通过。
- Task 57：两端验收：contrib-server-management 的两端用例加入经页面新建、修改与删除 agent。
  - 验证：分进程的 server 与节点上，新建的 agent 写入节点配置并运行；修改后没有提交的凭据沿用；删除后配置移除，工作区保留。
  - 三平台 CI。
