# bazaar-contrib-config-versions

BCN loads the configuration converters automatically during bootstrap.

```sh
bcn contrib add config-versions
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-config-versions==0.3.*"
```
