from __future__ import annotations

from .model import Migration

# A DM opened by handle kept that handle as its name, while the peer speaks
# under its own id, so one conversation ended up stored as two. The peer's id is
# recoverable from what it said: an inbound message carries both the handle it
# spoke under and the id it spoke from.
_RENAMED = """
    SELECT
        opened.agent_id AS agent_id,
        opened.channel AS channel,
        opened.id AS opened_id,
        REPLACE(
            opened.provider_thread_id,
            ':@' || opened.target_handle || ':',
            ':' || spoken.sender_id || ':'
        ) AS peer_thread_id
    FROM channel_sessions AS opened
    JOIN messages AS spoken
      ON spoken.agent_id = opened.agent_id
     AND spoken.channel = opened.channel
     AND spoken.direction = 'inbound'
     AND spoken.sender_id IS NOT NULL
     AND LOWER(spoken.sender) = opened.target_handle_key
     AND spoken.seq = (
        SELECT MAX(latest.seq) FROM messages AS latest
        WHERE latest.agent_id = opened.agent_id
          AND latest.channel = opened.channel
          AND latest.direction = 'inbound'
          AND latest.sender_id IS NOT NULL
          AND LOWER(latest.sender) = opened.target_handle_key
     )
    WHERE opened.target_kind = 'dm'
      AND opened.target_handle IS NOT NULL
      AND INSTR(opened.provider_thread_id, ':@' || opened.target_handle || ':') > 0
"""

# Where the peer already has a conversation under its own id, that one is the
# conversation, and what was written under the handle joins it.
_MERGED = f"""
    SELECT
        renamed.agent_id AS agent_id,
        renamed.opened_id AS opened_id,
        opened_thread.id AS opened_thread_id,
        peer.id AS peer_id,
        peer_thread.id AS peer_thread_id,
        'dm:' || peer.id AS peer_target
    FROM ({_RENAMED}) AS renamed
    JOIN channel_sessions AS peer
      ON peer.agent_id = renamed.agent_id
     AND peer.channel = renamed.channel
     AND peer.provider_thread_id = renamed.peer_thread_id
    JOIN threads AS opened_thread
      ON opened_thread.agent_id = renamed.agent_id
     AND opened_thread.channel_session_id = renamed.opened_id
    JOIN threads AS peer_thread
      ON peer_thread.agent_id = renamed.agent_id
     AND peer_thread.channel_session_id = peer.id
"""

# A cursor says what has been delivered, so the merged one may not pass a
# message that either side still had waiting.
_UNREAD_FLOOR = """
    SELECT MIN(waiting.seq) FROM messages AS waiting
    JOIN consumer_cursors AS reading
      ON reading.thread_id = waiting.thread_id
    WHERE waiting.thread_id IN (merged.opened_thread_id, merged.peer_thread_id)
      AND waiting.direction = 'inbound'
      AND waiting.seq > reading.delivered_through_seq
"""

NAME_DM_BY_PEER_ID_MIGRATION = Migration(
    version=27,
    name="name_dms_by_the_peer_id",
    statements=(
        # the rows that identify this work are the rows it rewrites, so settle
        # the set before touching any of them
        f"""
        CREATE TEMPORARY TABLE bcn_merged_dms AS {_MERGED}
        """,
        f"""
        CREATE TEMPORARY TABLE bcn_renamed_dms AS
        SELECT * FROM ({_RENAMED}) AS renamed
        WHERE renamed.opened_id NOT IN (SELECT opened_id FROM bcn_merged_dms)
        """,
        f"""
        UPDATE consumer_cursors SET delivered_through_seq = COALESCE(
            (
                SELECT ({_UNREAD_FLOOR}) - 1 FROM bcn_merged_dms AS merged
                WHERE merged.peer_thread_id = consumer_cursors.thread_id
            ),
            MAX(
                delivered_through_seq,
                COALESCE((
                    SELECT waiting.delivered_through_seq
                    FROM consumer_cursors AS waiting
                    JOIN bcn_merged_dms AS merged
                      ON merged.opened_thread_id = waiting.thread_id
                    WHERE merged.peer_thread_id = consumer_cursors.thread_id
                ), 0)
            )
        )
        WHERE thread_id IN (SELECT peer_thread_id FROM bcn_merged_dms)
        """,
        """
        DELETE FROM consumer_cursors
        WHERE thread_id IN (SELECT opened_thread_id FROM bcn_merged_dms)
        """,
        """
        UPDATE reminders SET owner_thread_id = (
            SELECT peer_thread_id FROM bcn_merged_dms
            WHERE bcn_merged_dms.opened_thread_id = reminders.owner_thread_id
        )
        WHERE owner_thread_id IN (SELECT opened_thread_id FROM bcn_merged_dms)
        """,
        # provider_thread_id stays as it was on a message: it records how that
        # message was addressed, which no later merge changes
        """
        UPDATE messages SET
            thread_id = (
                SELECT peer_thread_id FROM bcn_merged_dms
                WHERE bcn_merged_dms.opened_id = messages.channel_session_id
            ),
            target = (
                SELECT peer_target FROM bcn_merged_dms
                WHERE bcn_merged_dms.opened_id = messages.channel_session_id
            ),
            channel_session_id = (
                SELECT peer_id FROM bcn_merged_dms
                WHERE bcn_merged_dms.opened_id = messages.channel_session_id
            )
        WHERE channel_session_id IN (SELECT opened_id FROM bcn_merged_dms)
        """,
        """
        DELETE FROM threads WHERE id IN (SELECT opened_thread_id FROM bcn_merged_dms)
        """,
        """
        DELETE FROM channel_sessions WHERE id IN (SELECT opened_id FROM bcn_merged_dms)
        """,
        # the rest have no second half yet, so they only need their own name
        """
        UPDATE channel_sessions SET
            provider_thread_id = (
                SELECT peer_thread_id FROM bcn_renamed_dms
                WHERE bcn_renamed_dms.opened_id = channel_sessions.id
            ),
            provider_identity_ref_json = JSON_SET(
                CASE
                    WHEN JSON_VALID(provider_identity_ref_json)
                    THEN provider_identity_ref_json
                    ELSE '{}'
                END,
                '$.delivery_handle',
                target_handle
            )
        WHERE id IN (SELECT opened_id FROM bcn_renamed_dms)
        """,
        """
        DROP TABLE bcn_merged_dms
        """,
        """
        DROP TABLE bcn_renamed_dms
        """,
    ),
)

__all__ = ["NAME_DM_BY_PEER_ID_MIGRATION"]
