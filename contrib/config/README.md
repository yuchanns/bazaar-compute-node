# bazaar-contrib-config

The configuration contracts are installed with a carrier. BCN loads the carrier during bootstrap.

```sh
bcn contrib add config-filesystem
bcn --configer config-filesystem --configer-options ./config.toml contrib list
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-config-filesystem==0.3.*"
```
