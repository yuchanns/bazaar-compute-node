# bazaar-contrib-manager

The `contrib-manager` entry provides `CONTRIB_CATALOG` and `CONTRIB_MANAGER`.
It depends on `CONFIGURATION` and reads every location in the selected document's
`contrib_indexes`. Local and HTTP(S) indexes use this format:

```json
{"contribs": [{"package": "bazaar-contrib-example", "source": "https://packages.example.com/simple/"}]}
```

Matching package/source entries retain all contributing index locations. Different
sources for the same package remain separate choices. Each index has an independent
cache and read status, so an unavailable source can retain its previous entries.

Manage packages before starting the node with the installed `bcn contrib` commands:

```sh
bcn contrib index list
bcn contrib index add ./contribs.json
bcn contrib entries
bcn contrib add example --source https://packages.example.com/simple/ --version '>=1,<2' --enable
bcn contrib list
bcn contrib show example
bcn contrib configured
bcn contrib enable example --entry example-file --option path=./events.jsonl --option label=work
bcn contrib edit 2 --option path=./events.jsonl
bcn contrib disable 2
bcn contrib remove bazaar-contrib-example
bcn contrib upgrade 0.3.1
```

`--configer`, repeatable `--configer-options`, and the `--config` alias select the
same carrier as `bcn run`; put them immediately after `bcn contrib`. Configured
entry positions are zero-based. A package's `defaults.toml` imports its combination
while preserving existing parameters. Use `--entry` to select an individual entry
and repeat `--option KEY=VALUE` to supply its parameters; `show` includes its
configuration model. Dotted keys address nested fields, numeric path components
address list positions, and repeated keys supply list values. For example,
`--option paths.info=./info.jsonl --option tags=work --option tags=local` supplies
a nested path and two tags. Editing preserves fields omitted from the options.

Package operations use the node's `uv` executable and current Python environment.
The manager owns the full extra dependency manifest, source selections, resolved
lock, and an environment operation lock. It supports uv tool installations and
dedicated venvs. A candidate environment is installed and configuration is checked
there before changing the current environment. Removing a selected configuration
carrier or a package still required by another package is rejected during preparation.

Results record the operation ID, actual installed version, configuration save,
runtime application, and pending restart independently. `bcn contrib operation <id>`
reads a persisted result. `bcn contrib` starts a temporary composition containing
`contrib-manager`; configured business instances start with the next `bcn run`.
When the manager is enabled in a running node, it accepts the configuration
service through `CONFIGURATION`, then calls its `commit()` and `apply()` methods.
The configuration service builds the composition and
calls `ctx.update()`; `ContribHub` compares entries and starts or stops instances.
Package upgrades and updates that would stop the requesting manager keep the
saved configuration and report a pending restart.
