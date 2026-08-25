from __future__ import annotations

import locale
import tomllib
from importlib.resources import files

import pytest

import bazaar_compute_node.i18n.catalog as catalog_module
from bazaar_compute_node.i18n import ENGLISH, SIMPLIFIED_CHINESE, create_translator
from bazaar_compute_node.rendering import TextTemplate


def _catalog(language: str) -> dict[str, object]:
    resource = files("bazaar_compute_node").joinpath(
        "resources", "locales", f"{language}.toml"
    )
    return tomllib.loads(resource.read_text(encoding="utf-8"))


def test_catalogs_share_keys_and_template_variables() -> None:
    english = _catalog(ENGLISH)
    schinese = _catalog(SIMPLIFIED_CHINESE)

    assert english.keys() == schinese.keys()
    assert english
    for key in english:
        english_source = english[key]
        schinese_source = schinese[key]
        assert isinstance(english_source, str)
        assert isinstance(schinese_source, str)
        assert TextTemplate.from_source(key, english_source).variables == (
            TextTemplate.from_source(key, schinese_source).variables
        )


def test_translator_preserves_interpolation_values_and_requires_exact_keys() -> None:
    detail = "request failed: $HOME\n错误详情 {{ untouched }} {% raw %}"
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
    with pytest.raises(ValueError, match="missing: error"):
        english.text("runtime.error.failed")
    with pytest.raises(ValueError, match="unexpected: extra"):
        english.text("cli.agent.add", {"extra": "value"})
    assert english.text("missing.message.key", {"extra": "value"}) == (
        "missing.message.key"
    )


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
