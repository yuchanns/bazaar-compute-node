# bazaar-contrib-invoke

Accept `COMMANDS` to register Click commands in your contrib's lifespan:

```python
from contextlib import asynccontextmanager

import click
from bazaar_contrib_invoke import COMMANDS, CommandEntry
from bazaar_core import Context, Contrib


@click.command("hello", help="Say hello.")
@click.argument("name")
def hello(name: str) -> None:
    click.echo(f"Hello, {name}!")


@asynccontextmanager
async def lifespan(ctx: Context):
    with ctx.accept(COMMANDS).register(hello):
        yield


example = Contrib(accepts=[COMMANDS], lifespan=lifespan)
hello_entry = CommandEntry(contrib="example", help="Say hello.")
```

Declare both entry points in your package's `pyproject.toml`:

```toml
[project.entry-points."bazaar_compute_node.contribs"]
example = "example_package:example"

[project.entry-points."bazaar_compute_node.commands"]
hello = "example_package:hello_entry"
```

Install and enable the package:

```sh
bcn contrib add invoke --enable
bcn contrib add example-package --enable example
```

The corresponding entries in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-invoke~=0.3.0rc1"
[[contribs.enabled]]
entry = "commands"

[[contribs]]
package = "example-package>=0.1.0"
[[contribs.enabled]]
entry = "example"
```

Then run `bcn hello Alice`.

Async command callbacks call shared services directly to reuse business logic.
Calling Click `Context.invoke()` or `Context.forward()` from an async callback raises
`RuntimeError` before invoking the target callback.

A command entry that fails to load is reported independently. Other commands remain
available; selecting the failed command reports its loading error.
