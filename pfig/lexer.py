"""
pfig Lexer - Tokenizes pfig source code into a stream of tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List

from .logging import get_logger

logger = get_logger("lexer")


class TokenType(Enum):
    # Literals
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    TRUE = auto()
    FALSE = auto()
    NONE = auto()

    # Identifiers and symbols
    IDENT = auto()

    # Keywords
    IMPORT = auto()
    FROM = auto()
    AS = auto()
    INCLUDE = auto()
    OVERRIDE = auto()
    EXTERNAL = auto()

    # Operators and punctuation
    AT = auto()  # @
    EQUALS = auto()  # =
    COLON = auto()  # :
    COMMA = auto()  # ,
    DOT = auto()  # .
    SLASH = auto()  # /

    LPAREN = auto()  # (
    RPAREN = auto()  # )
    LBRACKET = auto()  # [
    RBRACKET = auto()  # ]
    LBRACE = auto()  # {
    RBRACE = auto()  # }

    # Arithmetic operators
    PLUS = auto()  # +
    MINUS = auto()  # -
    MULTIPLY = auto()  # *
    DIVIDE = auto()  # /
    FLOOR_DIVIDE = auto()  # //
    POWER = auto()  # **

    # Built-in functions
    LEN = auto()
    MIN = auto()
    MAX = auto()
    RANGE = auto()

    # Special
    NEWLINE = auto()
    EOF = auto()


@dataclass
class Position:
    """Source code position for error reporting."""

    filename: str | None
    line: int
    column: int

    def __str__(self) -> str:
        filename = self.filename or "<unknown>"
        return f"{filename}:{self.line}:{self.column}"


@dataclass
class Token:
    type: TokenType
    value: str
    position: Position


class LexerError(Exception):
    def __init__(self, message: str, position: Position):
        self.message = message
        self.position = position
        super().__init__(f"{position}: {message}")


class Lexer:
    """
    Lexer for pfig configuration language following the formal grammar in §3.
    """

    KEYWORDS = {
        "import": TokenType.IMPORT,
        "from": TokenType.FROM,
        "as": TokenType.AS,
        "include": TokenType.INCLUDE,
        "override": TokenType.OVERRIDE,
        "external": TokenType.EXTERNAL,
        "True": TokenType.TRUE,
        "False": TokenType.FALSE,
        "None": TokenType.NONE,
        "len": TokenType.LEN,
        "min": TokenType.MIN,
        "max": TokenType.MAX,
        "range": TokenType.RANGE,
    }

    SINGLE_CHAR_TOKENS = {
        "@": TokenType.AT,
        "=": TokenType.EQUALS,
        ":": TokenType.COLON,
        ",": TokenType.COMMA,
        ".": TokenType.DOT,
        "(": TokenType.LPAREN,
        ")": TokenType.RPAREN,
        "[": TokenType.LBRACKET,
        "]": TokenType.RBRACKET,
        "{": TokenType.LBRACE,
        "}": TokenType.RBRACE,
        "+": TokenType.PLUS,
        "-": TokenType.MINUS,
        "*": TokenType.MULTIPLY,
        "/": TokenType.DIVIDE,
    }

    def __init__(self, source: str, filename: str | None = None):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.column = 1

    def current_position(self) -> Position:
        return Position(self.filename, self.line, self.column)

    def peek(self, offset: int = 0, length: int = 1) -> str:
        """
        Peek at character without consuming it.
        Args:
            offset: The offset from the current position.
            length: The number of characters to peek at.
        Returns:
            The characters at the given position.
        """
        pos = self.pos + offset
        if pos > len(self.source) or pos + length > len(self.source):
            return "\0"
        return self.source[pos : pos + length]

    def advance(self) -> str:
        """Consume and return current character."""
        if self.pos >= len(self.source):
            return "\0"

        char = self.source[self.pos]
        self.pos += 1

        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1

        return char

    def skip_whitespace(self):
        """Skip whitespace except newlines (which are significant)."""
        while self.peek() in " \t\r":
            self.advance()

    def skip_comment(self):
        """Skip # comments to end of line."""
        if self.peek() == "#":
            while self.peek() != "\n" and self.peek() != "\0":
                self.advance()

        if self.peek(length=3) == "'''" or self.peek(length=3) == '"""':
            comment_start = self.peek(length=3)
            comment_char = comment_start[0]
            for _ in range(3):
                self.advance()

            # Search for the closing triple quote sequence
            while True:
                if self.peek() == "\0":
                    raise LexerError(f"Unterminated multi-line comment", self.current_position())

                # Check if we found the complete closing sequence
                if self.peek(length=3) == comment_start:
                    # Found it! Consume the closing triple quotes
                    for _ in range(3):
                        self.advance()
                    break

                # Check if we found a partial closing (1 or 2 quotes of the right type)
                # This catches attempts to close with "" or " instead of """
                if self.peek() == comment_char:
                    # Count how many consecutive quotes we have
                    quote_count = 0
                    temp_pos = 0
                    while self.peek(offset=temp_pos) == comment_char:
                        quote_count += 1
                        temp_pos += 1
                        if quote_count == 3:
                            break

                    # If we have 1 or 2 quotes, check if this looks like a closing attempt
                    # vs just quotes in the middle of text
                    if quote_count < 3:
                        next_char = self.peek(offset=quote_count)

                        # Only treat as error if it looks like an actual closing attempt:
                        # 1. Quotes followed by newline/end (strong signal of intent to close)
                        # 2. OR we're at the start of a line (after only whitespace)
                        if next_char in ("\n", "\r", "\0"):
                            # This looks like someone tried to close on its own line
                            raise LexerError(
                                f"Multi-line comment has incomplete closing delimiter: "
                                f"found {quote_count} quote(s), expected 3",
                                self.current_position(),
                            )

                # Not a closing sequence, continue searching
                self.advance()

    def read_string(self) -> str:
        """Read a quoted string literal."""
        start_pos = self.current_position()  # Remember start position for error reporting
        quote_char = self.advance()  # consume opening quote
        value = ""

        while self.peek() != quote_char and self.peek() != "\0":
            char = self.advance()
            if char == "\\":
                # Handle escape sequences
                next_char = self.advance()
                if next_char == "n":
                    value += "\n"
                elif next_char == "t":
                    value += "\t"
                elif next_char == "r":
                    value += "\r"
                elif next_char == "\\":
                    value += "\\"
                elif next_char == quote_char:
                    value += quote_char
                else:
                    value += next_char
            else:
                value += char

        if self.peek() != quote_char:
            raise LexerError(f"Unterminated string literal", start_pos)

        self.advance()  # consume closing quote
        return value

    def read_number(self) -> Token:
        """Read a numeric literal (int or float)."""
        position = self.current_position()
        value = ""
        is_float = False

        # Read digits and underscores
        while self.peek().isdigit() or self.peek() == "_":
            value += self.advance()

        # Check for decimal point
        if self.peek() == ".":
            is_float = True
            value += self.advance()  # consume '.'
            while self.peek().isdigit() or self.peek() == "_":
                value += self.advance()

        # Check for scientific notation (e.g., 1e-4, 2.5e10)
        if self.peek().lower() == "e":
            is_float = True
            value += self.advance()  # consume 'e' or 'E'

            # Optional sign after 'e'
            if self.peek() in "+-":
                value += self.advance()

            # Must have digits after 'e' (and optional sign)
            if not (self.peek().isdigit() or self.peek() == "_"):
                raise LexerError("Expected digits after 'e' in number", self.current_position())

            while self.peek().isdigit() or self.peek() == "_":
                value += self.advance()

        token_type = TokenType.FLOAT if is_float else TokenType.INT
        return Token(token_type, value, position)

    def read_identifier(self) -> Token:
        """Read an identifier or keyword."""
        position = self.current_position()
        value = ""

        # First character must be letter or underscore
        if self.peek().isalpha() or self.peek() == "_":
            value += self.advance()

        # Subsequent characters can be alphanumeric or underscore
        while self.peek().isalnum() or self.peek() == "_":
            value += self.advance()

        # Check if it's a keyword
        token_type = self.KEYWORDS.get(value, TokenType.IDENT)
        return Token(token_type, value, position)

    def tokenize(self) -> List[Token]:
        """Tokenize the entire source into a list of tokens."""
        logger.debug(f"Starting tokenization of {len(self.source)} characters")
        tokens = []

        while self.pos < len(self.source):
            self.skip_whitespace()
            self.skip_comment()

            if self.pos >= len(self.source):
                break

            position = self.current_position()
            char = self.peek()

            # Newlines (significant for pfig)
            if char == "\n":
                tokens.append(Token(TokenType.NEWLINE, self.advance(), position))
                continue

            # String literals
            if char in "\"'":
                value = self.read_string()
                tokens.append(Token(TokenType.STRING, value, position))
                continue

            # Numbers (not including negative - let parser handle unary minus)
            if char.isdigit():
                tokens.append(self.read_number())
                continue

            # Identifiers (no more dotted name tokens - let parser handle dots)
            if char.isalpha() or char == "_":
                # Read identifier and let parser handle any following dots
                ident_token = self.read_identifier()
                tokens.append(ident_token)
                continue

            if char == "*" and self.peek(offset=1) == "*":
                self.advance()
                self.advance()
                tokens.append(Token(TokenType.POWER, "**", position))
                continue

            if char == "/" and self.peek(offset=1) == "/":
                self.advance()
                self.advance()
                tokens.append(Token(TokenType.FLOOR_DIVIDE, "//", position))
                continue

            if char in self.SINGLE_CHAR_TOKENS:
                self.advance()
                tokens.append(Token(self.SINGLE_CHAR_TOKENS[char], char, position))
                continue

            # Unknown character
            raise LexerError(f"Unexpected character: '{char}'", position)

        # Add EOF token
        tokens.append(Token(TokenType.EOF, "", self.current_position()))
        logger.debug(f"Tokenization complete: produced {len(tokens)} tokens")
        return tokens
