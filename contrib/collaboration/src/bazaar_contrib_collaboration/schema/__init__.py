"""Own this contrib's tables, schema baseline and append-only migrations."""

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Dependency, Migration, ModuleSchema

from . import definitions

BASELINE = Migration(
    version=1,
    name="collaboration_schema",
    operations=(s.CreateTable(table=definitions.SETTINGS_V31),),
)

MODULE = ModuleSchema(
    tables=("handoffs", "settings"),
    migrations=(BASELINE,),
    baseline=BASELINE,
    predecessor=Dependency(package="bazaar-contrib-legacy-node-migrations", version=32),
)
