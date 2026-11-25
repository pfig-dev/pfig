from pathlib import Path
from pprint import pprint
from typing import Any

from . import fs
from .logging import get_logger

logger = get_logger("cli")


class Printer:
    def __init__(self, object: Any):
        self.object = object

    def __call__(self):
        pprint(self.object)


def load_config_file(filepath: str | Path) -> str:
    """Load pfig configuration file content."""
    if not fs.exists(filepath):
        raise FileNotFoundError(f"Configuration file not found: {filepath}")

    if not str(filepath).endswith(".pfig"):
        logger.warning(f"File {filepath} doesn't have .pfig extension")

    try:
        return fs.read_text(filepath)
    except UnicodeDecodeError as e:
        raise ValueError(f"Could not read {filepath}: {e}")


def normalize_path_display(path: str) -> str:
    """Add ./ prefix to relative paths for consistent display in error messages.

    URIs (e.g. gs://...) are returned as-is.
    """
    if fs.is_uri(path):
        return path
    path_obj = Path(path)
    if not path_obj.is_absolute() and not path.startswith(("./", "../")):
        return f"./{path}"
    return path
