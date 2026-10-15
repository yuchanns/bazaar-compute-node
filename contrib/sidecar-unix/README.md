# Bazaar Unix Sidecar

The `sidecar-unix` entry offers the public `SERVER` and `CLIENT` services
from `bazaar-contrib-sidecar`. Enable it with the interface package's `sidecar`
entry to expose that entry's Context handlers. Client consumers accept `CLIENT`
and depend only on the interface package.

`endpoint` selects the native address; `timeout` bounds client calls and defaults
to 30 seconds. The provider owns its client connection. Listening begins when the
handler owner enters `SERVER.serve(sessions)` and ends with that lifespan.

Startup checks an existing address before binding. A live listener keeps
ownership; a stale socket or invalid file is removed and rebuilt.
Other I/O errors propagate without reclaiming the address. Socket permissions are
restricted to the owner, and shutdown removes only the socket it created.
