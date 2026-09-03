from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Fence:
    marker: str
    opening: str


def advance_fence(
    fence: Fence | None,
    segment: str,
    *,
    initial_line_boundary: bool,
    terminal_line_complete: bool,
) -> Fence | None:
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
            fence = Fence(marker=marker, opening=stripped)
            continue
        if stripped[0] != fence.marker[0]:
            continue
        marker_length = len(stripped) - len(stripped.lstrip(fence.marker[0]))
        if marker_length >= len(fence.marker) and not stripped[marker_length:].strip():
            fence = None
    return fence


def closing_suffix(content: str, fence: Fence | None) -> str:
    if fence is None:
        return ""
    separator = "" if content.endswith(("\n", "\r")) else "\n"
    return f"{separator}{fence.marker}"


def preferred_boundary(content: str, minimum: int, end: int) -> int | None:
    """Find the latest place at or after minimum where a split reads naturally."""

    for separator in ("\n\n", "\n"):
        index = content.rfind(separator, minimum, end)
        if index >= minimum:
            return index + len(separator)
    for index in range(end - 1, minimum - 1, -1):
        if content[index].isspace():
            return index + 1
    return None


__all__ = ["Fence", "advance_fence", "closing_suffix", "preferred_boundary"]
