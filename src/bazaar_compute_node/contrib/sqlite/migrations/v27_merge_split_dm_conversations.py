from __future__ import annotations

from .model import Migration

# A DM the agent opened by handle and the same DM the peer speaks in are one
# conversation stored twice, because each side named it its own way. They are
# recognisable as a pair: one row is keyed by the very handle both answer to and
# the other is not. Two different peers who share a display name are not, since
# neither of their rows is keyed by that name.
_PAIRS = """
    SELECT
        loser.agent_id AS agent_id,
        loser.id AS loser_id,
        loser_thread.id AS loser_thread_id,
        winner.id AS winner_id,
        winner_thread.id AS winner_thread_id,
        CASE
            WHEN winner.target_handle IS NOT NULL THEN 'dm:@' || winner.target_handle
            ELSE 'dm:' || winner.id
        END AS winner_target
    FROM channel_sessions AS loser
    JOIN threads AS loser_thread
      ON loser_thread.agent_id = loser.agent_id
     AND loser_thread.channel_session_id = loser.id
    JOIN channel_sessions AS winner
      ON winner.agent_id = loser.agent_id
     AND winner.channel = loser.channel
     AND winner.target_kind = 'dm'
     AND winner.target_handle_key = loser.target_handle_key
     AND winner.id <> loser.id
     AND LOWER(winner.provider_thread_id)
         NOT LIKE '%:@' || winner.target_handle_key || ':%'
    JOIN threads AS winner_thread
      ON winner_thread.agent_id = winner.agent_id
     AND winner_thread.channel_session_id = winner.id
    WHERE loser.target_kind = 'dm'
      AND loser.target_handle_key IS NOT NULL
      AND LOWER(loser.provider_thread_id)
          LIKE '%:@' || loser.target_handle_key || ':%'
      AND (
        SELECT COUNT(*) FROM channel_sessions AS peer
        WHERE peer.agent_id = loser.agent_id
          AND peer.channel = loser.channel
          AND peer.target_kind = 'dm'
          AND peer.target_handle_key = loser.target_handle_key
      ) = 2
"""

SPLIT_DM_MERGE_MIGRATION = Migration(
    version=27,
    name="merge_split_dm_conversations",
    statements=(
        # the rows that identify a pair are the rows this merge deletes, so the
        # set has to be settled before the first of them goes
        f"""
        CREATE TEMPORARY TABLE bcn_split_dm_pairs AS {_PAIRS}
        """,
        # the surviving cursor cannot fall behind messages it is about to own
        """
        WITH pairs AS (SELECT * FROM bcn_split_dm_pairs)
        UPDATE consumer_cursors SET delivered_through_seq = MAX(
            delivered_through_seq,
            COALESCE((
                SELECT losing.delivered_through_seq
                FROM consumer_cursors AS losing
                JOIN pairs ON pairs.loser_thread_id = losing.thread_id
                WHERE pairs.winner_thread_id = consumer_cursors.thread_id
            ), 0)
        )
        WHERE thread_id IN (SELECT winner_thread_id FROM pairs)
        """,
        """
        WITH pairs AS (SELECT * FROM bcn_split_dm_pairs)
        DELETE FROM consumer_cursors
        WHERE thread_id IN (SELECT loser_thread_id FROM pairs)
        """,
        """
        WITH pairs AS (SELECT * FROM bcn_split_dm_pairs)
        UPDATE reminders SET owner_thread_id = (
            SELECT winner_thread_id FROM pairs
            WHERE pairs.loser_thread_id = reminders.owner_thread_id
        )
        WHERE owner_thread_id IN (SELECT loser_thread_id FROM pairs)
        """,
        # provider_thread_id stays as it was: it records how this very message
        # was addressed, and rewriting it would claim it arrived another way
        """
        WITH pairs AS (SELECT * FROM bcn_split_dm_pairs)
        UPDATE messages SET
            thread_id = (
                SELECT winner_thread_id FROM pairs
                WHERE pairs.loser_id = messages.channel_session_id
            ),
            target = (
                SELECT winner_target FROM pairs
                WHERE pairs.loser_id = messages.channel_session_id
            ),
            channel_session_id = (
                SELECT winner_id FROM pairs
                WHERE pairs.loser_id = messages.channel_session_id
            )
        WHERE channel_session_id IN (SELECT loser_id FROM pairs)
        """,
        """
        WITH pairs AS (SELECT * FROM bcn_split_dm_pairs)
        DELETE FROM threads WHERE id IN (SELECT loser_thread_id FROM pairs)
        """,
        """
        WITH pairs AS (SELECT * FROM bcn_split_dm_pairs)
        DELETE FROM channel_sessions WHERE id IN (SELECT loser_id FROM pairs)
        """,
        """
        DROP TABLE bcn_split_dm_pairs
        """,
    ),
)

__all__ = ["SPLIT_DM_MERGE_MIGRATION"]
