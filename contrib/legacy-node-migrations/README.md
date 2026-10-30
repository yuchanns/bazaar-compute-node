# bazaar-contrib-legacy-node-migrations

Upgrade existing Node databases through the published migrations 1–32; fresh databases use business baselines.

```sh
bcn contrib add legacy-node-migrations --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-legacy-node-migrations==0.3.*"
[[contribs.enabled]]
entry = "legacy-node-migrations"
```
