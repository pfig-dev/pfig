#!/usr/bin/env python3

"""
Entry point for running pfig as a module: python -m pfig
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
