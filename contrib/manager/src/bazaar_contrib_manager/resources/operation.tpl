ID: {{ result.id }}
Action: {{ result.kind }}
Target: {{ result.target }}
Package: {{ result.package or "" }}
Status: {{ result.phase }}
{% include "outcome.tpl" %}
