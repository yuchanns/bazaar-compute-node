#!/bin/sh
# {{ managed_marker }}
set -eu

directory=$BCN_HOME
if [ -n "${BCN_ENV_FILE:-}" ] && [ -f "$BCN_ENV_FILE" ]; then
    set -a
    . "$BCN_ENV_FILE"
    set +a
fi

export BCN_HOME="$directory"
exec "$BCN_EXECUTABLE" run --config "$BCN_CONFIG"
