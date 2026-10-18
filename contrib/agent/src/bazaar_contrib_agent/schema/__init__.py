"""Own this contrib's tables, schema baseline and append-only migrations."""

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Dependency, Migration, ModuleSchema

from . import definitions

BASELINE = Migration(
    version=1,
    name="agent_schema",
    operations=(
        s.CreateTable(table=definitions.RUNTIME_ATTEMPTS),
        s.CreateIndex(
            table="runtime_attempts",
            index=definitions.IDX_RUNTIME_ATTEMPTS_SESSION_STARTED_V13,
        ),
    ),
)

MODULE = ModuleSchema(
    tables=(
        "node_state",
        "runtime_attempts",
        "runtime_events",
        "runtime_sessions",
        "runtime_turns",
    ),
    migrations=(BASELINE,),
    baseline=BASELINE,
    predecessor=Dependency(package="bazaar-contrib-legacy-node-migrations", version=32),
)
