"""Tests for dependency-light core imports."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_core_imports_without_lsp_optional_dependencies() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = """
import importlib.util

assert importlib.util.find_spec("lsprotocol") is None
assert importlib.util.find_spec("pygls") is None

import pfig
import pfig.lsp
from pfig import index_from_ast, parse_pfig_source, resolve_include_path

ast = parse_pfig_source("x = 1\\n")
compiled, issues = pfig.compile_pfig(ast, filename="<string>")
assert issues == []
assert "x" in compiled["values"]

result = parse_pfig_source("x = 1\\n", with_index=True)
assert "x" in result.index.binding_defs
assert index_from_ast is not None
assert resolve_include_path("./missing.pfig", "/tmp/main.pfig") is None
"""
    subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_lsp_entrypoint_without_optional_dependencies_has_clear_error() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-S", "-m", "pfig.lsp"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    assert "pfig[lsp]" in proc.stderr
    assert "Traceback" not in proc.stderr
