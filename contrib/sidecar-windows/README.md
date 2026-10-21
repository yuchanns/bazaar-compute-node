# bazaar-contrib-sidecar-windows

Provide local Sidecar server and client connections.

```sh
bcn contrib add sidecar --enable
bcn contrib add sidecar-windows --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-sidecar-windows~=0.3.0rc1"
[[contribs.enabled]]
entry = "sidecar-windows"
[contribs.enabled.config]
timeout = 30
```

Enable the `sidecar` entry as shown in the [Sidecar usage](../sidecar/README.md).
Set `endpoint` to override the default named pipe address.
