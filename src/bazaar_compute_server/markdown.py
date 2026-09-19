"""What an agent wrote, as it meant it to be read: Markdown, and code within
it coloured by what it is."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.lexers.special import TextLexer
from pygments.util import ClassNotFound

# tokens by class only, prefixed so they meet none of the page's own; the
# colours are the page's, in its stylesheet
_FORMATTER = HtmlFormatter(nowrap=True, classprefix="tok-")


def _fence(
    renderer: Any,
    tokens: Sequence[Token],
    index: int,
    options: Mapping[str, Any],
    env: Mapping[str, Any],
) -> str:
    del renderer, options, env
    token = tokens[index]
    language = token.info.strip().split(" ", 1)[0] if token.info else ""
    try:
        lexer = get_lexer_by_name(language) if language else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return f'<pre class="code"><code>{highlight(token.content, lexer, _FORMATTER)}</code></pre>\n'


# html off: what the agent wrote is text, however it looks; a link is only a
# link on a scheme markdown-it lets through
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_MARKDOWN.add_render_rule("fence", _fence)


def render(text: str) -> Markup:
    return Markup(_MARKDOWN.render(text))


__all__ = ["render"]
