# BCN 0.3 模块工程

## 版本目标

BCN 的业务能力、平台 provider、存储查询、控制入口和界面扩展都通过公共模块范式装配。
所有组合使用同一个宿主和 `bcn run`：`bazaar-contrib-server` 提供原 BCS 的 API 能力，`bazaar-contrib-web` 依赖 server 提供页面。
应用入口、核心框架、公共能力与官方 contrib 在 BCN 同一仓库开发；各包分别构建 wheel，以同一版本统一发行，首版为 `0.3.0`。
用户可以发现并选择组合；功能的新增与替换由功能包完成，第三方 contrib 使用自己的仓库与版本。
contrib 表示可独立分发和装配的功能模块，官方默认、官方可选及第三方模块均使用这一称呼。

`0.3` 的 BCN 主配置使用 `version="5"`，按新的模块契约替换内部实现。
配置加载通过五个版本模块的依赖链将 V1／V2／V3／V4 转为 V5，把能力选择及参数映射到顶层 contribs，保留 agent、provider 和凭据引用。
配置读写由 `bazaar-contrib-config` 的 CONFIG_STORE 契约与载体模块提供，默认 `bazaar-contrib-config-filesystem` 读写 config.toml；
自定义配置中心模块提供相同服务，载体入口和连接参数由命令行 flags 自举，完整业务配置由所选载体读取。
bazaar-contrib-config-versions 提供 config-v1 至 config-v5，发行指定目标入口并在读取业务文档前直接装配其依赖；
配置包的公共加载函数调用目标版本服务，经 core 的通用候选校验后组织提交；迁移先形成候选配置与所需包清单，安装并校验完整组合后经载体保留旧内容、条件提交。
filesystem 实现锁、备份和原子替换，远端载体实现修订检查与历史保留；后续启动直接使用 V5。
具体启动和读写规则见 [配置载体与启动自举](framework.md#配置载体与启动自举)，版本服务定义见 [配置版本模块](framework.md#配置版本模块)。
集中式业务 repository、旧业务 API、
具体能力的中心装配分支和兼容适配层在所属功能完成替换后删除。
各功能模块依赖 `bazaar-contrib-storage` 的公共 `DB`，自己持有数据模型、schema、显式查询和业务规则；
数据库 provider 实现公共数据操作契约并翻译后端查询。
共享业务能力通过其拥有者的服务契约调用。core 是框架运行内核，执行自举阶段、入口发现、通用候选校验、装配、作用域与生命周期；
配置包负责文档读写、版本转换与条件提交，app 提供启动输入及发行资源。

框架名为 **Bazaar**，实现位于 `core/src/bazaar_core/bazaar.py`，由 `bazaar_core` 导出公共类型，提供模块宿主、typed 服务和事件、作用域、依赖与生命周期。
Contrib 通过 `offers / accepts` 声明服务，`accepts` 可以依据已校验配置计算依赖，
通过 `lifespan / listen` 登记生命周期与固定事件处理函数。
节点模块统一在 BCN 主配置的顶层 contribs 启用并填写参数，作者通过 scope 的 ScopeKind 单枚举声明运行上下文，默认 ROOT。
宿主按声明和已有实体自动创建实例：ROOT 共享，AGENT 随各 agent，CHANNEL／RUNTIME 由 builder 匹配运行实例。
函数显式接收本次挂载的 Context，通过 `offer / accept / announce` 使用宿主能力。
模块的异步上下文管理器负责创建和清理资源。
服务的接口、数据模型、查询、配置、命令、RPC 和 UI 注册协议由相应功能包拥有。
`bazaar-contrib-server` 与 `bazaar-contrib-web` 是独立安装的可选模块包。启用 server 后，该节点提供 API 与多节点管理；
启用 web 后提供管理页面。一个宿主可以只运行服务，也可以同时运行 agent，各模块管理任务或子进程以保证持续工作互不阻塞；
分开部署时使用独立配置分别执行 bcn run，通过远程连接模块通信。
审计同步、远程调用、控制、数据投影与访问策略由普通模块协作，core 宿主根据配置加载结果统一装配。
根作用域统一为 ScopeKind.ROOT，AGENT、CHANNEL、RUNTIME 对应内部子作用域。
server 的生命周期持有独立 ContribHost 与 ROOT，直接读取 ~/.bcs 的配置，使用自己的配置载体、storage 和数据库；
web 在该服务端 ROOT 挂载页面。节点与服务端的设置和资源分别归各自组合管理。
用户只选择模块与参数，运行实体继续保存在 agent 及其 provider 配置中；模块声明和实例状态分别管理。
存储组合由公共后端注册表、具体后端及 DB 选择入口构成，默认配置明确选中 SQLite，
用户可以启用 PG 等其他后端并更新数据库选择，业务继续通过公共 DB 使用显式数据操作。
语言由 `bazaar-contrib-language` 提供公共服务、后端登记与选择入口，随包附带默认英语实现；
中文由独立可选包 `bazaar-contrib-language-zh-cn` 提供。CLI、agent 和页面消费同一个 LANGUAGE 契约，
业务包持有自己的英语消息资源，语言后端按命名空间提供翻译。旧 node.lang 转为 language 的后端选择；
中文选择对应的额外包与启用配置随升级保留。具体规则见 [语言服务与后端](roadmap.md#语言服务与后端)
和 [V5 配置与版本转换](framework.md#v5-配置与版本转换)。
web 组合提供 Bazaar contrib 市场页面，读取官方与用户追加的模块索引，按条目的包名和 Python 包源交给目标宿主 uv 安装。
包的发行元数据将发行包关联到模块入口，用户选择入口和参数后经该宿主的当前 CONFIG_STORE 保存。
页面分别展示包安装、配置与实际运行状态，以及索引、包源和安装目标。

## 计划组织

| 文件 | 内容 |
| --- | --- |
| [framework.md](framework.md) | 首个特性分支：完整框架、启动接入、首个日志模块、模块安装与发现、串行 Tasks 和验收 |
| [roadmap.md](roadmap.md) | 全部能力的模块归属、替换依赖与顺序、阶段验收、0.3 发行清单及发布顺序 |
| [bazaar.md](bazaar.md) | Bazaar 页面：官方与额外模块索引、包安装到入口配置的流程、管理状态和串行 Tasks |

后续特性分支的详细计划继续放入本目录，按能力命名，并从本入口与路线图关联。
公共版本目标、分支及仓库组织由本文件维护，阶段实现细节由各阶段文件维护。

## 分支与 review

`0.3` 是版本集成分支，从 `main` 的 `a6516f4` 开始。
`feature/20261008-plugin-framework` 是首个特性分支，从同一基线开始，合入目标为 `0.3`。

```text
main
  └─ 0.3
       ├─ feature/20261008-plugin-framework ── review ──┐
       ├─ feature/<下一能力>              ── review ──┤ → 0.3 集成验收
       └─ feature/<后续能力>              ── review ──┘
                                                      ↓
                                      官方包候选组合全部验证
                                                      ↓
                                    0.3 → main → 官方包统一发版
```

每个能力有自己的详细计划、串行 Tasks、可审提交和特性分支，每个 Task 结束后 review。
特性分支合入 `0.3` 后，下一分支基于新的集成点创建，并验证已完成模块组合。
完整版本通过最终验收后，`0.3` 合入 `main`，再发布 BCN `0.3.0`。

## 仓库、包结构与发行

BCN 仓库根管理 uv workspace、共享锁、开发工具与 CI，各发行包拥有自己的 pyproject.toml、src、资源及 wheel。
仓库目录、发行包、entry point 与运行实例分别标识；一个发行包可以提供多个入口，运行实例由宿主自动创建。

```text
bcn/
├── pyproject.toml                         # workspace 与开发配置
├── uv.lock
├── app/
│   ├── pyproject.toml                     # bazaar-compute-node
│   └── src/bazaar_compute_node/           # 命令入口、启动输入、默认资源
├── core/
│   ├── pyproject.toml                     # bazaar-core
│   └── src/bazaar_core/                   # 框架运行内核与公共 API
├── contrib/
│   ├── catalog.json                      # 官方 package/source 索引
│   ├── config/
│   │   ├── pyproject.toml                 # bazaar-contrib-config
│   │   └── src/bazaar_contrib_config/
│   ├── logging/
│   │   ├── pyproject.toml                 # bazaar-contrib-logging
│   │   └── src/bazaar_contrib_logging/
│   └── <功能>/                           # 每个官方功能包使用相同结构
├── scripts/
│   └── release.py                        # 官方包版本与发行产物工具
└── tests/
    └── support/                          # 开发测试辅助包
```

发行名与 import 名采用同一套命名：`bazaar-core` 对应 `bazaar_core`，`bazaar-contrib-<功能>` 对应 `bazaar_contrib_<功能>`，
仅将连字符换为 Python 标识符允许的下划线。用户安装的应用发行名继续为 `bazaar-compute-node`，模块为 `bazaar_compute_node`。

### 应用入口与依赖方向

`app/` 的 `bazaar-compute-node` 提供 bcn 命令、bcc 调用入口、启动参数与进程退出信号，持有默认依赖及默认配置／自举资源。
默认节点组合位于 `bazaar_compute_node/defaults.toml`；五版本入口与目标 config-v5 位于同包的自举资源。
命令入口与启动调用使用普通 Python 函数；默认组合直接由 app 的发行依赖和配置资源表达。
app 直接 import bazaar_core，创建 ContribHost，将启动输入、自举条目与绑定发行资源的配置加载函数交给其统一运行接口，
在宿主就绪后等待进程退出信号。

`core/` 的 `bazaar-core` 是框架运行内核，通过普通 Python 公共 API 使用。
ContribHost 执行发行元数据与 entry point 发现、自举阶段、参数与服务依赖校验、实例装配、作用域、服务与事件，以及完整启动和退出。
运行图由 offers／accepts、scope 与已有实体建立；启动中断和退出统一按依赖回收资源。

contrib 持有功能契约、实现与第三方依赖。公共配置、审计、语言、storage 与 timer 包同样位于 contrib/，
消费者与实现者显式导入所属功能包的公共类型和 key，具体模块实现由 entry point 加载。
配置加载函数使用配置包的公共逻辑读写文档、调用版本服务与条件提交，再把模块条目及运行实体交给 core；
core 的运行接口以普通异步函数调用该加载逻辑，依赖和导入保持框架通用。

```text
bazaar-compute-node (app) → bazaar-core
bazaar-compute-node (app) → 默认 bazaar-contrib-* → bazaar-core
可选／第三方 contrib     → bazaar-core + 所需功能契约包
server／web 组合包       → 所需 contrib 功能包
```

core 的发行依赖与导入保持通用；app 直接声明默认官方包及资源，server／web 各自声明可选组合。
包依赖负责安装代码，运行依赖负责装配服务，两者按各自边界校验。

### 本地 workspace 开发

官方包在同一个源码 checkout 内作为 workspace 成员联调，共用根目录 uv.lock 与 .venv。
首阶段建立 core、app、config、audit、config-filesystem、config-versions、logging 与 manager 成员；
各成员的源码和元数据随同一提交保存。根配置如下：

```toml
[project]
name = "bcn-workspace"
version = "0.0.0"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = [
    "bazaar-test-support",
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.9.0",
    "pyright==1.1.408",
]

[tool.uv]
package = false

[tool.uv.workspace]
members = ["app", "core", "contrib/*"]
exclude = ["contrib/catalog.json"]

[tool.uv.sources]
bazaar-compute-node = { workspace = true }
bazaar-core = { workspace = true }
bazaar-contrib-config = { workspace = true }
bazaar-contrib-audit = { workspace = true }
bazaar-contrib-config-filesystem = { workspace = true }
bazaar-contrib-config-versions = { workspace = true }
bazaar-contrib-logging = { workspace = true }
bazaar-contrib-manager = { workspace = true }
bazaar-test-support = { path = "tests/support", editable = true }
```

根 project 是开发聚合配置；公开发行版本来自 app、core 与各 contrib 成员。
各包的 project.dependencies 保留正式包名、版本要求与 extras，tool.uv.sources 将官方依赖绑定到本地成员。
根 sources 供成员使用；成员的同名 source 优先，阶段检查复核覆盖与实际来源。
uv lock 解析完整依赖组合，解释器使用各成员 requires-python 的交集；
uv sync 将成员 editable 安装到开发环境，真实发行元数据与 entry points 用于发现。

在仓库根执行：

```sh
uv lock
uv sync --locked --all-packages
uv run --no-sync --package bazaar-compute-node bcn run --config /tmp/bcn-dev/config.toml
uv build --package bazaar-contrib-config-filesystem --no-sources --wheel --out-dir dist
```

源码编辑后重新启动开发宿主或执行检查；依赖、版本、入口和元数据变更后重新 lock／sync。
多成员环境以 all-packages 同步，运行使用 no-sync 保持该联调组合；开发使用测试专用配置与隔离数据。
各包保留自己的检查，仓库根执行规定的 Ruff、Pyright/LSP 与真实流程验收。
新增官方包时登记成员、workspace source、发行依赖和入口，并更新共享锁。
第三方 contrib 使用自己的仓库和版本；需要源码联调时，在本地开发配置中加入其 checkout 与 source，并记录所用提交。

交付时逐包以 uv build --package <发行名> --no-sources 构建 wheel 与 sdist，检查 Requires-Dist、入口及资源，
在 workspace 外的专用临时环境从这些产物安装所需组合，以正式依赖验证安装、发现与业务流程。
sdist 在独立环境重新构建 wheel，验证正式产物所需源码、配置和静态资源完整。
最终发行从实际发布地址重复安装验收。

### 官方统一版本与兼容规则

app、core、公共能力包及全部官方默认／可选 contrib 使用同一个版本，首版为 `0.3.0`，共用一次发行与 `v<版本>` 标签。
未改源码的官方包也随发行升版；只修复一个官方 contrib 时，仍发布整个官方包集合的新 patch 版本。
独立 wheel 保留选装、依赖隔离与替换边界，官方版本号由同一次发行维护。
仓库内的正式包依赖固定为同版精确要求，如 `bazaar-core==0.3.0`；app 固定默认组合，server／web 固定各自组合。
每个 contrib 声明对 core 的正式依赖；app 的发行资源登记全部官方包名及共同版本，管理服务按该清单识别官方可选包。
已安装的官方可选包随应用整体升级调整到目标同版，用户显式固定的版本或来源仍参与解析，冲突时给出具体约束。

第三方 contrib 独立发行，并声明 core 的最低版本与上限，例如 `bazaar-core>=0.3.0,<0.4.0`，
同时声明所需功能契约包的兼容范围。Contrib 声明的公共 API 范围供加载边界复核。
0.3.x 维护已公开的 0.3 框架契约，破坏性契约调整进入新的兼容边界；依赖范围外的组合在安装解析与启动校验时诊断。

固定发行名、目录、入口与默认／可选归属见 [路线图的拆包清单](roadmap.md#发行包模块入口与内置组合)：
app 和 core 两包、5 个公共能力 contrib、35 个实现／组合 contrib，共 42 个官方发行包、58 个唯一运行入口。
config 载体和五版服务先于完整业务文档装配；storage 与 language 各提供两个运行入口。
server／web 保持可选安装；BCS 组合直接读取自己的 ~/.bcs/config.toml、bcs.sqlite3 与 session.key，
保留原配置结构、账户、权限、历史数据、已发布迁移及旧节点协议。
完整升级规则见 [BCS 连续升级](framework.md#02-bcs-配置与数据源连续升级)。

### 发版工具与 CI

Release workflow 保留单个 version 输入（X.Y.Z 或 X.Y.ZrcN）。`scripts/release.py` 从根 workspace 成员发现官方公开包，
读取各成员 project.name、version、正式依赖和源码目录，形成发行集合与依赖顺序。
根开发 project 与 tests/support 属于开发资源；发行集合为 app、core 与 contrib/ 下的公开成员。

工具用一个目标版本同步所有官方成员的 project.version、仓库内精确依赖及所需资源版本引用，更新 uv.lock，
复核共同版本、依赖闭包和运行入口唯一性。变更形成一次已验证签名的 Release v<版本> 提交，
提交使用准备时的 main HEAD 作为预期值；源码推进时重新准备，所有构建与后续发布固定使用该发行提交。

CI 逐包构建独立 wheel／sdist，以完整本地产物集完成干净安装和三平台组合验收，
再按正式包依赖拓扑顺序上传：core 与基础契约先于消费者，app 在默认依赖上传并可解析后上传。
每包记录发行名、版本、源码提交、文件名、校验值及发布结果；部分上传失败时保留同一发行提交和构建产物，
重跑按目标版本找到已保存的发行提交与产物清单，核对已发布文件的校验值后补齐缺失项。
发现同名同版产物不一致时报告冲突并停止该次发行。
全部包发布和实际包源安装验收完成后，发布同一提交上的 annotated v<版本> 标签和整体 release。
版本准备、构建、验收与发布均由仓库工具及 CI 执行，人工输入只需要共同目标版本。
0.3 最终发行的冻结、升级验证与发布顺序见 [路线图的发行清单](roadmap.md#03-发行清单与发布顺序)。
