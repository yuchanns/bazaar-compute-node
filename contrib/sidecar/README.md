# bazaar-contrib-sidecar

Enable local calls to node services. Select the transport for your platform:

```sh
bcn contrib add sidecar --enable
bcn contrib add sidecar-unix --enable
# On Windows, use: bcn contrib add sidecar-windows --enable
```

The corresponding entries in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-sidecar==0.3.*"
[[contribs.enabled]]
entry = "sidecar"

[[contribs]]
package = "bazaar-contrib-sidecar-unix==0.3.*; sys_platform != 'win32'"
[[contribs.enabled]]
entry = "sidecar-unix"
[contribs.enabled.config]
timeout = 30

[[contribs]]
package = "bazaar-contrib-sidecar-windows==0.3.*; sys_platform == 'win32'"
[[contribs.enabled]]
entry = "sidecar-windows"
[contribs.enabled.config]
timeout = 30
```

Consumers accept `CLIENT`; handlers are served while the owning contrib is running.
Calls can run concurrently on one client. Stream transports pair each response
with its request ID and dispatch server calls concurrently within the connection.
`client.request(request, timeout=None)` lets the business operation own its wait
budget; omitted timeouts use the client's configured default.
