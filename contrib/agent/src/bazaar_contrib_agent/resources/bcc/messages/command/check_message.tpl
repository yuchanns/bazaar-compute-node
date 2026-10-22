[target={{ target }} msg={{ message_id }} time={{ timestamp }} type={{ sender_kind }}{% if reply_to_message_id is not none %} reply_to={{ reply_to_message_id }}{% endif %}] {% if sender is not none %}{{ sender }}: {% endif %}{{ body }}{{ attachment_suffix }}{% if system_message_kind == "reminder" %}
({% if repeats %}to snooze/update/cancel{% else %}to snooze/cancel{% endif %}: bcc reminder --help)
Respond as appropriate. Complete all your work before stopping.
Reply in the channel or create/reply in a thread as appropriate; use each message's `target` and `msg` fields to choose the exact target.{% elif system_message_kind == "handoff" %}
To understand why this message was sent, inspect the source context:
  bcc message read --target {{ source_target }} --around {{ source_message_id }}
If you have no objection to why the message was sent, do not announce or explain the handoff, and do not repeat or respond to the referenced message; it has already been delivered. Continue only work already in progress in this conversation that is independent of that message; if there is none, stop.
Mention the handoff only when its reason is unclear, conflicts with the current conversation, or requires a decision.{% endif %}{{ "" -}}
