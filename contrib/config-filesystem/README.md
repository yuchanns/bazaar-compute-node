# bazaar-contrib-config-filesystem

The `config-filesystem` entry stores configuration as TOML. Use
`bcn run --config ./config.toml` to select a file.

```python
from pathlib import Path
from bazaar_contrib_config_filesystem import FileConfigStore

async with FileConfigStore(Path("config.toml")) as store:
    snapshot = await store.read()
    if snapshot is not None:
        await store.write(snapshot.payload, expected_revision=snapshot.revision)
```
