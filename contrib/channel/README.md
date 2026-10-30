# bazaar-contrib-channel

Enable the channel provider registry. Install a concrete channel provider separately.

```sh
bcn contrib add channel --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-channel==0.3.*"
[[contribs.enabled]]
entry = "channel-builders"
```
