"""Every word the pages show, in the user's language."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from types import MappingProxyType

from jinja2 import Environment, StrictUndefined, Template

ENGLISH = "en"
SIMPLIFIED_CHINESE = "zh-CN"
LANGUAGES = (ENGLISH, SIMPLIFIED_CHINESE)

_environment = Environment(undefined=StrictUndefined, autoescape=False)


def _load_catalog(language: str) -> Mapping[str, Template]:
    resource = files("bazaar_compute_server").joinpath(
        "resources", "locales", f"{language}.toml"
    )
    document = tomllib.loads(resource.read_text(encoding="utf-8"))
    templates: dict[str, Template] = {}
    for key, source in document.items():
        if not isinstance(source, str):
            raise TypeError(f"i18n catalog {language!r} message {key!r} must be text")
        templates[key] = _environment.from_string(source)
    return MappingProxyType(templates)


_CATALOGS = {language: _load_catalog(language) for language in LANGUAGES}
if _CATALOGS[ENGLISH].keys() != _CATALOGS[SIMPLIFIED_CHINESE].keys():
    raise RuntimeError("i18n catalogs must contain the same message keys")


@dataclass(frozen=True, slots=True)
class Translator:
    language: str
    _messages: Mapping[str, Template] = field(repr=False)

    def text(self, key: str, arguments: Mapping[str, object] | None = None) -> str:
        template = self._messages.get(key)
        if template is None:
            return key
        return template.render(**(arguments or {}))

    def has(self, key: str) -> bool:
        return key in self._messages


def create_translator(language: str | None) -> Translator:
    chosen = language if language in _CATALOGS else ENGLISH
    return Translator(language=chosen, _messages=_CATALOGS[chosen])


def language_from_header(accept_language: str | None) -> str:
    """The first language the browser asks for that the pages speak."""

    for part in (accept_language or "").split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("zh"):
            return SIMPLIFIED_CHINESE
        if tag.startswith("en"):
            return ENGLISH
    return ENGLISH


__all__ = [
    "ENGLISH",
    "LANGUAGES",
    "SIMPLIFIED_CHINESE",
    "Translator",
    "create_translator",
    "language_from_header",
]
