"""Own this contrib's tables, schema baseline and append-only migrations."""

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Dependency, Migration, ModuleSchema

from . import definitions

BASELINE = Migration(
    version=1,
    name="reminder_schema",
    operations=(
        s.CreateTable(table=definitions.REMINDERS),
        s.CreateIndex(
            table="reminders", index=definitions.IDX_REMINDERS_STATE_NEXT_V12
        ),
        s.CreateIndex(
            table="reminders", index=definitions.IDX_REMINDERS_OWNER_STATE_UPDATED_V25
        ),
    ),
)

MODULE = ModuleSchema(
    tables=("reminder_occurrences", "reminders"),
    migrations=(BASELINE,),
    baseline=BASELINE,
    predecessor=Dependency(package="bazaar-contrib-legacy-node-migrations", version=32),
)
