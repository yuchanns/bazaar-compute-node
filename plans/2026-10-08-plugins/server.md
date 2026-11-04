# server 框架、登录与两端通信（第 11、12 阶段，2026-11-04）

依据消息 `063a69a7-4466-5989-987d-72340ff87d15`、`4a3df703-d8ba-52fd-8386-a80636c7ebd3`、`4a1410e9-fdd9-5e6d-9258-2b6524c85030`、`54a06153-2c7a-50d8-be38-d83215bb119f`、`a71294e0-d7b2-55ad-b070-0d39c6615bfc`、`361bd816-6861-5387-9de3-db06a0221bff`、`4a61b278-dbba-5110-b22a-1a48be84ee85`、`35a6f778-8313-5345-b815-dd56aadb82f4`、`5b10044f-5d13-593f-bdc1-f702f38d28c1`、`61a8fb0b-6faf-5b41-9845-5ce2b6255f50`、`0152f366-79a3-58a2-a4b8-097b74292762`、`048927d2-bcdb-5149-b3cd-74ba46652bd6`、`46bf2276-bd23-5186-a0e7-2e7fe36c32f6`、`6c355ba6-0bad-5525-92d7-0763f87ba60b`、`1f12a86a-5b7a-5cd1-8ab0-ab30cd64cbd4`、`75056a17-d242-53e0-b3d3-5541c3f8c2d0`、`c51fe75f-080c-5bbb-a5d4-73ebf2b05257`、`680370b8-e577-5ff8-86f0-8e7711ff16a2`、`34dd6558-b928-5db1-8744-dc150f1bcb1c`、`9fbda575-6578-574f-a885-eca2e6781f14`、`9b4c789d-27b9-52c0-a480-da6f675b957f`、`7392d984-82ef-5b71-a2dd-0f82fc8404bd`、`ae4a2fb3-637c-51f1-8661-9b00d43896c0`、`74cca34d-90c1-550a-ac3b-a75da7596efa`、`dec88203-3335-53ad-8f32-f599838099b5`、`732ad76b-eb60-5441-8c98-3c101345f20d`、`2377ea79-daee-5aff-8af4-efd3335a0a63`、`1ca34615-3618-57f4-a74c-a5de417fce7d`、`a00b3959-3fb4-5bd1-b080-ad718285cf71`、`96787fb9-cffc-545b-9637-cb35c238d7fc`、`72759245-29a2-5f1c-8420-f586190ac434`、`227a718a-65ce-55b2-bd6b-718bc585370f`、`9c4b2829-5eab-51e6-be65-ffacd7230ac2`、`8fe23c62-55a0-5ad2-9c8f-486f3459136d`、`60b4a535-b2cc-5ee2-954d-42cdd48ada8b`、`0848f322-562e-52d0-be22-8b2a5dbcc317`、`6d7ef808-9bbb-54d2-ba03-ab2ecfba8b79`、`11e6a63e-2572-5129-8f62-42c46e3844ca`、`4a309095-83e0-505e-be68-c6a0f6fe1c2b`、`c78439a5-8fcb-5c72-99b5-3fb718c03e55`、`ee83ba8e-da47-5385-b23a-bc34e7a6f58f`、`a088f3ea-c6c2-5f14-8831-3f1844592172`、`62072b45-7a4e-5a3a-b33b-ada5f46fa02a`、`2128b685-1165-5ffc-ae59-1bf377c0d729`、`a9707a85-61c1-5b2f-8212-6501f4a27597`、`89409f44-6c7f-5559-98dd-3c92e023980c`、`9a8a560e-ea1a-5b92-af21-27c315705f4f`、`04ebe6bb-3fde-5699-9478-cd4f92b06d0f`、`069a6203-552c-58c5-b57c-53b3dd5f055f`、`fe92f9bd-d2a3-5ee9-98ac-d05dfb4de85b`、`c829259a-d24e-5658-8862-8340fa564e6a`、`d929e3b7-1560-5a59-9d89-bb9bb0638eab`、`df181db4-2fb1-5bf6-9f35-13804366c141`、`fe7e2369-5a98-5338-b704-cc100dee30c9`、`145bdfe5-a2ba-5d54-98ee-b5a4c7fcf064`、`ea154f6b-e6ab-5e37-b761-21b8d3ba417d`、`0f163715-de78-5dda-ae47-86f1122c45d8`、`cca4a947-a5ad-51d8-8a61-8e890c84e2f8`、`c343e7fe-6a21-58a4-ba78-e4e9b031145d`、`2777ee0e-7dc2-5078-b8c8-26b36785fa88`、`c4993a58-dad1-5fb5-91a0-49252e01399c`、`2e906727-a570-51b8-b30d-dc5badd3b7b0`、`713d4cb5-ce3d-57a5-b325-ed96cc9d9b48`、`3cae9028-3f2d-552b-b954-adde6281dc51`、`e3d0a24f-d713-56f8-ad56-54af4e76f72f`、`eb67ecd4-cfc7-539b-8e4b-53608b0f134f`、`f9b97a53-3853-52dc-819c-9e669fdfeba1`、`617a0fd1-b2a3-50e9-86a0-edaacc3d31bd`、`0f086c35-1137-5163-81fc-8b175395b42e`、`ad1081e6-5140-5fe4-a0f6-0ec0cb58afb4`、`ccc3383f-6d1f-578e-a87f-36fd93a35d58`、`9dd2dc7d-666f-5ce3-9d64-d17cc57f2987`、`09872486-2607-5d05-bee4-a6a1387b1bd4`、`4ad7059a-2d64-5d5e-ae0d-5a3deb9a9366`、`2a8906da-4f07-5bb5-9bfd-1baab7146b33`、`13ecfc45-fbf6-583f-90ca-883d5aac1b6a`、`e1be9e30-31be-593a-afc4-056a711625f5`、`e0f47cec-5443-514b-b6d2-0a1c645071c7`、`18bee733-8431-54f9-945c-51158293a018`、`cbcdf14c-ef83-5766-b74b-17b8184f723b`、`1c8d85e7-863f-56d5-8664-2a2fb75270fa` 与 `7d53ab4b-35f8-56d7-8fa1-b60303bfa780`：

- 节点协议不兼容 0.2；server 的功能与数据结构保持一致，实现可以重新设计。
- server 按功能模块拆成 contrib：每个模块提供自己的 API 与页面，accept 所依赖的模块；server 本体提供页面框架与基础 API 服务。
- server 的 contrib 与节点的 contrib 是同一种插件，机制相同：都在配置顶层启用，由 contrib manager 安装、启用与停用，需要哪些服务就 accept 哪些；任何插件都可以 accept 它需要的服务。
- 私有绑定只用于同一接口的第二个实例：server 的数据库是 STORAGE、DB 与 SCHEMA 的第二个实例，以私有绑定装配后以 SERVER_DB 与 SERVER_SCHEMA 提供，与 harness 的多个实现同理。node 与 server 的数据库各自独立，可以选用不同的后端。
- server 的设置写在各自所属条目的 config 里。
- server 的结构与节点对应，遇到问题先参考节点的做法：节点由 storage 的 database 条目提供 DB、legacy-node-migrations 接续 0.2 的历史；server 由 server-database 提供 SERVER_DB 与 SERVER_SCHEMA、server-migrations 接续 0.2 的历史。
- 侧边栏、概览与权限点的登记入口由 server 本体提供。模块登记带名称与图标的页面后，页面出现在侧边栏；概览只展示各模块登记的内容，顺序按账户调整。
- 模块页面运行在同源 iframe 中，前端框架由模块选择，主题、语言与导航经框架的嵌入工具取得。iframe 用于前端框架的独立；服务端以只安装可信的 contrib 为边界。
- 主题经 THEME 接口由主题插件提供，在设置页的主题管理中切换；语言由框架统一给出，是否提供多语言由模块决定。
- 电脑的登记、身份、在线状态与对电脑的调用归 server-computer 模块，节点协议的三个接口由它注册。
- 0.2 的表结构由 server-migrations 按原历史接续，之后各模块持有自己的表，并按需新增。
- 节点经三个相互独立的 HTTP 接口接收调用、回传结果、上报事件，结果不混入事件上报；server 对节点的调用经节点 sidecar 分发。
- 副本之间的协作由可替换的中转 contrib 实现。
- 未确认的上报事件只留在有界的内存队列中，server 不可达时按退避重试。
- 页面写入的凭据进入 0.3 配置；server 的数据全部位于 ~/.bcn，首次启动从 ~/.bcs 导入。
- 缺少的节点能力由新的 contrib 补齐。
- 所有计时经 TIMER。
- server 的功能都由 contrib 实现，server-management 也在自己的仓库中维护；BCN 仓库只有 config-versions 的 V4→V5 转换，其他逻辑不变。
- 宿主读取各已安装包在自身 pyproject.toml 中声明的 `bazaar_compute_node.defaults` 入口并合并默认配置，主仓库只按包名引用 server-management；其他 management 包同样在各自的仓库发行，与 bazaar-compute 一起安装即可使用，不需要改动主仓库。

## 分支

BCN 仓库的改动在 `0.3-server` 上进行，该分支从 `0.3` 的 cd8470d 开始；第 11、12 阶段完成整体验收后合入 `0.3`。
新仓库在各自的仓库中按 Task review。BCN 分支记录它们的固定版本，并验证跨仓库组合。

## 包与仓库

| 发行包 | 仓库 | 入口 | 职责 |
| --- | --- | --- | --- |
| bazaar-contrib-http | open-bazaar/contrib-http | http | HTTP 契约（路由、中间件与 ASGI 应用的登记、监听地址、请求生命周期）及 Starlette／uvicorn 实现 |
| bazaar-contrib-server-database | open-bazaar/contrib-server-database | server-database | 以私有绑定装配 server 的数据库（storage、所选后端、database 与 schema），以 SERVER_DB 与 SERVER_SCHEMA 提供；打开数据库前从 ~/.bcs 复制数据库与 session.key |
| bazaar-contrib-server-migrations | open-bazaar/contrib-server-migrations | server-migrations | 以原 v01–v07 为历史接续 0.2 的 BCS 表结构，以 upgrade_only 登记，完成后提供 SERVER_MIGRATIONS |
| bazaar-contrib-server | open-bazaar/contrib-server | server、server-theme | server 是 server 本体，提供外壳、嵌入工具、侧边栏、概览、权限点登记、主题接口、语言、refs 与 API 挂载；server-theme 是自带的主题插件 |
| bazaar-contrib-server-access | open-bazaar/contrib-server-access | server-access、server-oidc | server-access 提供会话、登录页、账户、角色、分享与权限检查，并定义 OIDC 接口；server-oidc 是标准 OIDC 的实现 |
| bazaar-contrib-server-computer | open-bazaar/contrib-server-computer | server-computer、server-relay-memory | server-computer 持有电脑的登记、身份与在线状态，注册节点协议的三个接口，定义中转契约，提供对电脑的调用以及电脑的 API 与页面；server-relay-memory 是中转的单进程内存实现 |
| bazaar-contrib-server-management | open-bazaar/contrib-server-management | 无 contrib 入口，只有 defaults 入口 server | server 的默认配置；生成配置时读取 ~/.bcs 中 0.2 的设置 |
| bazaar-contrib-starlink | open-bazaar/contrib-starlink | starlink | 节点侧长轮询、经 sidecar 分发调用、回传结果、上报审计事件、退避重试 |

- contrib-server-migrations 由原 contrib-server 改名而来；contrib-server、contrib-server-access、contrib-server-computer、contrib-server-database、contrib-server-management 与 contrib-starlink 是新仓库。这些仓库都按现有 contrib 仓库建立 CI 与发版流程，八个新包各自登记到 contrib-index。
- server 的数据库由 server-database 条目选择后端，缺省为通用 sqlite 的私有实例。通用 contrib 只负责读写，表结构与查询归各业务 contrib。
- server-management 像 node-management 一样，在 `bazaar_compute_node.defaults` 入口组中登记 server 的默认配置，并与其他插件一样安装：
  - 与 bazaar-compute 一起新装（`uv tool install bazaar-compute --with bazaar-contrib-server-management`）的 host 首次运行即成为 server；
  - 同时装有 node 的包时，host 同时运行两者；
  - 已有的宿主经 contrib manager 加入 server 的包与条目。

  本条取代 framework.md「按 extra 选择初始依赖」中 `[server]` 随 server 交付的安排；宿主升级时，manager 把配置中的 contrib 与新宿主一起解析安装。
- 节点默认组合不包含 server 的包；节点填写 server 地址与 token 后才启用 starlink。

## server 的条目与数据

- server 的数据都在 ~/.bcn 下，登录签名密钥为 ~/.bcn/session.key。
- server 的条目在配置顶层启用，与节点的条目共享 TIMER 等服务。第 11 阶段的默认配置启用 http、server-database、server-migrations、server、server-theme、server-access 与 server-oidc；第 12 阶段加入 server-computer 与 server-relay-memory。
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
  - server 本体：refs，以及新增的概览顺序表；
  - server-access：accounts、roles、account_roles、auth_settings、oidc_providers、oidc_identities，以及记录归属与分享的 relations；0.2 的 oidc_transactions 已在 v07 删除，授权事务只保存在内存中；
  - server-computer：computers；
  - events：由第 14 阶段的活动模块接管，此前留在 bazaar-contrib-server-migrations。
- 模块新增的表登记在各自的版本链上，例如概览顺序表在 server 本体的版本链上。

## 第 11 阶段：server 框架与登录

### 外壳与嵌入

- 地址分为四类：
  - 外壳在 `/<模块>/<路径>`，由侧边栏、顶栏与一个 iframe 组成，iframe 加载 `/ui/<模块>/<路径>`；
  - 模块界面挂在 `/ui/<模块>` 下，是模块自己的 ASGI 应用，经 http 的 mount 挂载，前端框架由模块选择；
  - 模块的 API 挂在 `/api/<模块>` 下；
  - 静态资源在 `/static/<模块>/` 下。
- `<模块>` 是模块登记时给出的路径名，例如 computers、agents，与入口名无关。`ui`、`api`、`static`、`login` 与 `starlink` 是保留名，模块的路径名不能使用。
- 外壳与模块界面同源，登录 cookie 对两者同样有效，写操作的同源检查不变。
- 登录检查由 server-access 登记到 server 本体；没有登记时，非公开的页面与 API 一律返回 503。
- 嵌入工具由 server 本体提供：
  - 主题样式表：全部已登记主题的 CSS 变量与基础组件样式，每套主题限定在 `[data-theme="<名称>"]` 下；
  - 嵌入脚本：设置当前主题与语言，把 iframe 内的跳转同步到外壳的地址栏与标题，并把页面高度报告给外壳；
  - Jinja 基础模板：引入上述样式表与脚本，提供 0.2 的共用过滤器与模板全局（identicon、ago、clock、day、time、markdown、asset、ref、t）。自家模块在 iframe 中以它渲染 Jinja 与 htmx 页面，沿用 0.2 的 StrictUndefined 与自动转义。
- 外壳与模块界面的响应带 CSP `frame-ancestors 'self'`。页面响应带 `Cache-Control: private, no-store`；页面请求带 X-Timezone；静态资源版本变化时经 `HX-Trigger: stale` 提示刷新，与 0.2 的 Stale 相同。

### 侧边栏、概览、设置页与权限点

- 侧边栏登记项包括名称的文案键、图标、外壳路径、查看所需的权限点与顺序。当前账户具备该权限点时才显示；退出与设置固定在侧边栏底部，与 0.2 相同。
- 概览登记项包括键、标题的文案键、在 iframe 中加载的内容地址，以及查看所需的权限点。概览页按当前账户保存的顺序展示；没有调整过的，按登记顺序。
  - 概览顺序表记录账户、登记键与位置，登记在 server 本体的版本链上。
- 设置页由 server 本体提供，外观部分由它自己登记；其他模块登记设置页中的部分，登记项包括键、标题的文案键、在 iframe 中加载的内容地址，以及查看所需的权限点或仅 admin 可见。
- 权限点登记项包括权限点与说明。权限点写作「模块.动作」，同时是文案键。server-access 读取登记生成角色页的选项并执行检查；0.2 已有的权限点名字保持不变。

### 主题与语言

- THEME 接口：主题插件向 server 本体登记主题，每套包括名称、显示名的文案、全部 token 的取值，以及只在该主题下生效的附加样式。token 沿用定稿设计 tokens.css 中的名称。
- server-theme 登记夜市（night）与晨市（day）两套主题。
- 账户的 theme 列保存主题名，留空时跟随系统：系统为浅色时用晨市，深色时用夜市。0.2 保存的 light、dark 分别按晨市与夜市处理。
- 设置页的主题管理列出全部已登记的主题，供当前账户选择。
- 当前请求的语言依次取账户的 language 列、Accept-Language、英文，支持 en 与 zh-CN，与 0.2 的 LANGUAGES 相同。
- 模块自带文案目录（locales/en.toml、locales/zh-CN.toml），键位于模块自己的命名空间。只提供一种语言的模块，缺少的语言回退到它提供的那种。

### refs 与 API

- refs 表归 server 本体，提供值与短编号之间的转换；模板全局 ref 给出链接中的短编号，与 0.2 相同。
- 模块经 server 本体在 `/api/<模块>` 下挂载自己的 FastAPI 应用，输入输出由 Pydantic 校验并生成 OpenAPI 文档。server 本体提供取得当前账户与检查权限点的依赖，登录检查与写操作的同源检查与页面一致。

### 登录与权限

server-access 接受 server 本体，登记登录页，在设置页登记安全、角色、账户与登录方式四个部分，登记权限点 settings.roles、settings.accounts 与 settings.oidc，并提供当前账户与权限检查。行为与数据沿用 0.2：

- 会话：
  - cookie `bcs_session` 包含账户、签发时间、session_version 与到期时间，以 session.key 做 HMAC-SHA256 签名，校验使用恒定时间比较。
  - 本地登录的时长取 session_minutes，OIDC 登录取提供方的 session_minutes；会话不续期，也不在服务端保存。
  - 改密码使 session_version 加一，旧 cookie 随之失效；退出删除会话与重新认证的 cookie，返回 204 与 `HX-Redirect: /login`。
  - 全部 cookie 为 HttpOnly、SameSite=Lax，经 https 访问时带 Secure。
- 请求检查：
  - 登录页、OIDC 入口与静态资源公开；其余经 server 本体登记的页面与 API 需要有效会话，账户及其角色在每个请求中读取。
  - 请求体上限 1 MiB，超出返回 413。
  - 写操作要求 Sec-Fetch-Site 为 same-origin 或 none；没有该头时 Origin 的主机须与 Host 相同；两者都没有时放行。
  - 未登录时，htmx 请求得到 401 与 HX-Redirect，其他请求以 303 跳到登录页；带有重新认证提示的请求转到 `/login/oidc/resume`。
- admin：
  - 启动时没有名为 admin 的账户就创建，密码为 24 字节随机数的 base64url，以 WARNING 写入日志一次。
  - 本地的 admin 拥有全部权限点，可见全部电脑与 agent，可以分享任何对象；只有它能在设置页的安全部分修改密码。新密码至少 8 个字符，须提供当前密码。
  - 密码以 scrypt（N=2^15、r=8、p=1、32 字节输出、16 字节盐）保存，格式为 `scrypt$N$r$p$salt$digest`；同时计算的数量不超过 CPU 数与 4 中较小者；不存在的账户也按同样代价比对。
  - 本地账户只有 admin；OIDC 账户在首次登录时创建，设置页的账户部分只分配角色。
- 角色：
  - roles.permissions 是排序后的权限点 JSON 数组，账户的权限是其全部角色的并集，admin 除外。
  - 角色名唯一，长度 1–80。全局默认角色保存在 auth_settings.default_role_id；删除默认角色须指定替代，被提供方用作默认角色的角色不能删除，删除角色时一并删除它的分享。
  - 缺少权限点返回 403，目标不可见返回 404。
- 分享：
  - relations 记录 computer_owner、agent_owner、computer_share_role 与 agent_share_role 四种关系，分享的权限点放在 ext 中。
  - 账户可操作的目标，是它拥有的目标，加上分享给它某个角色、且带有该权限点的目标；前提是账户的角色具备这个权限点。
  - 可分享的权限点：电脑为 computers.view、agents.create、computers.delete；agent 为 agents.view、agents.update、agents.delete、agents.approve。computers.share 只作用于自己拥有的电脑。
  - 编辑分享时只能授予自己在该目标上具备的权限点。
- 设置页：外观（主题与语言，所有账户可用）由 server 本体登记，经 server-access 写入账户；安全部分只对 admin 显示；角色、账户与登录方式部分分别由 settings.roles、settings.accounts 与 settings.oidc 控制。
- session.key 归 server-access，它同时以带标签的 HMAC 为其他模块签名，例如 0.2 中以 `image:` 为标签的图片地址。

#### OIDC

- server-access 定义 OIDC 接口：实现插件向 server-access 登记登录方式，负责发起登录、处理回调并返回身份（issuer、subject、显示名与邮箱）。server-oidc 入口是标准 OIDC 的实现，GitHub、飞书等其他登录方式各自实现同一接口。
- server-oidc 沿用 0.2 的提供方配置：名称、说明、图标、issuer、client_id、client_secret、redirect_uri、默认角色与 session_minutes。保存提供方需要已有全局默认角色；提供方不能删除。
- 发现地址为 issuer 去掉末尾斜杠后加 `/.well-known/openid-configuration`，元数据中的 issuer 必须与配置完全一致。scope 为 `openid profile email`，使用 PKCE S256，请求时限 15 秒，经 TIMER。
- 授权事务保存在内存中：state、nonce、PKCE verifier 与浏览器令牌各为 32 字节随机数，600 秒过期，最多保留 1024 个，只能使用一次。浏览器令牌放在 path 为 `/login/oidc` 的 cookie `bcs_oidc` 中，回调须带回相同的令牌。
- ID token 的 iss、aud（client_id）与 nonce 必须匹配，取用 iss、sub、name 与 email，不调用 userinfo。
- 首次登录创建账户（auth_type 为 oidc）、身份与一个角色，角色取提供方的默认角色，没有则取全局默认角色；之后的登录只更新显示名与邮箱。
- 重新认证：OIDC 登录成功后设置 30 天的 `bcs_reauth` 提示 cookie。会话失效时，带着它的请求以 `prompt=none` 重新认证，回调须返回相同的 iss 与 sub；提示只用于发起登录，不授予访问。
- 登录页按 auth_settings.login_order 排列登录方式，第一项预选；新提供方追加在末尾。
- return_to 只接受站内绝对路径，长度不超过 8192，否则回到默认页面。
- OIDC 回调以 `@redact("code", "state")` 标记，access log 中隐藏这两个值；OIDC 的响应带 `Cache-Control: no-store` 与 `Referrer-Policy: no-referrer`。

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

- server-computer 持有 computers 表的身份与登记。登记电脑返回一次性 token，节点以它鉴权；登记由 server-computer 的页面与 API 发起，并经 server-access 把登记者记为该电脑的 computer_owner，与 0.2 相同。
- server-computer 的页面与 API 包括电脑列表、登记、删除与分享，在侧边栏登记，并登记自己的概览内容；权限点沿用 0.2 的 computers.view、computers.create、computers.delete 与 computers.share。
- 供其他 contrib 调用节点的接口为 `call(电脑, service, method, 参数)`，与 sidecar Client 的 call 同名同参。
- 调用与在线状态经中转契约完成，server-computer 不在进程内假定节点连在哪个副本。
- 节点 report 上来的事件以一个事件 key 在 Hub 内 announce，供后续的 agent、会话与活动模块以 listen 订阅；本阶段不写 events 表。
- 节点协议的三个接口由 server-computer 经 http 直接注册，以 token 鉴权，不经过登录检查。

### 节点侧

- starlink 的配置条目包含 server 地址，以及 token 或 token_env。token 直接写值，token_env 写环境变量名；两者都填时以 token 为准，与渠道凭据的规则一致。
- starlink 提供私有的 sidecar SERVER 实现，并在自己的子组合中启用一个 sidecar 实例。server 发来的调用经该实例分发，只能调用 contrib 以 RemoteEntry 声明的成员，与 bcc 可调用的范围相同。
- starlink 监听 AUDIT_EVENT：回调只负责入队，网络发送由它自己的后台任务完成。listen、announce 与 report 的最近一次失败反映在 starlink 的 contrib 状态中。
- config-versions 的 V4→V5 转换把 0.2 节点配置中的 audit="server"、control="server" 及 url、token_env 转成启用的 starlink 条目，并加入 starlink 包声明。迁移后的节点使用 token_env，值仍在原 runtime.env 中。

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
- Task 46：新建 contrib-server，交付 server 本体与 server-theme：外壳与嵌入工具、侧边栏、概览、设置页、权限点登记、主题接口、语言、refs 与 API 挂载。
  - 验证：经真实 HTTP 取得公开页面与嵌入工具；主题登记与撤销后主题样式表随之变化；保留名被拒绝；响应带 frame-ancestors 与缓存头；没有登录模块时非公开页面与 API 一律拒绝。
  - 验证：Jinja 基础模板与嵌入脚本在真实浏览器中设置主题与语言，同步地址栏、标题与高度。
- Task 47：server-access 与 server-oidc：会话、登录页、admin、账户、角色、分享、权限检查、OIDC 接口与标准 OIDC 实现，以及个人设置中的语言与主题。
  - 验证：用 v0.2.3 BCS 生成的真实数据导入后，旧账户与密码、有效 cookie、角色、分享与 OIDC 设置照常可用。
  - 验证：登录、退出与重新认证；权限点检查与 admin；侧边栏、概览与设置页按权限点显示，登记撤销后随之消失；按账户的主题、语言与概览顺序；未登录时页面与 API 的跳转和拒绝。
- Task 48：server-computer 与 server-relay-memory：身份与登记、三个接口、中转契约、调用与在线状态，以及电脑的页面、API、权限点与概览内容。
  - 验证：登记电脑后得到一次性 token，错误 token 被拒。
  - 验证：长轮询的保持、返回与退出时释放；调用与结果的关联、在途去重与 60 秒超时。
  - 验证：事件确认与重放跳过；在线时间的记录与查询；电脑页面与 API 按权限点与分享显示。
- Task 49：bazaar-contrib-starlink。
  - 验证：sidecar 分发只到声明成员，结果回传与失败重试，上报的确认与重放。
  - 验证：token 与 token_env 的选择，server 不可达时的退避重试，退出时结束在途调用与后台任务。
- Task 50：V4→V5 转换中的 starlink 迁移，并在 contrib-index 登记八个新包。
- Task 51：两端联合验收。
  - 在独立进程中分别运行 server 与节点，各自使用自己的配置、数据库、端口与运行目录。
  - 经真实 HTTP 验证身份、调用 agent-activity 与 node 的已声明成员、结果、事件确认与重放、server 重启后的恢复，以及两端退出。
  - 三平台 CI。
