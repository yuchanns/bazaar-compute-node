# bazaar-contrib-harness

Enable harness support and install your chosen harness contrib:

```sh
bcn contrib add harness --enable
bcn contrib add HARNESS_KIND --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-harness==0.3.*"
[[contribs.enabled]]
entry = "harness-builders"
```

After enabling a channel contrib, select the harness when creating an agent:

```sh
bcn agent add --name Desk --channel CHANNEL_KIND --harness HARNESS_KIND
```

Replace `HARNESS_KIND` and `CHANNEL_KIND` with your enabled contrib names.
See [agent configuration](../agent/README.md) for permissions and
[agent commands](../agent-command/README.md) for managing agents.
