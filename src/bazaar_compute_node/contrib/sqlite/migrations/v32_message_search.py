from __future__ import annotations

from .model import Migration

MESSAGE_SEARCH_MIGRATION = Migration(
    version=32,
    name="message_search",
    statements=(
        """
        CREATE VIRTUAL TABLE message_search USING fts5(
            body,
            content = 'messages',
            content_rowid = 'seq',
            tokenize = 'trigram'
        )
        """,
        """
        CREATE INDEX idx_messages_agent_search_time ON messages (
            agent_id,
            COALESCE(provider_time_ms, received_at_ms, created_at_ms),
            seq
        )
        """,
        """
        CREATE TRIGGER message_search_insert AFTER INSERT ON messages BEGIN
            INSERT INTO message_search(rowid, body) VALUES (new.seq, new.body);
        END
        """,
        """
        CREATE TRIGGER message_search_delete AFTER DELETE ON messages BEGIN
            INSERT INTO message_search(message_search, rowid, body)
            VALUES ('delete', old.seq, old.body);
        END
        """,
        """
        CREATE TRIGGER message_search_update AFTER UPDATE OF body, seq ON messages BEGIN
            INSERT INTO message_search(message_search, rowid, body)
            VALUES ('delete', old.seq, old.body);
            INSERT INTO message_search(rowid, body) VALUES (new.seq, new.body);
        END
        """,
        "INSERT INTO message_search(message_search) VALUES ('rebuild')",
    ),
)
