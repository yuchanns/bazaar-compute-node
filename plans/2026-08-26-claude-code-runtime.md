# Claude Code Runtime 接入

## 状态

- 当前阶段：设计已按 SDK-reference direct Claude Code streaming 方案完成 review，implementation 尚未开始。
- 实施分支：`feat/claude-agent-sdk-runtime`。
- 基线：`main@cf85c34dd472f091eb893a0bb0c2f2b5dccb3391`（v0.1.29）。
- 本次设计变更只新增本计划；依赖、生产代码、测试和产品文档均未修改。
- 全部实现保留在同一个分支，最终组成一个变更集。
- Task 严格串行执行；每完成一个 Task 都停下来等待 review，review 通过后才进入下一个 Task。
- 本计划已授权 commit 和 push；不授权 implementation、PR、发布或部署。

## 目标

1. 新增直接驱动外部 `claude` executable 的 Runtime adapter，使 BCN 可以通过现有 `IRuntime` contract 运行 Claude Code session。
2. 每个 `RuntimeSession` 持有一个长生命周期 Claude Code process，支持 session 启停、turn streaming、运行中 steer、interrupt、tool approval、background task 状态和进程重启后的 session resume/reconcile。
3. 保持 provider-neutral core contract 不变；Claude 特有的 process、protocol、client 和 event mapping 封装在 `src/bazaar_compute_node/contrib/claude/`。
4. Claude Code 由节点独立安装，BCN package 不依赖或内嵌 `claude-agent-sdk` 和 Claude Code binary。
5. 保持 BCN Runtime 子进程环境正向白名单约束，child environment 精确来自现有 Runtime composition。
6. 通过 runtime entry point、测试和 README Runtime 支持条目完成组合接入。

## 已确认依据

- 当前 `IRuntime` 已提供 `start_session`、`reconcile_session`、`start_turn`、`steer_turn`、`interrupt_turn`、`has_background_job` 和 `stop_session`，无需扩展 core API。
- `RuntimeSession.provider_thread_id` 可以持久化 Claude session ID；BCN turn/session correlation 不依赖 provider 自身提供 turn ID。
- `SessionOrchestrator` 已定义 steer 返回 `False` 时的排队行为，也允许 reconcile 返回 idle，因此 Claude adapter 不需要修改 orchestration。
- Claude Code streaming mode 公开提供 `--input-format stream-json`、`--output-format stream-json`、partial messages、explicit session ID 和 `--resume`；这组参数不需要 `-p` 即可进入 JSONL mode。
- Claude Code 的 permission/interrupt control messages 是 Agent SDK 使用的 stdio protocol；direct adapter 以官方 SDK 源码为 reference，将所需子集封装在 `process.py`、`protocol.py`、`client.py` 和 contract tests 中。
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
- `tests/e2e/test_claude_runtime.py`：标记 `pytest.mark.e2e`，使用真实外部 Claude Code 覆盖 process、protocol、session、turn、steer、interrupt、approval 和 reconcile。
- `README.md`：只在现有 Runtime 支持列表或表格加入 Claude 条目。

`core/`、`app/`、storage schema、command contract、Channel adapter 和 `uv.lock` 明确排除在本计划外，也不列入未来扩展。若实施中发现这些边界不足，停止该 feature，不提出扩大 Core 或 contract 的后续方案。

测试与验收不使用 fake、mock、stub、httptest、替代 executable/process、生成的 provider transcript 或 production test injection。所有涉及外部 Claude 的 subprocess/session/turn/steer/interrupt/approval/reconcile 场景都归类到 `tests/e2e/` 并驱动 PATH 中真实安装且已认证的 `claude >= 2.1.239`；缺少 executable、认证或 provider connectivity 时明确报告验收阻塞，不以 skip 计为通过。测试使用自然输入并断言 protocol/state/correlation invariant，不断言模型的精确回答文本。

## 固定 SDK 源码参考结果

参考版本固定为 Python Agent SDK `0.2.144` / bundled Claude Code `2.1.239`，对应模块为：

- `_internal/transport/subprocess_cli.py`：argv、version check、1 MiB line bound、stdout/stderr framing、write serialization 和 process close escalation。
- `_internal/query.py`：initialize、outbound/inbound control correlation、permission callback、interrupt、task lifecycle 和 message queue cleanup。
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
- stdout reader 到 runtime event queue 使用 100-item bounded buffer；control messages 在 reader 内消费，不占用 turn event stream buffer。
- 所有 stdin write 经过一个 connection write lock；ready/process-exit check 与 write 在同一 critical section，避免 close/write TOCTOU。
- stderr 始终 drain，按行保存有界 tail；stderr handler 失败不终止 reader。
- close 顺序固定为：停止新 write -> 关闭 stdin -> 等待 graceful exit -> terminate -> kill -> reap。SDK reference 每段使用 5 秒；BCN 按 caller 提供的剩余 timeout 划分相同三段，不使用无界 wait。
- reader failure 会唤醒全部 pending control requests，并向 active turn stream 注入一个 typed transport/protocol failure；cleanup 在 cancellation path 仍执行。

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
- reader 收到 `result.origin` 缺失或 `kind == "human"` 时递减该计数；非 human origin 是 background/scheduled/channel injected turn，不终止 BCN turn。
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

Interrupt 使用同一个 request/response correlation：

```json
{
  "type": "control_request",
  "request_id": "req_<counter>_<random>",
  "request": {"subtype": "interrupt"}
}
```

control success 只确认 interrupt 已被 CLI 接受；BCN 继续读到 `result.terminal_reason` 为 `"aborted_streaming"` 或 `"aborted_tools"` 才产生 cancelled terminal。

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
    "allowUnsandboxedCommands": false
  }
}
```

- Claude sandbox 默认只允许 cwd 子树写入；不增加 workspace 外 `filesystem.allowWrite`。
- `network_access=true` 不附加 domain deny policy；新 domain 仍走 Claude permission/control flow。
- `network_access=false` 在 sandbox 中增加 `network.allowedDomains=[]`，并在 settings `permissions.deny` 加入 `WebFetch`、`WebSearch`。sandboxed Bash 自动允许；任何退回 permission flow 的 Bash request 由 adapter 直接 deny，因此不能通过 channel approval 绕过 network/sandbox boundary。
- `danger-full-access` 使用 `sandbox.enabled=false`；tool permission control 仍启用，不等于 `bypassPermissions`。
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

- 使用 Agent SDK `0.2.144` 的 `_internal/transport/subprocess_cli.py` 作为 command construction、stdout line framing、bounded buffer、stderr drain、version check 和 process cleanup reference。
- 使用 `_internal/query.py` 作为 initialize/interrupt/permission control correlation reference，使用 `_internal/message_parser.py` 和公开 message types 作为 provider envelope reference。
- 启动 argv 与 reference 保持同形：`claude --output-format stream-json --verbose ... --input-format stream-json`，并按 session options 加入 partial messages、stdio permission prompt、session ID/resume、system prompt、model 和 settings；不添加 `-p`。
- `protocol.py` 在 provider boundary 验证 user/system/assistant/result/stream/control envelopes，保留未知内容字段但拒绝无法安全路由的 envelope。
- `client.py` 负责 user input、control request/response correlation、interrupt request、permission response 和 initialization handshake。
- request ID 由 connection 本地生成；pending control future 在 response、timeout、process exit 和 cancellation 时确定清理。
- Claude Code protocol 差异集中在 `protocol.py` / `client.py`，Runtime 不直接拼装 JSON；SDK source 仅作为行为 reference，BCN package 不 import、vendor 或安装 SDK。

### Session lifecycle

- `_Connection` 保存 process supervisor、client、Claude session ID、当前 approval handler、active BCN turn ID 和 active background task IDs。
- fresh session 在 spawn 前生成 UUID session ID，写入 `RuntimeSession.provider_thread_id`。
- recovered session 以 persisted provider thread ID 重新启动 process；reconcile 完成 protocol initialization 后返回 idle。进程崩溃前未完成的 BCN turn 保持现有 unknown 语义，不伪造 terminal。
- `stop_session` 先结束输入、等待正常退出，再按 timeout 执行 terminate/kill；重复 stop 保持幂等。
- `environment_variable_names` 只声明 Claude-owned 的通用配置、认证选择和网络入口：`CLAUDE_CONFIG_DIR`、`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_BASE_URL`、`CLAUDE_CODE_USE_BEDROCK`、`CLAUDE_CODE_USE_VERTEX`、`CLAUDE_CODE_USE_FOUNDRY`、`CLAUDE_CODE_USE_MANTLE`、`SSL_CERT_FILE`、`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY`。AWS、GCP、Azure 等 provider-specific credential/project/region 变量不做隐式扩散，必须由 Agent `env_include` 显式加入。

### Sandbox 与 system prompt

- `workspace-write` 通过 Claude Code settings JSON 启用 sandbox、禁止 unsandboxed command，并按 BCN `network_access` 生成 network policy。
- `danger-full-access` 关闭 Claude sandbox，但 stdio tool approval 仍然生效。
- 使用 Claude Code 默认 system prompt，通过 `--append-system-prompt` 注入 `DeveloperInstructionContext.render()` 的 BCN instructions。
- settings、system prompt 和 model 作为独立 argv values 传入，不经过 shell。

### Turn、steer 与 event stream

- `start_turn` 向长连接 process 写入初始 canonical user message，并返回只消费该 turn event 的异步 stream。
- `steer_turn` 仅在同一 connection 存在 active turn 时写入新的 canonical user message 并返回 `True`；不存在 active turn 时返回 `False`，交由现有 orchestrator 排队。
- Claude system/assistant/result/partial stream envelopes 映射为 provider-neutral `RuntimeEvent` / `StreamEvent`；文本、thinking、tool use、tool result、usage、terminal result 和 provider errors 保留可用字段。
- Claude 没有独立 provider turn ID 时保持 `provider_turn_id=None`，不制造不稳定 identity。
- 收到 human-origin result 后在 connection state lock 下递减 `pending_human_results`；只在计数归零时收敛当前 BCN turn stream 并清理 active turn state。process 和 stdin 保持可用于同一 RuntimeSession 的下一 turn。
- `interrupt_turn` 发送 interrupt control request 后继续 drain，直到 interrupted result 或 transport error，再关闭当前 turn stream。
- `local_agent` / `local_workflow` task start/notification/update envelopes 按固定 terminal status 集合更新 active background task IDs；`has_background_job` 读取该集合。

### Approval bridge

- stdio `can_use_tool` control request 转发给 `_Connection` 当前 turn 的 BCN approval handler。
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
| `receive_expire()` | Claude CLI 没有 Codex app-server 的 global context-change notification；实现为阻塞读取 adapter-owned queue，当前没有 producer。 | 不把单个 child death 写入该 queue，因为 orchestrator 会把一次 `RuntimeExpire` 扩散到该 Runtime 的全部 sessions。active child death 由 turn stream 产出 unknown；idle child death 让下一次 `start_turn` 抛 `RuntimeSessionUnavailable` 并由现有 orchestration 重建 session。 |
| `start_session(session, timeout)` | 创建 workspace，生成 UUID；先用 session environment probe version，再启动不带 `-p` 的 `claude --output-format stream-json --verbose ... --input-format stream-json` process，使用 `--session-id=<uuid>`，发送 initialization control request 并等待 response。 | handshake confirmed 后把 UUID 写入 `provider_thread_id`；首次 user input 尚未写入。version/spawn/init 确定失败返回 `FAILED`，超时或 write outcome 不可判定返回 `UNKNOWN`。 |
| `reconcile_session(session, turn, approval_handler, timeout)` | 用 session environment probe version，再使用 `--resume=<provider_thread_id>` 启动新的 streaming process 并完成 initialization。 | 没有 active turn 时 confirmed `IDLE`；daemon/process 已丢失的 active turn 无法恢复原 stdout stream，返回 confirmed `IDLE` 让现有 orchestration 保留该 turn 的 unknown 结果并允许后续新 turn。 |
| `stop_session(session, timeout)` | 从 registry 摘除 connection，关闭 stdin 并 bounded wait，必要时 terminate/kill，清理 control futures、approval 和 background-task state。 | process 已不存在时仍 confirmed；成功回收后返回原 `RuntimeSession`；caller cancellation 传播。 |

### Turn、stream 与 approval contract

| Core contract | Claude Code SDK-style streaming 实现 | 结果边界 |
| --- | --- | --- |
| `start_turn(session, turn, input_text, approval_handler, timeout)` | 校验 connection idle 后，把 canonical input 编码为 stream-json user envelope 写入 stdin，绑定本 turn 的 approval handler，并返回 `ClaudeTurnEventStream`。 | write 前发现 process unavailable 抛 `RuntimeSessionUnavailable`，让 orchestrator 安全重建 session；write 后 outcome 不明由 stream 产出 terminal unknown。 |
| `IRuntimeTurnStream.__anext__()` | 持续读取该 connection 的 assistant/system/stream/result/control envelopes；control request 内联处理，业务 envelope 映射为 `RuntimeEvent` 或 `StreamEvent`。 | 每个 turn 首先产出 started，恰好产出一个 terminal；protocol/transport 无法判定时 terminal state 为 `UNKNOWN`。 |
| `IRuntimeTurnStream.aclose()` | 停止该 consumer、解绑 approval handler 并清理 stream-local state，不直接关闭可复用的 session process。 | process lifecycle 仍由 interrupt/stop_session/Runtime stop 管理；重复 close 幂等。 |
| `steer_turn(session, turn, input_text, timeout)` | active turn 匹配时向同一 stdin 写新的 stream-json user envelope。 | write confirmed 返回 `True`；没有 matching active turn、process 已退出或无法安全写入时返回 `False`，现有 orchestrator 把 inbound 留给下一 turn。 |
| `interrupt_turn(session, turn, timeout)` | 写入 `{"type":"control_request","request_id":...,"request":{"subtype":"interrupt"}}`，等待 matching control response，再由 event stream drain 到 interrupted result。 | response confirmed 后返回 cancelled `RuntimeTurn`；明确拒绝/失败返回 `FAILED`；request 已写但 response 不可判定返回 `UNKNOWN`。 |
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
| tool result/progress/task notification | `TOOL_PROGRESS` 或 `ITEM_PROGRESS`；background task envelope 同时更新 active task set。 |
| successful human-origin `result` with more pending inputs | `StreamEvent(kind=TURN_PROGRESS)` 并递减 `pending_human_results`，不提前 terminal。 |
| successful human-origin `result` with no pending input | terminal `RuntimeEvent(state=COMPLETED, event_name="claudecode.turn.completed")`，usage/cost/session ID 放入 metadata。 |
| non-human-origin `result` | background/injected turn progress；不结束 BCN active turn。 |
| interrupted/aborted `result` | terminal `RuntimeEvent(state=CANCELLED, event_name="claudecode.turn.interrupted")`。 |
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
| `sandbox_mode=workspace-write` | settings 中使用 `enabled=true`、`failIfUnavailable=true`、`autoAllowBashIfSandboxed=true`、`allowUnsandboxedCommands=false`。 |
| `sandbox_mode=danger-full-access` | settings 中使用 `sandbox.enabled=false`；stdio approval 仍保持启用。 |
| `network_access=true` | 不附加 domain deny；Claude sandbox 对新 domain 的 permission request 进入 BCN approval bridge。 |
| `network_access=false` | `network.allowedDomains=[]` + deny `WebFetch` / `WebSearch`；任何 fallback Bash permission request 由 adapter 直接 deny。 |
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
2. 以 SDK `SubprocessCLITransport` 为 reference，实现 no-`-p` command construction、executable version check、严格环境白名单 spawn、JSONL framing、bounded buffer、process lifecycle 和有界 stderr capture。
3. 以 SDK `Query` / message parser 为 reference，实现 provider envelope validation、initialization handshake 和 control correlation foundation。
4. 实现 `start_session`、idle/active-turn `reconcile_session`、blocking `receive_expire`、`stop_session`、provider session ID 持久化、sandbox/system prompt options 和 Runtime stop。
5. 使用真实 `claude` process 覆盖 fresh session、resume/reconcile、实际 initialization response、session `system` message、严格环境白名单、sandbox fail-closed 和三段式 clean stop；通过真实 process signal/exit observation 验证 child exit，不替换 executable 或制造 provider transcript。
6. command construction、1 MiB framing boundary、100-item queue 和 control cleanup 只测试 adapter 自身的确定性纯逻辑；process/protocol compatibility 结论必须由同一 Task 的真实 CLI 验收确认，不能由构造 transcript 代替。

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
3. 实现 `interrupt_turn` request-and-drain、turn state cleanup 和 process disconnect handling。
4. 只跟踪 `local_agent` / `local_workflow` 的 start/notification/terminal update，并实现 `has_background_job`。
5. 使用真实、已认证的 Claude session 和自然输入覆盖 start -> stream -> terminal、运行中 steer、interrupt request/response + aborted result、delegated background task lifecycle、idle/active child exit 和 authoritative provider error；测试只断言观察到的 wire/state invariant，不固定模型文本。result/steer lock ordering、event dispatch 和 terminal uniqueness 的确定性并发逻辑在同一真实 stream observation 上施加调度压力，不用替代 process 重放顺序。

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
2. 按固定 wire shape 映射 allow/deny、timeout、`control_cancel_request`、interrupt 和 session shutdown cleanup；allow 必须回传原始 `updatedInput`，deny 必须携带 provider-visible message。
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

## 完成条件

- `claudecode` runtime 可通过同名 entry point 加载，BCN Python package 不安装、import 或 vendor Claude Agent SDK，也不携带 Claude Code binary。
- fresh 与 resumed RuntimeSession 均可连接外部 Claude Code，Claude session ID 稳定保存于 `provider_thread_id`。
- start、stream、steer、interrupt、approval、background task、stop 和 reconcile 均满足现有 `IRuntime` contract。
- Claude child 只收到 BCN 正向白名单环境，sandbox/network/system prompt 映射有测试覆盖。
- Claude protocol error、process exit、permission deny/timeout、deferred result 和 crashed active turn 均有确定的 provider-neutral result。
- 全量 pytest、Ruff、Pyright、compileall、lock 和 diff check 通过。
- README 的 Runtime 支持情况包含 Claude。
