"""Validation via the pfig package's ``compile_pfig()`` API.

Delegates all semantic validation to the pfig compiler. Uses pfig's
ValidationIssue with position_to_lsp for 0-based coordinates.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from lsprotocol.types import Diagnostic, DiagnosticSeverity, Position, Range

from pfig.lsp.analysis.resolver import _uri_to_path

try:
    from pfig.lsp_utils import position_to_lsp
except ImportError:
    position_to_lsp = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from pfig.lsp.state import DocumentIndex

logger = logging.getLogger(__name__)


def _is_profile_only(index: DocumentIndex | None) -> bool:
    """Best-effort heuristic: file is a profile if it has overrides but no config content.

    Per the pfig spec, profiles can contain: override, includes, imports.
    They cannot contain: regular bindings, provider definitions.
    Profiles are applied at runtime against a config; override targets refer to
    bindings in that config, not in the profile. Skip semantic validation to
    avoid false override_not_found errors.

    Boundaries (when this returns False, full ``compile_pfig`` validation runs):

    - **Includes or externals** — treated as config surface; may still need merge context.
    - **Call sites in override RHS** — the indexer records provider calls in override
      values; such files are not classified as profile-only, so editor validation matches
      the pre-heuristic behavior (including possible spurious override errors for profiles
      that only override with ``SomeProvider(...)``).
    - **Refs** — not part of this predicate; ``@`` references in override values may
      still be diagnosed by other LSP passes (e.g. provider ref resolution).

    Lexer/parser errors are always reported before this check runs.
    """
    if index is None:
        return False
    if not index.overrides:
        return False
    # Has overrides but no config content: no bindings, includes, externals, providers
    # (imports are allowed in profiles)
    return (
        not index.binding_defs
        and not index.external_defs
        and not index.includes
        and not index.externals
        and not index.call_sites
    )


def validate_with_pfig(
    text: str,
    uri: str,
    *,
    profiles: list[str] | None = None,
    set_overrides: dict[str, str] | None = None,
    allow_missing_externals: bool = False,
    strict: bool = False,
    index: DocumentIndex | None = None,
) -> tuple[list[Diagnostic], dict | None]:
    """Parse and validate *text* using the pfig package.

    Returns (diagnostics, compiled). compiled has include_graph for dependent revalidation.
    """
    try:
        from pfig import compile_pfig, parse_pfig_source
        from pfig.lexer import LexerError
        from pfig.parser import ParseError
    except ImportError:
        logger.warning("pfig package not available; skipping validation")
        return [], None

    filename = _uri_to_path(uri)

    diagnostics: list[Diagnostic] = []

    try:
        ast = parse_pfig_source(text, filename=filename)
    except (LexerError, ParseError) as exc:
        diagnostics.append(_exception_to_diagnostic(exc))
        return diagnostics, None
    except Exception as exc:
        logger.debug("pfig parse failed unexpectedly: %s", exc, exc_info=True)
        diagnostics.append(
            Diagnostic(
                range=Range(Position(0, 0), Position(0, 0)),
                message=str(exc),
                severity=DiagnosticSeverity.Error,
                source="pfig",
            )
        )
        return diagnostics, None

    # Profile-only files: override targets refer to the config at runtime, not this file.
    # Skip semantic validation to avoid false override_not_found errors.
    if _is_profile_only(index):
        return diagnostics, None

    try:
        compiled, issues = compile_pfig(
            ast,
            filename=filename,
            externals=set_overrides or {},
            profile_paths=profiles or [],
            strict=strict,
            allow_missing_externals=allow_missing_externals,
        )
    except Exception as exc:
        logger.debug("pfig compile_pfig failed unexpectedly: %s", exc, exc_info=True)
        diagnostics.append(_exception_to_diagnostic(exc))
        return diagnostics, None

    for issue in issues:
        diagnostics.append(_issue_to_diagnostic(issue, index))

    return diagnostics, compiled


def _range_for_unknown_symbol(symbol: str, index: DocumentIndex) -> Range | None:
    """Find the range of a symbol in the document index."""
    # Import: from X import Y - Y is the unknown symbol
    if symbol in index.imports:
        return index.imports[symbol].range
    # Call site callee: MLPs(...) or transforms.ToTensors(...)
    for cs in index.call_sites:
        if cs.callee_range and (cs.callee == symbol or cs.callee.endswith("." + symbol)):
            return cs.callee_range
    # Provider/symbol ref
    for ref in index.refs:
        if ref.name == symbol and ref.range:
            return ref.range
    return None


def _range_for_unknown_kwarg(kwarg_name: str, index: DocumentIndex) -> Range | None:
    """Find the range of a kwarg name in call sites."""
    for cs in index.call_sites:
        if kwarg_name in cs.kwargs:
            return cs.kwargs[kwarg_name]
    return None


def _range_for_include_error(issue: object, index: DocumentIndex) -> Range | None:
    """Find the range of the include path for include_error/include_cycle issues."""
    pos = getattr(issue, "position", None)
    if not pos:
        return None

    # Convert pfig position to 0-based (line, char)
    line, char = 0, 0
    if position_to_lsp is not None:
        try:
            lsp_pos = position_to_lsp(pos)
            line = lsp_pos["line"]
            char = lsp_pos["character"]
        except (AttributeError, TypeError, KeyError):
            return None
    elif hasattr(pos, "line") and hasattr(pos, "column"):
        line = max(0, (pos.line or 1) - 1)
        char = max(0, (pos.column or 1) - 1)
    else:
        return None

    # Find the IncludeEdge whose range contains this position
    for inc in index.includes:
        r = inc.range
        if r.start.line <= line <= r.end.line:
            if line == r.start.line and line == r.end.line:
                if r.start.character <= char <= r.end.character:
                    return r
            elif line == r.start.line:
                if char >= r.start.character:
                    return r
            elif line == r.end.line:
                if char <= r.end.character:
                    return r
            else:
                return r
    return None


def _issue_to_diagnostic(issue: object, index: DocumentIndex | None = None) -> Diagnostic:
    """Convert a ``ValidationIssue`` to an LSP ``Diagnostic``."""
    pos = getattr(issue, "position", None)
    message = getattr(issue, "message", str(issue))
    code = getattr(issue, "code", "")

    severity = DiagnosticSeverity.Error
    if code in ("missing_external",):
        severity = DiagnosticSeverity.Warning

    r: Range | None = None

    # Fix wrong ranges from pfig: use symbol location from index when available
    if index:
        if code == "import_error":
            # "module 'X' has no attribute 'Y'" -> underline Y, not module
            match = re.search(r"has no attribute ['\"]([^'\"]+)['\"]", message)
            if match:
                symbol = match.group(1)
                r = _range_for_unknown_symbol(symbol, index)
        elif code == "compile_error":
            if message.startswith("Unknown import: "):
                # "Unknown import: MLPs" -> underline MLPs, clearer message
                symbol = message[len("Unknown import: ") :].strip()
                r = _range_for_unknown_symbol(symbol, index)
                message = f"Unknown Python symbol: {symbol}"
            elif message.startswith("Unknown attribute: "):
                # "Unknown attribute: module 'X' has no attribute 'Y'" -> underline Y
                match = re.search(r"has no attribute ['\"]([^'\"]+)['\"]", message)
                if match:
                    symbol = match.group(1)
                    r = _range_for_unknown_symbol(symbol, index)
        elif code == "unknown_kwarg":
            # "Unknown kwargs for 'X': A, B, C. Valid parameters are: ..." -> underline kwarg
            match = re.search(r"Unknown kwargs for [^:]+:\s*([^.]+)\.", message)
            if match:
                kwarg_list = match.group(1).strip()
                for name in (n.strip() for n in kwarg_list.split(",")):
                    if name and (r := _range_for_unknown_kwarg(name, index)):
                        break
        elif code in ("include_error", "include_cycle"):
            # Underline the full include path string, not just the first character
            r = _range_for_include_error(issue, index)
        elif code == "override_not_found":
            # Underline the override target path (e.g. consts/epochs), not just first char
            binding = getattr(issue, "binding", None)
            if binding and binding in index.overrides:
                r = index.overrides[binding]

    if r is None and pos:
        if position_to_lsp is not None:
            try:
                lsp_pos = position_to_lsp(pos)
                start = Position(lsp_pos["line"], lsp_pos["character"])
                r = Range(start, Position(start.line, start.character + 1))
            except (AttributeError, TypeError, KeyError):
                pass
        if r is None and hasattr(pos, "line"):
            line = max(0, pos.line - 1)
            col = max(0, (pos.column or 1) - 1)
            r = Range(Position(line, col), Position(line, col + 1))

    if r is None:
        r = Range(Position(0, 0), Position(0, 0))

    return Diagnostic(
        range=r,
        message=message,
        severity=severity,
        source="pfig",
        code=code or None,
    )


def _exception_to_diagnostic(exc: Exception) -> Diagnostic:
    """Convert a parse/lexer/execution exception to an LSP ``Diagnostic``."""
    pos = getattr(exc, "position", None)
    r: Range
    if pos and position_to_lsp is not None:
        try:
            lsp_pos = position_to_lsp(pos)
            start = Position(lsp_pos["line"], lsp_pos["character"])
            r = Range(start, Position(start.line, start.character + 1))
        except (AttributeError, TypeError, KeyError):
            r = Range(Position(0, 0), Position(0, 0))
    elif pos and hasattr(pos, "line"):
        line = max(0, pos.line - 1)
        col = max(0, (pos.column or 1) - 1)
        r = Range(Position(line, col), Position(line, col + 1))
    else:
        r = Range(Position(0, 0), Position(0, 0))

    return Diagnostic(
        range=r,
        message=str(exc),
        severity=DiagnosticSeverity.Error,
        source="pfig",
    )
