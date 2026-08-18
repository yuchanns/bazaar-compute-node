from __future__ import annotations

import locale

import pytest

import bazaar_compute_node.i18n.catalog as catalog_module
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator
from bazaar_compute_node.i18n.english import MESSAGES as ENGLISH_MESSAGES
from bazaar_compute_node.i18n.schinese import MESSAGES as SCHINESE_MESSAGES


def test_catalogs_share_keys_and_preserve_interpolation_values() -> None:
    assert ENGLISH_MESSAGES.keys() == SCHINESE_MESSAGES.keys()
    detail = "request failed: $HOME\n错误详情"

    english = create_translator(ENGLISH)
    schinese = create_translator(SIMPLIFIED_CHINESE)

    assert english.text("runtime.error.failed", {"error": detail}) == (
        f"Execution failed: {detail}"
    )
    assert schinese.text("runtime.error.failed", {"error": detail}) == (
        f"执行失败：{detail}"
    )
    assert english.text("runtime.error.unknown", {"error": detail}) == (
        f"Execution status is unknown: {detail}"
    )
    assert schinese.text("runtime.error.unknown", {"error": detail}) == (
        f"执行状态未知：{detail}"
    )
    assert english.text("missing.message.key") == "missing.message.key"


@pytest.mark.parametrize(
    ("system_language", "expected"),
    (
        ("zh_CN", SIMPLIFIED_CHINESE),
        ("zh_TW", ENGLISH),
        ("en_US", ENGLISH),
        (None, ENGLISH),
    ),
)
def test_system_locale_selects_only_simplified_chinese(
    monkeypatch: pytest.MonkeyPatch,
    system_language: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(
        catalog_module.locale,
        "getlocale",
        lambda: (system_language, "UTF-8"),
    )

    assert create_translator(None).language == expected


def test_explicit_language_precedes_system_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_getlocale() -> tuple[str | None, str | None]:
        raise AssertionError("system locale must not be read for explicit language")

    monkeypatch.setattr(catalog_module.locale, "getlocale", fail_getlocale)

    assert create_translator(SIMPLIFIED_CHINESE).language == SIMPLIFIED_CHINESE
    assert create_translator("ja").language == ENGLISH


def test_locale_error_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_getlocale() -> tuple[str | None, str | None]:
        raise locale.Error("locale unavailable")

    monkeypatch.setattr(catalog_module.locale, "getlocale", fail_getlocale)

    assert create_translator(None).language == ENGLISH
