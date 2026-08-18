from __future__ import annotations

import locale
from collections.abc import Mapping
from dataclasses import dataclass, field
from string import Template

from .english import MESSAGES as ENGLISH_MESSAGES
from .schinese import MESSAGES as SCHINESE_MESSAGES

ENGLISH = "en"
SIMPLIFIED_CHINESE = "zh-CN"

if ENGLISH_MESSAGES.keys() != SCHINESE_MESSAGES.keys():
    raise RuntimeError("i18n catalogs must contain the same message keys")


@dataclass(frozen=True, slots=True)
class Translator:
    language: str
    _messages: Mapping[str, str] = field(repr=False)

    def text(
        self,
        key: str,
        arguments: Mapping[str, object] | None = None,
    ) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("message key must be non-empty text")
        template = self._messages.get(key, ENGLISH_MESSAGES.get(key, key))
        return Template(template).substitute(arguments or {})


def create_translator(configured_language: str | None) -> Translator:
    language = configured_language
    if language is None:
        try:
            system_language, _encoding = locale.getlocale()
        except ValueError, locale.Error:
            system_language = None
        language = SIMPLIFIED_CHINESE if system_language == "zh_CN" else ENGLISH
    if language == SIMPLIFIED_CHINESE:
        return Translator(language=SIMPLIFIED_CHINESE, _messages=SCHINESE_MESSAGES)
    return Translator(language=ENGLISH, _messages=ENGLISH_MESSAGES)
