# Jinja Text Resources

## 状态

- 模式：Plan。
- 状态：待 review；review 通过后只进入 Task 1。
- 分支：`feature/jinja-templates`。
- 基线：`main` 的 `8b2f536251217aeb020c91bcffa8b57d6e42a80f`
  （`Release v0.1.27`）。
- 本文只迁移现有 developer instructions 与 i18n 文本的存储、渲染和已有条件拼接；
  不改变任何当前可见文案、runtime policy、locale 选择或 Channel 行为。
- 所有 Task 按本文顺序串行实施。每完成一个 Task，运行该 Task 的 focused checks，发送业务
  diff 并停在 review；未经 review 不进入下一 Task。

## 1. 目标

BCN 当前有两条相互独立但本质相同的文本渲染路径：

1. `core/instruction.py` 在一个 240 多行 Python raw string 上依次执行五次
   `str.replace()`，最后只通过搜索残留的 `{{` / `}}` 判断是否漏填；
2. `i18n/catalog.py` 使用标准库 `string.Template` 渲染单条 message，但 catalog 本身仍是
   Python mapping，跨段落、可选段落和 provider-specific Markdown 由调用方继续用 list、
   f-string 和换行拼接。

本次改造建立一条共享的 Jinja2 text-rendering boundary：

- 长文本、locale catalog 全部放到 package 内的非 Python resource；
- 一个受约束的 Jinja `Environment` 统一语法、undefined 行为和空白语义；
- 模板只拥有文本结构与有限的 `if` / `for` / include，领域状态和派生数据仍由 typed Python
  context 提供；
- 模板的 declared variables 与调用方 arguments 形成可验证的严格合同；
- wheel 与 source distribution 都包含全部 resource，安装后不依赖源码 checkout。

Jinja2 的引入是已选方案，不再并行保留 `${...}` 与 `{{ ... }}` 两套占位符语法，也不实现自定义
Go-style parser。

## 2. 明确边界

### 2.1 本次包含

- 将 developer instructions 移到
  `src/bazaar_compute_node/resources/developer_instructions.md`；文件保持普通 `.md` 名称，
  不使用 `.tmpl` 后缀；
- 将英文和简体中文 catalog 移到
  `src/bazaar_compute_node/resources/locales/en.toml` 与 `zh-CN.toml`；
- 使用 Jinja2 统一渲染 instruction 与 locale message；
- 把 Telegram / Lark approval prompt 中现存的 optional description 文本拼接迁入 locale
  template；
- 扩展真实 wheel/source distribution smoke，证明 resource 随 PyPI 构建产物安装并可渲染。

### 2.2 本次不包含

- 不允许用户、Channel 或配置文件提供模板，不增加 runtime template override 或 hot reload；
- 不启用 Jinja sandbox、HTML autoescape、Babel、gettext 或 Jinja i18n extension；
- 不把 JSON/Card 结构选择、审批 decision、Markdown fence 长度计算等领域逻辑移入模板；
- 不把所有 CLI formatter、bcc wire output 或数据库文案纳入 i18n；
- 不修改现有英文/中文文案、标点、换行、developer-instruction policy 或 locale fallback；
- 不新增 Changelog command，也不修改 release-time changelog 流程。讨论中的 embedded
  changelog 与 `bcc changelog` 是独立的后续功能，只复用本次建立的 package-resource 边界。

## 3. Package resource 合同

资源布局固定为：

```text
src/bazaar_compute_node/resources/
├── developer_instructions.md
└── locales/
    ├── en.toml
    └── zh-CN.toml
```

`resources/` 与 `locales/` 不需要成为 Python subpackage，也不增加占位用途的
`__init__.py`。Jinja 使用 `PackageLoader("bazaar_compute_node", "resources")`，locale loader
使用 `importlib.resources.files("bazaar_compute_node")` 从同一个 package resource root 读取
TOML。

当前 `uv_build` 会把 `src/bazaar_compute_node` module root 下的全部非排除文件放入 wheel 与
source distribution，因此不增加 `tool.uv.build-backend.data`、`source-include` 或
`wheel-include`。resource 不复制到 environment root 或 `.data`，运行时也不得通过
`Path(__file__)`、当前工作目录或 repository root 定位它。

`tests/package_smoke.py` 必须在 release workflow 现有的隔离 wheel 与 source-distribution 安装
中真实加载并渲染 instruction 与两个 locale catalog。普通 editable-install unit test 不能替代
该 gate，因为 editable source tree 会掩盖漏打包问题。

## 4. Jinja Environment 与渲染合同

新增 `src/bazaar_compute_node/rendering.py`，由它唯一创建同步 text environment。配置固定为：

```python
Environment(
    loader=PackageLoader("bazaar_compute_node", "resources"),
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
    auto_reload=False,
    enable_async=False,
)
```

含义如下：

- 所有模板来自随 BCN 发布的可信 resource，因此不使用 sandbox；
- 输出目标是 Markdown 或 plain text，显式关闭 HTML autoescape；
- `StrictUndefined` 禁止漏字段变成空字符串；
- 不自动 trim/lstrip block，保留文案的精确空白合同；
- package resource 在进程运行期间不可变，关闭 auto reload，由 Environment 自身缓存已加载模板；
- 不注册 BCN domain global、会产生副作用的 callable 或 custom extension。Jinja 内建 filter/test
  可以使用，但领域派生值必须在 Python 中计算后作为 flat context 字段传入。

同一模块提供一个共享 `TextTemplate` 值对象。它封装已编译的 Jinja `Template` 与通过
`jinja2.meta.find_undeclared_variables()` 得到的 `frozenset[str]`：

- `from_resource(name)` 从固定 PackageLoader 编译 instruction；
- `from_source(name, source)` 编译 TOML catalog 中的一条 message，并在 syntax error 中保留
  locale/key 名称；
- `render(arguments)` 要求 argument keys 与 declared variables 完全相等；missing 与 unexpected
  keys 都抛出稳定的 `ValueError`，Jinja 的 `StrictUndefined` 作为第二层保护；
- arguments 必须是 flat mapping。可以包含字符串、数字、布尔值、`None` 与调用方已经构造好的
  sequence，但不向模板暴露 storage、Channel、runtime 或其他有行为的 domain object；
- 模板只执行一次。用户输入中出现 `{{...}}`、`{%...%}` 或 `$HOME` 只是插入后的普通文本，
  不会再次被解释。

Jinja 的 `if` 只负责文本是否出现，例如 optional paragraph；它不能承担权限判断、状态迁移、
route 选择或 provider payload 结构选择。

## 5. Developer instructions 迁移

`DeveloperInstructionContext` 的公开构造字段与现有 validation 保持不变：

```text
agent_name
bot_name
agent_id
runtime_session_id
runtime
workspace
```

`DEVELOPER_INSTRUCTIONS` Python constant 被删除，`render()` 改为向共享的 resource template 传入
上述六个字段。第一行使用 Jinja condition 表达当前 identity 规则：

```jinja2
You're {% if bot_name %}{{ bot_name }}, A.K.A {% endif %}{{ agent_name }}, an AI agent in bcn ...
```

渲染结果必须与当前实现逐字一致：有 `bot_name` 时为
`You're <bot>, A.K.A <agent>`，没有时为 `You're <agent>`。字段的非空与禁止换行检查继续由
`DeveloperInstructionContext.__post_init__()` 完成；模板不负责输入合法性。

resource 保留当前文件末尾换行。测试同时覆盖 bot/no-bot 两条 identity 分支、关键 policy 段落、
不解析插入值内的 Jinja token，以及不存在 unresolved template variables。迁移时使用固定 context
对旧 renderer 与新 renderer 做一次 byte-for-byte parity 对照；删除旧 constant 后由上述 focused
contract tests 持续保护。

## 6. Locale catalog 合同

英文与简体中文 catalog 使用 TOML quoted keys，value 必须全部为字符串：

```toml
"cli.bcn.description" = "Runtime-agnostic ... {{ data_dir }}."
"runtime.error.failed" = "Execution failed: {{ error }}"
```

`tomllib` 负责解析。catalog import 时一次性完成：

1. 验证顶层是 `dict[str, str]`，拒绝 nested table、空 key 与非字符串 value；
2. 验证 en 与 zh-CN key 集合完全一致；
3. 为每个 value 创建带 locale/key 名称的 `TextTemplate`；
4. 验证两个 locale 中同一 key 的 declared-variable 集合完全一致；
5. 将编译后的 mapping 暴露给 immutable `Translator`，不在每次 `text()` 时重新 parse/compile。

`Translator.text(key, arguments)` 的外部合同保持不变：

- 已知 key 使用严格 template argument 合同；
- 当前 locale 缺 key 时仍尝试英文，不过 catalog parity 会让该分支只作为防御；
- 两个 catalog 都不存在的 key 仍原样返回 key，保持现有 fallback；
- 不向用户输入做 HTML/Markdown escaping，也不对插入结果执行第二轮 render。

删除 `i18n/english.py` 与 `i18n/schinese.py`。测试不再通过导入 Python mapping 证明 parity，而是
覆盖真实 TOML resource 的 schema、key parity、variable parity、strict missing/unexpected arguments、
unknown-key fallback，以及含 `$HOME`/Jinja delimiters/换行的插入值保持原样。

## 7. 已有条件拼接迁移

本次只迁移经审计确认属于“文本结构”的两处 approval prompt：

### 7.1 Lark

当前 `_approval_card_content()` 先渲染 action 行，再以 f-string 拼接可选 description。改为一个
provider-specific locale template，输入固定为 `action` 与 `description`，由 Jinja `if description`
控制两个换行和 description block。Card JSON、button columns、pending/resolved decision 与 title
仍由 Python 构造。

### 7.2 Telegram

当前 `_approval_markdown()` 用 list/`extend()` 拼出 title、action 与可选 fenced description。
改为一个 provider-specific locale template，输入固定为：

```text
action
description
description_fence
```

Python 继续根据 description 中最长 backtick run 计算 `description_fence`；Jinja 只决定 description
block 是否出现及其换行位置。无 description 时仍精确输出当前两段 Markdown，有 description 时
仍使用相同 fence 算法与正文。

两个 provider 需要不同 Markdown 结构，因此使用两个清晰的 catalog key，不通过一个模板内的
provider branch 合并。已无 caller 的旧 action-line key 删除；仍被 Lark card header 使用的 title
与 button/status/callback keys 保留。

不迁移以下条件：approval decision 到 key 的选择、action enum 到 locale key 的选择、Lark card
element 是否出现、Telegram callback/route validation。这些是 domain 或 payload structure，不是文本
模板职责。

## 8. 任务拆分

### Task 1：建立 Jinja resource foundation 并迁移 developer instructions

- 在 `pyproject.toml` 增加 `jinja2>=3.1.6`，更新 `uv.lock`；
- 新增共享 `rendering.py` 与固定 Environment/TextTemplate 合同；
- 新增 `resources/developer_instructions.md`，迁移并删除 Python raw-string constant；
- 更新 `DeveloperInstructionContext.render()` 与 instruction focused tests；
- 扩展 `tests/package_smoke.py`，从已安装 distribution 渲染 developer instructions；
- 运行 instruction tests、package-smoke unit coverage、Ruff、Pyright、compileall、lock check 与
  `git diff --check`；
- 发送排除 tests/lock 的业务 diff，停在 review。

### Task 2：迁移 locale catalog

- 新增 `resources/locales/en.toml` 与 `zh-CN.toml`；
- 将 `Translator` 改为使用 importlib resource、TOML schema validation 与预编译
  `TextTemplate` mappings；
- 删除 Python catalog files，保持 locale selection 与 unknown-key fallback；
- 扩展 i18n tests 与 package smoke，覆盖两个真实 locale resource；
- 运行 i18n/CLI/approval focused tests、Ruff、Pyright、compileall、lock check 与
  `git diff --check`；
- 发送排除 tests/lock 的业务 diff，停在 review。

### Task 3：迁移已有条件文本拼接

- 将 Lark optional description block 迁入 Lark locale template；
- 将 Telegram title/action/fenced description 迁入 Telegram locale template，fence 计算留在
  Python；
- 删除不再使用的 fragment key，完成 catalog caller/dead-key audit；
- 使用现有 provider tests 加强 exact English/Chinese、description present/absent 与 embedded
  Jinja-token coverage；
- 运行 Lark/Telegram/i18n focused tests、Ruff、Pyright、compileall、lock check 与
  `git diff --check`；
- 发送排除 tests/lock 的业务 diff，停在 review。

### Task 4：构建产物与最终验收

- 运行完整非 e2e pytest suite；
- 运行 `ruff format --check .`、`ruff check .`、
  `scripts/pyright_lsp_check.py --outputjson .`、compileall、`uv lock --check` 与
  `git diff --check`；
- 运行 `uv build --no-sources`；检查 wheel 与 source distribution 均含 instruction 与两个
  locale resources；
- 分别从真实 wheel 与 source distribution 运行现有 `tests/package_smoke.py`，确认版本、entry
  point、developer instruction 与两个 locale render 全部通过；
- 审计仓库中不再存在 `string.Template`、旧 Python catalog、developer-instruction 手写
  placeholder replacement，以及 approval prompt 的旧文本拼接；
- 不再改生产代码，报告完整结果并停在 final review。

## 9. 风险与控制

- **无意的文案/空白变化：** 固定 Jinja whitespace 配置；迁移时做旧/新 renderer parity；保留
  provider exact-output tests。
- **模板字段静默漏填或 locale 漂移：** `TextTemplate` exact argument keys、
  `StrictUndefined`、catalog key/variable parity 三层失败保护。
- **模板吞入业务逻辑：** 只允许文本结构条件；领域状态、权限、route、payload shape 和派生算法
  保留在 typed Python code。
- **resource 在 editable 环境可用但 wheel 漏失：** release package smoke 必须从真实 wheel/sdist
  import 并 render，Task 4 额外检查 archive contents。
- **用户文本被当成模板执行：** 模板只编译一次、再插入 value，不进行 recursive render；测试使用
  Jinja delimiters 作为输入证明其保持字面值。
- **新增依赖扩大平台风险：** 锁定 Jinja2/MarkupSafe，复用现有 Linux/Windows/macOS 安装与
  release smoke；不使用 optional Babel 或额外 extensions。
- **两个抽象并存：** Task 2 后删除 `string.Template` 与旧 Python catalogs；所有文本模板只通过
  共享 Environment 编译。
