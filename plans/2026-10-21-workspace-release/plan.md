# workspace 批量发版

## 目标与入口

从 GitHub Actions 中 Release workflow 的 Branch 下拉框选择发行分支，输入一次 `X.Y.Z` 或
`X.Y.ZrcN`，统一准备并发布 app、core 和 contrib/ 下的公开 workspace 成员。
正式版 `X.Y.Z` 必须从 `main` 运行；RC 可以选择其他发行分支。
当前 28 个成员的版本均为 `0.3.0rc1`；首个 RC 为后续独立 provider 提供可下载的正式依赖。
实现位于 `feature/20261021-workspace-release`，通过 PR 合入 `0.3`。

GitHub 要求默认分支已有 `workflow_dispatch` 入口，再允许通过界面的 Branch 选择或
`gh workflow run release.yml --ref 0.3` 运行所选分支的 workflow。本仓库 main 已有该入口，
本次将分支支持与批量发版放在同一特性分支，继续保留单个 version 输入。

参考：[GitHub 手动运行 workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow?tool=webui)。

## 版本、源码和产物

- 复用 `scripts/workspace.py` 的 uv metadata 发现公开成员；虚拟根及 tests/support 是开发资源。
  从成员正式依赖建立发行顺序，包含各平台条件分支，app 在本仓库其余包上传后发布。
- `scripts/release.py` 校验输入版本及各成员的兼容范围，通过 `uv version --package ... --frozen`
  同步成员版本，再更新共享锁。仍接受目标版本的依赖下限和资源兼容范围继续保留；不接受目标版本
  的正式依赖在准备前明确报告，修改真实兼容契约后再发行。
- 发行计划生成在已忽略的 `build/release/<version>.json`，记录发行分支、准备前提交和成员清单，
  在创建版本提交前保存为 Actions artifact。GitHub 使用该分支的预期 HEAD 创建并验证签名的
  `Release v<version>` 提交；源码已经推进时重新准备。版本提交只包含版本、锁和正式版变更日志。
  构建、验收和上传从 artifact 取得计划，全部固定该发行提交。
  源码已经声明目标版本且无需更新变更日志时，直接固定已验证签名的源码提交。
- `uv build --all-packages --no-sources` 一次构建公开成员的 wheel 和 sdist；产物清单记录
  每个包的源码位置、版本、依赖、文件名和 SHA-256，并核对发行元数据及 contrib 入口唯一性。
- GitHub artifact 保留固定产物。重跑相同版本时复用已记录的发行提交和对应 artifact，校验提交、
  清单和文件校验值后继续，上传由 uv 核对已有文件，仅补齐内容相同组合中的缺失项。
  计划恢复先取同分支发行 workflow 的 `release-plan-<version>`，首个已发布 RC 使用现有
  `python-dist-<version>` 内的 manifest 恢复。计划保存后版本提交才开始创建；重试按提交标题和
  唯一父提交找回固定版本提交，再核对 GitHub 验证签名。尚未创建版本提交的计划允许重新准备。

参考：[uv build](https://docs.astral.sh/uv/reference/cli/#uv-build)、
[uv 发布及重复上传](https://docs.astral.sh/uv/guides/publish/)。

## 共用 CI action

Release 与 pull-request workflow 共用 `.github/actions/` 下的 composite action：

- `setup` 统一安装 uv 0.13.0、Python 3.15 与全部 workspace 的锁定依赖，发行后续 job 也复用它。
- `restore-release-plan` 从同分支的发行 Actions artifact 恢复计划，供源码检查和版本准备复用。
- 现有 `checks` 改为接收 `packages`，逐包调用 `scripts/ci.py check`，由两份 workflow 的
  checks job 引用；PR 传入受影响集合，Release 传入全部成员。
- `test` 接收单个 `package`，依次执行普通测试、Linux 用户服务环境准备、独立 system 测试和
  wheel／sdist 验收。三系统矩阵及源码 checkout 由各自 workflow 维护；Windows 符号链接设置
  继续先于 checkout，短 socket 目录由公共测试入口创建。

本地 action 从 checkout 的源码读取；每个脚本步骤明确指定 Bash，参数通过步骤环境传入。
引用方式遵循 [GitHub composite action 文档](https://docs.github.com/en/actions/tutorials/create-actions/create-a-composite-action)
及 [action 元数据语法](https://docs.github.com/en/actions/reference/workflows-and-actions/metadata-syntax)。

## 验收与发布

1. 复用现有 workspace 静态检查、各包的真实测试及平台矩阵；system 测试仅在隔离 CI runner 运行。
2. Linux、macOS、Windows 从同一批候选产物在 workspace 外安装，核对所有公开包的版本和许可证，
   按各包已声明的平台范围运行现有 package smoke，分别验证 wheel 与 sdist。
3. 使用同一个 PyPI environment，通过 uv Trusted Publishing 按依赖顺序上传，每包保存状态；
   将 PyPI 返回的文件 SHA-256 与固定清单比较，再从真实 PyPI 干净安装全体公开成员并运行验收。
4. 完成后仅推送发行提交上的 annotated tag；固定产物清单随分发文件保存在 GitHub Actions artifact。
   RC 使用带 rc 的版本与标签；同一版本重跑继续核对原标签、提交及清单。

首次上传各新项目需要对应的 PyPI Trusted Publisher。仓库 pypi environment 当前没有分支限制。

## 发布分支隔离

`main` 的 0.2 发布与 `0.3` 的 workspace 发布写回各自分支，可以独立运行。
workspace workflow 使用 `pypi-release-${{ github.ref }}` 作为 concurrency group，
保留 `cancel-in-progress: false`。同一分支的不同版本仍串行，保护版本提交的写回顺序；
不同发行分支使用独立组。当前 `main` 的旧 workflow 只允许从 main 发布，使用原有独立组。

GitHub 的 concurrency group 在仓库内共享，可使用 `github.ref` 区分分支。
参考：[GitHub workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)。

## Tasks

- [ ] Task 1：交付可选择分支的 workspace 发版完整流程。同步工具、workflow 和原发行计划；
  两份 workflow 共用环境、静态检查与逐包测试 action；
  用真实候选产物完成隔离安装、相同版本准备及重试验收，通过 Ruff、根目录 LSP 与 PR CI，
  创建目标为 `0.3` 的 PR 后停下 review。
- [x] Task 2：按本次发布复查修正 CI：发行收尾仅推送验证过的 annotated tag，移除 GitHub Release
  及其资产发布步骤；workspace 发布锁按发行分支隔离，完成 actionlint、Ruff、根目录 LSP 和 diff 检查。
- [x] Task 3：从当前源码移除生成的发行计划，改用 Actions artifact 传递与恢复；保留已发布标签和
  提交，验证真实 RC 清单恢复、后续版本准备与固定产物构建，通过静态检查后按本次明确授权直推 `0.3`。
