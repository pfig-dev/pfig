"""Python symbol resolution using Jedi with optional runtime fallback.

Provides:
- Go-to-definition for dotted Python symbols
- Hover information (signature + docstring excerpt)
- Callable parameter listing for kwarg completion
"""

from __future__ import annotations

import importlib
import inspect
import logging
from typing import Any

from lsprotocol.types import (
    Location,
    ParameterInformation,
    Position,
    Range,
    SignatureInformation,
)

from pfig.lsp.state import ImportBinding

logger = logging.getLogger(__name__)

try:
    import jedi
except ImportError:
    jedi = None  # type: ignore[assignment]


class PythonResolver:
    """Resolves Python symbols for the pfig LSP."""

    def __init__(self) -> None:
        self._jedi_project: Any = None

    def _get_jedi_script(self, source: str = "") -> Any:
        if jedi is None:
            return None
        try:
            return jedi.Script(source, project=self._jedi_project)
        except Exception:
            logger.debug("Failed to create Jedi script", exc_info=True)
            return None

    # -------------------------------------------------------------------
    # Go to definition
    # -------------------------------------------------------------------

    def goto_definition(
        self,
        symbol: str,
        imports: dict[str, ImportBinding],
        use_runtime: bool = False,
    ) -> Location | None:
        resolved = self._resolve_symbol(symbol, imports)
        if resolved is None:
            return None

        if jedi is not None and not use_runtime:
            loc = self._jedi_goto(resolved)
            if loc:
                return loc

        return self._runtime_goto(resolved)

    def _resolve_symbol(self, symbol: str, imports: dict[str, ImportBinding]) -> str | None:
        """Expand a symbol using the import table."""
        parts = symbol.split(".")
        first = parts[0]
        if first in imports:
            imp = imports[first]
            if imp.imported_name:
                base = f"{imp.module}.{imp.imported_name}"
            else:
                base = imp.module
            if len(parts) > 1:
                return f"{base}.{'.'.join(parts[1:])}"
            return base
        return symbol

    def _jedi_goto(self, dotted: str) -> Location | None:
        if jedi is None:
            return None
        try:
            source = f"import {dotted.rsplit('.', 1)[0]}\n{dotted}"
            script = jedi.Script(source)
            defs = script.goto(2, 0)
            if defs:
                d = defs[0]
                if d.module_path:
                    return Location(
                        uri=f"file://{d.module_path}",
                        range=Range(
                            start=Position(
                                line=max(0, (d.line or 1) - 1),
                                character=(d.column or 0),
                            ),
                            end=Position(
                                line=max(0, (d.line or 1) - 1),
                                character=(d.column or 0),
                            ),
                        ),
                    )
        except Exception:
            logger.debug("Jedi goto failed for %s", dotted, exc_info=True)
        return None

    def _runtime_goto(self, dotted: str) -> Location | None:
        try:
            parts = dotted.rsplit(".", 1)
            if len(parts) == 2:
                mod = importlib.import_module(parts[0])
                obj = getattr(mod, parts[1], None)
            else:
                mod = importlib.import_module(dotted)
                obj = mod

            if obj is None:
                return None

            source_file = inspect.getfile(obj)
            try:
                _, lineno = inspect.getsourcelines(obj)
            except (OSError, TypeError):
                lineno = 1

            return Location(
                uri=f"file://{source_file}",
                range=Range(
                    start=Position(line=max(0, lineno - 1), character=0),
                    end=Position(line=max(0, lineno - 1), character=0),
                ),
            )
        except Exception:
            logger.debug("Runtime goto failed for %s", dotted, exc_info=True)
            return None

    # -------------------------------------------------------------------
    # Hover
    # -------------------------------------------------------------------

    def get_hover_info(self, symbol: str, imports: dict[str, ImportBinding]) -> str | None:
        resolved = self._resolve_symbol(symbol, imports)
        if resolved is None:
            return None

        if jedi is not None:
            try:
                source = f"import {resolved.rsplit('.', 1)[0]}\n{resolved}"
                script = jedi.Script(source)
                names = script.help(2, 0)
                if names:
                    n = names[0]
                    parts = []
                    sigs = n.get_signatures()
                    if sigs:
                        parts.append(f"```python\n{sigs[0].to_string()}\n```")
                    doc = n.docstring(raw=True)
                    if doc:
                        first_line = doc.strip().split("\n")[0]
                        parts.append(first_line)
                    if parts:
                        return "\n\n".join(parts)
            except Exception:
                pass

        try:
            parts = resolved.rsplit(".", 1)
            if len(parts) == 2:
                mod = importlib.import_module(parts[0])
                obj = getattr(mod, parts[1], None)
            else:
                return None

            if obj is None:
                return None

            result_parts = []
            try:
                sig = inspect.signature(obj)
                result_parts.append(f"```python\n{resolved}{sig}\n```")
            except (ValueError, TypeError):
                pass

            doc = inspect.getdoc(obj)
            if doc:
                result_parts.append(doc.strip().split("\n")[0])

            return "\n\n".join(result_parts) if result_parts else None
        except Exception:
            return None

    # -------------------------------------------------------------------
    # Signature help
    # -------------------------------------------------------------------

    def get_signature(
        self, callee: str, imports: dict[str, ImportBinding]
    ) -> SignatureInformation | None:
        resolved = self._resolve_symbol(callee, imports)
        if resolved is None:
            return None

        if jedi is not None:
            try:
                source = f"import {resolved.rsplit('.', 1)[0]}\n{resolved}("
                script = jedi.Script(source)
                sigs = script.get_signatures(2, len(f"{resolved}("))
                if sigs:
                    s = sigs[0]
                    params = [ParameterInformation(label=p.name) for p in s.params]
                    return SignatureInformation(
                        label=s.to_string(),
                        parameters=params,
                    )
            except Exception:
                pass

        try:
            parts = resolved.rsplit(".", 1)
            if len(parts) == 2:
                mod = importlib.import_module(parts[0])
                obj = getattr(mod, parts[1], None)
            else:
                return None

            if obj is None:
                return None

            sig = inspect.signature(obj)
            params = [ParameterInformation(label=p.name) for p in sig.parameters.values()]
            return SignatureInformation(
                label=f"{resolved}{sig}",
                parameters=params,
            )
        except Exception:
            return None

    # -------------------------------------------------------------------
    # Kwarg completion
    # -------------------------------------------------------------------

    def get_callable_params(self, callee: str, imports: dict[str, ImportBinding]) -> list[str]:
        resolved = self._resolve_symbol(callee, imports)
        if resolved is None:
            return []

        if jedi is not None:
            try:
                source = f"import {resolved.rsplit('.', 1)[0]}\n{resolved}("
                script = jedi.Script(source)
                sigs = script.get_signatures(2, len(f"{resolved}("))
                if sigs:
                    return [p.name for p in sigs[0].params]
            except Exception:
                pass

        try:
            parts = resolved.rsplit(".", 1)
            if len(parts) == 2:
                mod = importlib.import_module(parts[0])
                obj = getattr(mod, parts[1], None)
            else:
                return []

            if obj is None:
                return []

            sig = inspect.signature(obj)
            return list(sig.parameters.keys())
        except Exception:
            return []
