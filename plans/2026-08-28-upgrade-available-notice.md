# 新版本提示与升级

## 1. 现状

`bazaar-compute-node` 发布在 PyPI（`.github/workflows/release.yml`），README 的安装方式是
`uv tool install bazaar-compute-node`，`__init__.py` 的 `__version__` 取自已安装包的元数据。
新版本判定用 `https://pypi.org/pypi/bazaar-compute-node/json` 的 `info.version`，与升级路径同源。

`TimerWheel` 的上限是 `_MAX_DELAY_TICKS`（2^32-1）× `tick_ms`（10）≈ 497 天。`ReminderScheduler`
是节点级周期任务的现成形状：`IAsyncLifecycle`，由 `NodeApplication` 持有，内部一个定时器。

`NodeApplication.wait()`（`app/application.py:257-263`）把 `timer_wheel`、`reminder_scheduler`、
`command_server`、`storage` 当作致命源，任一失败即退出进程。

`inbox_notice`（`core/orchestration/turn.py:88`）拼出的文本就是 runtime 每个 turn 的输入。

Python 标准库没有 PEP 440 版本比较；`packaging` 已作为传递依赖出现在 `uv.lock`。

`bcc` 的命令按 `resource` + `command` 分发（`app/resource_dispatch.py:196-232`），现有 resource
是 `message` / `inbox` / `thread` / `reminder`。`bcn system-service restart` 走各平台原生命令：
`systemctl --user restart`、`launchctl kickstart -k`、`schtasks /End` + `/Run`。

POSIX 上覆盖正在使用的文件是允许的，运行中的进程持有旧 inode。Windows 上
`.local\bin\bcn.exe` 与 live venv 的 `python.exe` 被运行中的进程占着，无法覆盖；
`windows.ps1` 启动的是外层 `.local\bin\bcn.exe`，它是内嵌解释器绝对路径
`<tools>\bazaar-compute-node\Scripts\python.exe` 的 uv trampoline，因此 live 目录换成新的
之后它解析到的就是新版本的解释器。

## 2. 实现范围

- 节点内置版本检查任务，启动后立即查一次，之后每小时一次；
- 查到更新时，`inbox_notice` 追加一行提示，让 Agent 在对话中主动询问用户是否升级；
  **每个 bcn session 对每个版本听到一次**，版本更新了就再提示一次；
- 新增 `bcc node upgrade`：按平台执行安装，装成功后才挂一个升级后唤醒的 Reminder，最后触发
  `RESTART_EXIT_CODE` 退出交给托管方拉起；任一步失败都把错误返回给 Agent，不退出；
- POSIX 直接就地安装，`systemd.service` 与 `launchd.sh` 不做改动；
- Windows 装到 `.staging`，由 `windows.ps1` 在拉起 bcn 之前完成交换与中断恢复，旧目录留作
  回滚点；
- 托管文件带修订号，`bcc node upgrade` 在 Windows 上先自检并按需重写 wrapper 脚本，再进入安装；
  这一步不碰服务注册，也不需要用户参与。

## 3. 版本检查任务

新增 `app/version_check.py`：

```python
_CHECK_INTERVAL_MS = 3_600_000
_RELEASE_URL = "https://pypi.org/pypi/bazaar-compute-node/json"


class VersionWatcher(IAsyncLifecycle):
    def __init__(
        self,
        *,
        timer_wheel: TimerWheel,
        current_version: str,
        request_timeout_seconds: float,
    ) -> None: ...

    def available_version(self) -> str | None: ...
```

- `start()` 建一个任务：先查一次，然后用 `timer_wheel.create(_CHECK_INTERVAL_MS)` 等待，循环；
- 每次查询用 `aiohttp.ClientSession` 取 `_RELEASE_URL`，读 `info.version`；
- `Version(latest) > Version(current_version)` 成立则存进 `self._available`，否则清空，
  这样 PyPI 撤回导致 `info.version` 变小也能自动收敛；
- `asyncio.CancelledError` 照常向上传播；其余异常记 warning 后继续下一个周期，
  `self._available` 保持上一次的结果；
- `stop()` 取消任务并等待，与 `ReminderScheduler.stop()` 一致；
- 不实现 `ITaskFailureSource`，不加入 `NodeApplication.wait()` 的 sources。

**每个 bcn session 对每个版本听到一次。** 提示的频率由会话自己记住的状态控制：会话没听说过
当前这个版本就带上提示行，带过就不再带，直到出现更新的版本为止。

早先按「只在新建 runtime session 的那一次带」实现过，那样一个不过期的会话可能永远等不到提示：
节点刚启动、版本检查还没回来时进来的第一条消息就会建出这样的会话。按会话记版本没有这个洞——
检查什么时候回来，提示就跟着之后的第一个 turn 走。

`available_version()` 是纯读的 getter，`VersionWatcher` 不保存任何提示相关的状态。

`pyproject.toml` 的 `[project] dependencies` 增加 `packaging>=25.0`，并更新 `uv.lock`。

`NodeApplication.__init__` 构造 `VersionWatcher(timer_wheel=self.timer_wheel,
current_version=__version__, request_timeout_seconds=timeout_budget.command_seconds)`，在启动
`timer_wheel` 与 `reminder_scheduler` 的同一处 `start()`，在 `stop()` 中一并停止。

`NodeConfiguration` 增加 `version_check: bool = True`，对应 `[node] version_check`，非布尔值报
`ConfigurationError`，序列化时无条件写出。为 `false` 时 `NodeApplication.start()` 不启动
`VersionWatcher`，`available_version()` 恒为 `None`，节点因此不会访问 PyPI。

## 4. 提示的传递

`inbox_notice` 增加关键字参数 `upgrade_version: str | None = None` 与
`installed_version: str | None = None`。两者同时给出时，在 `rows` 之后、闭合括号之前追加：

```text
Upgrade available: bazaar-compute-node {upgrade_version} (installed {installed_version}). Mention
it in passing when you reply and offer to upgrade; if the user agrees, run `bcc node upgrade`. If they
do not want it, just carry on.
```

`Mention it in passing when you reply` 让提示跟着回复带出来而不是变成话题主线；最后一句让
Agent 在用户不想升级时直接继续，不追问、也不需要记录任何状态。

**提示行在所有平台上完全一致**，不区分 Windows 的托管文件是否陈旧——那是 `bcc node upgrade` 自己
要处理的事，不该泄漏到提示里。

`inbox_notice` 只负责渲染，不读取全局状态。该文本与 notice 其余内容一样是给 Agent 看的，
不进 locale。

notice 的正文本身此前是 f-string 拼接，没有跟上模板渲染的迁移。本次一并迁移：新增
`resources/inbox_notice.tpl`，由 `TextTemplate.from_resource` 加载，`inbox_notice` 只负责整理
每个 target 的行数据，措辞、单复数、标志位的写法与闭合括号的位置都由模板决定。

同一批漏网的还有另外两处，也在本任务内迁移：

- `app/command.py`：发送回执、附件后缀、进入 turn 的消息头（含 reminder 与 handoff 两种系统
  消息的附加说明）、`bcc message read` 的单条渲染、草稿被拦截时的整块回复，分别对应
  `resources/command/` 下的 `send_result.tpl`、`attachment_suffix.tpl`、`check_message.tpl`、
  `read_message.tpl`、`freshness_hold.tpl`。
- `bcc.py`：`inbox list` 的表头与每行、`message check`、`message read`、`thread unfollow`、
  reminder 的五个输出、以及出错时的 `Error:`/`Code:`/`Draft saved:`/`Next action:`，分别对应
  `resources/bcc/` 下的九个模板。

`bcc.py` 中 argparse 的 help 与 description 不迁移：它们是逐个选项的元数据而不是成块文案。
各处 `raise` 的错误文案同样不迁移，它们是诊断信息。

措辞的选择一律由模板承担：单复数、标志位的写法、`none` 占位、reminder 的 one-time 与重复
规则、系统消息的两种附加说明、发送回执的两种状态、`Draft saved:` 与 `Next action:` 是否出现，
都是模板里的条件分支。Python 侧只整理数据。

迁移不改变任何一个字节的输出，现有的精确断言即是验证。

`SessionOrchestrator.__init__` 增加 `upgrade_notice: Callable[[], tuple[str, str] | None]`，
由 `NodeApplication` 接上 `VersionWatcher`，返回 `(available_version, installed_version)` 或
`None`，缺省 `lambda: None`。它是纯读的。

`SessionOrchestrator` 为每个 bcn session 记一个 `UpgradeNotice`
（`core/orchestration/upgrade_notice.py`，`UpgradePending | UpgradeAnnounced`，没听说过任何版本
的会话没有条目）。`_run_notification` 构造 `input_text` 前先问 `_upgrade_for(session_id)`：当前
有可用版本、且这个会话记的不是同一个版本时，返回它并把会话标成已告知。`session.py:810` 的 steer 路径是往一个正在跑的
turn 里追加通知；那条消息会被这次 steer 消费掉，之后的 turn 路径就找不到未读可跑，因此 steer
也要问一次 `_upgrade_for`，否则提示要等下一条入站消息才有机会送出。
`AgentApplication` 增加同名构造参数并原样传给 `SessionOrchestrator`；`NodeApplication` 在
`app/application.py:159-170` 处传入自己持有的 watcher。

## 5. 升级命令

`bcc` 增加 resource `node` 与两个 command：`upgrade` 与 `version`，`app/resource_dispatch.py`
增加对应请求模型与分发分支。用户同意就升级，不同意就什么都不做。

`bcc node version` 返回当前进程的 `__version__`。它读的是运行中的节点自己，而不是磁盘上装了什么，
因此升级后 Agent 用它就能确认运行时确实换了版本。

`bcc node upgrade` 阻塞到安装结束，两端都不设超时。harness runtime 会把长时间运行的命令自动转入
后台，因此阻塞不会卡住 Agent 的这一轮；而两端都不放弃，也就不会出现「调用方已经走了、uv 还在
装」的半途状态。

0. 整段升级是一个事务，由 `UpgradeService` 的一把锁串起来：查可用版本、安装、挂 Reminder、
   请求退出都在锁内。锁只包住安装是不够的——放开之后第一个请求还在挂 Reminder，第二个就能开始
   装，而这期间 PyPI 可能已经前进一版，于是回执与 Reminder 说的是一个版本、机器上起来的是另一
   个，且已经跑起来的 uv 线程取消不掉；
1. 没有可用版本就返回错误，不做任何事；
2. 安装。两个平台都执行 `uv tool install --force <发行包名>==<目标版本>`：POSIX 就地装；
   Windows 把 `UV_TOOL_DIR` 指到一个临时目录,装完再把里面那份改名成
   `<tools>\<发行包名>.staging`。不自己建 venv、也不指定解释器——那本来就是 uv 的事,它按
   `requires-python` 挑,挑不到还能自己下一个；
3. 安装失败：把错误返回给命令调用方并结束。不挂 Reminder、不重启，节点继续以旧版本运行，
   Agent 当场就能告诉用户装不上；
4. 安装成功：用 `reminder_service` 挂一个 Reminder，锚定当前会话的入站消息，60 秒后触发，标题
   写明目标版本。重启会掐断这条阻塞的连接，Agent 拿不到成功的回执，只能靠它唤醒回来，再用
   `bcc node version` 确认；
5. 节点以 `RESTART_EXIT_CODE` 退出，由托管方把它拉起来。

**节点不自己发起重启。** 它做不到：Windows 上 `system-service restart` 的第一步是对 bcn 的进程号
执行 `taskkill /T`，而发起这条命令的进程正是 bcn 的子进程，会连同整棵树一起被杀，第二步的
`schtasks /Run` 永远轮不到。这一点在真机上实测过：终止之后没有任何启动事件。

因此重启统一交给本来就负责拉起节点的一方：Linux 的 `systemd.service` 已经是
`Restart=on-failure`，macOS 的 `launchd.plist` 已经是 `KeepAlive=true`，两者对非零退出本来就会
重新拉起，托管文件不需要任何改动；Windows 的 `windows.ps1` 把交换与启动包进一个 `do/while`，
看到这个退出码就再转一圈——交换正好在每圈开头，而计划任务与 wrapper 进程全程存活，没有任何
进程需要活过一次针对自己的清理。

`bcc node upgrade` 因此只在节点由托管方式拉起时完整可用。前台手工 `bcn run` 的节点会退出而没有
人拉回来，命令的回执文案对此写明。

Reminder 挂在安装之后、退出之前：安装失败时不会留下一个空转的 Reminder，而退出会中断本进程，
所以它也不能挪到之后。

安装不设期限。没有第二个期限能起作用——uv 自己对不响应的请求会放弃，再加一层只会在
「连得上但很慢」时把一次本可以成功的升级打断。`CommandDispatcher` 因此对 resource `node`
不施加命令窗口，`bcc` 客户端同样不设等待上限。

Windows 在安装之前多一步 wrapper 自检：读已安装 `windows.ps1` 首行的 `template-revision`，偏低
或缺失就按当前模板重写它（见第 6 节）。失败就直接返回错误，不进入安装。

## 6. Windows 的交换

交换由 `windows.ps1` 在 `[BcnNoWindowProcess]::Run` 之前完成，此时旧 bcn 已经退出，没有任何
进程持有目标目录：

1. `<tools>\bazaar-compute-node` 不存在（上一次交换在两次改名之间被打断）：有
   `bazaar-compute-node.staging` 就把它改回 live 完成升级，否则把 `.old` 改回 live 完成回滚；
2. `.staging` 与 live 都在：`bazaar-compute-node -> bazaar-compute-node.old`、
   `bazaar-compute-node.staging -> bazaar-compute-node`；
3. 照常启动。

整段包在 `try { } catch { }` 里，失败只记日志并继续启动当前 live 版本——文件开头是
`$ErrorActionPreference = 'Stop'`，不包的话一次改名失败就会让服务起不来。

`.old` 不在交换时删除，留作回滚点。新节点启动、`NodeApplication.ready` 为真且 `__version__`
等于目标版本时删除它。

**托管文件的版本识别。** `MANAGED_MARKER`（`system_service.py:27`）扩成带修订号的形式：

```text
Managed by bazaar-compute-node. template-revision=2
```

模板内容每次变化就把 `TEMPLATE_REVISION` 加一。`bcc node upgrade` 在 Windows 上的第一步是读已安装
`windows.ps1` 的首行、解析修订号：偏低或缺失就先按当前模板重写它，再走安装与重启。这是命令
自己的前置条件，提示行不提，用户与 Agent 都不需要知道。

重写只动 wrapper 脚本，不碰服务注册（`windows.xml` + `schtasks /Create`）：注册项指向的路径
没有变，因此不需要权限、也不改动系统里的任何登记。

**重写要保住目标文件的 ACL**：普通临时文件带的是继承来的权限，直接替换会改掉文件的 SDDL。
因此这一步交给 PowerShell 而不是在 Python 里写文件——`Get-Acl` 取到目标文件的 ACL，写好临时
文件后 `Set-Acl` 应用同一份 ACL，再 `[System.IO.File]::Replace`。节点侧本来就要 shell out 调
`uv` 与 `schtasks`，多这一次调用是同一类操作。

任一步失败就把错误返回给命令调用方，由 Agent 告诉用户。

**安装完成后才改名成 `.staging`。** 安装期间 `UV_TOOL_DIR` 指向一个临时目录，`uv tool install`
成功返回之后才改名成 `.staging`，避免一次装到一半的失败留下一个看起来可用、实际残缺的目录。
入口脚本一并落在那个临时目录里丢弃：正式安装的 trampoline 指向的是被替换的那个目录本身，交换
之后自然指到新的一份。

**目标版本的处理。** 版本字符串在 bcn 侧使用前用 `packaging.version.Version` 校验；传给 uv 时
作为独立参数，不拼进 shell 字符串。

## 7. 任务拆分

### Task 1：版本检查任务

按第 3 节实现 `VersionWatcher`、`packaging` 依赖与 `NodeApplication` 的构造/启动/停止接线。

测试：`tests/app/test_version_check.py` 覆盖未检查前 `available_version()` 为 `None`、`stop()`
后任务结束，以及 `available_version()` 在没有更新时返回 `None`。`tests/app/test_config.py` 覆盖
`version_check` 的默认值、`false` 的解析与序列化往返、非布尔值报错；`tests/app/test_composition.py`
覆盖关闭时节点不启动 watcher。其余非 e2e 测试构造 `NodeConfiguration` 时一律传
`version_check=False`，避免测试访问 PyPI。
真实 PyPI 查询属于外部依赖，按第 16 条写成 e2e：`tests/e2e/test_pypi_version.py`
标记 `e2e`，真实请求一次 `_RELEASE_URL`，断言 `info.version` 能被 `Version` 解析，且用一个明显
更旧的当前版本能判出有更新。

checks：`tests/app/`、`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`uv lock --check`、`git diff --check`。

### Task 2：inbox notice 追加提示

按第 4 节实现 `inbox_notice` 的两个新参数与 `upgrade_notice` 的传递链，并完成第 4 节列出的
三处模板迁移。

测试：扩展 `tests/contrib/test_orchestration.py` 的 `inbox_notice` 契约测试，覆盖无更新时输出
不变、有更新时提示行出现在闭合括号之前；再加一个 orchestrator 级用例，确认**新建 runtime
turn 输入里带提示行、同一会话的下一个 turn 不带、出现更新版本时再次带上**。模板迁移由现有的
精确断言验证；`bcc` 的草稿保存提示、`thread unfollow` 的两个分支与空 reminder 列表此前没有覆盖，
补上用例。

checks：`tests/contrib/test_orchestration.py`、`tests/app/test_composition.py`、
`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 3：`bcc node upgrade` 与 `bcc node version`

按第 5 节实现 resource `node` 与两个 command：`bcc node version` 返回进程内版本；`bcc node upgrade`
阻塞到安装结束，成功则挂 Reminder 并重启，失败则把错误返回给调用方。Windows 的 wrapper 自检与
重写属于 Task 4：它读的修订号由那一步引入，两者是同一件事的两半。

节点在响应写回之后才退出，因此不需要为「先回话再重启」安排任何等待。

测试：`uv` 是外部依赖，按第 16 条走 e2e，`tests/e2e/test_upgrade_install.py` 三个用例。

两个走完整链路：真实节点、sqlite 库、真实会话与锚点消息，`UV_TOOL_DIR` 与 `UV_TOOL_BIN_DIR`
指到临时目录真实执行 `uv tool install`，`PATH` 换成只包含一份 `uv` 与一个记录 argv 的 `bcn`
的目录——把已安装的 bcn 从 `PATH` 上移除，测试就碰不到这台机器上真正运行的节点，而那个记录器
同时证明了节点没有 spawn 任何东西。成功一侧断言命令返回时目标版本已经在隔离目录里、Reminder
已经落进临时库、节点请求了一次重启、记录器没有被调用过；失败一侧把索引地址指向无人监听的
端口，断言错误当场返回、什么都没装、没有 Reminder 落库、没有请求重启，且可以再试。

`windows.ps1` 的循环由 `tests/app/test_system_service.py` 覆盖：渲染出的脚本认这个退出码、
交换在每圈开头、脚本里不出现 `schtasks`。

第三个覆盖另一类失败：`PATH` 上没有 `uv`，也就是这个节点不是用 uv 装的。

单独测安装本身是多余的：完整链路里那次安装就是真的。

托管方在节点退出之后是否真的把它拉起来，不在测试范围内：那要么需要一个隔离的 systemd unit
（unit 名是单一常量，得先参数化），要么需要真实的 Windows 计划任务。这一条在 Task 5 的真机
运行里验证。

checks：`tests/contrib/test_orchestration.py`、`tests/app/`、`ruff format --check .`、
`ruff check .`、`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 4：Windows 的交换、托管文件刷新与 wrapper 自检

按第 6 节改 `windows.ps1`：加入恢复与交换两段并包在 `try/catch` 里；实现安装完成后才改名成
`.staging`；`MANAGED_MARKER` 加上 `template-revision` 并由 `bcc node upgrade` 比对与重写；节点启动后
在版本对上时删除 `.old`。

重写 wrapper 时不能凭当前进程重新推导安装参数：被替换的那份是旧版本装的，`--env-file` 之类的
入参没有任何别处的记录，而节点的密钥正是从那个环境脚本来的，推导错会让节点起不来。因此重写
读取已安装 wrapper 里的四个变量原样复用，只换模板本身。

`.old` 的删除需要知道目标版本，而它同样没有别处的记录，所以安装成功时在 `<tools>` 下写一个
`<发行包名>.upgrade-target` 记下目标版本；节点启动时读它，只有当前进程的 `__version__` 与之
相等才删除 `.old` 与这个记录文件。版本对不上说明交换没有生效，`.old` 就是回退路径，必须留着。

测试：`tests/app/test_system_service.py` 覆盖渲染出的 `windows.ps1` 里交换段在启动 bcn 之前、
含恢复分支、失败只记日志不退出，以及托管文件首行带当前修订号；`tests/app/test_upgrade_state.py`
用临时目录覆盖「修订号相同则不动」「没有 wrapper 则跳过」「wrapper 读不出变量则中止升级」
「版本对不上则保留 `.old`」「版本对上则删除」。改名、`Set-Acl` 与 `schtasks` 属于 Windows 平台
行为，进程内不模拟，放到 Task 5 的真实回归。

checks：`tests/app/`、`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 5：最终验收

- 运行完整非 e2e pytest suite 与 `-m e2e` 中新增的版本检查用例；
- 运行 `ruff format --check .`、`ruff check .`、
  `uv run scripts/pyright_lsp_check.py --outputjson .`、
  `uv run python -m compileall -q src tests`、`uv lock --check`、`git diff --check`；
- 在一台真实 Windows 主机上验证交换链路。装一份本分支构建、版本号标低一档的 fixture，
  执行一次 `bcc node upgrade`，确认目标进入 `.staging`、Reminder 在退出之前落库、`windows.ps1`
  完成 `live → .old` 与 `.staging → live` 并重新启动 bcn；另外单独构造一次「只完成第一次改名」
  的中断，确认下一次启动能把 `.staging` 恢复成 live。这是这条链路唯一无法在 Linux 上验证的部分；
- 汇总结果，停在最终 review。

**升级目标只能是 PyPI 报出的版本。** 版本检查读的是写死的 PyPI 地址，本地索引只影响 uv 从哪里
下载，不影响选哪个版本。因此在本分支发布之前，Windows 上换进去的目标必然是一个更旧的正式包，
它起不来是预期结果。这使得两件事只能留到发布之后验证：新进程的 `bcc node version` 等于目标
版本，以及 `.old` 在版本对上后被删除。发布之后拿那次真实的版本更新补验即可。

节点退出之后托管方是否真的把它拉起来，同样只能在真机上看：Linux 与 macOS 靠各自托管配置，
Windows 靠 wrapper 循环。

## 8. 验收标准

1. 节点启动后立即完成一次版本检查，之后每小时一次。
2. PyPI 不可达或返回异常时，检查静默跳过并在下一个周期重试，节点不退出、健康状态不变。
3. 存在更新时，提示行出现在该 bcn session 之后第一个 turn 的 inbox notice 闭合括号之前，
   同一个版本不再重复；出现更新的版本时再提示一次。不存在更新时，输出与现在完全一致。
4. 提示行在所有平台一致，不含任何平台特有内容。
5. `bcc node upgrade` 的顺序是安装、挂 Reminder、以 `RESTART_EXIT_CODE` 退出；Reminder 在退出
   之前就已经落库。命令两端都不设超时。节点不 spawn 任何进程去重启自己。
6. 安装失败时既不挂 Reminder 也不触发重启，错误当场返回给 Agent，节点继续以旧版本运行，之后
   允许再试。
7. `bcc node version` 返回的是当前进程的版本，而不是磁盘上安装了什么。
8. POSIX 上升级就是就地安装加重启，`systemd.service` 与 `launchd.sh` 没有任何改动。
9. Windows 上安装写进 `.staging`，且只有安装成功后才使用这个名字；交换由 `windows.ps1` 在拉起
   bcn 之前完成，此时没有任何 bcn 进程持有目标目录；交换失败或被打断时下一次启动能恢复。
10. Windows 的 wrapper 自检与重写由 `bcc node upgrade` 自己完成，重写只动 wrapper 脚本、不碰服务
   注册，失败时命令返回错误而不是继续一个注定不生效的升级。
11. `.old` 保留到新进程的 `__version__` 等于目标版本时才删除。
12. 目标版本使用前经过 `Version` 校验，传给 uv 时是独立参数而不是拼接的 shell 字符串。
13. `packaging` 出现在 `[project] dependencies` 且 `uv lock --check` 通过。
14. `[node] version_check = false` 时节点不发起任何 PyPI 请求，inbox notice 与现在完全一致。
15. inbox notice、`app/command.py` 与 `bcc.py` 的成块文案全部由 `resources/` 下的模板渲染，
    输出与迁移前逐字节一致，且模板随 wheel 一起分发。
16. full pytest、Ruff、Pyright、compileall、lock 与 diff gates 全部通过。
