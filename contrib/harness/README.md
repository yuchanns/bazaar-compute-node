# bazaar-contrib-harness

Enable the harness provider registry. Install a concrete harness provider separately.

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
