# bazaar-contrib-agent-command

Manage agents and approve or deny conversations.

```sh
bcn contrib add agent-command --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-agent-command~=0.3.0rc1"
[[contribs.enabled]]
entry = "agent-command"
```

```sh
bcn agent list
bcn agent add --name Desk --channel CHANNEL_KIND --harness HARNESS_KIND
bcn agent edit Desk --set agent.idle_timeout=300
bcn agent remove Desk
bcn agent review list AGENT_ID
bcn agent review approve THREAD_ID
bcn agent review deny THREAD_ID
```
