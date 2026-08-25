from __future__ import annotations

import locale
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from types import MappingProxyType

from ..rendering import TextTemplate

ENGLISH = "en"
SIMPLIFIED_CHINESE = "zh-CN"


def _load_catalog(language: str) -> Mapping[str, TextTemplate]:
    resource = files("bazaar_compute_node").joinpath(
        "resources", "locales", f"{language}.toml"
    )
    document = tomllib.loads(resource.read_text(encoding="utf-8"))
    templates: dict[str, TextTemplate] = {}
    for key, source in document.items():
        if not key:
            raise RuntimeError(f"i18n catalog {language!r} contains an empty key")
        if not isinstance(source, str):
            raise TypeError(f"i18n catalog {language!r} message {key!r} must be text")
        templates[key] = TextTemplate.from_source(
            f"resources/locales/{language}.toml:{key}",
            source,
        )
    return MappingProxyType(templates)


_ENGLISH_TEMPLATES = _load_catalog(ENGLISH)
_SCHINESE_TEMPLATES = _load_catalog(SIMPLIFIED_CHINESE)

if _ENGLISH_TEMPLATES.keys() != _SCHINESE_TEMPLATES.keys():
    raise RuntimeError("i18n catalogs must contain the same message keys")
for _message_key, _english_template in _ENGLISH_TEMPLATES.items():
    if _english_template.variables != _SCHINESE_TEMPLATES[_message_key].variables:
        raise RuntimeError(
            f"i18n message {_message_key!r} must contain the same template variables"
        )


@dataclass(frozen=True, slots=True)
class Translator:
    language: str
    _messages: Mapping[str, TextTemplate] = field(repr=False)

    def text(
        self,
        key: str,
        arguments: Mapping[str, object] | None = None,
    ) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("message key must be non-empty text")
        template = self._messages.get(key, _ENGLISH_TEMPLATES.get(key))
        if template is None:
            return key
        return template.render(arguments)


def create_translator(configured_language: str | None) -> Translator:
    language = configured_language
    if language is None:
        try:
            system_language, _ = locale.getlocale()
        except ValueError:
            system_language = None
        except locale.Error:
            system_language = None
        language = SIMPLIFIED_CHINESE if system_language == "zh_CN" else ENGLISH
    if language == SIMPLIFIED_CHINESE:
        return Translator(
            language=SIMPLIFIED_CHINESE,
            _messages=_SCHINESE_TEMPLATES,
        )
    return Translator(language=ENGLISH, _messages=_ENGLISH_TEMPLATES)
