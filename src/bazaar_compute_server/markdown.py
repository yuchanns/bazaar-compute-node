"""What an agent wrote, as it meant it to be read: Markdown, and code within
it coloured by what it is."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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
    del renderer, options
    token = tokens[index]
    language = token.info.strip().split(" ", 1)[0] if token.info else ""
    try:
        lexer = get_lexer_by_name(language) if language else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return env["code"](Markup(highlight(token.content, lexer, _FORMATTER))) + "\n"


def _image(
    renderer: Any,
    tokens: Sequence[Token],
    index: int,
    options: Mapping[str, Any],
    env: Mapping[str, Any],
) -> str:
    token = tokens[index]
    return env["image"](
        src=token.attrGet("src") or "",
        alt=renderer.renderInlineAsText(token.children or [], options, env),
    )


# html off: what the agent wrote is text, however it looks; a link is only a
# link on a scheme markdown-it lets through
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_MARKDOWN.add_render_rule("fence", _fence)
_MARKDOWN.add_render_rule("image", _image)


def render(
    text: str, *, code: Callable[[Markup], str], image: Callable[..., str]
) -> Markup:
    """The text as HTML; each code block drawn by `code`, given its
    highlighted content, and each image by `image`, given its `src` and
    `alt`, so both are the page's own."""

    return Markup(_MARKDOWN.render(text, {"code": code, "image": image}))


__all__ = ["render"]
