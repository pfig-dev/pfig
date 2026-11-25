"""
pfig logging configuration and utilities.

Provides centralized logging setup for the pfig package with appropriate
formatters and handlers.
"""

import logging
import os
import sys
from typing import Optional

# Package logger
logger = logging.getLogger("pfig")


class ColorFormatter(logging.Formatter):
    """
    Simple level-based color formatter.

    Uses standard ANSI color slots so the user's terminal theme controls the
    actual palette.
    """

    DIM = "\x1b[2m"
    GREEN = "\x1b[32m"
    CYAN = "\x1b[36m"
    YELLOW = "\x1b[33m"
    RED = "\x1b[31m"
    BOLD_RED = "\x1b[1;31m"
    RESET = "\x1b[0m"

    FORMATS = {
        logging.DEBUG: DIM,
        logging.INFO: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
    }
    ROLES = {
        "added": GREEN,
        "removed": RED,
        "hunk": CYAN,
    }

    def format(self, record):
        # Accessing _fmt is the standard way to get the format string in Formatter subclasses
        # across Python versions, although technically internal.
        color = self.ROLES.get(
            getattr(record, "color_role", ""),
            self.FORMATS.get(record.levelno, ""),
        )
        log_fmt = color + (self._fmt or "") + self.RESET
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def supports_color(stream) -> bool:
    """
    Determine whether a given stream supports color.

    Honors the NO_COLOR de-facto standard and only enables colors on TTYs.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return stream.isatty()


def color_text(text: str, color_role: str) -> str:
    """Wrap text in a terminal-palette color role."""
    color = ColorFormatter.ROLES.get(color_role)
    if not color:
        return text
    return f"{color}{text}{ColorFormatter.RESET}"


def setup_logging(
    level: str = "INFO",
    format_string: Optional[str] = None,
    include_timestamp: bool = False,
    use_colors: bool = True,
) -> None:
    """
    Configure logging for the pfig package.

    Args:
        level: Logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
        format_string: Custom format string. If None, uses a sensible default
        include_timestamp: Whether to include timestamps in log messages
        use_colors: Whether to use colored output if supported (tty / NO_COLOR)
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Default format
    # Use %-14s for name to align (pfig.execution is 14 chars)
    # Use %-7s for levelname to align all levels (WARNING is 7 chars)
    if format_string is None:
        if include_timestamp:
            format_string = "%(asctime)s - %(name)-14s - %(levelname)-7s - %(message)s"
        else:
            format_string = "%(name)-14s - %(levelname)-7s - %(message)s"

    # Configure the root pfig logger
    logger.setLevel(numeric_level)

    # Remove any existing handlers to avoid duplicates
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)

    # Decide whether to use colors for this handler
    use_color_for_stderr = use_colors and supports_color(sys.stderr)

    # Create formatter
    if use_color_for_stderr:
        formatter = ColorFormatter(format_string)
    else:
        formatter = logging.Formatter(format_string)

    console_handler.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(console_handler)

    # Prevent propagation to root logger to avoid duplicate messages
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific pfig module.

    Args:
        name: Module name (e.g., 'parser', 'lexer', 'execution')

    Returns:
        Configured logger instance
    """
    return logging.getLogger(f"pfig.{name}")


# Initialize with default settings
setup_logging()
