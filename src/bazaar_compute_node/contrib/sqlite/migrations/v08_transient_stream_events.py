from __future__ import annotations

from .model import Migration

TRANSIENT_STREAM_EVENT_MIGRATION = Migration(
    version=8,
    name="transient_stream_events",
    statements=(
        """
        DELETE FROM runtime_events
        WHERE event_name = 'codex.turn.progress'
          AND (
              json_extract(metadata_json, '$.provider_method') = 'turn/progress'
              OR (
                  json_extract(metadata_json, '$.provider_method') LIKE 'item/%'
                  AND json_extract(metadata_json, '$.provider_method') NOT IN (
                      'item/started',
                      'item/completed',
                      'item/autoApprovalReview/started',
                      'item/autoApprovalReview/completed'
                  )
              )
          )
        """,
    ),
)


__all__ = ["TRANSIENT_STREAM_EVENT_MIGRATION"]
