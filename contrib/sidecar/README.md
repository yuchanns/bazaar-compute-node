# Bazaar Sidecar Contrib

This interface package defines `SERVER` and `CLIENT`. The `sidecar` entry
accepts `SERVER` and the public `CONFIGURATION` interface, and binds its Context
handlers. A transport provider offers both sidecar services; consumers accept
`CLIENT` and depend only on this package.

```toml
[[contribs]]
contrib = "sidecar"

[[contribs]]
contrib = "sidecar-unix"  # Use sidecar-windows on Windows.
```

`Server.serve(sessions)` binds a factory of asynchronous session contexts. Each
context yields a `Handler(Request) -> Response`; leaving it releases that
caller's object references and remote resources. `Client` provides a shared
remote-object facade over a provider's send and close operations. Neither public
service contract requires socket, pipe, or stream types, so an HTTP provider can
implement the same contract.

The native providers each accept `endpoint` and `timeout` (30 seconds by default).
Unix defaults to `$BCN_HOME/.bcn/sidecar.sock` (home when unset); Windows defaults
to a native named pipe keyed by that directory. They open the client connection
on its first request and listen only while the handler owner is serving.
Native JSONL readers read fixed-size chunks and retain bytes after each newline,
so complete requests and responses can exceed asyncio's per-line buffer limit.

A consuming plugin declares `accepts=[CLIENT]` and uses the offered object:

```python
from bazaar_contrib_sidecar import CLIENT, Symbol

client = ctx.accept(CLIENT)
remote = client.context
configuration = await remote.call(
    "accept", Symbol("bazaar_contrib_config:CONFIGURATION")
)
candidate, snapshot = await configuration.call("read")
result = await configuration.call("apply", snapshot)
applied = await result.get("applied")
```

The caller owns this orchestration. Sidecar does not define configuration,
manager, or agent business handlers. The entry declares its public interface
dependencies in `Contrib.accepts`; the framework supplies their implementations.
The same service visibility, bindings, and composition ownership rules apply
remotely. Configuration application uses the configuration service's owning
Context.

`Remote.get(name)` reads a public attribute; `Remote.call(name, *args, **kwargs)`
invokes a public method and awaits its result when needed. JSON scalars, lists,
tuples, and string-keyed dictionaries cross by value. Other results remain in
the server and return a connection-owned `Remote` reference. References can be
passed into subsequent calls or released explicitly with `release()`.

`Symbol("module:name")` resolves an installed public Python symbol in the server,
preserving service-key identity. `Construct("module:Type", {...})` constructs a
server-side object, including Pydantic models and compositions, from recursively
encoded fields. For example, `ctx.enable` can receive a constructed Composition,
and the returned context can be entered with `async with remote.enter()`.
Entered contexts exit when the caller leaves the block or closes the connection.
The server lifespan cancels active connection tasks before waiting for the
listener and connection-owned resources to close.

Method failures return `RemoteError` with the original exception type and message.
A timeout or lost connection closes the client session; a call may already have
changed server state, and requests are never replayed automatically. Create a new
client session before making further calls after such a transport failure.

The native transport follows [CPython's pipe implementation](https://github.com/python/cpython/blob/3.15/Lib/asyncio/windows_events.py)
and [asyncio Unix streams](https://docs.python.org/3.15/library/asyncio-stream.html).
