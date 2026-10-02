# 三系统 PR CI

## 目标

PR 先执行独立的类型检查 job，通过后并行运行 Linux、macOS、Windows 的测试和安装包冒烟验证。

## 调研结论

- 现有 `.github/workflows/pull-request.yml` 只有 Ubuntu job，调用 `.github/actions/checks` 后构建并检查 wheel 和源码包；共享 checks action 也用于 release workflow。
- [Soluna nightly workflow](https://github.com/cloudwu/soluna/blob/master/.github/workflows/nightly.yml) 使用独立前置 job，后续 job 通过 `needs` 等待，再以三系统矩阵并行运行，`fail-fast: false` 保留每个平台的执行结果。
- [GitHub job 依赖文档](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds) 规定依赖 job 成功后才启动后续 job；矩阵使用 `ubuntu-latest`、`macos-latest`、`windows-latest`，最多并行三个测试 job。
- 项目要求 Python 3.14，锁文件已有 Windows 条件依赖；pytest 的 `not e2e` 排除真实外部 Provider 测试，并已有本机通信和隔离子进程测试。
- `scripts/pyright_lsp_check.py` 对子进程 stdout 管道调用 `select`；[Python 官方文档](https://docs.python.org/3/library/select.html) 明确 Windows 的 `select` 仅支持 socket。因此类型检查及 Ruff 使用 Linux runner。
- Windows runner 提供 Bash；PR workflow 明确使用 Bash，使现有版本输出、环境变量和安装包 glob 命令在三个系统采用同一语法。POSIX 测试使用 `/tmp`，避免 macOS 默认临时目录过长导致 Unix socket 路径超限；Windows 使用 runner 的临时目录。

## Task 1：前置类型检查与三系统测试

- [ ] 将 PR workflow 拆为独立 `typecheck` job、Ruff checks job 和三系统 tests 矩阵；后两者通过 `needs: typecheck` 等待，测试矩阵并行三个 job，`fail-fast: false`。
- [ ] 三个测试 job 各自安装锁定依赖、运行 `pytest -m "not e2e"`、构建 wheel/sdist 并执行安装包冒烟验证。
- [ ] 检查 workflow 语法，执行 Ruff 和仓库根目录 Pyright LSP，并用 GitHub 三系统 runner 验证实际测试及包安装结果；根据实际失败修正平台问题。

## 验证结果

待实施后记录本地检查和三系统 CI 结果。
