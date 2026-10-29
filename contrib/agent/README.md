# bazaar-contrib-agent

Configure agents with channel and harness contribs.

```sh
bcn contrib add agent --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-agent==0.3.*"
[[contribs.enabled]]
entry = "agent-execution"
[[contribs.enabled]]
entry = "agent"
[contribs.enabled.config]
agents = []
```

Enable the channel and harness contribs you want to use, then create an agent:

```sh
bcn agent add \
  --name Desk \
  --channel CHANNEL_KIND \
  --harness HARNESS_KIND \
  --set agent.session.config.sandbox.policy=workspace-write
```

Replace `CHANNEL_KIND` and `HARNESS_KIND` with your enabled contrib names.
The default `session` mode gives each conversation a separate session.

Choose a value for `agent.session.config.sandbox.policy`:

| Value | Tool permissions and approval |
| --- | --- |
| `workspace-write` (default) | Write within the workspace; respond to tool approval requests using the channel's approval card. |
| `danger-full-access` | Full access with automatic tool approval. |

For example, give Desk full access:

```sh
bcn agent edit Desk --set agent.session.config.sandbox.policy=danger-full-access
```

To share one session across the agent's conversations, create it with
`dangerous_individual` mode. This mode always uses full access and automatic approval:

```sh
bcn agent add \
  --name Shared \
  --channel CHANNEL_KIND \
  --harness HARNESS_KIND \
  --set agent.mode=dangerous_individual
```

See [agent commands](../agent-command/README.md) for listing, editing, removing
agents and reviewing conversations.
