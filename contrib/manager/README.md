# bazaar-contrib-manager

Install and manage contrib packages with `bcn contrib`.

```sh
bcn contrib add manager --enable
bcn contrib list
bcn contrib configured
bcn contrib --help
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-manager~=0.3.0rc1"
[[contribs.enabled]]
entry = "contrib-manager"
```
