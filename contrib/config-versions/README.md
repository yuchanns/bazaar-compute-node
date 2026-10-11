# bazaar-contrib-config-versions

`config-v1` through `config-v5` provide the `CONFIG_V1` through `CONFIG_V5` converter
services from `bazaar-contrib-config`. Load `VERSIONS` from this package and accept
`CONFIG_CURRENT` from `bazaar-contrib-config`
to convert a document into a `ConfigCandidate` containing its payload, field
changes and required packages.
