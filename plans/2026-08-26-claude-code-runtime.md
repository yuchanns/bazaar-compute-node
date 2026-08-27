# Claude Code Runtime 接入

## 状态

- 当前阶段：Task 1–4 已实现并提交，PR #48 正在 review；进入 review hardening，生产修复尚未开始。
- 实施分支：`feat/claude-agent-sdk-runtime`。
- 基线：`main@cf85c34dd472f091eb893a0bb0c2f2b5dccb3391`（v0.1.29）。
- 已提交实现：`a297128`、`70d3a51`、`54916ce`、`d7961a2`；PR 为 `#48`。
- 全部实现与 review hardening 保留在同一个分支，最终组成一个变更集。
- Task 严格串行执行；每完成一个 Task 都停下来等待 review，review 通过后才进入下一个 Task。
- PR 已由 Hanchin 授权并发起；本轮先更新计划，生产修复、commit、push、merge、发布和部署分别等待后续明确指令。

## 目标

1. 新增直接驱动外部 `claude` executable 的 Runtime adapter，使 BCN 可以通过现有 `IRuntime` contract 运行 Claude Code session。
2. 每个 `RuntimeSession` 持有一个长生命周期 Claude Code process，支持 session 启停、turn streaming、运行中 steer、tool approval、background task 状态和进程重启后的 session resume/reconcile。
3. 保持 provider-neutral core contract 不变；Claude 特有的 process、protocol、client 和 event mapping 封装在 `src/bazaar_compute_node/contrib/claude/`。
4. Claude Code 由节点独立安装，BCN package 不依赖或内嵌 `claude-agent-sdk` 和 Claude Code binary。
5. 保持 BCN Runtime 子进程环境正向白名单约束，child environment 精确来自现有 Runtime composition。
6. 通过 runtime entry point、测试和 README Runtime 支持条目完成组合接入。

## 已确认依据

- 当前 `IRuntime` 已提供 `start_session`、`reconcile_session`、`start_turn`、`steer_turn`、`has_background_job` 和 `stop_session`；Task 6 将原本只承载 context expiry 的 runtime lifecycle receive contract 扩展为 provider-neutral event stream，使 adapter 还能上报某个 runtime session 的 background-idle edge。
- `RuntimeSession.provider_thread_id` 可以持久化 Claude session ID；BCN turn/session correlation 不依赖 provider 自身提供 turn ID。
- `SessionOrchestrator` 已定义 steer 返回 `False` 时的排队行为，也允许 reconcile 返回 idle，因此 Claude adapter 不需要修改 orchestration。
- Claude Code streaming mode 公开提供 `--input-format stream-json`、`--output-format stream-json`、partial messages、explicit session ID 和 `--resume`；这组参数不需要 `-p` 即可进入 JSONL mode。
- Claude Code 的 permission control messages 是 Agent SDK 使用的 stdio protocol；direct adapter 以官方 SDK 源码为 reference，将所需子集封装在 `process.py`、`protocol.py`、`client.py` 和 contract tests 中。
- compatibility reference 固定为 `claude-agent-sdk==0.2.144` 和其内嵌 Claude Code `2.1.239`；BCN 要求 external CLI `>=2.1.239`，确保本计划使用的 terminal reason、message origin、task lifecycle 和 sandbox fields 已存在。BCN 启动每个 connection 时使用该 session 的精确 environment 检查 executable version，并验证 protocol initialization，不静默兼容未知 wire shape。
- streaming stdin 可以维持动态输入流，因此 active turn 的新增 inbound 可以写入同一 process 实现真实 steer。
- `AskUserQuestion` 需要结构化答案，不能映射为 BCN 的二元 `ApprovalResult`；通过 CLI `--disallowedTools AskUserQuestion` 不向模型暴露该工具，adapter 不接收或专门处理该 tool request。

## 组合边界

生产逻辑位于：

```text
src/bazaar_compute_node/contrib/claude/
├── __init__.py
├── approval.py
├── client.py
├── events.py
├── plugin.py
├── process.py
├── protocol.py
└── runtime.py
```

组合与交付文件：

- `pyproject.toml`：只新增 `claudecode` runtime entry point，不新增 Python dependency 或 extra。
- `tests/contrib/test_claude.py`：只覆盖不依赖外部 provider 的 command construction、framing、queue/state 和 mapping 纯逻辑，不使用 fake/mock process 或 provider transcript。
- `tests/e2e/test_claude_runtime.py`：标记 `pytest.mark.e2e`，使用真实外部 Claude Code 覆盖 process、protocol、session、turn、steer、approval 和 reconcile。
- `README.md`：只在现有 Runtime 支持列表或表格加入 Claude 条目。

`app/`、storage schema、command contract、Channel adapter 和 `uv.lock` 继续排除在本计划外。Task 5 对 Core 的唯一扩展是移除 orchestration steer 对 `provider_turn_id is not None` 的错误前置条件：RUNNING turn 是否可 steer 由既有 `IRuntime.steer_turn() -> bool` contract 决定。Task 6 经 review 后扩展 provider-neutral runtime lifecycle event contract；foreground turn 结束时若 runtime 仍报告 background work，orchestrator 不启动 idle timer，background 集合随后从非空变为空时由 owning provider process 上报 exact runtime-session event，Core 重新检查该 session 仍为 IDLE 且 background 为空后启动完整 idle timeout。

所有涉及外部 Claude 的测试与验收不使用 fake、mock、stub、httptest、替代 executable/process、生成的 provider transcript 或 production test injection；subprocess/session/turn/steer/approval/reconcile 场景都归类到 `tests/e2e/` 并驱动 PATH 中真实安装且已认证的 `claude >= 2.1.239`。缺少 executable、认证或 provider connectivity 时明确报告验收阻塞，不以 skip 计为通过。测试使用自然输入并断言 protocol/state/correlation invariant，不断言模型的精确回答文本。provider-independent 的 Core orchestration contract 继续使用现有 test-support Runtime 做确定性状态验证，不伪造 Claude protocol 或 transcript。

## 固定 SDK 源码参考结果

参考版本固定为 Python Agent SDK `0.2.144` / bundled Claude Code `2.1.239`，对应模块为：

- `_internal/transport/subprocess_cli.py`：argv、version check、1 MiB line bound、stdout/stderr framing、write serialization 和 process close escalation；其 bounded message queue 不沿用。
- `_internal/query.py`：initialize、outbound/inbound control correlation、permission callback、task lifecycle 和 message queue cleanup。
- `_internal/message_parser.py` 与公开 `types.py`：user/assistant/system/result/stream/task envelope fields 和 terminal metadata。
- `client.py`：persistent client 的 user-message envelope、query/receive split 和 connection reuse。

BCN 只实现以下已确认的 Core 所需行为。

### Invocation reference

基础 argv 顺序为：

```text
claude
--output-format stream-json
--verbose
--append-system-prompt <bcn-developer-instructions>
--model <model>                         # when configured
--permission-prompt-tool stdio
--permission-mode default
--disallowedTools AskUserQuestion
--session-id=<uuid>                    # fresh session
--resume=<provider-thread-id>          # recovered session, mutually exclusive
--settings <json>
--include-partial-messages
--effort <effort>                      # when configured and supported
--input-format stream-json
```

- 不添加 `-p`；`--input-format stream-json` 与 `--output-format stream-json` 直接进入 SDK-style bidirectional mode。
- `--resume` / `--session-id` 使用 equals form，避免 optional-value flag injection。
- process 通过 argv 直接启动，不经过 shell。
- BCN 不设置 `CLAUDE_CODE_ENTRYPOINT=sdk-py` 或 `CLAUDE_AGENT_SDK_VERSION`，因为它不是 SDK host；streaming behavior 由 argv 决定。
- 与 Codex Runtime 保持一致：`Runtime.start()` 只进入 started lifecycle，不解析 executable 也不启动 child；`start_session` / `reconcile_session` 打开 connection 时才用 daemon PATH 解析真实 `claude`，然后使用该 session 的白名单 environment 执行 `claude --version`，要求版本 `>=2.1.239`，随后 connection 必须完成 initialize control handshake。

### Transport reference

- stdin/stdout 是 UTF-8 NDJSON；一个 JSON object 对应一行并以 `\n` 结束。
- stdout 按 chunk 读取后自行 frame line，单个 complete/pending line 上限为 1 MiB；超过上限立即成为 protocol error。
- blank line 与不以 `{` 开头的 CLI diagnostic line 跳过；以 `{` 开头但 JSON decode 失败的完整行是 protocol error。EOF 的残缺 JSON tail 不当作有效 message，并与 process exit 一起形成 unknown terminal。
- stdout reader 到 Client router 与 Codex `JsonlProcessSupervisor` 一致使用 unbounded queue；router 持续 drain connection，control messages 独立处理，尚未被 BCN turn adopt 的 provider-injected output 保持 connection-level。SDK reference 的 100-item bounded business queue 不适合 BCN 在 foreground result 后仍保持 process、允许 background 跨 turn 长期运行的 persistent-session lifecycle，因此不沿用。
- 所有 stdin write 经过一个 connection write lock；与 Codex 一样，底层 `_write_message()` 不接收或嵌套 timeout，每个公开 Client operation 只在最外层建立一次 `asyncio.timeout()`，完整覆盖等待 write lock、ready/process-exit check、write、drain，以及 control operation 的 matching response wait。
- stderr 始终 drain，按行保存有界 tail；stderr handler 失败不终止 reader。
- close 顺序固定为：停止新 write -> 关闭 stdin -> 等待 graceful exit -> terminate -> kill -> reap。SDK reference 每段使用 5 秒；BCN 按 caller 提供的剩余 timeout 划分相同三段，不使用无界 wait。
- reader failure 会唤醒全部 pending control requests，并向 active turn stream 注入一个 typed transport/protocol failure；cleanup 在 cancellation path 仍执行。
- Client reader 是 connection lifetime 内唯一 router，任何时候都持续消费 stdout。control correlation 独立处理；Claude streaming-input 会把 host 提交的 human turn 与 session 自行注入的 turn 串行输出，injected turn 以 `user.origin.kind != "human"` 开始，以同 origin 的 `result` 结束，中间 assistant/stream/system envelope 属于该完整 injected turn。router 按这个 provider turn boundary 跟踪当前 provider lane；`local_agent` / `local_workflow` lifecycle 同时更新 background state。没有 BCN foreground sink 时，injected output 保持 connection-level；若 channel inbound 在 injected lane 运行时创建新的 BCN turn，`start_turn` 在同一 connection state lock 内 attach fresh sink，并把 initial human input 写成对现有 injected provider turn 的 steer，此后输出进入该 BCN stream。injected lane 的 non-human result 不结束已 attach 的 BCN turn，后续 human-origin result 才按 `pending_human_results` 收敛它。不停止 process、不等待 background 完成。
- 已知 envelope 的 routing/version/result shape 发生 protocol error，或收到 `conversation_reset` 时，当前 turn 产生唯一 unknown terminal，同时从 Runtime registry 摘除并 bounded stop owning connection；该 process 不再供下一 turn 复用。

### Outbound user 与 steer envelope

初始 turn 和 steer 使用同一 envelope；`session_id` 是 SDK streaming lane，固定为 `"default"`，不等同于 Claude conversation UUID：

```json
{
  "type": "user",
  "message": {"role": "user", "content": "<canonical-input>"},
  "parent_tool_use_id": null,
  "session_id": "default",
  "origin": {"kind": "human"}
}
```

- `start_turn` 与 confirmed `steer_turn` 在同一个 connection state lock 下写入，并递增 `pending_human_results`。
- SDK `client.py` 的最小 string-query envelope 到 `session_id` 为止；BCN 明确增加 Claude Code message types 已定义的 `origin={"kind":"human"}`，使 result correlation 不依赖 origin 缺失的兼容分支。
- reader 收到 `result.origin` 缺失或 `kind == "human"` 时递减该计数；非-human origin 是 background/scheduled/channel/peer injected turn 的 terminal，由 connection router 收敛该 injected lane，不进入或终止 BCN foreground turn。
- human result 只有在计数减为零时才成为 BCN terminal。若 result 先拿到 state lock，turn 先 terminal，随后 steer 返回 `False`；若 steer write 先 confirmed，前一个 result 不会提前关闭 BCN turn。
- connection 采用 SDK persistent-client 路径：每个 result 后保持 stdin/process 可用；不采用 one-shot query path 的 end-input behavior。

### Control handshake 与 correlation

BCN 发送最小 initialize request：

```json
{
  "type": "control_request",
  "request_id": "req_<counter>_<random>",
  "request": {"subtype": "initialize", "hooks": null}
}
```

CLI success response：

```json
{
  "type": "control_response",
  "response": {
    "subtype": "success",
    "request_id": "req_<counter>_<random>",
    "response": {}
  }
}
```

- outbound request ID 是单 connection monotonic counter 加随机 suffix；pending map 在 response、timeout、reader failure、cancellation 和 close 时清理。
- `control_response.response.subtype == "error"` 使用其 `error` 作为确定 provider failure；success 只返回内层 `response` object。
- CLI 发 `control_cancel_request` 时，以其 `request_id` 取消 matching inbound handler；handler 不再写 response。

### Permission control reference

CLI 的 inbound request shape：

```json
{
  "type": "control_request",
  "request_id": "<cli-request-id>",
  "request": {
    "subtype": "can_use_tool",
    "tool_name": "<tool-name>",
    "input": {},
    "tool_use_id": "<tool-use-id>",
    "permission_suggestions": [],
    "agent_id": null,
    "blocked_path": null,
    "decision_reason": null,
    "title": null,
    "display_name": null,
    "description": null
  }
}
```

BCN approved response 保留原 tool input，不持久化 CLI permission updates：

```json
{
  "type": "control_response",
  "response": {
    "subtype": "success",
    "request_id": "<cli-request-id>",
    "response": {"behavior": "allow", "updatedInput": {}}
  }
}
```

BCN rejected/timeout response：

```json
{
  "type": "control_response",
  "response": {
    "subtype": "success",
    "request_id": "<cli-request-id>",
    "response": {"behavior": "deny", "message": "<reason>"}
  }
}
```

- Core `ApprovalRequest.request_id` 使用 non-empty `tool_use_id`，wire response correlation 仍使用外层 CLI request ID。
- handler 内部异常返回 `control_response.response.subtype="error"`；turn/session cancellation 取消 handler 并清理 state。

### Message、result 与 background-task reference

- consumer-visible message types 为 `user`、`assistant`、`system`、`result`、`stream_event`、`rate_limit_event` 和 `conversation_reset`；unknown top-level type 保留 debug metadata 后跳过，缺失 routing-required fields 则成为 protocol error。
- `assistant.message.content` 是 block list，Core 使用 `text`、`thinking`、`tool_use` 和 `tool_result`；server-side tool blocks 按 generic tool progress 保留 raw metadata。
- `stream_event.event` 是 raw Anthropic API stream event；text/thinking/input-json delta 从这里生成 Core delta，最终 `assistant` message 不重复发送已经 emitted 的 text。
- `result` 的必需 fields 为 `subtype`、`duration_ms`、`duration_api_ms`、`is_error`、`num_turns` 和 `session_id`；可选保留 `stop_reason`、`total_cost_usd`、`usage`、`result`、`modelUsage`、`permission_denials`、`deferred_tool_use`、`errors`、`api_error_status`、`terminal_reason` 和 `origin`。
- `is_error=true` 或 provider error fields -> failed；`terminal_reason` 为 `aborted_streaming` / `aborted_tools` -> cancelled；`deferred_tool_use` -> failed with `error_kind="provider_deferred"`；其余 human-origin success result -> completed。
- CLI 在 error result 后可能以 exit code 1 退出；已收到的 error result 是权威 terminal，不再被后续 generic process exit 覆盖为 unknown。没有 result 的 non-zero exit/EOF 才是 unknown。
- background tracking 只采纳 SDK reference 的 delegated work：`system/task_started` 且 `task_type` 为 `local_agent` 或 `local_workflow` 时加入 ID；`task_notification` 或 `task_updated.patch.status` 为 `completed`、`failed`、`stopped`、`killed` 时移除。background shell 不进入该集合，避免永不 terminal 的 process 让状态永久为 busy。

### Sandbox reference result

`workspace-write` 使用 command-line `--settings` JSON，并要求 sandbox unavailable 时 fail closed：

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "autoAllowBashIfSandboxed": true,
    "allowUnsandboxedCommands": true
  }
}
```

- Claude sandbox 默认只允许 cwd 子树写入；不增加 workspace 外 `filesystem.allowWrite`。
- `network_access=true` 不附加 domain deny policy；新 domain 仍走 Claude permission/control flow。
- `network_access=false` 在 sandbox 中增加 `network.allowedDomains=[]`，并在 settings `permissions.deny` 加入 `WebFetch`、`WebSearch`。sandboxed Bash 自动允许；命令需要退出 sandbox 时进入同一 stdio permission flow，不由 adapter 额外 deny。
- 自动 sandbox/permission policy 先行裁决；只有自动策略无法决定时才请求 human approval，human decision 是最终裁决。批准 unsandboxed fallback 代表操作者显式允许该次命令超出自动 workspace/network containment，adapter 不覆盖该决定。
- `danger-full-access` 使用 `sandbox.enabled=false` 和 `permission-mode=bypassPermissions`，同时移除 filesystem sandbox 与 tool permission prompts。
- native platform 无法启动 requested sandbox 时，`workspace-write` session startup 失败，不静默降级为 unsandboxed execution。

## Runtime 设计

### External executable 与 process

- 只解析 PATH 中的真实 `claude`；constructor 不提供 executable/process 替换入口，生产代码和测试都不增加环境覆盖或 test seam。
- 与 Codex Runtime 一样，`Runtime.start()` 不做 `which`；每次打开 connection 时用 daemon PATH 解析真实 `claude`，并将该次解析结果用于 version probe 和该 connection child。不由 version probe child 继承 daemon environment；connection 启动前使用 `environment_for_session(session)` 执行 `claude --version`，拒绝低于 `2.1.239` 或无法解析的版本，并在 protocol initialization 中核对/记录实际 Claude Code version。
- 每个 RuntimeSession 启动一个 cwd 指向该 Agent workspace 的 Claude Code child，stdin/stdout 使用 newline-delimited JSON，stderr 独立 drain 并保留有界 tail。
- child environment 直接使用 Runtime composition 提供的正向白名单 mapping，不合并 daemon `os.environ`。
- fresh connection 使用 `--session-id=<uuid>`；recovered connection 使用 `--resume=<provider_thread_id>`。所有可选值使用不会被解析为额外 flag 的 argv 形式。
- process supervisor 负责 spawn、并发安全写入、stdout/stderr drain、exit observation 和 stop escalation；process failure 只终止 owning RuntimeSession。

### Protocol 与 client

- 使用 Agent SDK `0.2.144` 的 `_internal/transport/subprocess_cli.py` 作为 command construction、stdout line framing、stderr drain、version check 和 process cleanup reference；message queue lifecycle 与 Codex Runtime 对齐。
- 使用 `_internal/query.py` 作为 initialize/permission control correlation reference，使用 `_internal/message_parser.py` 和公开 message types 作为 provider envelope reference。
- 启动 argv 与 reference 保持同形：`claude --output-format stream-json --verbose ... --input-format stream-json`，并按 session options 加入 partial messages、stdio permission prompt、session ID/resume、system prompt、model 和 settings；不添加 `-p`。
- `protocol.py` 在 provider boundary 验证 user/system/assistant/result/stream/control envelopes，保留未知内容字段但拒绝无法安全路由的 envelope。
- `client.py` 负责 user input、control request/response correlation、permission response 和 initialization handshake。
- request ID 由 connection 本地生成；pending control future 在 response、timeout、process exit 和 cancellation 时确定清理。
- Claude Code protocol 差异集中在 `protocol.py` / `client.py`，Runtime 不直接拼装 JSON；SDK source 仅作为行为 reference，BCN package 不 import、vendor 或安装 SDK。

### Session lifecycle

- `_Connection` 保存 process supervisor、client、Claude session ID、当前 approval handler、active BCN turn ID 和 active background task IDs。
- fresh session 在 spawn 前生成 UUID session ID，写入 `RuntimeSession.provider_thread_id`。
- recovered session 以 persisted provider thread ID 重新启动 process；reconcile 完成 protocol initialization 后返回 idle。进程崩溃前未完成的 BCN turn 保持现有 unknown 语义，不伪造 terminal。
- `stop_session` 先结束输入、等待正常退出，再按 timeout 执行 terminate/kill；重复 stop 保持幂等。
- `environment_variable_names` 只声明 Claude-owned 的通用配置、认证选择和网络入口：`CLAUDE_CONFIG_DIR`、`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、`CLAUDE_CODE_USE_BEDROCK`、`CLAUDE_CODE_USE_VERTEX`、`CLAUDE_CODE_USE_FOUNDRY`、`CLAUDE_CODE_USE_MANTLE`、`SSL_CERT_FILE`、`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY`。AWS、GCP、Azure 等 provider-specific credential/project/region 变量不做隐式扩散，必须由 Agent `env_include` 显式加入。

### Sandbox 与 system prompt

- `workspace-write` 通过 Claude Code settings JSON 启用 sandbox、在 sandbox 不可用时 fail closed，并按 BCN `network_access` 生成自动 network policy；需要 unsandboxed fallback 的命令进入 stdio approval，human decision 为最终裁决。
- `danger-full-access` 关闭 Claude sandbox，并使用 `bypassPermissions` 跳过 stdio tool approval。
- 使用 Claude Code 默认 system prompt，通过 `--append-system-prompt` 注入 `DeveloperInstructionContext.render()` 的 BCN instructions。
- settings、system prompt 和 model 作为独立 argv values 传入，不经过 shell。

### Turn、steer 与 event stream

- `start_turn` 向长连接 process 写入初始 canonical user message，并返回只消费该 turn event 的异步 stream。
- `steer_turn` 仅在同一 connection 存在 active turn 时写入新的 canonical user message 并返回 `True`；不存在 active turn 时返回 `False`，交由现有 orchestrator 排队。
- Claude system/assistant/result/partial stream envelopes 映射为 provider-neutral `RuntimeEvent` / `StreamEvent`；文本、thinking、tool use、tool result、usage、terminal result 和 provider errors 保留可用字段。
- Claude stream-json 没有 Codex 式 universal `turnId`；保持 `provider_turn_id=None`，不制造不稳定 identity。改用 Claude 自身的 streaming-input turn provenance：BCN outbound 明确标记 `origin={"kind":"human"}`，session-injected `user` / `result` 携带 non-human origin，且其间输出按 wire order 属于同一个 injected turn。router 由此跟踪 provider lane；无 channel inbound 时 injected turn 留在 connection 层，有 inbound 时新的 BCN turn adopt 该 lane 并以 initial input steer，行为等价于对 provider 已运行 turn 的 steer。
- 收到 human-origin result 后在 connection state lock 下递减 `pending_human_results`；只在计数归零时收敛当前 BCN turn stream 并清理 active turn state。process 和 stdin 保持可用于同一 RuntimeSession 的下一 turn。
- `local_agent` / `local_workflow` task start/notification/update envelopes 按固定 terminal status 集合更新 active background task IDs；`has_background_job` 读取该集合。
- foreground terminal 时若 background task set 非空，Core 不启动 idle timer；set 后续从非空变为空时产生 exact runtime-session background-idle event。下一次 inbound 仍可复用该 connection；Core 串行处理迟到 event，并在启动 timer 前重新确认 current binding、IDLE state 与 background 空集合。

### Approval bridge

- stdio `can_use_tool` control request 转发给 `_Connection` 当前 turn 的 BCN approval handler。
- foreground human result 后不立即移除该 handler：它作为该 connection 的 latest human approval context 保留，供随后由其 background subagent completion 触发的 injected turn 继续完成 stdio control；下一 BCN turn 原子替换 handler，session stop/connection retirement 取消全部 inflight request。这样 background 跨 turn 不会因 `no active turn handler` 被 provider-side 中断。
- 每个 tool request 使用 Claude `tool_use_id` 作为 provider request correlation，映射 tool name、input 和可读说明；BCN allow/deny 映射为 Claude permission control response。
- approval 等待遵守 BCN timeout、turn cancellation 和 session shutdown；结束后清理 pending state，避免 callback 悬挂。
- `AskUserQuestion` 由 process argv 显式禁用，不进入 approval bridge，adapter 不实现该工具的 deny 或回退分支。
- deferred tool result 映射为明确的非成功 terminal/provider status，不把尚未执行的 tool 当作完成。

## Core contract 对应关系

### Lifecycle 与 session contract

| Core contract | Claude Code SDK-style streaming 实现 | 结果边界 |
| --- | --- | --- |
| `IAsyncLifecycle.start(timeout)` | 与 Codex Runtime 一样，只进入 started lifecycle；不解析 executable，不提前创建 session process，也不启动 version probe。 | executable 和版本/protocol floor 在 `start_session` / `reconcile_session` 打开 connection 时校验；重复 start 幂等。 |
| `IAsyncLifecycle.stop(timeout)` | 禁止新 session/turn，关闭所有 live process stdin，等待退出，并在剩余 timeout 内 terminate/kill。 | 只释放 Claude Runtime 自己拥有的 process/task；传播 caller cancellation；重复 stop 幂等。 |
| `name` | 固定返回 `"claudecode"`。 | 与 runtime entry point 同名，用于 registry、audit 和 persisted `RuntimeSession.runtime`；`"claude"` 只是外部 CLI executable 名。 |
| `environment_variable_names()` | 按上节固定顺序返回 `CLAUDE_CONFIG_DIR`、`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、四个 `CLAUDE_CODE_USE_*` provider switch、`SSL_CERT_FILE` 与三项 proxy 变量；provider-specific 云凭据/项目/region 由 `env_include` 显式加入。 | `process.py` 只接收 `environment_for_session(session)` 的结果，不读取或合并 ambient environment；不自动透传整个 `AWS_*`、`GOOGLE_*` 或 `AZURE_*` family。 |
| `receive_event()` | 阻塞读取 adapter-owned lifecycle queue。Claude task lifecycle 在 delegated background set 从非空变为空时写入 `RuntimeBackgroundIdle`；Claude CLI 没有 Codex app-server 的 global context-change notification。 | 不把单个 child death写入该 queue。active child death 由 turn stream 产出 unknown；idle child death让下一次 `start_turn` 抛 `RuntimeSessionUnavailable` 并由现有 orchestration 重建 session。 |
| `start_session(session, timeout)` | 创建 workspace，生成 UUID；先用 session environment probe version，再启动不带 `-p` 的 `claude --output-format stream-json --verbose ... --input-format stream-json` process，使用 `--session-id=<uuid>`，发送 initialization control request 并等待 response。 | handshake confirmed 后把 UUID 写入 `provider_thread_id`；首次 user input 尚未写入。version/spawn/init 确定失败返回 `FAILED`，超时或 write outcome 不可判定返回 `UNKNOWN`。 |
| `reconcile_session(session, turn, approval_handler, timeout)` | 用 session environment probe version，再使用 `--resume=<provider_thread_id>` 启动新的 streaming process 并完成 initialization。 | 没有 active turn 时 confirmed `IDLE`；daemon/process 已丢失的 active turn 无法恢复原 stdout stream，返回 confirmed `IDLE` 让现有 orchestration 保留该 turn 的 unknown 结果并允许后续新 turn。 |
| `stop_session(session, timeout)` | 从 registry 摘除 connection，关闭 stdin 并 bounded wait，必要时 terminate/kill，清理 control futures、approval 和 background-task state。 | process 已不存在时仍 confirmed；成功回收后返回原 `RuntimeSession`；caller cancellation 传播。 |

### Turn、stream 与 approval contract

| Core contract | Claude Code SDK-style streaming 实现 | 结果边界 |
| --- | --- | --- |
| `start_turn(session, turn, input_text, approval_handler, timeout)` | 校验 connection idle 后，把 canonical input 编码为 stream-json user envelope 写入 stdin，绑定本 turn 的 approval handler，并返回 `ClaudeTurnEventStream`。 | write 前发现 process unavailable 抛 `RuntimeSessionUnavailable`，让 orchestrator 安全重建 session；write 后 outcome 不明由 stream 产出 terminal unknown。 |
| `IRuntimeTurnStream.__anext__()` | 持续读取该 connection 的 assistant/system/stream/result/control envelopes；control request 内联处理，业务 envelope 映射为 `RuntimeEvent` 或 `StreamEvent`。 | 每个 turn 首先产出 started，恰好产出一个 terminal；protocol/transport 无法判定时 terminal state 为 `UNKNOWN`。 |
| `IRuntimeTurnStream.aclose()` | 停止该 consumer、解绑 approval handler 并清理 stream-local state，不直接关闭可复用的 session process。 | process lifecycle 仍由 stop_session/Runtime stop 管理；重复 close 幂等。 |
| `steer_turn(session, turn, input_text, timeout)` | active turn 匹配时向同一 stdin 写新的 stream-json user envelope。 | write confirmed 返回 `True`；没有 matching active turn、process 已退出或无法安全写入时返回 `False`，现有 orchestrator 把 inbound 留给下一 turn。 |
| `has_background_job(session, timeout)` | 读取 connection 中由 `local_agent` / `local_workflow` task start/notification/update envelopes 维护的 active task ID set。 | 纯内存查询；connection 不存在返回 `False`；timeout/cancellation 遵守 core call boundary。 |
| `IApprovalHandler.request_approval()` bridge | Claude `can_use_tool` control request 转成 `ApprovalRequest`，以 `tool_use_id` 作为 core `request_id`；decision 转成 matching stdio control response。 | approved -> allow，rejected/timeout -> deny；turn/session cancellation 终止等待并清理 correlation。`AskUserQuestion` 已由 argv 禁用，不进入 bridge。 |

### Provider-neutral event mapping

| Claude stream-json envelope/content | Core output |
| --- | --- |
| confirmed initial user-envelope write | adapter 合成 `RuntimeEvent(event_name="claudecode.turn.started", state=STARTED)`；process initialization `system` envelope 属于 session handshake，不充当 turn start。 |
| non-turn `system` / rate-limit envelope | nonterminal `RuntimeEvent` 或 metadata update；不改变 `pending_human_results`。 |
| assistant text `content_block_delta.text_delta` | `StreamEvent(kind=AGENT_MESSAGE_DELTA)`。 |
| assistant thinking delta | `StreamEvent(kind=REASONING_TEXT_DELTA)`；可用 summary 内容映射为 `REASONING_SUMMARY_DELTA`。 |
| tool-use start/input delta | `StreamEvent(kind=COMMAND_INTERACTION)`；非 command tool 使用 `ITEM_PROGRESS`，并以 `tool_use_id` 作为 `stream_id`。 |
| current human turn 的 tool result/progress | `TOOL_PROGRESS` 或 `ITEM_PROGRESS`。 |
| task lifecycle / 尚未被 BCN turn adopt 的 non-human-origin injected turn | connection router 更新 active background set 并跟踪 injected lane；没有 foreground owner 时不发送 Channel turn event。 |
| successful human-origin `result` with more pending inputs | `StreamEvent(kind=TURN_PROGRESS)` 并递减 `pending_human_results`，不提前 terminal。 |
| successful human-origin `result` with no pending input | terminal `RuntimeEvent(state=COMPLETED, event_name="claudecode.turn.completed")`，usage/cost/session ID 放入 metadata。 |
| non-human-origin `result` | 收敛 injected provider lane；即使该 lane 已被新 BCN turn adopt，也不结束其 active foreground stream。 |
| provider-declared error `result` | terminal `RuntimeEvent(state=FAILED, error_kind="provider_failed")`。 |
| malformed envelope、stdout EOF、process exit 或 result 缺失 | terminal `RuntimeEvent(state=UNKNOWN, error_kind="provider_unknown")`，附安全错误摘要和有界 stderr tail。 |

所有 Core `StreamEventKind` 的映射是封闭的；没有可靠 Claude source 的 kind 不伪造：

| Core `StreamEventKind` | Claude source / policy |
| --- | --- |
| `AGENT_MESSAGE_DELTA` | assistant `text_delta`。 |
| `PLAN_DELTA` | Claude stream-json 没有独立 plan channel；不产生。 |
| `REASONING_SUMMARY_DELTA` | 仅在 provider 明确给出 summary block/delta 时产生，不从普通 thinking 截断合成。 |
| `REASONING_TEXT_DELTA` | assistant `thinking_delta`。 |
| `COMMAND_OUTPUT_DELTA` | `Bash` tool result/output 的增量或可识别 output block。 |
| `COMMAND_INTERACTION` | `Bash` tool-use start/input delta。 |
| `FILE_CHANGE_UPDATE` | `Edit` / `Write` tool-use 与对应 result。 |
| `TOOL_PROGRESS` | server-side tool progress、tool result 与 delegated-task notification。 |
| `ITEM_PROGRESS` | 其他 tool-use block 与 provider content block progress。 |
| `TURN_PROGRESS` | 非 terminal human result、non-human result、rate-limit/status progress。 |

- `conversation_reset` 没有可安全复用的 provider conversation identity：active turn 产出 `UNKNOWN` terminal，connection 标记 unavailable；idle connection 只标记 unavailable，下一次 `start_turn` 走 `RuntimeSessionUnavailable` 重建路径。
- unknown top-level envelope 只写 debug log 并跳过，不作为用户可见 metadata；已知 envelope 缺失 routing-required fields 才是 protocol error。

### Runtime options 对应关系

| Core/context value | Claude Code streaming 参数或设置 |
| --- | --- |
| `RuntimeCommandContext.agent_name`、`bot_name()`、`agent_id`、RuntimeSession/workspace context | `DeveloperInstructionContext.render()` 后作为 `--append-system-prompt` 的单独 argv value。 |
| `RuntimeCommandContext.run_command` | 不使用；persistent stdio protocol 必须由 adapter-owned supervisor 直接管理 argv、pipes 与 lifecycle，不能降级为 one-shot command callback。 |
| `RuntimeCommandContext.startup_timeout_seconds` | 作为 executable version probe、spawn 和 initialize handshake 的总 startup budget；各阶段只消费剩余时间。 |
| `RuntimeCommandContext.client_info` | Claude initialize protocol 没有对应 public field；不伪造 SDK entrypoint/version marker。BCN identity 只进入 appended developer instructions。 |
| `runtime_options["model"]` | `--model <value>`。 |
| `runtime_options["effort"]` | 配置存在时使用 `--effort <value>`；最低支持版本已固定为 reference CLI `2.1.239`。 |
| `sandbox_mode=workspace-write` | settings 中使用 `enabled=true`、`failIfUnavailable=true`、`autoAllowBashIfSandboxed=true`、`allowUnsandboxedCommands=true`；自动策略未决定时由 human approval 最终裁决。 |
| `sandbox_mode=danger-full-access` | settings 中使用 `sandbox.enabled=false`，argv 使用 `--permission-mode bypassPermissions`。 |
| `network_access=true` | 不附加 domain deny；Claude sandbox 对新 domain 的 permission request 进入 BCN approval bridge。 |
| `network_access=false` | `network.allowedDomains=[]` + deny `WebFetch` / `WebSearch`；unsandboxed fallback 仍进入 human approval，不由 adapter 强制覆盖人工决定。 |
| `environment_for_session(session)` | 原样作为 child `env` mapping；不经过 shell，不追加 daemon environment。 |

## 串行任务

### Task 1：Process、protocol foundation 与 session lifecycle

修改范围：

- `pyproject.toml`
- `src/bazaar_compute_node/contrib/claude/__init__.py`
- `src/bazaar_compute_node/contrib/claude/plugin.py`
- `src/bazaar_compute_node/contrib/claude/process.py`
- `src/bazaar_compute_node/contrib/claude/protocol.py`
- `src/bazaar_compute_node/contrib/claude/client.py`
- `src/bazaar_compute_node/contrib/claude/runtime.py`
- `tests/contrib/test_claude.py`
- `tests/e2e/test_claude_runtime.py`

实现内容：

1. 添加 `claudecode` runtime entry point，不增加 dependency。
2. 以 SDK `SubprocessCLITransport` 为 reference，实现 no-`-p` command construction、executable version check、严格环境白名单 spawn、JSONL framing、process lifecycle 和有界 stderr capture；business queue 与 Codex 一致使用 unbounded queue。
3. 以 SDK `Query` / message parser 为 reference，实现 provider envelope validation、initialization handshake 和 control correlation foundation。
4. 实现 `start_session`、idle/active-turn `reconcile_session`、blocking `receive_expire`、`stop_session`、provider session ID 持久化、sandbox/system prompt options 和 Runtime stop。
5. 使用真实 `claude` process 覆盖 fresh session、resume/reconcile、实际 initialization response、session `system` message、严格环境白名单、sandbox fail-closed 和三段式 clean stop；通过真实 process signal/exit observation 验证 child exit，不替换 executable 或制造 provider transcript。
6. command construction、1 MiB framing boundary 和 control cleanup 只测试 adapter 自身的确定性纯逻辑；process/protocol compatibility 结论必须由同一 Task 的真实 CLI 验收确认，不能由构造 transcript 代替。

验证：

```bash
uv run pytest tests/contrib/test_claude.py
uv run pytest -m e2e tests/e2e/test_claude_runtime.py
uv run ruff format --check src/bazaar_compute_node/contrib/claude tests/contrib/test_claude.py
uv run ruff format --check tests/e2e/test_claude_runtime.py
uv run ruff check src/bazaar_compute_node/contrib/claude tests/contrib/test_claude.py tests/e2e/test_claude_runtime.py
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

完成 Task 1 后停止，提交 diff 给 Hanchin review；review 通过前不进入 Task 2。

### Task 2：Turn lifecycle、steer 与 event mapping

修改范围：

- `src/bazaar_compute_node/contrib/claude/client.py`
- `src/bazaar_compute_node/contrib/claude/runtime.py`
- `src/bazaar_compute_node/contrib/claude/events.py`
- `tests/contrib/test_claude.py`
- `tests/e2e/test_claude_runtime.py`

实现内容：

1. 实现 persistent streaming input、`start_turn`、`IRuntimeTurnStream` 和 active-turn `steer_turn`，使用 state lock + `pending_human_results` 解决 result/steer race。
2. 按固定 message/result reference 映射 origin、system/assistant/result envelopes、partial stream、usage 和 provider/process errors，保证每个 stream 恰好一个 terminal。
3. 只跟踪 `local_agent` / `local_workflow` 的 start/notification/terminal update，并实现 `has_background_job`。
4. 使用真实、已认证的 Claude session 和自然输入覆盖 start -> stream -> terminal、运行中 steer、delegated background task lifecycle、idle/active child exit 和 authoritative provider error；测试只断言观察到的 wire/state invariant，不固定模型文本。result/steer lock ordering、event dispatch 和 terminal uniqueness 的确定性并发逻辑在同一真实 stream observation 上施加调度压力，不用替代 process 重放顺序。

验证沿用 Task 1 命令，并显式运行 `uv run pytest -m e2e tests/e2e/test_claude_runtime.py`。完成后停止等待 review。

### Task 3：Approval 与 protocol compatibility

修改范围：

- `src/bazaar_compute_node/contrib/claude/approval.py`
- `src/bazaar_compute_node/contrib/claude/client.py`
- `src/bazaar_compute_node/contrib/claude/runtime.py`
- `tests/contrib/test_claude.py`
- `tests/e2e/test_claude_runtime.py`

实现内容：

1. 实现 per-turn stdio permission request bridge 和 tool-use correlation。
2. 按固定 wire shape 映射 allow/deny、timeout、`control_cancel_request` 和 session shutdown cleanup；allow 必须回传原始 `updatedInput`，deny 必须携带 provider-visible message。
3. 完成 deferred tool result 分类，以及 permission/control envelope 不兼容时的 provider-unknown boundary。
4. 覆盖批准后继续、拒绝后继续、approval 中断，以及 resumed session 的下一 turn approval。

上述 approval 场景使用真实 Claude tool request 和真实 stdio control round trip；自然输入要求 Claude 执行受控的 workspace 操作来触发 permission，不直接调用 adapter 内部 callback 伪造 provider request，也不精确匹配模型措辞。

验证：

```bash
uv run pytest tests/contrib/test_claude.py
uv run pytest -m e2e tests/e2e/test_claude_runtime.py
uv run ruff format --check src/bazaar_compute_node/contrib/claude tests/contrib/test_claude.py tests/e2e/test_claude_runtime.py
uv run ruff check src/bazaar_compute_node/contrib/claude tests/contrib/test_claude.py tests/e2e/test_claude_runtime.py
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

完成后停止等待 review。

### Task 4：支持情况与全量验收

修改范围：

- `README.md`
- Task 1–3 已修改的 Claude adapter/test 文件，仅处理 review 或验收暴露的问题。

实现内容：

1. README diff 严格限定为在现有 Runtime 支持列表或表格中加入 Claude 条目。
2. 执行全仓测试与质量门禁，修复本特性引入的失败。

验证：

```bash
uv run pytest
uv run pytest -m e2e tests/e2e/test_claude_runtime.py
uv run ruff format --check .
uv run ruff check .
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

完成 Task 4 后停止，提交完整分支 diff 和验收结果给 Hanchin review。

### Task 5：PR review hardening

修改范围：

- `plans/2026-08-26-claude-code-runtime.md`
- `src/bazaar_compute_node/core/orchestration/session.py`
- `src/bazaar_compute_node/contrib/claude/client.py`
- `src/bazaar_compute_node/contrib/claude/events.py`
- `src/bazaar_compute_node/contrib/claude/process.py`
- `src/bazaar_compute_node/contrib/claude/runtime.py`
- `tests/contrib/test_claude.py`
- `tests/contrib/test_orchestration.py`
- `tests/e2e/test_claude_runtime.py`

实现内容：

1. 将 Claude transport 写入结构与 Codex 对齐：`ProcessSupervisor` 只负责无 timeout 参数的 serialized write；`Client` 的 user/control/response operation 各自在最外层建立一个 deadline，覆盖 lock acquisition、write/drain 与所需 response wait，不嵌套或逐层传递 timeout。
2. 将 SDK-reference 的 100-item business queue 改为与 Codex 一致的 unbounded connection input，并让 Client reader 成为 connection lifetime 内唯一 router。按 Claude streaming-input 的 origin boundary 跟踪完整 provider turn：BCN human lane 进入 active foreground sink；non-human-origin `user` 开始、同 origin `result` 结束 background-task notification、scheduled/channel/peer injected lane。无 BCN owner 时 injected output 留在 connection 层；channel inbound 创建新 BCN turn 时，`start_turn` 在 connection state lock 内原子 adopt 当前 injected lane并将 initial input 作为 provider steer，后续 non-human result 不 terminal BCN stream，human result 才 terminal。不得等待 background 完成或重建 connection。真实 E2E 必须覆盖 A 启动 delegated subagent 后 terminal、B 在 injected lane 运行时到达并 steer/adopt、B 完成、background set 最终清空且 child PID 全程不变。
3. 修正 Core steer eligibility：`_steer_active_turn()` 只要求 matching `RuntimeTurnState.RUNNING`，不再要求 provider turn ID；调用既有 runtime `steer_turn()` 后由其 bool 结果记录 accepted/not accepted。使用现有 test-support Runtime 新增确定性 orchestration contract test，证明 `provider_turn_id=None` 的 RUNNING turn 仍会调用 runtime steer，同时保留 Codex 既有测试与行为；该测试不声称验证 Claude provider protocol。
4. routing-required field、CLI version、result shape 或 `conversation_reset` 形成 protocol terminal 时，从 Runtime registry 摘除 matching connection 并 bounded stop child；`start_turn` 不得复用 poisoned connection。普通成功/失败/cancelled provider result 仍只结束当前 turn，保持正常 persistent connection。
5. 保留已确认的 sandbox 行为：`allowUnsandboxedCommands=true`，自动 policy 无法决定时进入 stdio permission flow，human approval 是最终裁决；同步修正本计划内全部旧描述，不按该条 review 修改生产代码。
6. 清理不合规 E2E：全部用户 prompt 改为带业务上下文的自然请求，不指定 `Bash` / `Task` 等工具名、具体 shell command 或“只用某工具”；删除 `_RecordingApprovalHandler` / `_BlockingApprovalHandler` 驱动的伪 channel approval 场景。需要 approval 或 channel inbound 的 provider runtime E2E 必须启动真实 orchestration，并用 `TestChannel` 作为控制面注入 inbound、观察 stream/audit、提供 human decision；Claude/Kimi process 仍是真实 provider。pure protocol mapping 继续只覆盖无需 provider/channel 替身的确定性逻辑。
7. 删除 `/proc/<pid>/environ` 的 Linux-only 反向断言和固定假设 sandbox unavailable 的环境依赖测试；不以平台分支、fake capability 或“成功/失败均通过”的弱断言替代。保留真实跨平台 session/process lifecycle 验收，以及已有 production construction 对环境白名单和 sandbox settings 的正向约束。
8. 保留 foreground 后 background 存在时不启动 idle timer、background 后续完成不主动重启 timer 的有意语义；除第 3 项 steer eligibility 外不修改 Core，也不修改 Codex runtime，并在对应 review thread 说明 idle comment 不成立。

验证：

```bash
uv run pytest tests/contrib/test_claude.py
uv run pytest -m e2e tests/e2e/test_claude_runtime.py
uv run pytest -m 'not e2e' -q
uv run ruff format --check .
uv run ruff check .
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

自动化 approval 验收通过真实 orchestration + `TestChannel` 控制面批准、拒绝并让一条等待中的审批超时，确认 Claude tool request、BCN approval request、human decision、provider-visible allow/deny 与 turn terminal 全链路；不得直接向 runtime 注入测试 approval handler。完成 Task 5 后停止，提交 Plan 与代码 diff、自动门禁和 live E2E 结果给 Hanchin review；未获指令不 commit/push。

### Task 6：background-idle lifecycle event

修改范围：

- `plans/2026-08-26-claude-code-runtime.md`
- `src/bazaar_compute_node/core/runtime.py`
- `src/bazaar_compute_node/core/orchestration/session.py`
- `src/bazaar_compute_node/contrib/claude/runtime.py`
- `src/bazaar_compute_node/contrib/codex/runtime.py`
- `tests/support/src/bcn_test_support/runtime.py`
- `tests/core/test_ports.py`
- `tests/contrib/test_claude.py`
- `tests/contrib/test_codex.py`
- `tests/contrib/test_orchestration.py`
- `tests/e2e/test_claude_runtime.py`
- `tests/contrib/test_codex.py`

实现内容：

1. 新增 provider-neutral `RuntimeBackgroundIdle(runtime_session_id)`，与 `RuntimeExpire` 组成 `RuntimeLifecycleEvent`；将只接收 expire 的 Runtime port/consumer 改名为 `receive_event()`。`RuntimeExpire` 保持 global context-change 语义并扩散到该 Agent 的全部 live sessions，`RuntimeBackgroundIdle` 只投递给 ID 相同的 current runtime session，不能影响同一 provider process registry 中的其他 session。
2. Core 将 background-idle event 放入目标 session 已有的 runtime queue 串行处理。handler 必须重新解析 current runtime binding，丢弃旧 runtime-session event，并调用现有 idle-timer gate；只有 session 此刻仍为 `IDLE`、未过期且 `has_background_job()` 再次返回 `False` 时才启动完整 idle timeout。event 与 turn terminal、inbound 或 expiry 竞态时由同一 queue 收敛，重复 event 只重置为一个当前 timer binding。
3. Claude connection 继续由唯一 Client router 观察 SDK-reference task lifecycle。仅当受支持的 `local_agent` / `local_workflow` active ID set 确实从非空变为空时，向该 Runtime 的 lifecycle queue 写入 owning runtime-session event；未知 task notification、重复 terminal、单个 task 完成但集合仍非空都不发送。
4. Codex 在 POSIX 上复用同一 app-server process 的 notification router 观察属于 owning thread 的 `item/completed(commandExecution)`，不消费 turn stream 所需 notification。`has_background_job()` 和完成观察均通过 `thread/backgroundTerminals/list` 更新 per-connection observed state；完成观察按 connection 串行刷新，只有先前观察到非空且本次确认为空才发送 event。旧 connection、provider query failure 与 Windows 不发送；Windows 继续保持 background-conservative 行为。
5. 确定性测试覆盖 Core exact-session routing、旧 binding、WORKING/IDLE gate、background 复检、重复 edge，以及 Claude 多 task transition 和 Codex per-connection nonempty-to-empty refresh。测试支持 Runtime 直接发布 lifecycle event，不为 production 添加测试 hook。
6. 分别使用真实 Claude/Kimi 与 Codex provider、真实 orchestration 和 `TestChannel` 做 E2E。自然业务输入要求启动短暂后台工作并先返回前台确认；验证 foreground terminal 时同一 child PID 仍因 background 存活、background 结束后 lifecycle event 命中原 runtime session、idle timer 到期回收该 PID。测试通过 TestChannel 注入 inbound、处理 approval 和观察 audit/output，不直接驱动 runtime 或 fake approval handler。

验证：

```bash
uv run pytest tests/core/test_ports.py tests/contrib/test_claude.py tests/contrib/test_codex.py tests/contrib/test_orchestration.py
uv run pytest -m e2e tests/e2e/test_claude_runtime.py tests/contrib/test_codex.py
uv run pytest -m 'not e2e' -q
uv run ruff format --check .
uv run ruff check .
uv run scripts/pyright_lsp_check.py --outputjson .
uv run python -m compileall -q src tests
uv lock --check
git diff --check
```

完成 Task 6 后停止，提交 Plan、代码 diff、确定性门禁和两个 provider 的 live E2E 结果给 Hanchin review；获准后 commit/push。

## 完成条件

- `claudecode` runtime 可通过同名 entry point 加载，BCN Python package 不安装、import 或 vendor Claude Agent SDK，也不携带 Claude Code binary。
- fresh 与 resumed RuntimeSession 均可连接外部 Claude Code，Claude session ID 稳定保存于 `provider_thread_id`。
- start、stream、steer、approval、background task、stop 和 reconcile 均满足现有 `IRuntime` contract。
- Claude child 只收到 BCN 正向白名单环境，sandbox/network/system prompt 映射有测试覆盖。
- Claude protocol error、process exit、permission deny/timeout、deferred result 和 crashed active turn 均有确定的 provider-neutral result。
- protocol failure 后 owning connection 不再复用；connection router 可保留或由新 BCN turn adopt 跨-turn injected provider lane，且 non-human result 不误终止 foreground；`provider_turn_id=None` 的 RUNNING turn 仍可从 Channel 路径 steer；每次 stdin operation 的单一 deadline 覆盖 write lock 与完整 I/O。
- 全量 pytest、Ruff、Pyright、compileall、lock 和 diff check 通过。
- README 的 Runtime 支持情况包含 Claude。
