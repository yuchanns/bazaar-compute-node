# bazaar-contrib-timer

The timer contracts and timeout utility are installed with a provider.
Use `TIMER` from `bazaar_contrib_timer`.

```sh
bcn contrib add timer-wheel --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-timer-wheel~=0.3.0rc1"
[[contribs.enabled]]
entry = "timer-wheel"
```

Declare `accepts=[TIMER]` on your contrib and use its injected timer:

```python
from bazaar_contrib_timer import TIMER, timeout, wait_for

timer = ctx.accept(TIMER)
await timer.create(50).wait()
reply = await wait_for(timer, receive_message(), 5_000)

try:
    async with timeout(timer, 5_000):
        await send_message()
except TimeoutError:
    await report_timeout()
```

Delays are in milliseconds and follow the provider's scheduling resolution.
`wait_for(timer, awaitable, delay_ms)` returns the awaited result and applies the
same timeout and cancellation rules to a single operation.
Pass `None` to disable a timeout. Non-positive budgets follow asyncio's behavior
without creating a timer handle: `wait_for` returns an already completed result
or cancels the pending operation; `timeout` schedules cancellation for the next
loop iteration. Positive budgets use the injected provider for timing and the
public asyncio timeout scope for cancellation and exception handling.
Nested scopes retain their own deadlines;
external task cancellation propagates as `asyncio.CancelledError`.
The context manager cancels and joins its timer wait when the scope ends.
