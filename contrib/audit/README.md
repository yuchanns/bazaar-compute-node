# bazaar-contrib-audit

Publish agent execution events through `AUDIT` and subscribe with `AUDIT_EVENT`.

```sh
bcn contrib add audit --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-audit~=0.3.0rc1"
[[contribs.enabled]]
entry = "audit"
```
