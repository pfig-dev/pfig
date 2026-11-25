"""Entry point for ``python -m pfig.lsp``."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from pfig.lsp.server import create_server
    except ModuleNotFoundError as exc:
        if exc.name in {"lsprotocol", "pygls"}:
            print(
                "pfig LSP dependencies are not installed. "
                'Install them with: python -m pip install "pfig[lsp]"',
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        raise

    server = create_server()
    server.start_io()


if __name__ == "__main__":
    main()
