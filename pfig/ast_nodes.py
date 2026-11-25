"""
pfig AST Node Classes - Represents the parsed pfig configuration structure.

These nodes correspond to the formal grammar defined in §3 of the specification.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from .lexer import Position


@dataclass
class ASTNode(ABC):
    """Base class for all AST nodes with source position tracking."""

    position: Position


# ===== Top-level Statements =====


@dataclass
class ImportStmt(ASTNode):
    """import dotted_name [as IDENT]"""

    module: str
    alias: Optional[str] = None
    module_position: Optional[Position] = None  # For LSP (span of module path)
    alias_position: Optional[Position] = None  # For LSP (when "as alias" present)


@dataclass
class FromImportStmt(ASTNode):
    """from dotted_name import IDENT [, IDENT]*"""

    module: str
    names: List[ImportItem]
    module_position: Optional[Position] = None  # For LSP (span of module path)


@dataclass
class ImportItem:
    """Helper for import items with optional aliases."""

    name: str
    alias: Optional[str] = None
    name_position: Optional[Position] = None  # For LSP (span of imported name)
    alias_position: Optional[Position] = None  # For LSP (when "as alias" present)


@dataclass
class IncludeStmt(ASTNode):
    """include STRING as IDENT"""

    path: str
    alias: str
    path_position: Optional[Position] = None  # Position of path string (for LSP)


# FromIncludeStmt removed - only explicit aliasing with 'include "file" as alias' is supported


@dataclass
class ExternalStmt(ASTNode):
    """external path"""

    path: PathExpr


@dataclass
class OverrideStmt(ASTNode):
    """override path = expr"""

    path: PathExpr
    value: Expr


@dataclass
class Binding(ASTNode):
    """IDENT = expr"""

    name: str
    value: Expr


# ===== Expressions =====


@dataclass
class Expr(ASTNode, ABC):
    """Base class for all expressions."""

    pass


@dataclass
class PathExpr(Expr):
    """Namespace path: IDENT [/ IDENT]*"""

    parts: List[str]

    def __str__(self) -> str:
        return "/".join(self.parts)


@dataclass
class DottedNameExpr(Expr):
    """Dotted name: IDENT [. IDENT]+"""

    parts: List[str]

    def __str__(self) -> str:
        return ".".join(self.parts)


@dataclass
class LiteralExpr(Expr):
    """Literal values: int, float, string, bool, None"""

    value: Any


@dataclass
class ListExpr(Expr):
    """List literal: [expr, expr, ...]"""

    elements: List[Expr]
    had_trailing_comma: bool = False


@dataclass
class TupleExpr(Expr):
    """Tuple literal: (expr, expr, ...)"""

    elements: List[Expr]
    had_trailing_comma: bool = False


@dataclass
class DictExpr(Expr):
    """Dictionary literal: {key: value, ...}"""

    pairs: List[tuple[Expr, Expr]]
    had_trailing_comma: bool = False


@dataclass
class ProviderRefExpr(Expr):
    """Provider reference: @path"""

    path: PathExpr


@dataclass
class ProviderMethodRefExpr(Expr):
    """Provider attribute/method reference: @path.attr[.attr...]"""

    path: PathExpr
    attrs: List[str]

    def __post_init__(self) -> None:
        if not self.attrs:
            raise ValueError("ProviderMethodRefExpr.attrs must be non-empty")

    @property
    def method(self) -> str:
        """First attribute name (backward-compatible alias)."""
        return self.attrs[0]


@dataclass
class CallExpr(Expr):
    """Function/constructor call: callable(kwargs)"""

    callable: Union[ProviderRefExpr, DottedNameExpr]
    kwargs: Dict[str, Expr]
    kwarg_positions: Optional[Dict[str, Position]] = None  # For LSP (kwarg name -> position)
    had_trailing_comma: bool = False


@dataclass
class BinaryOpExpr(Expr):
    """Binary operation: left op right"""

    left: Any
    operator: str  # +, -, *, /, //, **
    right: Any


@dataclass
class UnaryOpExpr(Expr):
    """Unary operation: op operand"""

    operator: str  # -
    operand: Any


@dataclass
class BuiltinCallExpr(Expr):
    """Built-in function call: len(expr), min(expr, expr), etc."""

    function: str  # len, min, max, range
    args: List[Expr]
    had_trailing_comma: bool = False


# ===== File Structure =====


@dataclass
class PFigFile(ASTNode):
    """Complete pfig file AST"""

    statements: List[
        Union[
            ImportStmt,
            FromImportStmt,
            IncludeStmt,
            ExternalStmt,
            OverrideStmt,
            Binding,
        ]
    ]

    def imports(self) -> List[Union[ImportStmt, FromImportStmt]]:
        """Get all import statements."""
        return [stmt for stmt in self.statements if isinstance(stmt, (ImportStmt, FromImportStmt))]

    def includes(self) -> List[IncludeStmt]:
        """Get all include statements."""
        return [stmt for stmt in self.statements if isinstance(stmt, IncludeStmt)]

    def externals(self) -> List[ExternalStmt]:
        """Get all external declarations."""
        return [stmt for stmt in self.statements if isinstance(stmt, ExternalStmt)]

    def overrides(self) -> List[OverrideStmt]:
        """Get all override statements."""
        return [stmt for stmt in self.statements if isinstance(stmt, OverrideStmt)]

    def bindings(self) -> List[Binding]:
        """Get all binding assignments."""
        return [stmt for stmt in self.statements if isinstance(stmt, Binding)]
