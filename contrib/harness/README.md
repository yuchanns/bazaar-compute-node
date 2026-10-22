# bazaar-contrib-harness

Enable the harness provider registry. Install a concrete harness provider separately.

Call a registered builder's `inspect(workspace, timeout=...)` to query availability,
the provider CLI version, models, reasoning efforts and workspace skills before
creating an agent or session. The result is a `HarnessInspection`; its `error`
field reports query failures alongside any metadata already returned.

`SessionInput.approval_policy` uses `ApprovalPolicy.ON_REQUEST` or `NEVER`.
`sandbox_policy` uses `SandboxPolicy.WORKSPACE_WRITE` or `DANGER_FULL_ACCESS`;
`network_access` carries the selected network permission. The upstream agent
selects these policies from the session's Actor. Each provider translates them
into its native protocol and retains them when resuming that session.
Provider configuration reads the fields it uses and ignores additional fields.

Execution calls return `confirmed` or `failed`. A failed call or terminal output
carries `HarnessFailure` with a provider-neutral `reason`, the actual `message`
and an optional original error `code`. `needs_reconciliation` asks the agent to
query and reconnect to the existing execution before deciding its final result.
Recovery and retry errors go to audit/logs; Channel feedback shows the final
failure if no execution provider completes the request.

An approval request offers `allowed_scopes` using `ApprovalScope`, a `StrEnum`
with `ONCE`, `SESSION`, and `ALWAYS`. `ApprovalResult.scope` selects one of those
scopes and defaults to `ONCE`. The decision remains `approved` or `rejected`.
Providers map the selected scope into their own authorization protocol.
In `dangerous_individual` mode, the agent approves every request directly.

```sh
bcn contrib add harness --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-harness~=0.3.0rc1"
[[contribs.enabled]]
entry = "harness-builders"
```
