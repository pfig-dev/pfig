"""CLI entry point for ``python -m pfig.vis``."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from pfig.cli import main as cli_main

    old_argv = sys.argv
    sys.argv = [old_argv[0], "view", *(argv if argv is not None else old_argv[1:])]
    try:
        return cli_main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    sys.exit(main())
