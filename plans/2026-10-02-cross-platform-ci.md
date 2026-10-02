# 三系统 PR CI

## 目标

PR 先执行独立的类型检查 job，通过后并行运行 Linux、macOS、Windows 的普通测试、系统服务测试和安装包冒烟验证。

## 调研结论

- 现有 `.github/workflows/pull-request.yml` 只有 Ubuntu job，调用 `.github/actions/checks` 后构建并检查 wheel 和源码包；共享 checks action 也用于 release workflow。
- [Soluna nightly workflow](https://github.com/cloudwu/soluna/blob/master/.github/workflows/nightly.yml) 使用独立前置 job，后续 job 通过 `needs` 等待，再以三系统矩阵并行运行，`fail-fast: false` 保留每个平台的执行结果。
- [GitHub job 依赖文档](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds) 规定依赖 job 成功后才启动后续 job；矩阵使用 `ubuntu-latest`、`macos-latest`、`windows-latest`，最多并行三个测试 job。
- 项目要求 Python 3.14，锁文件已有 Windows 条件依赖。外部 Provider 测试归类为 `e2e`，本机系统服务测试按用户确定的分类统一标记 `system`。普通测试使用 `not e2e and not system`，每个系统的测试 job 另用 `pytest -m system` 明确执行全部系统服务测试，失败会阻止该 job 成功。
- `scripts/pyright_lsp_check.py` 对子进程 stdout 管道调用 `select`；[Python 官方文档](https://docs.python.org/3/library/select.html) 明确 Windows 的 `select` 仅支持 socket。因此类型检查及 Ruff 使用 Linux runner。
- Windows runner 提供 Bash；PR workflow 明确使用 Bash，使现有版本输出、环境变量和安装包 glob 命令在三个系统采用同一语法。POSIX 测试使用 `/tmp`，避免 macOS 默认临时目录过长导致 Unix socket 路径超限；Windows 使用 runner 的临时目录。
- [Apple XNU `killpg1` 实现](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_sig.c) 过滤僵尸进程；组内没有可发送信号的进程时，POSIX 路径返回 `EPERM`。仅在 Darwin 的权限异常中读取真实 `ps` 进程组和状态，确认组内没有活进程才按退出处理；活进程的权限错误继续抛出。
- [pytest marker 文档](https://docs.pytest.org/en/stable/how-to/usage.html#specifying-which-tests-to-run) 说明 `-m` 按标记选择测试；在 `pyproject.toml` 注册 `system`，在系统服务测试模块设置统一 `pytestmark`，命令行表达式覆盖项目的默认筛选。普通测试和服务测试分别执行，收集结果验证两组边界。
- 三个平台的 system 测试都要求 `CI=true` 的隔离 runner，fixture 在调用原生命令前检查；本地仅运行普通测试和静态检查，真实服务验证交给三系统 CI。
- Linux runner 通过 `loginctl enable-linger` 和 `user@UID.service` 启用真实用户服务管理器，并把用户 bus 地址传给独立的 system 测试步骤。临时 unit 名称与正式服务隔离。
- macOS 通过实际 `launchctl print` 查找可用用户域，再 bootstrap 唯一 Label 的真实 job，结束时 bootout；[Apple launchctl 讨论](https://developer.apple.com/forums/thread/16206) 说明域与 service target 的对应关系，CI 使用本机 `launchctl` 验证。
- [Microsoft schtasks 文档](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks) 说明 `/Run` 使用注册时保存的程序和账号立即启动任务。Windows system 测试实际启动 bcn，等待健康端点就绪并验证临时数据库，然后实际停止并删除任务；CI 中服务启动失败会使测试失败。
- Windows 退出验证读取 `tasklist` 的真实 PID 查询结果；环境文件验证使用平台实际语法，保留 token 更新与其他变量。Windows 环境变量不区分大小写，采用两个不同名字；POSIX 保留仅大小写不同的两个名字。POSIX 权限位检查限定到 POSIX 系统。
- Windows 首轮普通测试全部通过，但 SQLite 广播测试缺少 `storage.stop()`，会让退出清理遇到仍打开的数据库文件；在该测试的 finally 中关闭真实 storage，释放文件后由原清理流程删除。

## Task 1：前置类型检查与三系统测试

- [x] 将 PR workflow 拆为独立 `typecheck` job、Ruff checks job 和三系统 tests 矩阵；后两者通过 `needs: typecheck` 等待，测试矩阵并行三个 job，`fail-fast: false`。
- [x] 三个测试 job 各自安装锁定依赖、依次运行 `pytest -m "not e2e and not system"` 和 `pytest -m system`、构建 wheel/sdist 并执行安装包冒烟验证。
- [x] 检查 workflow 语法，执行 Ruff 和仓库根目录 Pyright LSP，并用 GitHub 三系统 runner 验证实际测试及包安装结果；根据实际失败修正平台问题。

## 验证结果

- actionlint、全仓库 Ruff lint/format 和根目录 Pyright LSP 已通过；LSP 检查 347 个文件，零诊断。
- [三系统 GitHub CI](https://github.com/yuchanns/bazaar-compute-node/actions/runs/37010460774) 全部成功。普通测试：Linux 583 passed、1 skipped；macOS 582 passed、2 skipped；Windows 572 passed、12 skipped。每个平台均排除 49 个外部 Provider e2e 和 2 个 system 测试。
- 独立的 `pytest -m system` 步骤实际运行原生管理器和真实节点：Linux 2 passed，macOS 和 Windows 各 1 passed、1 skipped。三个平台均执行真实服务启动、健康检查、临时数据库读写、清除终端目录后恢复管理路径、配置及 token 写回原注册文件。跳过的仅为 Linux 专属的数字 UID 测试。
- 类型检查通过后，三个测试 job 并行执行；三个平台的 wheel/sdist 构建和两种安装包冒烟验证均成功。
- 临时目录与节点路径解析保持一致，Windows 测试结束时真实 SQLite 连接全部关闭，临时文件和服务均完成清理。

Task 1 完成，当前停止点为 review。
