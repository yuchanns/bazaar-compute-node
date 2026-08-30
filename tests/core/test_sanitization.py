from __future__ import annotations

from bazaar_compute_node.core.sanitization import (
    REDACTED,
    is_sensitive_field,
    redact_sensitive_text,
    redact_sensitive_value,
)


def test_sensitive_field_vocabulary_is_case_insensitive() -> None:
    assert is_sensitive_field("Authorization")
    assert is_sensitive_field("raw_payload")
    assert not is_sensitive_field("command")


def test_sensitive_values_and_token_shaped_text_are_redacted() -> None:
    value = redact_sensitive_value(
        {
            "token": "plain-secret",
            "nested": [
                {"message": "use sk-example_secret"},
                "0123456789abcdef0123456789abcdef",
            ],
        }
    )
    rendered = str(value)

    assert rendered.count(REDACTED) == 3
    assert "plain-secret" not in rendered
    assert "sk-example_secret" not in rendered
    assert "0123456789abcdef0123456789abcdef" not in rendered
    assert redact_sensitive_text("safe text") == "safe text"
