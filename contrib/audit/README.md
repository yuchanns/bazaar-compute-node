# bazaar-contrib-audit

Enable agent execution audit events. Install and enable an audit subscriber
contrib to store or forward the events.

```sh
bcn contrib add audit --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-audit==0.3.*"
[[contribs.enabled]]
entry = "audit"
```
