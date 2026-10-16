# bazaar-contrib-timer

The timer contracts are installed with a provider. Use `TIMER` from `bazaar_contrib_timer`.

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
