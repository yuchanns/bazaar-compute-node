# Bazaar Windows Sidecar

The `sidecar-windows` entry offers the public `SERVER` and `CLIENT` services
from `bazaar-contrib-sidecar`. Enable it with the interface package's `sidecar`
entry to expose that entry's Context handlers. Client consumers accept `CLIENT`
and depend only on the interface package.

`endpoint` selects the native address; `timeout` bounds client calls and defaults
to 30 seconds. The provider owns its client connection. Listening begins when the
handler owner enters `SERVER.serve(sessions)` and ends with that lifespan.

The first pipe instance reserves the address exclusively, and all instances reject
remote clients. IOCP accepts and streams belong to the serving lifespan; shutdown
closes pending accepts and connected sessions. Windows releases pipe handles when
the owning process exits, allowing the address to be reused after a crash.
