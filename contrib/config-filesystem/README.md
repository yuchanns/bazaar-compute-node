# bazaar-contrib-config-filesystem

Store node configuration in a TOML file. Select the file before the subcommand.

```sh
bcn contrib add config-filesystem
bcn --configer config-filesystem --configer-options ./config.toml contrib list
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-config-filesystem~=0.3.0rc1"
```
