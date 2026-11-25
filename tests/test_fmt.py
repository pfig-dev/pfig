"""Tests for ``pfig fmt`` — the formatter.

Fixture-based: each subdirectory under ``tests/fmt/cases/`` is a single case
containing ``input.pfig`` and ``expected.pfig``. The runner formats the input
and compares against expected. Idempotency is asserted too: formatting the
expected output must be a no-op.

Set ``PFIG_FMT_UPDATE=1`` to rewrite every ``expected.pfig`` (and the locked
snapshots under ``tests/fmt/examples/``) from current formatter output —
useful when you intentionally changed the formatter. Always review
``git diff`` before committing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pfig.fmt import FormatError, FormatOptions, format_source

CASES_DIR = Path(__file__).parent / "fmt" / "cases"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
EXAMPLES_SNAPSHOT_DIR = Path(__file__).parent / "fmt" / "examples"
UPDATE_SNAPSHOTS = os.environ.get("PFIG_FMT_UPDATE") == "1"


def _discover_cases() -> list[tuple[str, Path, Path]]:
    if not CASES_DIR.exists():
        return []
    cases = []
    for case_dir in sorted(CASES_DIR.iterdir()):
        if not case_dir.is_dir():
            continue
        inp = case_dir / "input.pfig"
        exp = case_dir / "expected.pfig"
        if inp.exists() and exp.exists():
            cases.append((case_dir.name, inp, exp))
    return cases


@pytest.mark.parametrize(
    "name,input_path,expected_path",
    _discover_cases(),
    ids=[c[0] for c in _discover_cases()],
)
def test_fmt_fixture(name: str, input_path: Path, expected_path: Path) -> None:
    source = input_path.read_text(encoding="utf-8")
    actual = format_source(source, filename=str(input_path))

    if UPDATE_SNAPSHOTS:
        expected_path.write_text(actual, encoding="utf-8")
        return

    expected = expected_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"\n--- expected ({expected_path}) ---\n{expected}\n--- actual ---\n{actual}"
    )
    # Idempotency: formatting the expected output is a no-op.
    assert format_source(expected) == expected, (
        f"{name}: expected.pfig is not a fixed point of format_source"
    )
    # Also: formatting the *input* twice is a fixed point.
    assert format_source(actual) == actual, (
        f"{name}: format(input) is not a fixed point of format_source"
    )


def test_empty_input() -> None:
    assert format_source("") == ""


def test_parse_error_raises_format_error() -> None:
    with pytest.raises(FormatError):
        format_source("=== not valid pfig ===")


def test_line_length_option_changes_wrapping() -> None:
    src = "x = [11111, 22222, 33333]\n"
    # At 100 chars the list fits on one line.
    assert format_source(src) == src
    # At a very small budget the list wraps.
    wrapped = format_source(src, options=FormatOptions(line_length=10))
    assert wrapped == ("x = [\n    11111,\n    22222,\n    33333,\n]\n")


def test_magic_trailing_comma_forces_multiline() -> None:
    """A trailing comma in the input forces multi-line even when content fits."""
    src = "x = [1, 2, 3,]\n"
    assert format_source(src) == "x = [\n    1,\n    2,\n    3,\n]\n"


def _curated_examples() -> list[Path]:
    """The curated example sources (skips auto-generated snapshot artifacts)."""
    if not EXAMPLES_DIR.exists():
        return []
    return [
        p
        for p in sorted(EXAMPLES_DIR.rglob("*.pfig"))
        if "snapshots" not in p.relative_to(EXAMPLES_DIR).parts
    ]


def test_idempotent_on_examples() -> None:
    """Formatting the existing example .pfig files must be idempotent."""
    for pfig_file in _curated_examples():
        source = pfig_file.read_text(encoding="utf-8")
        once = format_source(source, filename=str(pfig_file))
        twice = format_source(once, filename=str(pfig_file))
        assert once == twice, f"{pfig_file} is not idempotent under format_source"


def _discover_examples() -> list[tuple[str, Path, Path]]:
    """Pair each curated examples/*.pfig with its locked snapshot path."""
    out: list[tuple[str, Path, Path]] = []
    for src in _curated_examples():
        rel = src.relative_to(EXAMPLES_DIR)
        snapshot = EXAMPLES_SNAPSHOT_DIR / rel
        out.append((str(rel), src, snapshot))
    return out


@pytest.mark.parametrize(
    "name,source_path,snapshot_path",
    _discover_examples(),
    ids=[c[0] for c in _discover_examples()],
)
def test_examples_snapshot(name: str, source_path: Path, snapshot_path: Path) -> None:
    """Lock each example's formatted output so a regression in the formatter
    surfaces here. ``PFIG_FMT_UPDATE=1`` regenerates the snapshots."""
    source = source_path.read_text(encoding="utf-8")
    actual = format_source(source, filename=str(source_path))

    if UPDATE_SNAPSHOTS:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(actual, encoding="utf-8")
        return

    if not snapshot_path.exists():
        pytest.fail(
            f"Missing snapshot for {name}: {snapshot_path}\n"
            "Run with PFIG_FMT_UPDATE=1 to generate, then review and commit."
        )
    expected = snapshot_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"\n--- snapshot ({snapshot_path}) ---\n{expected}\n--- actual ---\n{actual}"
    )
