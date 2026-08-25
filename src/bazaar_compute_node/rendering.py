from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from jinja2 import Environment, PackageLoader, StrictUndefined, Template, meta
from jinja2.exceptions import TemplateSyntaxError

_RESOURCE_LOADER = PackageLoader("bazaar_compute_node", "resources")
_TEXT_ENVIRONMENT = Environment(
    loader=_RESOURCE_LOADER,
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
    auto_reload=False,
    enable_async=False,
)


@dataclass(frozen=True, slots=True)
class TextTemplate:
    name: str
    variables: frozenset[str]
    _template: Template = field(repr=False)

    @classmethod
    def from_resource(cls, name: str) -> TextTemplate:
        if not name:
            raise ValueError("template name must be non-empty text")
        source, filename, _ = _RESOURCE_LOADER.get_source(_TEXT_ENVIRONMENT, name)
        return cls._compile(
            name,
            source,
            filename=filename,
            template=_TEXT_ENVIRONMENT.get_template(name),
        )

    @classmethod
    def from_source(cls, name: str, source: str) -> TextTemplate:
        if not name:
            raise ValueError("template name must be non-empty text")
        return cls._compile(name, source)

    @classmethod
    def _compile(
        cls,
        name: str,
        source: str,
        *,
        filename: str | None = None,
        template: Template | None = None,
    ) -> TextTemplate:
        try:
            parsed = _TEXT_ENVIRONMENT.parse(source, name=name, filename=filename)
            compiled = template or _TEXT_ENVIRONMENT.from_string(source)
        except TemplateSyntaxError as error:
            raise ValueError(f"invalid text template {name!r}: {error}") from error
        return cls(
            name=name,
            variables=frozenset(meta.find_undeclared_variables(parsed)),
            _template=compiled,
        )

    def render(self, arguments: Mapping[str, object] | None = None) -> str:
        values = arguments or {}
        if any(not isinstance(key, str) or not key for key in values):
            raise ValueError("template argument keys must be non-empty text")
        supplied = frozenset(values)
        missing = self.variables - supplied
        unexpected = supplied - self.variables
        if missing or unexpected:
            details: list[str] = []
            if missing:
                details.append(f"missing: {', '.join(sorted(missing))}")
            if unexpected:
                details.append(f"unexpected: {', '.join(sorted(unexpected))}")
            raise ValueError(
                f"template {self.name!r} arguments do not match variables "
                f"({'; '.join(details)})"
            )
        return self._template.render(dict(values))


__all__ = ["TextTemplate"]
