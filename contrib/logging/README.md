# bazaar-contrib-logging

Choose the logging level and output file. Omit `path` to write to stderr.

```sh
bcn contrib add logging --enable
```

The corresponding entry in `config.toml`:

```toml
version = "5"

[[contribs]]
package = "bazaar-contrib-logging~=0.3.0rc1"
[[contribs.enabled]]
entry = "logging"
[contribs.enabled.config]
level = "info"
path = "/path/to/node.jsonl"
retention_days = 7
max_size = "20 MiB"
```

Set `format` to `"text"` or `"json"`; file output defaults to JSON lines.
Use `[contribs.enabled.config.paths]` to override `info` or `error` destinations.
