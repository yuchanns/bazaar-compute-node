# bazaar-contrib-agent

Configure agents and their channel and harness providers.

```sh
bcn contrib add agent --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-agent~=0.3.0rc1"
[[contribs.enabled]]
entry = "agent-execution"
[[contribs.enabled]]
entry = "agent"
[contribs.enabled.config]
agents = []
```

Add an agent with `bcn agent add --name Desk --channel CHANNEL_KIND --harness HARNESS_KIND`.
See [agent commands](../agent-command/README.md) for editing and conversation review.
