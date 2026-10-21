"""Own this contrib's tables, schema baseline and append-only migrations."""

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Dependency, Migration, ModuleSchema

from . import definitions

BASELINE = Migration(
    version=1,
    name="messages_node_schema",
    operations=(
        s.CreateTable(table=definitions.CHANNEL_SESSIONS),
        s.CreateTable(table=definitions.THREADS),
        s.CreateTable(table=definitions.INBOUND_ATTACHMENTS_V1),
        s.CreateTable(table=definitions.CONSUMER_CURSORS),
        s.CreateTable(table=definitions.MESSAGES_V30),
        s.CreateIndex(
            table="channel_sessions",
            index=definitions.IDX_CHANNEL_SESSIONS_PROVIDER_IDENTITY_V13,
        ),
        s.CreateIndex(
            table="channel_sessions",
            index=definitions.IDX_CHANNEL_SESSIONS_TARGET_HANDLE_V22,
        ),
        s.CreateIndex(table="threads", index=definitions.IDX_THREADS_CHANNEL_V25),
        s.CreateIndex(
            table="messages", index=definitions.IDX_MESSAGES_AGENT_DIRECTION_SEQ_V18
        ),
        s.CreateIndex(
            table="messages",
            index=definitions.IDX_MESSAGES_INBOUND_PROVIDER_IDENTITY_V18,
        ),
        s.CreateIndex(
            table="messages", index=definitions.IDX_MESSAGES_OUTBOUND_STATE_CREATED_V18
        ),
        s.CreateIndex(
            table="messages", index=definitions.IDX_MESSAGES_REPLY_TO_MESSAGE_V18
        ),
        s.CreateIndex(
            table="messages", index=definitions.IDX_MESSAGES_AGENT_THREAD_TARGET_SEQ_V25
        ),
        s.CreateIndex(
            table="messages", index=definitions.IDX_MESSAGES_AGENT_SEARCH_TIME_V32
        ),
        s.CreateIndex(table="messages", index=definitions.MESSAGE_SEARCH_V32),
    ),
)

MODULE = ModuleSchema(
    tables=(
        "_consumer_cursors_v20",
        "_message_events_v18",
        "_message_order_v18",
        "_messages_v18",
        "_messages_v19",
        "_messages_v20",
        "_messages_v30",
        "_outbound_messages_provider_attempts",
        "bcn_sessions",
        "channel_sessions",
        "consumer_cursors",
        "inbound_attachments",
        "inbound_messages",
        "message_search",
        "messages",
        "outbound_messages",
        "threads",
    ),
    migrations=(BASELINE,),
    baseline=BASELINE,
    predecessor=Dependency(package="bazaar-contrib-legacy-node-migrations", version=32),
)
