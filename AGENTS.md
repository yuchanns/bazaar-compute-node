1. 禁止编写 normalize 或者同等含义的方法. 正常内联即可
2. 禁止封装无意义的只使用一次的小函数片段
3. 必须使用 ruff 进行代码格式检查
4. 在没有计划的情况下，新增一个功能，必须经过讨论后编写 plans/{your_plan}/plan.md. 不要写非目标,
   不要写反向计划, 不做的事情不用写出来. 编写 plan 时禁止把参考推迟到执行时, 必须在 plan
   中明确参考调研后的结论和实现细节
5. 必须按照 plan 的 tasks 串行开发，每完成一个 task 都停下来等待 review
6. 代码修改完成后，必须在仓库根目录执行 `uv run --quiet scripts/pyright_lsp_check.py .`，并确保检查通过
7. 禁止为了方便测试而修改生产代码加入不必要的注入函数或者可覆盖的环境变量
8. 当被要求发送 diff 时，默认只发送业务代码，排除 test 与 lock 相关改动，直接通过消息发送，不使用附件，并放在 code fence 中
9. 发送较大的 diff 时，每条消息的 code fence 控制在 8 KB 以内；大文件按文件或连续 hunk 拆分，保持每条消息独立可读，以保留颜色渲染
10. 项目状态未上线，禁止为虚构场景编写兼容性代码
11. 测试问答必须模拟普通用户的自然问法，并提供足够的业务上下文；禁止在用户消息中精确描述执行步骤、工具调用或工具使用建议
12. 如果作用域内没有分歧, 禁止为变量名添加前缀，例如: 优先使用 `session_id` 而不是 `bcn_session_id`
13. 当变量在后续没有被使用时，优先复用同名, 例如: 不要使用 resolved_xxx = resolved(xxx), 而是使用 xxx = resolved(xxx) 进行变量遮蔽
14. 代码内部禁止无脑添加异常数据校验, 对于输入数据的校验，应当在接口边界使用 pydantic 进行校验
15. 禁止任何反向测试和精确断言, 这种测试毫无意义
16. 禁止使用 fake/mock 测试。真实外部 Provider 依赖归类为 e2e；所有平台的 system-service 测试归类为 system，只在隔离 CI runner 执行，三系统 CI 必须独立执行 `pytest -m system`。
17. Provider runtime 的 e2e 测试必须使用 TestChannel 作为控制面，通过它注入 inbound、观察输出并处理审批；
    禁止绕过 orchestration 直接用测试 approval handler 模拟 Channel 行为
18. 已发布的 migration 属于不可变历史：禁止修改其任何内容（包括注释、空白、名称和 SQL）；schema 变化只能新增 migration。
    禁止修改或绕过 migration checksum 校验，也禁止手工篡改 migration ledger
19. BCN e2e 测试必须使用测试专用配置、临时数据库和隔离进程；禁止安装开发分支覆盖正式 uv tool，禁止启动、停止、
    重启或复用正式 `bcn.service`，禁止连接 `~/.bcn` 的正式数据库、socket 或 Channel
20. 发现额外 BCN 进程时，必须先核对其 PPID、启动来源、配置和用途；人工为故障恢复而启动的临时进程未经操作者明确确认
    禁止终止。运行当前 agent 的 BCN 进程不得由该 agent 自行终止

## 目录职责

项目在独立 workspace 成员中开发；各成员拥有自己的发行元数据、源码、资源与验证。

```text
app/                            # bazaar-compute-node：CLI、启动输入、默认依赖与组合装配
├── src/bazaar_compute_node/     # 应用入口、自举声明、默认组合与 run 命令
└── build_backend.py            # 应用发行资源的构建；从共享锁生成内置模块索引
core/                           # bazaar-core：通用 contrib 运行内核
└── src/bazaar_core/             # 入口发现、声明校验、服务与事件绑定、依赖图和生命周期
contrib/                        # 各功能的独立发行包
└── ...
scripts/                        # workspace 发现、逐包检查、构建与 CI 工具
plans/                          # 已讨论的设计、任务顺序与交付要求
```

功能包的 `tests/` 验证该包的公共行为；真实独立安装验收使用 workspace 外的临时环境。
保持相关契约、实现与资源的内聚性，按独立职责或实际复用拆分文件。

## 依赖与隔离边界

```text
app → core
app → 默认 contrib
contrib → core + 所需功能的公共契约包
可选 contrib → core + 所需功能的公共契约包
```

- core 只处理通用定义、配置模型、typed key、绑定、依赖与生命周期；业务类型、配置字段和功能分支归所属 contrib。
  后续功能先通过已有公共框架装配；需要改 core 时，核对现有接口无法表达的通用能力及已讨论设计。
- app 提供启动输入、默认依赖和组合，调用功能服务；功能模块的业务规则、资源、命令契约和配置归其拥有者。
- contrib 自持实现、第三方依赖与资源，通过声明的服务和事件协作；公共契约归功能包，contrib 不导入 app 或访问 Hub 的内部实例记录。
- 配置载体负责文档存储，版本包负责转换，配置服务负责候选校验、保存和更新请求；manager 消费 CONFIGURATION，
  分别记录包安装、配置保存与运行应用结果，通过公共服务完成操作。
- 服务的可见范围和生命周期由组合的绑定及实例归属表达；各模块在自己的 lifespan 中创建和释放资源。
- 一次应用运行只由入口创建并持有一个 ContribHub，配置自举与所有功能组合共用它。插件通过 offers／accepts
  声明服务，由框架管理依赖、启动、更新与退出；功能模块不得另建宿主、重复配置加载或自行维护依赖图。
- 实现功能前先核对已有代码及已讨论设计的意图，复用现有契约、服务与装配流程。新增参数、状态、分支或抽象
  必须有实际需求和现有接口无法满足的依据，禁止为局部功能另写一套并行流程。
