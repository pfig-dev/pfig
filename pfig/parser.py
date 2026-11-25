"""
pfig Recursive Descent Parser - Implements the formal grammar from §3.

This parser transforms a stream of tokens into an AST following the pfig grammar.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Union, overload

from .ast_nodes import *
from .lexer import Lexer, Position, Token, TokenType
from .logging import get_logger
from .utils import load_config_file, normalize_path_display

logger = get_logger("parser")

if TYPE_CHECKING:
    from .lsp_index import ParseResult


class ParseError(Exception):
    def __init__(self, message: str, position: Position):
        self.message = message
        self.position = position
        super().__init__(f"{position}: {message}")


class Parser:
    """
    Recursive descent parser for pfig configuration language.

    Implements the grammar from §3:

    file := { import_stmt | include_stmt | extern_stmt | override_stmt | binding }*
    """

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self._skip_newlines()  # Skip any initial newlines

    def current_token(self) -> Token:
        """Get current token without consuming it."""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return self.tokens[-1]  # EOF token

    def peek_token(self, offset: int = 1) -> Token:
        """Peek at token at given offset."""
        pos = self.pos + offset
        if pos < len(self.tokens):
            return self.tokens[pos]
        return self.tokens[-1]  # EOF token

    def advance(self) -> Token:
        """Consume and return current token."""
        token = self.current_token()
        if self.pos < len(self.tokens) - 1:  # Don't advance past EOF
            self.pos += 1
        self._skip_newlines()  # Skip any newlines after advancing
        return token

    def _skip_newlines(self):
        """Skip over newline tokens."""
        while self.pos < len(self.tokens) - 1 and self.current_token().type == TokenType.NEWLINE:
            self.pos += 1

    def expect(self, token_type: TokenType) -> Token:
        """Consume token of expected type or raise error."""
        token = self.current_token()
        if token.type != token_type:
            raise ParseError(
                f"Expected {token_type.name}, got {token.type.name} '{token.value}'",
                token.position,
            )
        return self.advance()

    def match(self, *token_types: TokenType) -> bool:
        """Check if current token matches any of the given types."""
        return self.current_token().type in token_types

    def skip_newlines(self):
        """Skip over newline tokens."""
        while self.match(TokenType.NEWLINE):
            self.advance()

    def parse(self) -> PFigFile:
        """Parse complete pfig file."""
        self.skip_newlines()
        statements = []

        while not self.match(TokenType.EOF):
            statements.append(self.parse_statement())
            self.skip_newlines()

        position = self.tokens[0].position if self.tokens else Position("", 1, 1)
        return PFigFile(position, statements)

    def parse_statement(
        self,
    ) -> Optional[
        Union[
            ImportStmt,
            FromImportStmt,
            IncludeStmt,
            ExternalStmt,
            OverrideStmt,
            Binding,
        ]
    ]:
        """Parse a top-level statement."""
        if self.match(TokenType.IMPORT):
            return self.parse_import_stmt()
        elif self.match(TokenType.FROM):
            # Could be "from module import" or "from file include"
            return self.parse_from_stmt()
        elif self.match(TokenType.INCLUDE):
            return self.parse_include_stmt()
        elif self.match(TokenType.EXTERNAL):
            return self.parse_external_stmt()
        elif self.match(TokenType.OVERRIDE):
            return self.parse_override_stmt()
        elif self.match(TokenType.IDENT):
            return self.parse_binding()
        else:
            # Skip unknown tokens or handle error
            token = self.current_token()
            raise ParseError(f"Unexpected token: {token.value}", token.position)

    def parse_import_stmt(self) -> ImportStmt:
        """Parse: import dotted_name [as IDENT]"""
        position = self.advance().position  # consume 'import'

        module_position = self.current_token().position
        module = self.parse_dotted_name()
        alias = None
        alias_position = None

        if self.match(TokenType.AS):
            self.advance()  # consume 'as'
            alias_token = self.expect(TokenType.IDENT)
            alias = alias_token.value
            alias_position = alias_token.position

        return ImportStmt(
            position,
            module,
            alias,
            module_position=module_position,
            alias_position=alias_position,
        )

    def parse_from_stmt(self) -> FromImportStmt:
        """Parse: from dotted_name import ..."""
        position = self.advance().position  # consume 'from'

        # Only support Python module imports, no file includes with hoisting
        module_position = self.current_token().position
        module = self.parse_dotted_name()
        self.expect(TokenType.IMPORT)
        items = self.parse_import_items()
        return FromImportStmt(position, module, items, module_position=module_position)

    def parse_include_stmt(self) -> IncludeStmt:
        """Parse: include STRING as IDENT"""
        position = self.advance().position  # consume 'include'

        path_token = self.expect(TokenType.STRING)
        path = path_token.value
        path_position = path_token.position
        self.expect(TokenType.AS)
        alias = self.expect(TokenType.IDENT).value

        return IncludeStmt(position, path, alias, path_position=path_position)

    def parse_external_stmt(self) -> ExternalStmt:
        """Parse: external path"""
        position = self.advance().position  # consume 'external'
        path = self.parse_path()
        return ExternalStmt(position, path)

    def parse_override_stmt(self) -> OverrideStmt:
        """Parse: override path = expr"""
        position = self.advance().position  # consume 'override'

        path = self.parse_path()
        self.expect(TokenType.EQUALS)
        value = self.parse_expr()

        return OverrideStmt(position, path, value)

    def parse_binding(self) -> Binding:
        """Parse: IDENT = expr"""
        name_token = self.expect(TokenType.IDENT)
        self.expect(TokenType.EQUALS)
        value = self.parse_expr()

        return Binding(name_token.position, name_token.value, value)

    def parse_import_items(self) -> List[ImportItem]:
        """Parse: import_item [, import_item]*"""
        items = []

        # First item
        items.append(self.parse_import_item())

        # Additional items
        while self.match(TokenType.COMMA):
            self.advance()  # consume comma
            items.append(self.parse_import_item())

        return items

    def parse_import_item(self) -> ImportItem:
        """Parse: IDENT [as IDENT]"""
        name_token = self.expect(TokenType.IDENT)
        name = name_token.value
        alias = None
        alias_position = None

        if self.match(TokenType.AS):
            self.advance()  # consume 'as'
            alias_token = self.expect(TokenType.IDENT)
            alias = alias_token.value
            alias_position = alias_token.position

        return ImportItem(
            name,
            alias,
            name_position=name_token.position,
            alias_position=alias_position,
        )

    def parse_path(self) -> PathExpr:
        """Parse: IDENT [/ IDENT]*"""
        position = self.current_token().position
        parts = [self.expect(TokenType.IDENT).value]

        while self.match(TokenType.DIVIDE):  # Use DIVIDE token for namespace paths
            self.advance()  # consume '/'
            parts.append(self.expect(TokenType.IDENT).value)

        return PathExpr(position, parts)

    def parse_dotted_name(self) -> str:
        """Parse dotted name and return as string."""
        # Parse as sequence of IDENT . IDENT (no more DOTTED_NAME tokens)
        parts = [self.expect(TokenType.IDENT).value]
        while self.match(TokenType.DOT):
            self.advance()  # consume '.'
            parts.append(self.expect(TokenType.IDENT).value)
        return ".".join(parts)

    # ===== Expression Parsing =====

    def parse_expr(self) -> Expr:
        """Parse expression with operator precedence."""
        return self.parse_additive()

    def parse_additive(self) -> Expr:
        """Parse: multiplicative ((+ | -) multiplicative)*"""
        left = self.parse_multiplicative()

        while self.match(TokenType.PLUS, TokenType.MINUS):
            op = self.advance().value
            right = self.parse_multiplicative()
            left = BinaryOpExpr(left.position, left, op, right)

        return left

    def parse_multiplicative(self) -> Expr:
        """Parse: power ((* | / | //) power)*"""
        left = self.parse_power()

        while self.match(TokenType.MULTIPLY, TokenType.DIVIDE, TokenType.FLOOR_DIVIDE):
            op = self.advance().value
            right = self.parse_power()
            left = BinaryOpExpr(left.position, left, op, right)

        return left

    def parse_power(self) -> Expr:
        """Parse: unary (** unary)*"""
        left = self.parse_unary()

        if self.match(TokenType.POWER):
            op = self.advance().value
            right = self.parse_power()  # Right associative
            left = BinaryOpExpr(left.position, left, op, right)

        return left

    def parse_unary(self) -> Expr:
        """Parse: (- | +) primary | primary"""
        if self.match(TokenType.MINUS):
            op_token = self.advance()
            operand = self.parse_unary()
            return UnaryOpExpr(op_token.position, op_token.value, operand)

        return self.parse_primary()

    def parse_primary(self) -> Expr:
        """Parse primary expressions."""
        position = self.current_token().position

        # Literals
        if self.match(TokenType.INT):
            token = self.advance()
            try:
                value = int(token.value)
            except ValueError as e:
                raise ParseError(f"Invalid integer literal: {e}", token.position)
            return LiteralExpr(position, value)

        elif self.match(TokenType.FLOAT):
            token = self.advance()
            try:
                value = float(token.value)
            except ValueError as e:
                raise ParseError(f"Invalid float literal: {e}", token.position)
            return LiteralExpr(position, value)

        elif self.match(TokenType.STRING):
            value = self.advance().value
            return LiteralExpr(position, value)

        elif self.match(TokenType.TRUE):
            self.advance()
            return LiteralExpr(position, True)

        elif self.match(TokenType.FALSE):
            self.advance()
            return LiteralExpr(position, False)

        elif self.match(TokenType.NONE):
            self.advance()
            return LiteralExpr(position, None)

        # Containers
        elif self.match(TokenType.LBRACKET):
            return self.parse_list()

        elif self.match(TokenType.LPAREN):
            return self.parse_tuple_or_paren_expr()

        elif self.match(TokenType.LBRACE):
            return self.parse_dict()

        # Provider reference
        elif self.match(TokenType.AT):
            return self.parse_provider_ref()

        # Built-in functions
        elif self.match(TokenType.LEN, TokenType.MIN, TokenType.MAX, TokenType.RANGE):
            return self.parse_builtin_call()

        # Dotted name (potentially with call)
        elif self.match(TokenType.IDENT):
            return self.parse_dotted_name_or_call()

        else:
            token = self.current_token()
            if token.value == "":
                raise ParseError(f"Unexpected end of input", position)
            else:
                raise ParseError(f"Unexpected token: {token.value}", position)

    def parse_list(self) -> ListExpr:
        """Parse: [expr, expr, ...]"""
        position = self.advance().position  # consume '['
        elements = []
        had_trailing_comma = False

        if not self.match(TokenType.RBRACKET):
            elements.append(self.parse_expr())

            while self.match(TokenType.COMMA):
                self.advance()  # consume comma
                if self.match(TokenType.RBRACKET):  # Trailing comma
                    had_trailing_comma = True
                    break
                elements.append(self.parse_expr())

        self.expect(TokenType.RBRACKET)
        return ListExpr(position, elements, had_trailing_comma)

    def parse_tuple_or_paren_expr(self) -> Union[TupleExpr, Expr]:
        """Parse: (expr) or (expr, expr, ...)"""
        position = self.advance().position  # consume '('

        if self.match(TokenType.RPAREN):
            # Empty tuple
            self.advance()
            return TupleExpr(position, [])

        elements = [self.parse_expr()]
        had_trailing_comma = False

        # Check if it's a tuple or just parentheses
        if self.match(TokenType.COMMA):
            self.advance()  # consume comma

            # It's definitely a tuple. The first comma may itself be trailing
            # (e.g. `(x,)` — a one-element tuple).
            had_trailing_comma = self.match(TokenType.RPAREN)
            while not self.match(TokenType.RPAREN):
                if self.match(TokenType.COMMA):  # Trailing comma
                    had_trailing_comma = True
                    break
                elements.append(self.parse_expr())
                if self.match(TokenType.COMMA):
                    self.advance()
                    if self.match(TokenType.RPAREN):
                        had_trailing_comma = True
                        break
                else:
                    break

            self.expect(TokenType.RPAREN)
            return TupleExpr(position, elements, had_trailing_comma)
        else:
            # Just parenthesized expression
            self.expect(TokenType.RPAREN)
            return elements[0]

    def parse_dict(self) -> DictExpr:
        """Parse: {key: value, ...}"""
        position = self.advance().position  # consume '{'
        pairs = []
        had_trailing_comma = False

        if not self.match(TokenType.RBRACE):
            key = self.parse_expr()
            self.expect(TokenType.COLON)
            value = self.parse_expr()
            pairs.append((key, value))

            while self.match(TokenType.COMMA):
                self.advance()  # consume comma
                if self.match(TokenType.RBRACE):  # Trailing comma
                    had_trailing_comma = True
                    break
                key = self.parse_expr()
                self.expect(TokenType.COLON)
                value = self.parse_expr()
                pairs.append((key, value))

        self.expect(TokenType.RBRACE)
        return DictExpr(position, pairs, had_trailing_comma)

    def parse_provider_ref(
        self,
    ) -> Union[ProviderRefExpr, ProviderMethodRefExpr, CallExpr]:
        """Parse: @path [.attr...] [(kwargs)]"""
        position = self.advance().position  # consume '@'

        # All provider references now use path parsing (no more DOTTED_NAME tokens)
        path = self.parse_path()

        if self.match(TokenType.DOT):
            # Provider attribute chain: @path.attr[.attr...]
            attrs: list[str] = []
            while self.match(TokenType.DOT):
                self.advance()  # consume the DOT
                attrs.append(self.expect(TokenType.IDENT).value)
            return ProviderMethodRefExpr(position, path, attrs)
        elif self.match(TokenType.LPAREN):
            # Provider call: @path(kwargs)
            kwargs, kwarg_positions, had_trailing_comma = self.parse_kwargs()
            return CallExpr(
                position,
                ProviderRefExpr(position, path),
                kwargs,
                kwarg_positions=kwarg_positions,
                had_trailing_comma=had_trailing_comma,
            )
        else:
            # Provider reference: @path
            return ProviderRefExpr(position, path)

    def parse_builtin_call(self) -> BuiltinCallExpr:
        """Parse: (len|min|max|range)(args)"""
        func_token = self.advance()
        position = func_token.position

        self.expect(TokenType.LPAREN)
        args = []
        had_trailing_comma = False

        if not self.match(TokenType.RPAREN):
            args.append(self.parse_expr())

            while self.match(TokenType.COMMA):
                self.advance()  # consume comma
                if self.match(TokenType.RPAREN):  # Trailing comma
                    had_trailing_comma = True
                    break
                args.append(self.parse_expr())

        self.expect(TokenType.RPAREN)
        return BuiltinCallExpr(position, func_token.value, args, had_trailing_comma)

    def parse_dotted_name_or_call(self) -> Union[DottedNameExpr, CallExpr]:
        """Parse dotted name, potentially followed by call."""
        position = self.current_token().position

        # Parse as sequence of IDENT . IDENT (no more DOTTED_NAME tokens)
        parts = [self.expect(TokenType.IDENT).value]
        while self.match(TokenType.DOT):
            self.advance()  # consume '.'
            parts.append(self.expect(TokenType.IDENT).value)

        dotted_expr = DottedNameExpr(position, parts)

        if self.match(TokenType.LPAREN):
            # Function/constructor call
            kwargs, kwarg_positions, had_trailing_comma = self.parse_kwargs()
            return CallExpr(
                position,
                dotted_expr,
                kwargs,
                kwarg_positions=kwarg_positions,
                had_trailing_comma=had_trailing_comma,
            )
        else:
            # Just a dotted name reference
            return dotted_expr

    def parse_kwargs(self) -> tuple[Dict[str, Expr], Dict[str, Position], bool]:
        """Parse: (kwarg, kwarg, ...) where kwarg = IDENT = expr

        Returns (kwargs, kwarg_positions, had_trailing_comma).
        """
        self.expect(TokenType.LPAREN)
        kwargs: Dict[str, Expr] = {}
        kwarg_positions: Dict[str, Position] = {}
        had_trailing_comma = False

        if not self.match(TokenType.RPAREN):
            # Check if this looks like a positional argument instead of keyword argument
            current = self.current_token()

            # If not an identifier, it's definitely a positional argument
            if not self.match(TokenType.IDENT):
                raise ParseError(
                    f"Positional arguments are not supported. "
                    f"Use keyword arguments instead (e.g., param=value)",
                    current.position,
                )

            # If it's an identifier but not followed by '=', it's a positional argument
            next_token = self.peek_token()
            if next_token.type != TokenType.EQUALS:
                raise ParseError(
                    f"Positional arguments are not supported. "
                    f"Use keyword arguments instead (e.g., {current.value}=...)",
                    current.position,
                )

            # First kwarg
            name_token = self.expect(TokenType.IDENT)
            name = name_token.value
            self.expect(TokenType.EQUALS)
            value = self.parse_expr()
            kwargs[name] = value
            kwarg_positions[name] = name_token.position

            # Additional kwargs
            while self.match(TokenType.COMMA):
                self.advance()  # consume comma
                if self.match(TokenType.RPAREN):  # Trailing comma
                    had_trailing_comma = True
                    break

                # Check for positional arguments in subsequent arguments too
                current = self.current_token()

                if not self.match(TokenType.IDENT):
                    raise ParseError(
                        f"Positional arguments are not supported. "
                        f"Use keyword arguments instead (e.g., param=value)",
                        current.position,
                    )

                next_token = self.peek_token()
                if next_token.type != TokenType.EQUALS:
                    raise ParseError(
                        f"Positional arguments are not supported. "
                        f"Use keyword arguments instead (e.g., {current.value}=...)",
                        current.position,
                    )

                name_token = self.expect(TokenType.IDENT)
                name = name_token.value
                self.expect(TokenType.EQUALS)
                value = self.parse_expr()
                kwargs[name] = value
                kwarg_positions[name] = name_token.position

        self.expect(TokenType.RPAREN)
        return kwargs, kwarg_positions, had_trailing_comma


@overload
def parse_pfig_source(
    source: str,
    *,
    filename: str | None = None,
    with_index: Literal[False] = False,
) -> PFigFile: ...


@overload
def parse_pfig_source(
    source: str,
    *,
    filename: str | None = None,
    with_index: Literal[True],
) -> "ParseResult": ...


def parse_pfig_source(
    source: str,
    *,
    filename: str | None = None,
    with_index: bool = False,
) -> Union[PFigFile, "ParseResult"]:
    """Parse pfig configuration source code into an Abstract Syntax Tree.

    This function takes pfig DSL source code and produces a structured AST
    representation following the formal grammar defined in the pfig v1 specification.
    The parser supports all pfig language constructs including imports, includes,
    externals, overrides, bindings, and expressions.

    Args:
        source: The pfig source code to parse as a string. This should be
            valid pfig DSL syntax including statements like:
            - Import statements: `import torch`, `from collections import defaultdict`
            - Include statements: `include "./base.pfig" as base`
            - External declarations: `external output_dir`
            - Override statements: `override batch_size = 128`
            - Bindings: `model = torch.nn.Linear(input_dim=784, output_dim=10)`
            - Provider references: `optimizer = @model`, `fresh_model = @model()`

        filename: Optional filename for error reporting. If provided, parse errors
            will include this filename in their error messages (e.g.,
            "config.pfig:5:10: Unexpected token"). If None, errors will show
            "<unknown>" as the filename. This parameter is keyword-only.

        with_index: If True, also build a DocumentIndex and return ParseResult. The
            index is useful for LSP tooling (go-to-definition, hover, completions).
            This parameter is keyword-only.

    Returns:
        PFigFile when with_index=False, or ParseResult(ast, index) when with_index=True.

    Raises:
        LexerError: If the source contains invalid tokens, malformed strings,
            or other lexical errors.
        ParseError: If the source contains valid tokens but invalid syntax
            according to the pfig grammar.

    Examples:
        Basic usage with just source code:

        >>> ast = parse_pfig_source('batch_size = 64')
        >>> len(ast.statements)
        1

        With index for LSP:

        >>> result = parse_pfig_source('batch_size = 64', with_index=True)
        >>> result.ast.statements
        >>> result.index.binding_defs['batch_size']

    Note:
        The parser follows the pfig v1 specification grammar and supports
        the complete DSL including nested expressions, provider references,
        and all statement types. For execution of the parsed AST, use the
        `execute_pfig` function from the execution module.
    """
    logger.debug(f"Starting parse of {len(source)} characters from {filename or '<string>'}")

    lexer = Lexer(source, filename)
    tokens = lexer.tokenize()
    logger.debug(f"Lexer produced {len(tokens)} tokens")

    parser = Parser(tokens)
    try:
        ast = parser.parse()
    except RecursionError:
        raise ParseError(
            "Maximum nesting depth exceeded",
            Position(filename or "<unknown>", 1, 1),
        )

    logger.debug(f"Parse complete: {len(ast.statements)} top-level statements")

    if with_index:
        from .lsp_index import ParseResult, index_from_ast

        path_str = filename or "<string>"
        index = index_from_ast(ast, path_str, source)
        return ParseResult(ast=ast, index=index)

    return ast


def parse_config_file(path: Union[str, Path]) -> PFigFile:
    """
    Parse a pfig configuration file into an AST.

    Args:
        path: path to a pfig file

    Returns:
        Parsed PFigFile AST

    Raises:
        FileNotFoundError: If Path doesn't exist
        ParseError: If parsing fails
    """
    path_str = normalize_path_display(str(path))
    return parse_pfig_source(source=load_config_file(path), filename=path_str)
