# bazaar-contrib-config

The configuration storage contrib offers `CONFIG_STORE`. Its `read()` returns
a `ConfigSnapshot` containing the document and revision, or `None` when no
document exists. Its `write()` checks `expected_revision` before saving;
concurrent changes raise `ConfigConflict`.

The converter accepted through `CONFIG_CURRENT` returns a `ConfigCandidate`:
the converted payload, field changes, and required packages.
`configuration(defaults, build)` creates a loader that reads the store,
converts the document, and calls `build`
to produce a `Composition`. Core validates its entries into a `Candidate` before
the loader offers it as `COMPOSITION`; `ContribHub.run()` then starts those entries.

The loader also offers the `Configuration` service as `CONFIGURATION`.
`prepare()` converts and validates a document, `commit()` saves it, and
`import_defaults()` merges a package's entries while preserving existing
parameters and choices. `apply()` submits the saved composition through
`ctx.update()`. Configuration edits from management commands are saved and
reported as pending restart; the configured business instances start with
the next `bcn run`.
