# bazaar-contrib-storage

Enable backend registration and select the database backend. Install the selected provider as well.

```sh
bcn contrib add storage --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-storage~=0.3.0rc1"
[[contribs.enabled]]
entry = "storage"
[[contribs.enabled]]
entry = "database"
[contribs.enabled.config]
backend = "sqlite"
```
