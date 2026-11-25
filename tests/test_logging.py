import io
import logging

from pfig.logging import ColorFormatter, color_text, logger, setup_logging, supports_color


class TtyStringIO(io.StringIO):
    def isatty(self):
        return True


class NonTtyStringIO(io.StringIO):
    def isatty(self):
        return False


def test_color_formatter_uses_terminal_palette_slots():
    formatter = ColorFormatter("%(levelname)s:%(message)s")

    cases = [
        (logging.DEBUG, "\x1b[2m"),
        (logging.INFO, "\x1b[36m"),
        (logging.WARNING, "\x1b[33m"),
        (logging.ERROR, "\x1b[31m"),
        (logging.CRITICAL, "\x1b[1;31m"),
    ]

    for level, prefix in cases:
        record = logging.LogRecord("pfig", level, __file__, 1, "message", (), None)
        formatted = formatter.format(record)
        assert formatted.startswith(prefix)
        assert formatted.endswith("\x1b[0m")


def test_color_formatter_uses_diff_color_roles():
    formatter = ColorFormatter("%(message)s")

    cases = [
        ("added", "\x1b[32m"),
        ("removed", "\x1b[31m"),
        ("hunk", "\x1b[36m"),
    ]

    for role, prefix in cases:
        record = logging.LogRecord("pfig", logging.INFO, __file__, 1, "message", (), None)
        record.color_role = role
        formatted = formatter.format(record)
        assert formatted == f"{prefix}message\x1b[0m"


def test_color_text_wraps_known_roles():
    assert color_text("line\n", "added") == "\x1b[32mline\n\x1b[0m"
    assert color_text("line\n", "unknown") == "line\n"


def test_supports_color_requires_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)

    assert supports_color(TtyStringIO()) is True
    assert supports_color(NonTtyStringIO()) is False


def test_supports_color_honors_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")

    assert supports_color(TtyStringIO()) is False


def test_setup_logging_uses_plain_formatter_when_colors_disabled(monkeypatch):
    stream = TtyStringIO()
    monkeypatch.setattr("sys.stderr", stream)

    setup_logging(format_string="%(levelname)s:%(message)s", use_colors=False)
    logger.info("plain")

    assert stream.getvalue() == "INFO:plain\n"
    assert not isinstance(logger.handlers[0].formatter, ColorFormatter)


def test_setup_logging_uses_color_formatter_for_tty(monkeypatch):
    stream = TtyStringIO()
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stderr", stream)

    setup_logging(format_string="%(levelname)s:%(message)s", use_colors=True)
    logger.info("colored")

    assert stream.getvalue().startswith("\x1b[36mINFO:colored")
    assert isinstance(logger.handlers[0].formatter, ColorFormatter)
