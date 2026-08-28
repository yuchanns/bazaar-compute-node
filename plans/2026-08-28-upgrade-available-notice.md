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
`_run_notification` 里 `self.runtime_session(...)` 返回 `None` 即表示这次要新建 runtime session。

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
  **提示只在为这次入站消息新建 runtime session 时带上**，复用既有会话时不带，因此频率由会话
  生命周期天然控制，不需要任何抑制状态；
- 新增 `bcc upgrade`：按平台执行安装，装成功后才挂一个升级后唤醒的 Reminder，最后触发
  `system-service restart`；任一步失败都把错误返回给 Agent，不重启；
- POSIX 直接就地安装，`systemd.service` 与 `launchd.sh` 不做改动；
- Windows 装到 `.staging`，由 `windows.ps1` 在拉起 bcn 之前完成交换与中断恢复，旧目录留作
  回滚点；
- 托管文件带修订号，`bcc upgrade` 在 Windows 上先自检并按需重写 wrapper 脚本，再进入安装；
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

**提示的频率由 runtime session 的生命周期控制**：只有在为这次入站消息新建了 runtime session
时才带上提示行，复用既有会话（resume）时不带。一个 runtime session 对应一段连续的对话，空闲
过期或失败之后才会重建，因此提示是「每开一段新对话最多一次」。

`available_version()` 是纯读的 getter，`VersionWatcher` 不保存任何提示相关的状态。

`pyproject.toml` 的 `[project] dependencies` 增加 `packaging>=25.0`，并更新 `uv.lock`。

`NodeApplication.__init__` 构造 `VersionWatcher(timer_wheel=self.timer_wheel,
current_version=__version__, request_timeout_seconds=timeout_budget.command_seconds)`，在启动
`timer_wheel` 与 `reminder_scheduler` 的同一处 `start()`，在 `stop()` 中一并停止。

## 4. 提示的传递

`inbox_notice` 增加关键字参数 `upgrade_version: str | None = None` 与
`installed_version: str | None = None`。两者同时给出时，在 `rows` 之后、闭合括号之前追加：

```text
Upgrade available: bazaar-compute-node {upgrade_version} (installed {installed_version}). Mention
it in passing when you reply and offer to upgrade; if the user agrees, run `bcc upgrade`. If they
do not want it, just carry on.
```

`Mention it in passing when you reply` 让提示跟着回复带出来而不是变成话题主线；最后一句让
Agent 在用户不想升级时直接继续，不追问、也不需要记录任何状态。

**提示行在所有平台上完全一致**，不区分 Windows 的托管文件是否陈旧——那是 `bcc upgrade` 自己
要处理的事，不该泄漏到提示里。

`inbox_notice` 只负责渲染，不读取全局状态。该文本与 notice 其余内容一样是给 Agent 看的，
不进 locale。

`SessionOrchestrator.__init__` 增加 `upgrade_notice: Callable[[], tuple[str, str] | None]`，
由 `NodeApplication` 接上 `VersionWatcher`，返回 `(available_version, installed_version)` 或
`None`，缺省 `lambda: None`。它是纯读的。

**只在新建 runtime session 的那一次带上提示行。** `_run_notification`（`session.py:1176` 附近）
里，`self.runtime_session(...)` 返回 `None` 就说明这次要新建会话；把这个布尔量一路带到
`inbox_notice` 的调用点，只有它为真时才传版本参数。`session.py:810` 的 steer 路径是往一个正在
跑的 turn 里追加通知，必然是复用会话，因此那里永远不带提示行。两个调用点
（`session.py:810` 的 steer 路径与 `session.py:1176` 的 turn 路径）都把结果转成两个参数。
`AgentApplication` 增加同名构造参数并原样传给 `SessionOrchestrator`；`NodeApplication` 在
`app/application.py:159-170` 处传入自己持有的 watcher。

## 5. 升级命令

`bcc` 增加 resource `node`、command `upgrade`（命令行写作 `bcc upgrade`），
`app/resource_dispatch.py` 增加对应请求模型与分发分支。用户同意就升级，不同意就什么都不做。

节点侧的顺序是「先装，装成了再挂 Reminder，最后重启」；Windows 在安装之前多一步 wrapper
自检：

0. 仅 Windows：读已安装 `windows.ps1` 首行的 `template-revision`，偏低或缺失就按当前模板重写
   它（见第 6 节）。重写失败就把错误返回给命令调用方并结束，不继续升级；
1. 安装。POSIX 执行 `<uv> tool install --force bazaar-compute-node==<目标版本>`；Windows 把
   目标版本装进 `<tools>\bazaar-compute-node.staging`（`uv venv` 建环境，
   `uv pip install --python <staging> bazaar-compute-node==<目标版本>` 装包，装成功之后才改名
   成 `.staging`）；
2. 安装失败：把错误返回给命令调用方并结束。不挂 Reminder、不重启，节点继续以旧版本运行，
   Agent 当场就能告诉用户装不上；
3. 安装成功：用 `reminder_service` 挂一个 Reminder，锚定当前会话的入站消息，60 秒后触发，
   标题写明「升级后回报结果」——它在重启后把 Agent 唤醒，用户才能拿到结论；
4. 调用 `system-service restart`。

Reminder 挂在安装之后、重启之前：安装失败时不会留下一个空转的 Reminder，而重启会中断本进程，
所以它也不能挪到重启之后。

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

模板内容每次变化就把 `TEMPLATE_REVISION` 加一。`bcc upgrade` 在 Windows 上的第一步是读已安装
`windows.ps1` 的首行、解析修订号：偏低或缺失就先按当前模板重写它，再走安装与重启。这是命令
自己的前置条件，提示行不提，用户与 Agent 都不需要知道。

重写只动 wrapper 脚本，不碰服务注册（`windows.xml` + `schtasks /Create`）：注册项指向的路径
没有变，因此不需要权限、也不改动系统里的任何登记。

**重写要保住目标文件的 ACL**：普通临时文件带的是继承来的权限，直接替换会改掉文件的 SDDL。
因此这一步交给 PowerShell 而不是在 Python 里写文件——`Get-Acl` 取到目标文件的 ACL，写好临时
文件后 `Set-Acl` 应用同一份 ACL，再 `[System.IO.File]::Replace`。节点侧本来就要 shell out 调
`uv` 与 `schtasks`，多这一次调用是同一类操作。

任一步失败就把错误返回给命令调用方，由 Agent 告诉用户。

**安装完成后才改名成 `.staging`。** 安装期间用一个临时目录名，`uv pip install` 成功返回之后才
改名成 `.staging`，避免一次装到一半的失败留下一个看起来可用、实际残缺的目录。

**目标版本的处理。** 版本字符串在 bcn 侧使用前用 `packaging.version.Version` 校验；传给 uv 时
作为独立参数，不拼进 shell 字符串。

## 7. 任务拆分

### Task 1：版本检查任务

按第 3 节实现 `VersionWatcher`、`packaging` 依赖与 `NodeApplication` 的构造/启动/停止接线。

测试：`tests/app/test_version_check.py` 覆盖未检查前 `available_version()` 为 `None`、`stop()`
后任务结束，以及 `available_version()` 在没有更新时返回 `None`。
真实 PyPI 查询属于外部依赖，按第 16 条写成 e2e：`tests/e2e/test_version_check.py`
标记 `e2e`，真实请求一次 `_RELEASE_URL`，断言 `info.version` 能被 `Version` 解析，且用一个明显
更旧的当前版本能判出有更新。

checks：`tests/app/`、`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`uv lock --check`、`git diff --check`。

### Task 2：inbox notice 追加提示

按第 4 节实现 `inbox_notice` 的两个新参数与 `upgrade_notice` 的传递链。

测试：扩展 `tests/contrib/test_orchestration.py` 的 `inbox_notice` 契约测试，覆盖无更新时输出
不变、有更新时提示行出现在闭合括号之前；再加一个 orchestrator 级用例，确认**新建 runtime
session 的那一次** turn 输入里带提示行，而同一会话的后续 turn 不带。

checks：`tests/contrib/test_orchestration.py`、`tests/app/test_composition.py`、
`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 3：`bcc upgrade`

按第 5 节实现 resource `node` 与 command `upgrade`：Windows 的 wrapper 自检与重写、按平台
分叉的安装、成功后挂 Reminder 并触发 `system-service restart`，任一步失败都把错误返回给命令
调用方。

测试：扩展 `tests/contrib/test_orchestration.py`，覆盖安装成功时按「装、挂 Reminder、重启」的
顺序执行，以及安装失败时既没有 Reminder 落库也没有触发重启、错误被返回。真实的
`uv tool install` 与 `uv venv` 属于外部依赖，不在进程内执行；通过注入的原生命令执行器观察被
调用的命令与顺序。

checks：`tests/contrib/test_orchestration.py`、`tests/app/`、`ruff format --check .`、
`ruff check .`、`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 4：Windows 的交换与托管文件刷新

按第 6 节改 `windows.ps1`：加入恢复与交换两段并包在 `try/catch` 里；实现安装完成后才改名成
`.staging`；`MANAGED_MARKER` 加上 `template-revision` 并由 `bcc upgrade` 比对与重写；节点启动后
在版本对上时删除 `.old`。

测试：`tests/test_cli.py` 覆盖渲染出的 `windows.ps1` 含恢复段与交换段且带 `try/catch`、渲染出的
托管文件首行带当前修订号；`tests/app/test_upgrade_state.py` 用临时目录覆盖「修订号偏低则重写 wrapper」「修订号相同则
不动」「版本对上则删除 `.old`」。改名与 `schtasks` 属于 Windows 平台行为，进程内不模拟，
放到 Task 5 的真实回归。

checks：`tests/app/`、`tests/test_cli.py`、`ruff format --check .`、`ruff check .`、
`uv run scripts/pyright_lsp_check.py --outputjson .`、`git diff --check`。

### Task 5：最终验收

- 运行完整非 e2e pytest suite 与 `-m e2e` 中新增的版本检查用例；
- 运行 `ruff format --check .`、`ruff check .`、
  `uv run scripts/pyright_lsp_check.py --outputjson .`、
  `uv run python -m compileall -q src tests`、`uv lock --check`、`git diff --check`；
- 在 Linux 上跑一次完整升级：`bcc upgrade` → 就地安装 → `system-service restart` →
  新版本启动 → Reminder 唤醒 Agent 回报；
- 在一台真实 Windows 主机上先跑一次 `bcn system-service install` 拿到带交换段的 wrapper，再跑
  一次完整升级：`bcc upgrade` → 装进 `.staging` → 计划任务重启 → `windows.ps1` 完成交换 →
  新版本启动 → Reminder 唤醒 Agent 回报，并确认 `.old` 在版本对上后被删除；另外手工制造一次
  「只完成第一次改名」的中断，确认下一次启动能恢复。这是这条链路唯一无法在 Linux 上验证的
  部分；
- 汇总结果，停在最终 review。

## 8. 验收标准

1. 节点启动后立即完成一次版本检查，之后每小时一次。
2. PyPI 不可达或返回异常时，检查静默跳过并在下一个周期重试，节点不退出、健康状态不变。
3. 存在更新且这次入站消息新建了 runtime session 时，提示行出现在 inbox notice 的闭合括号
   之前；复用既有会话、或不存在更新时，输出与现在完全一致。节点不保存任何抑制状态。
4. 提示行在所有平台一致，不含任何平台特有内容。
5. `bcc upgrade` 的顺序是安装、挂 Reminder、重启；Reminder 在重启之前就已经落库。
6. 安装失败时既不挂 Reminder 也不触发重启，错误当场返回给 Agent，节点继续以旧版本运行。
7. POSIX 上升级就是就地安装加重启，`systemd.service` 与 `launchd.sh` 没有任何改动。
8. Windows 上安装写进 `.staging`，且只有安装成功后才使用这个名字；交换由 `windows.ps1` 在拉起
   bcn 之前完成，此时没有任何 bcn 进程持有目标目录；交换失败或被打断时下一次启动能恢复。
9. Windows 的 wrapper 自检与重写由 `bcc upgrade` 自己完成，重写只动 wrapper 脚本、不碰服务
   注册，失败时命令返回错误而不是继续一个注定不生效的升级。
10. `.old` 保留到新进程的 `__version__` 等于目标版本时才删除。
11. 目标版本使用前经过 `Version` 校验，传给 uv 时是独立参数而不是拼接的 shell 字符串。
12. `packaging` 出现在 `[project] dependencies` 且 `uv lock --check` 通过。
13. full pytest、Ruff、Pyright、compileall、lock 与 diff gates 全部通过。
