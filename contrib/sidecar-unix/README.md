# bazaar-contrib-sidecar-unix

Provide local Sidecar server and client connections.

```sh
bcn contrib add sidecar --enable
bcn contrib add sidecar-unix --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-sidecar-unix~=0.3.0rc1"
[[contribs.enabled]]
entry = "sidecar-unix"
[contribs.enabled.config]
timeout = 30
```

Enable the `sidecar` entry as shown in the [Sidecar usage](../sidecar/README.md).
Set `endpoint` to override the default socket path. Existing ordinary files are preserved; only stale sockets and broken links are reclaimed.
