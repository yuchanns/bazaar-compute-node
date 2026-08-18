from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Fence:
    marker: str
    opening: str


def split_markdown(content: str, *, limit: int) -> tuple[str, ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not content:
        return (content,)

    chunks: list[str] = []
    cursor = 0
    fence: _Fence | None = None
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
            next_fence = _advance_fence(
                fence,
                content[cursor:end],
                initial_line_boundary=(cursor == 0 or content[cursor - 1] in "\r\n"),
                terminal_line_complete=(end == len(content) or content[end] in "\r\n"),
            )
            suffix = _closing_suffix(prefix + content[cursor:end], next_fence)
            if len((prefix + content[cursor:end] + suffix).encode("utf-8")) <= limit:
                break
            end -= 1
            if end == cursor:
                raise ValueError(
                    "markdown fence closure exceeds the provider byte limit"
                )

        if end < len(content):
            minimum = cursor + max(1, (end - cursor) // 2)
            preferred = _preferred_boundary(content, cursor, end, minimum)
            if preferred is not None:
                preferred_fence = _advance_fence(
                    fence,
                    content[cursor:preferred],
                    initial_line_boundary=(
                        cursor == 0 or content[cursor - 1] in "\r\n"
                    ),
                    terminal_line_complete=(
                        preferred == len(content) or content[preferred] in "\r\n"
                    ),
                )
                preferred_suffix = _closing_suffix(
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


def _preferred_boundary(content: str, _: int, end: int, minimum: int) -> int | None:
    for separator in ("\n\n", "\n"):
        index = content.rfind(separator, minimum, end)
        if index >= minimum:
            return index + len(separator)
    for index in range(end - 1, minimum - 1, -1):
        if content[index].isspace():
            return index + 1
    return None


def _advance_fence(
    fence: _Fence | None,
    segment: str,
    *,
    initial_line_boundary: bool,
    terminal_line_complete: bool,
) -> _Fence | None:
    lines = segment.splitlines(keepends=True)
    for index, line in enumerate(lines):
        complete = (index > 0 or initial_line_boundary) and (
            line.endswith(("\n", "\r"))
            or (index == len(lines) - 1 and terminal_line_complete)
        )
        if not complete:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if fence is None:
            marker_char = stripped[0]
            if marker_char not in {"`", "~"}:
                continue
            marker_length = len(stripped) - len(stripped.lstrip(marker_char))
            if marker_length < 3:
                continue
            marker = marker_char * marker_length
            info = stripped[marker_length:]
            if marker_char == "`" and "`" in info:
                continue
            fence = _Fence(marker=marker, opening=stripped)
            continue
        if stripped[0] != fence.marker[0]:
            continue
        marker_length = len(stripped) - len(stripped.lstrip(fence.marker[0]))
        if marker_length >= len(fence.marker) and not stripped[marker_length:].strip():
            fence = None
    return fence


def _closing_suffix(content: str, fence: _Fence | None) -> str:
    if fence is None:
        return ""
    separator = "" if content.endswith(("\n", "\r")) else "\n"
    return f"{separator}{fence.marker}"


__all__ = ["split_markdown"]
