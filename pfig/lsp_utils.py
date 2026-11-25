"""
LSP coordinate utilities for pfig.

Converts between pfig's 1-based (line, column) positions and LSP's 0-based
(line, character) positions. LSP uses 0-based indices for all ranges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .lexer import Position


def position_to_lsp(pos: "Position") -> dict[str, int]:
    """Convert pfig's 1-based (line, column) to LSP Position {line, character} (0-based).

    Args:
        pos: pfig Position with 1-based line and column

    Returns:
        LSP Position dict: {"line": int, "character": int}
    """
    return {
        "line": pos.line - 1,
        "character": pos.column - 1,
    }


def position_to_offset(source: str, line: int, column: int) -> int:
    """Convert 1-based (line, column) to byte offset in source.

    Args:
        source: Full source text
        line: 1-based line number
        column: 1-based column number

    Returns:
        Zero-based offset into source
    """
    lines = source.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return 0
    offset = sum(len(lines[i]) for i in range(line - 1))
    # column is 1-based; subtract 1 for 0-based index within line
    col_index = min(column - 1, len(lines[line - 1]))
    return offset + col_index


def offset_to_position(source: str, offset: int) -> tuple[int, int]:
    """Convert byte offset to 1-based (line, column).

    Args:
        source: Full source text
        offset: Zero-based offset into source

    Returns:
        (line, column) 1-based
    """
    lines = source.splitlines(keepends=True)
    if offset <= 0:
        return (1, 1)
    current = 0
    for i, line in enumerate(lines):
        line_len = len(line)
        if current + line_len > offset:
            return (i + 1, offset - current + 1)
        current += line_len
    # Past end of file
    if lines:
        return (len(lines), len(lines[-1].rstrip("\n\r")) + 1)
    return (1, 1)


def offset_to_lsp(source: str, offset: int) -> dict[str, int]:
    """Convert byte offset to LSP Position (0-based).

    Args:
        source: Full source text
        offset: Zero-based offset into source

    Returns:
        LSP Position dict: {"line": int, "character": int}
    """
    line, col = offset_to_position(source, offset)
    return {"line": line - 1, "character": col - 1}


def range_from_position(pos: "Position", length: int, source: str) -> dict[str, Any]:
    """Build LSP Range from start position and length.

    Args:
        pos: pfig Position (1-based) of range start
        length: Length of the span in characters
        source: Full source text for computing end position

    Returns:
        LSP Range: {"start": Position, "end": Position}
    """
    start_offset = position_to_offset(source, pos.line, pos.column)
    end_offset = min(start_offset + length, len(source))
    return {
        "start": offset_to_lsp(source, start_offset),
        "end": offset_to_lsp(source, end_offset),
    }
