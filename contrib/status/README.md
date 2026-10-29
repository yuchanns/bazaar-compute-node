# bazaar-contrib-status

Read contrib instance status through `ctx.accept(CONTRIB_STATUS).snapshot()`.

```sh
bcn contrib add status --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-status==0.3.*"
[[contribs.enabled]]
entry = "status"
```
