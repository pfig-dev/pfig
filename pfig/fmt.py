"""
pfig formatter — produce a canonical, idempotent rendering of a `.pfig` source.

Style summary (see plan for full details):
  * 4-space indent, default line length 100.
  * Top-level binding uses spaces around `=`; kwargs do not (`Foo(a=1)`).
  * No space around `/` in `alias/name`; no space after `@`.
  * Single-line if the container fits within ``line_length``; otherwise each
    element on its own line, with a trailing comma.
  * Magic trailing comma (black-style): a trailing comma in the input forces
    multi-line output even when the content would fit on one line.
  * Strings prefer double quotes, falling back to single quotes when the
    literal contains an unescaped ``"``.
  * Imports are sorted within blank-line-separated groups.

This file is the single home of the formatter. ``format_source`` is the
public entry point used by both the CLI (``pfig fmt``) and the LSP
(``textDocument/formatting``).

Comments are preserved in these positions: leading the file, standalone
between statements, trailing on the same source line as a statement, and
inside List/Dict/Tuple containers (leading an element, trailing an
element on the same line, or dangling at the end before the close
bracket).

TODO: Comments inside a Call/BuiltinCall's parentheses still fall back
to Chunk-2 behavior — they get yanked to the leading-comment block of
the following statement rather than staying with the enclosing call.
The bracket-pair scanner sees the parens but ``_collect_containers``
deliberately skips Call/BuiltinCall because ``node.position`` points to
the callable, not the ``(``. Unblocking this needs an explicit
``open_position`` field on CallExpr/BuiltinCallExpr (mirror the
``had_trailing_comma`` addition). Revisit if real configs surface the
need.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ast_nodes import (
    ASTNode,
    BinaryOpExpr,
    Binding,
    BuiltinCallExpr,
    CallExpr,
    DictExpr,
    DottedNameExpr,
    Expr,
    ExternalStmt,
    FromImportStmt,
    ImportStmt,
    IncludeStmt,
    ListExpr,
    LiteralExpr,
    OverrideStmt,
    PathExpr,
    PFigFile,
    ProviderMethodRefExpr,
    ProviderRefExpr,
    TupleExpr,
    UnaryOpExpr,
)
from .lexer import Lexer
from .logging import get_logger
from .parser import parse_pfig_source

logger = get_logger("fmt")


@dataclass(frozen=True)
class FormatOptions:
    """Knobs for the formatter. Defaults match the agreed style."""

    line_length: int = 100
    indent: int = 4


class FormatError(Exception):
    """Raised when source cannot be formatted (e.g. parse failure)."""


# Sentinel default so we don't share a mutable FormatOptions instance.
_DEFAULT_OPTIONS = FormatOptions()


def format_source(
    source: str,
    *,
    filename: str | None = None,
    options: FormatOptions | None = None,
) -> str:
    """Parse ``source`` and return the canonically-formatted rendering.

    Args:
        source: pfig source code.
        filename: Optional filename used in parse-error messages.
        options: Formatter options; defaults to ``FormatOptions()``.

    Raises:
        FormatError: When the source cannot be parsed.
    """
    opts = options or _DEFAULT_OPTIONS
    try:
        ast = parse_pfig_source(source, filename=filename)
    except Exception as exc:  # ParseError / LexerError both inherit Exception
        raise FormatError(str(exc)) from exc

    comments = _scan_comments(source, filename=filename)
    bracket_pairs = _scan_bracket_pairs(source, filename=filename)
    printer = _Printer(
        opts,
        source=source,
        comments=comments,
        bracket_pairs=bracket_pairs,
    )
    return printer.print_file(ast)


@dataclass(frozen=True)
class _Comment:
    """A captured comment with its source position.

    ``line`` and ``col`` are 1-indexed and point to the first character of
    the comment marker (``#`` or triple-quote). ``text`` is the raw
    source slice including the marker — for ``#`` comments it stops at the
    newline; for block comments it includes the closing delimiter.
    """

    line: int
    col: int
    text: str
    is_block: bool


class _CommentRecordingLexer(Lexer):
    """Tokenises like ``Lexer`` but records every comment it would skip.

    Subclassing ``Lexer`` keeps comment detection in one place (the parent's
    ``skip_comment`` method handles malformed block-comment closing
    delimiters); this class just snapshots ``(line, col, pos)`` before
    delegating and reads the consumed slice afterwards.
    """

    def __init__(self, source: str, filename: str | None = None) -> None:
        super().__init__(source, filename)
        self.comments: list[_Comment] = []

    def skip_comment(self) -> None:
        if self.peek() == "#":
            start_line, start_col, start_pos = self.line, self.column, self.pos
            super().skip_comment()  # advances to end of line
            self.comments.append(
                _Comment(start_line, start_col, self.source[start_pos : self.pos], False)
            )
            # ``super().skip_comment`` continues to check for a block comment
            # after the ``#`` case; we've already consumed past the newline so
            # that check will be a no-op and we don't need to re-call it.
            return

        if self.peek(length=3) in ("'''", '"""'):
            start_line, start_col, start_pos = self.line, self.column, self.pos
            super().skip_comment()
            self.comments.append(
                _Comment(start_line, start_col, self.source[start_pos : self.pos], True)
            )


def _scan_comments(source: str, *, filename: str | None = None) -> list[_Comment]:
    """Return every ``#`` and block comment in ``source`` in source order.

    Strings are skipped by the parent lexer's string-handling, so a ``#``
    inside ``"a # b"`` is not mistaken for a comment.
    """
    lex = _CommentRecordingLexer(source, filename)
    try:
        lex.tokenize()
    except Exception:
        # Parsing already failed loudly via format_source; comment scanning
        # is best-effort on malformed source.
        pass
    return lex.comments


def _scan_bracket_pairs(
    source: str, *, filename: str | None = None
) -> dict[tuple[int, int], tuple[int, int]]:
    """Map ``(open_line, open_col)`` → ``(close_line, close_col)`` for every
    bracket pair in the source: ``()``, ``[]``, and ``{}``. The lexer's own
    string-handling means brackets inside string literals are ignored.

    Used by comment classification to decide which container (if any) a
    comment lives inside.
    """
    from .lexer import TokenType  # local import to avoid module-import cycle

    pairs: dict[tuple[int, int], tuple[int, int]] = {}
    stack: list = []
    lex = Lexer(source, filename)
    try:
        tokens = lex.tokenize()
    except Exception:
        return pairs
    opens = (TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE)
    closes = (TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE)
    for tok in tokens:
        if tok.type in opens:
            stack.append(tok.position)
        elif tok.type in closes:
            if stack:
                op = stack.pop()
                pairs[(op.line, op.column)] = (tok.position.line, tok.position.column)
    return pairs


class _Printer:
    """Walks a ``PFigFile`` and produces canonical text."""

    def __init__(
        self,
        options: FormatOptions,
        source: str = "",
        comments: list[_Comment] | None = None,
        bracket_pairs: dict | None = None,
    ) -> None:
        self.options = options
        # We hold onto the source so blank-line preservation can ask "was the
        # line above this statement blank?". Empty when callers don't pass one
        # (in which case no blank lines are preserved).
        self._source_lines = source.splitlines() if source else []
        self._comments = comments or []
        self._bracket_pairs = bracket_pairs or {}
        # ``_container_comments[id(node)]`` holds the per-element internal
        # comments attached to a List/Dict/Tuple. Populated by ``print_file``
        # before rendering.
        self._container_comments: dict[int, _ContainerComments] = {}

    def print_file(self, ast: PFigFile) -> str:
        if not ast.statements:
            return self._render_only_comments()

        emit_plan = self._plan_statements(ast.statements)
        # Classify comments AGAINST THE ORIGINAL statement order (before
        # import sorting reshuffles things), so a trailing comment on
        # ``import zlib  # core`` stays glued to that import wherever it
        # lands in the sorted output.
        containers = _collect_containers(ast)
        bucket = _classify_comments(
            self._comments,
            ast.statements,
            self._source_lines,
            containers=containers,
            bracket_pairs=self._bracket_pairs,
        )
        self._container_comments = bucket.container
        # Build a map from id(stmt) -> (leading_comments, trailing_same_line)
        # so we can look them up regardless of post-sort iteration order.
        leading: dict[int, list[_Comment]] = {}
        trailing_same: dict[int, list[_Comment]] = {}
        for c, kind, target in bucket.attached:
            if kind == "leading":
                leading.setdefault(id(target), []).append(c)
            else:
                trailing_same.setdefault(id(target), []).append(c)

        out: list[str] = []
        # Leading-file block first.
        for c in bucket.leading_file:
            out.append(c.text)

        for i, (stmt, blank_before) in enumerate(emit_plan):
            leading_for_stmt = leading.get(id(stmt), [])
            # For non-import statements with a leading-comment block, the
            # blank-line test must use the *comment block's* position rather
            # than the statement's, so comments yanked out of a container
            # (Chunk 2 limitation) don't produce a phantom blank that flips on
            # the second formatting pass.
            #
            # For imports we *don't* do this: ``_plan_statements`` has already
            # decided the group's blank line once from the original first
            # member, and per-member adjustments would let a leading comment
            # on a non-first sorted member re-introduce a blank inside the
            # group.
            is_import = isinstance(stmt, (ImportStmt, FromImportStmt))
            if leading_for_stmt and not is_import:
                effective_blank = self._blank_line_before(leading_for_stmt[0].line)
            else:
                effective_blank = blank_before
            if effective_blank and out and out[-1] != "":
                out.append("")
            for c in leading_for_stmt:
                out.append(c.text)
            rendered = self.stmt(stmt)
            same_line = trailing_same.get(id(stmt), [])
            if same_line:
                # Black-style: two spaces before the trailing ``#`` and only
                # one trailing comment per statement.
                lines = rendered.split("\n")
                lines[-1] = lines[-1] + "  " + same_line[0].text
                rendered = "\n".join(lines)
                # Any additional trailing comments (rare; multiple ``#`` on
                # one source line is malformed in practice) emit as
                # standalone lines after the statement.
                for extra in same_line[1:]:
                    out.append(rendered)
                    rendered = extra.text
            out.append(rendered)

        for c in bucket.trailing_file:
            out.append(c.text)
        return "\n".join(out) + "\n"

    def _render_only_comments(self) -> str:
        """Render a file with no statements but possibly only comments."""
        if not self._comments:
            return ""
        return "\n".join(c.text for c in self._comments) + "\n"

    def _plan_statements(self, statements: list) -> list[tuple]:
        """Walk ``statements`` and produce ``(stmt, blank_before)`` pairs.

        Runs of consecutive imports (not broken by a blank line or a
        non-import) are sorted in place; blank-line preservation is decided
        once per group from the group's original first statement, so sorting
        cannot move a blank line into the middle of a sorted group.

        The ``blank_before`` flag here is purely "was there a blank line in
        source above this statement". ``print_file`` is responsible for
        suppressing leading blank lines when nothing has been emitted yet.
        """
        plan: list[tuple] = []
        i = 0
        while i < len(statements):
            s = statements[i]
            if not isinstance(s, (ImportStmt, FromImportStmt)):
                blank = self._blank_line_before(s.position.line)
                plan.append((s, blank))
                i += 1
                continue
            # Import group: accumulate consecutive imports without a blank gap.
            group = [s]
            j = i + 1
            while j < len(statements):
                nxt = statements[j]
                if not isinstance(nxt, (ImportStmt, FromImportStmt)):
                    break
                if self._blank_line_before(nxt.position.line):
                    break
                group.append(nxt)
                j += 1
            group_blank = self._blank_line_before(group[0].position.line)
            group.sort(key=_import_sort_key)
            for k, stmt in enumerate(group):
                plan.append((stmt, group_blank if k == 0 else False))
            i = j
        return plan

    def _blank_line_before(self, line_num: int) -> bool:
        """True iff the source line immediately above ``line_num`` is blank.

        ``line_num`` is the 1-indexed line where the next statement starts.
        Used to decide whether to emit a single blank line between two
        rendered statements; multiple blank lines collapse to one.
        """
        if line_num <= 1:
            return False
        idx = line_num - 2  # 0-indexed previous source line
        if idx >= len(self._source_lines):
            return False
        return self._source_lines[idx].strip() == ""

    # ----- Statements -----

    def stmt(self, node: ASTNode) -> str:
        method = getattr(self, f"_visit_{type(node).__name__}", None)
        if method is None:
            raise FormatError(f"formatter: unsupported statement {type(node).__name__}")
        return method(node)

    def _visit_ImportStmt(self, node: ImportStmt) -> str:
        if node.alias:
            return f"import {node.module} as {node.alias}"
        return f"import {node.module}"

    def _visit_FromImportStmt(self, node: FromImportStmt) -> str:
        # ``from x import a, b, c`` — the grammar does not allow a parenthesized
        # form, so we never wrap. Long imports stay on one line and may exceed
        # ``line_length``; that's acceptable in chunk 1 and rare in practice.
        # Names are sorted alphabetically within a single from-import.
        sorted_names = sorted(node.names, key=lambda it: (it.name, it.alias or ""))
        items = []
        for item in sorted_names:
            if item.alias:
                items.append(f"{item.name} as {item.alias}")
            else:
                items.append(item.name)
        return f"from {node.module} import " + ", ".join(items)

    def _visit_IncludeStmt(self, node: IncludeStmt) -> str:
        return f"include {_format_string(node.path)} as {node.alias}"

    def _visit_ExternalStmt(self, node: ExternalStmt) -> str:
        return f"external {self.expr(node.path)}"

    def _visit_OverrideStmt(self, node: OverrideStmt) -> str:
        path_str = self.expr(node.path)
        prefix = f"override {path_str} = "
        value_str = self.expr(node.value, col=len(prefix), line_indent=0)
        return f"{prefix}{value_str}"

    def _visit_Binding(self, node: Binding) -> str:
        prefix = f"{node.name} = "
        value_str = self.expr(node.value, col=len(prefix), line_indent=0)
        return f"{prefix}{value_str}"

    # ----- Expressions -----

    def expr(
        self,
        node: Expr,
        *,
        col: int = 0,
        line_indent: int = 0,
        allow_multi: bool = True,
    ) -> str:
        """Render an expression, possibly across multiple lines.

        Args:
            col: Column where this expression begins on the current line.
                Used for line-length fit decisions.
            line_indent: Indent of the line this expression starts on. When a
                container wraps, its inner elements live at ``line_indent +
                options.indent`` and its closing bracket aligns at
                ``line_indent``.
            allow_multi: When False, container/call expressions stay on one
                line even if they overflow ``line_length``. Used by callers
                that are trying a single-line layout before committing.
        """
        method = getattr(self, f"_visit_{type(node).__name__}", None)
        if method is None:
            raise FormatError(f"formatter: unsupported expression {type(node).__name__}")
        return method(node, col=col, line_indent=line_indent, allow_multi=allow_multi)

    # Leaf and operator visitors — always single-line; ignore col / line_indent / allow_multi.

    def _visit_LiteralExpr(self, node: LiteralExpr, **_: object) -> str:
        return _format_literal(node.value)

    def _visit_PathExpr(self, node: PathExpr, **_: object) -> str:
        return "/".join(node.parts)

    def _visit_DottedNameExpr(self, node: DottedNameExpr, **_: object) -> str:
        return ".".join(node.parts)

    def _visit_ProviderRefExpr(self, node: ProviderRefExpr, **_: object) -> str:
        return f"@{'/'.join(node.path.parts)}"

    def _visit_ProviderMethodRefExpr(self, node: ProviderMethodRefExpr, **_: object) -> str:
        return f"@{'/'.join(node.path.parts)}.{'.'.join(node.attrs)}"

    def _visit_BinaryOpExpr(self, node: BinaryOpExpr, **_: object) -> str:
        op = node.operator
        my_prec = _OP_PRECEDENCE[op]
        # ``**`` is right-associative; everything else is left-associative.
        # For left-assoc, the right child needs parens at equal precedence;
        # for right-assoc, the left child needs parens at equal precedence.
        right_assoc = op == "**"
        left_str = self._operand(node.left, my_prec, side="left", right_assoc=right_assoc)
        right_str = self._operand(node.right, my_prec, side="right", right_assoc=right_assoc)
        return f"{left_str} {op} {right_str}"

    def _visit_UnaryOpExpr(self, node: UnaryOpExpr, **_: object) -> str:
        operand = self.expr(node.operand)
        # Unary binds tighter than any binary op, so a binary operand needs parens.
        if isinstance(node.operand, BinaryOpExpr):
            operand = f"({operand})"
        return f"{node.operator}{operand}"

    def _operand(
        self,
        child: Expr,
        parent_prec: int,
        *,
        side: str,
        right_assoc: bool,
    ) -> str:
        """Render a binary-op child, wrapping in parens if precedence demands."""
        needs_parens = False
        if isinstance(child, BinaryOpExpr):
            child_prec = _OP_PRECEDENCE[child.operator]
            if child_prec < parent_prec:
                needs_parens = True
            elif child_prec == parent_prec:
                # Equal precedence: parenthesize on the associativity-breaking side.
                if right_assoc and side == "left":
                    needs_parens = True
                elif not right_assoc and side == "right":
                    needs_parens = True
        rendered = self.expr(child)
        return f"({rendered})" if needs_parens else rendered

    # Container visitors — may render multi-line.

    def _visit_ListExpr(
        self, node: ListExpr, *, col: int, line_indent: int, allow_multi: bool
    ) -> str:
        cc = self._container_comments.get(id(node))
        if not node.elements:
            if cc and cc.end:
                return self._wrap_with_comments("[", "]", [], cc.end, line_indent=line_indent)
            return "[]"
        if _has_internal_comments(cc):
            # Internal comments force multi-line regardless of allow_multi —
            # there's no faithful single-line rendering of "a list with a
            # comment between elements 2 and 3".
            return self._render_seq_multiline("[", "]", node.elements, cc, line_indent)
        inline = self._inline_list(node)
        if not allow_multi or (
            not node.had_trailing_comma and col + len(inline) <= self.options.line_length
        ):
            return inline
        return self._render_seq_multiline("[", "]", node.elements, None, line_indent)

    def _visit_DictExpr(
        self, node: DictExpr, *, col: int, line_indent: int, allow_multi: bool
    ) -> str:
        cc = self._container_comments.get(id(node))
        if not node.pairs:
            if cc and cc.end:
                return self._wrap_with_comments("{", "}", [], cc.end, line_indent=line_indent)
            return "{}"
        if _has_internal_comments(cc):
            return self._render_dict_multiline(node, cc, line_indent)
        inline = self._inline_dict(node)
        if not allow_multi or (
            not node.had_trailing_comma and col + len(inline) <= self.options.line_length
        ):
            return inline
        return self._render_dict_multiline(node, None, line_indent)

    def _visit_TupleExpr(
        self, node: TupleExpr, *, col: int, line_indent: int, allow_multi: bool
    ) -> str:
        cc = self._container_comments.get(id(node))
        if not node.elements:
            if cc and cc.end:
                return self._wrap_with_comments("(", ")", [], cc.end, line_indent=line_indent)
            return "()"
        if len(node.elements) == 1 and not _has_internal_comments(cc):
            # Syntactic trailing comma for one-element tuples — not a layout signal.
            inline = f"({self.expr(node.elements[0], allow_multi=False)},)"
            if not allow_multi or col + len(inline) <= self.options.line_length:
                return inline
            inner_indent = line_indent + self.options.indent
            inner = self.expr(node.elements[0], col=inner_indent, line_indent=inner_indent)
            return self._wrap_sequence("(", ")", [inner], line_indent=line_indent)
        if _has_internal_comments(cc):
            return self._render_seq_multiline("(", ")", node.elements, cc, line_indent)
        inline = self._inline_tuple(node)
        if not allow_multi or (
            not node.had_trailing_comma and col + len(inline) <= self.options.line_length
        ):
            return inline
        return self._render_seq_multiline("(", ")", node.elements, None, line_indent)

    def _render_seq_multiline(
        self,
        open_bracket: str,
        close_bracket: str,
        elements: list,
        cc,
        line_indent: int,
    ) -> str:
        """Multi-line render of a list or tuple, interleaving internal comments."""
        inner_indent = line_indent + self.options.indent
        items = []
        for i, e in enumerate(elements):
            rendered = self.expr(e, col=inner_indent, line_indent=inner_indent)
            leading = cc.leading.get(i, []) if cc else []
            trailing = cc.trailing.get(i, []) if cc else []
            items.append((rendered, leading, trailing))
        end_comments = cc.end if cc else []
        return self._wrap_with_comments(
            open_bracket, close_bracket, items, end_comments, line_indent=line_indent
        )

    def _render_dict_multiline(self, node: DictExpr, cc, line_indent: int) -> str:
        inner_indent = line_indent + self.options.indent
        items = []
        for i, (key, value) in enumerate(node.pairs):
            key_str = self.expr(key, allow_multi=False)
            value_col = inner_indent + len(key_str) + 2  # ": "
            value_str = self.expr(value, col=value_col, line_indent=inner_indent)
            rendered = f"{key_str}: {value_str}"
            leading = cc.leading.get(i, []) if cc else []
            trailing = cc.trailing.get(i, []) if cc else []
            items.append((rendered, leading, trailing))
        end_comments = cc.end if cc else []
        return self._wrap_with_comments("{", "}", items, end_comments, line_indent=line_indent)

    def _visit_CallExpr(
        self, node: CallExpr, *, col: int, line_indent: int, allow_multi: bool
    ) -> str:
        callable_str = self.expr(node.callable)
        if not node.kwargs:
            return f"{callable_str}()"
        inline = self._inline_call(node)
        if not allow_multi or (
            not node.had_trailing_comma and col + len(inline) <= self.options.line_length
        ):
            return inline
        inner_indent = line_indent + self.options.indent
        items: list[str] = []
        for name, value in node.kwargs.items():
            # Each kwarg lives on its own line as ``name=<value>``. The value
            # starts at ``inner_indent + len(name) + 1`` (the column after ``=``).
            value_col = inner_indent + len(name) + 1
            value_str = self.expr(value, col=value_col, line_indent=inner_indent)
            items.append(f"{name}={value_str}")
        wrapped = self._wrap_sequence("(", ")", items, line_indent=line_indent)
        return f"{callable_str}{wrapped}"

    def _visit_BuiltinCallExpr(
        self, node: BuiltinCallExpr, *, col: int, line_indent: int, allow_multi: bool
    ) -> str:
        if not node.args:
            return f"{node.function}()"
        inline = self._inline_builtin(node)
        if not allow_multi or (
            not node.had_trailing_comma and col + len(inline) <= self.options.line_length
        ):
            return inline
        inner_indent = line_indent + self.options.indent
        items = [self.expr(a, col=inner_indent, line_indent=inner_indent) for a in node.args]
        wrapped = self._wrap_sequence("(", ")", items, line_indent=line_indent)
        return f"{node.function}{wrapped}"

    # ----- Single-line helpers (used for fit-checking) -----

    def _inline_list(self, node: ListExpr) -> str:
        parts = [self.expr(e, allow_multi=False) for e in node.elements]
        return "[" + ", ".join(parts) + "]"

    def _inline_dict(self, node: DictExpr) -> str:
        items = [
            f"{self.expr(k, allow_multi=False)}: {self.expr(v, allow_multi=False)}"
            for k, v in node.pairs
        ]
        return "{" + ", ".join(items) + "}"

    def _inline_call(self, node: CallExpr) -> str:
        callable_str = self.expr(node.callable)
        parts = [
            f"{name}={self.expr(value, allow_multi=False)}" for name, value in node.kwargs.items()
        ]
        return f"{callable_str}(" + ", ".join(parts) + ")"

    def _inline_builtin(self, node: BuiltinCallExpr) -> str:
        parts = [self.expr(a, allow_multi=False) for a in node.args]
        return f"{node.function}(" + ", ".join(parts) + ")"

    def _inline_tuple(self, node: TupleExpr) -> str:
        if len(node.elements) == 1:
            return f"({self.expr(node.elements[0], allow_multi=False)},)"
        parts = [self.expr(e, allow_multi=False) for e in node.elements]
        return "(" + ", ".join(parts) + ")"

    def _wrap_sequence(
        self,
        open_bracket: str,
        close_bracket: str,
        item_renderings: list[str],
        *,
        line_indent: int,
    ) -> str:
        """Format an already-rendered list of items as a multi-line container."""
        inner_pad = " " * (line_indent + self.options.indent)
        close_pad = " " * line_indent
        body = ",\n".join(f"{inner_pad}{item}" for item in item_renderings)
        return f"{open_bracket}\n{body},\n{close_pad}{close_bracket}"

    def _wrap_with_comments(
        self,
        open_bracket: str,
        close_bracket: str,
        items: list[tuple],
        end_comments: list,
        *,
        line_indent: int,
    ) -> str:
        """Multi-line container rendering with per-element internal comments.

        ``items`` is ``[(rendered, leading_comments, trailing_comments), …]``
        where ``leading_comments`` go on their own lines above the item and
        ``trailing_comments`` go on the same line as the item, after a
        trailing comma and two spaces. ``end_comments`` are emitted between
        the last element and the closing bracket.
        """
        inner_pad = " " * (line_indent + self.options.indent)
        close_pad = " " * line_indent
        lines = [open_bracket]
        for rendered, leading, trailing in items:
            for c in leading:
                lines.append(f"{inner_pad}{c.text}")
            item_line = f"{inner_pad}{rendered},"
            if trailing:
                item_line += "  " + trailing[0].text
                # Multiple trailing comments on one source line are unusual;
                # spill extras onto their own lines after the item.
                lines.append(item_line)
                for extra in trailing[1:]:
                    lines.append(f"{inner_pad}{extra.text}")
            else:
                lines.append(item_line)
        for c in end_comments:
            lines.append(f"{inner_pad}{c.text}")
        lines.append(f"{close_pad}{close_bracket}")
        return "\n".join(lines)


@dataclass
class _ContainerComments:
    """Comments attached to a single List/Dict/Tuple container.

    All three dicts are keyed by element index (``element[i]`` for List/Tuple,
    ``pairs[i]`` for Dict).

    * ``leading[i]`` — standalone comments on lines above element ``i``.
    * ``trailing[i]`` — comments on the same source line as element ``i``,
      after its content.
    * ``end`` — comments after the last element but before the closing
      bracket (i.e. dangling inside the container with nowhere else to go).
    """

    leading: dict
    trailing: dict
    end: list


@dataclass
class _CommentBucket:
    """Output of comment classification.

    ``leading_file`` and ``trailing_file`` are comments at the top/bottom
    of the file with no neighbouring statement on one side. ``attached`` is
    a flat list of ``(comment, kind, target_stmt)`` where ``kind`` is
    either ``"leading"`` (standalone, above the target) or
    ``"trailing"`` (same-line as the target). ``container`` is keyed by
    ``id(container_node)`` and holds the per-element comments attached
    inside that List/Dict/Tuple.
    """

    leading_file: list[_Comment]
    trailing_file: list[_Comment]
    attached: list[tuple]
    container: dict


def _pos_lt(a: tuple, b: tuple) -> bool:
    """``(line, col)`` lexicographic less-than."""
    return a[0] < b[0] or (a[0] == b[0] and a[1] < b[1])


def _collect_containers(ast) -> dict:
    """Walk the AST and return a map ``(line, col) -> node`` for every
    List/Dict/Tuple container, keyed by their opening bracket position
    (which ``node.position`` already points to).

    TODO: Call/BuiltinCall are intentionally skipped — their ``(`` isn't on
    ``node.position``, so we can't index them here without an additional
    ``open_position`` field on the AST. See the module docstring.
    """
    result: dict = {}

    def walk(node):
        if node is None:
            return
        if isinstance(node, (ListExpr, TupleExpr)):
            result[(node.position.line, node.position.column)] = node
            for e in node.elements:
                walk(e)
        elif isinstance(node, DictExpr):
            result[(node.position.line, node.position.column)] = node
            for k, v in node.pairs:
                walk(k)
                walk(v)
        elif isinstance(node, CallExpr):
            walk(node.callable)
            for v in node.kwargs.values():
                walk(v)
        elif isinstance(node, BuiltinCallExpr):
            for a in node.args:
                walk(a)
        elif isinstance(node, BinaryOpExpr):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, UnaryOpExpr):
            walk(node.operand)
        elif isinstance(node, Binding):
            walk(node.value)
        elif isinstance(node, ExternalStmt):
            walk(node.path)
        elif isinstance(node, OverrideStmt):
            walk(node.path)
            walk(node.value)

    for stmt in ast.statements:
        walk(stmt)
    return result


def _element_positions(container) -> list[tuple[int, int]]:
    """Return ``(line, col)`` positions of each element in a container.

    For DictExpr we use the key's position to anchor the pair.
    """
    if isinstance(container, DictExpr):
        return [(k.position.line, k.position.column) for k, _ in container.pairs]
    return [(e.position.line, e.position.column) for e in container.elements]


def _classify_container_comment(c: _Comment, container, source_lines: list[str]) -> tuple[str, int]:
    """Decide where inside a container the comment ``c`` lives.

    Returns ``(kind, index)`` where kind ∈ {"leading", "trailing", "end"} and
    index is the element index (ignored for "end").
    """
    elem_positions = _element_positions(container)
    cpos = (c.line, c.col)

    # Same-line trailing check works against *every* element: if the comment
    # shares a source line with element N and there's content before ``#`` on
    # that line, it's a trailing-same-line of N.
    line_text = source_lines[c.line - 1] if c.line - 1 < len(source_lines) else ""
    before_marker = line_text[: c.col - 1]
    same_line = bool(before_marker.strip())

    for i, ep in enumerate(elem_positions):
        if same_line and ep[0] == c.line and _pos_lt(ep, cpos):
            return ("trailing", i)

    # Otherwise it's a standalone (on its own line). Find first element that
    # comes after the comment in source order — comment is its leading
    # comment. If none, the comment dangles at the end of the container.
    for i, ep in enumerate(elem_positions):
        if _pos_lt(cpos, ep):
            return ("leading", i)
    return ("end", -1)


def _classify_comments(
    comments: list[_Comment],
    statements: list,
    source_lines: list[str],
    *,
    containers: dict | None = None,
    bracket_pairs: dict | None = None,
) -> _CommentBucket:
    """Bucket each captured comment by its position relative to statements.

    The decision rules:

      * Before the first statement → leading_file.
      * On the same source line as something from the *previous* statement
        (i.e. text exists on that line before the ``#``) → trailing-same-line
        of that statement.
      * Between statements on its own line → standalone, attached to the
        *next* statement as a leading comment.
      * After the last statement on its own line → trailing_file.
    """
    bucket = _CommentBucket(leading_file=[], trailing_file=[], attached=[], container={})
    containers = containers or {}
    bracket_pairs = bracket_pairs or {}
    if not statements:
        # Caller (print_file) handles the no-statement case directly.
        bucket.leading_file.extend(comments)
        return bucket

    first_line = statements[0].position.line
    for c in comments:
        # First check: is this comment inside a List/Dict/Tuple container we
        # know how to render? If so it routes through the per-container
        # bucket instead of the top-level attached/leading_file machinery.
        container_node = _container_for_comment(c, bracket_pairs, containers)
        if container_node is not None:
            kind, idx = _classify_container_comment(c, container_node, source_lines)
            cc = bucket.container.setdefault(
                id(container_node),
                _ContainerComments(leading={}, trailing={}, end=[]),
            )
            if kind == "leading":
                cc.leading.setdefault(idx, []).append(c)
            elif kind == "trailing":
                cc.trailing.setdefault(idx, []).append(c)
            else:
                cc.end.append(c)
            continue

        if c.line < first_line:
            # If any line between this comment and the first statement is
            # blank, the comment sits in the file header (leading_file); if
            # the path is unbroken, it belongs to the first statement's
            # leading-comment block.
            has_blank_gap = any(
                i < len(source_lines) and source_lines[i].strip() == ""
                for i in range(c.line, first_line - 1)
            )
            if has_blank_gap:
                bucket.leading_file.append(c)
            else:
                bucket.attached.append((c, "leading", statements[0]))
            continue

        prev_idx = -1
        for i, s in enumerate(statements):
            if s.position.line <= c.line:
                prev_idx = i
            else:
                break
        next_idx = prev_idx + 1 if prev_idx + 1 < len(statements) else None

        # Is there non-whitespace on the comment's source line before the
        # ``#``? If so this is a trailing-same-line comment of prev.
        line_text = source_lines[c.line - 1] if c.line - 1 < len(source_lines) else ""
        before_marker = line_text[: c.col - 1]
        same_line = bool(before_marker.strip())

        if same_line and prev_idx >= 0:
            bucket.attached.append((c, "trailing", statements[prev_idx]))
        elif next_idx is not None:
            bucket.attached.append((c, "leading", statements[next_idx]))
        else:
            bucket.trailing_file.append(c)
    return bucket


def _has_internal_comments(cc) -> bool:
    """True iff the container has any attached internal comments."""
    return cc is not None and bool(cc.leading or cc.trailing or cc.end)


def _container_for_comment(c: _Comment, bracket_pairs: dict, containers: dict):
    """Return the deepest known List/Dict/Tuple container that strictly
    contains the comment, or ``None`` if the comment is at statement level
    or sits inside a Call/BuiltinCall (whose internal comments are still a
    Chunk-2 limitation).
    """
    best_open = None
    cpos = (c.line, c.col)
    for op, cp in bracket_pairs.items():
        if _pos_lt(op, cpos) and _pos_lt(cpos, cp):
            if op in containers and (best_open is None or _pos_lt(best_open, op)):
                best_open = op
    return containers.get(best_open) if best_open is not None else None


# Precedence levels mirror the parser (parse_additive < parse_multiplicative < parse_power).
_OP_PRECEDENCE = {
    "+": 1,
    "-": 1,
    "*": 2,
    "/": 2,
    "//": 2,
    "**": 3,
}


def _import_sort_key(stmt: object) -> tuple:
    """Sort key for import statements within a single group.

    Plain ``import x`` sorts before ``from x import …`` (isort convention),
    then alphabetically by module within each kind.
    """
    if isinstance(stmt, ImportStmt):
        return (0, stmt.module, stmt.alias or "")
    if isinstance(stmt, FromImportStmt):
        return (1, stmt.module)
    return (2, "")


def _format_literal(value: object) -> str:
    """Format a Python value as a pfig literal."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        # bool must be checked before int (bool is a subclass of int in Python).
        return "True" if value else "False"
    if isinstance(value, str):
        return _format_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # ``repr`` round-trips floats and uses scientific notation when natural.
        return repr(value)
    # Defensive: parser shouldn't produce other literal types.
    raise FormatError(f"formatter: unsupported literal type {type(value).__name__}")


def _format_string(value: str) -> str:
    """Format a string literal. Prefers ``"``; falls back to ``'`` when the
    value contains ``"`` but no ``'``. When both quote characters appear we
    keep ``"`` and escape the embedded ``"``.
    """
    has_double = '"' in value
    has_single = "'" in value
    quote = '"'
    if has_double and not has_single:
        quote = "'"
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace(quote, "\\" + quote)
    )
    return f"{quote}{escaped}{quote}"
