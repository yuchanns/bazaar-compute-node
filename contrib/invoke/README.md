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

Install the package and add its entry to your node configuration:

```toml
[[contribs]]
contrib = "example"
```

Then run `bcn hello Alice`.
