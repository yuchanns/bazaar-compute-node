from __future__ import annotations

from ...core.markdown import (
    Fence,
    advance_fence,
    closing_suffix,
    preferred_boundary,
)


def split_markdown(content: str, *, limit: int) -> tuple[str, ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not content:
        return (content,)

    chunks: list[str] = []
    cursor = 0
    fence: Fence | None = None
    while cursor < len(content):
        prefix = f"{fence.opening}\n" if fence is not None else ""
        prefix_bytes = len(prefix.encode("utf-8"))
        end = cursor
        size = prefix_bytes
        while end < len(content):
            encoded = content[end].encode("utf-8")
            if size + len(encoded) > limit:
                break
            size += len(encoded)
            end += 1
        if end == cursor:
            raise ValueError("markdown fence overhead exceeds the provider byte limit")

        while True:
            next_fence = advance_fence(
                fence,
                content[cursor:end],
                initial_line_boundary=(cursor == 0 or content[cursor - 1] in "\r\n"),
                terminal_line_complete=(end == len(content) or content[end] in "\r\n"),
            )
            suffix = closing_suffix(prefix + content[cursor:end], next_fence)
            if len((prefix + content[cursor:end] + suffix).encode("utf-8")) <= limit:
                break
            end -= 1
            if end == cursor:
                raise ValueError(
                    "markdown fence closure exceeds the provider byte limit"
                )

        if end < len(content):
            minimum = cursor + max(1, (end - cursor) // 2)
            preferred = preferred_boundary(content, minimum, end)
            if preferred is not None:
                preferred_fence = advance_fence(
                    fence,
                    content[cursor:preferred],
                    initial_line_boundary=(
                        cursor == 0 or content[cursor - 1] in "\r\n"
                    ),
                    terminal_line_complete=(
                        preferred == len(content) or content[preferred] in "\r\n"
                    ),
                )
                preferred_suffix = closing_suffix(
                    prefix + content[cursor:preferred], preferred_fence
                )
                if (
                    len(
                        (prefix + content[cursor:preferred] + preferred_suffix).encode(
                            "utf-8"
                        )
                    )
                    <= limit
                ):
                    end = preferred
                    next_fence = preferred_fence
                    suffix = preferred_suffix

        chunks.append(prefix + content[cursor:end] + suffix)
        cursor = end
        fence = next_fence

    return tuple(chunks)


__all__ = ["split_markdown"]
