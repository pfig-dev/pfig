"""Smoke-test the stdio server entry point."""

from __future__ import annotations

import subprocess
import sys
import time


def test_python_module_stdio_server_starts() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "pfig.lsp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(0.5)
        returncode = proc.poll()
        stderr = ""
        if returncode is not None and proc.stderr:
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
        assert returncode is None, stderr
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
