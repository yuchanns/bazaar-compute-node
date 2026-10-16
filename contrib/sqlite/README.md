# bazaar-contrib-sqlite

Store node data in SQLite. Enable `storage` and select its `database` entry with `backend = "sqlite"`.

```sh
bcn contrib add sqlite --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-sqlite~=0.3.0rc1"
[[contribs.enabled]]
entry = "sqlite"
[contribs.enabled.config]
path = "/path/to/node.db"
```
