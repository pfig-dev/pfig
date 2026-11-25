"""
Unit tests for the pfig lexer.
"""

import pytest

from pfig.lexer import Lexer, LexerError, Token, TokenType


class TestBasicTokenization:
    """Test basic token recognition."""

    def test_keywords(self):
        """Test that pfig keywords are recognized."""
        source = "import from as include override external"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        expected_types = [
            TokenType.IMPORT,
            TokenType.FROM,
            TokenType.AS,
            TokenType.INCLUDE,
            TokenType.OVERRIDE,
            TokenType.EXTERNAL,
            TokenType.EOF,
        ]

        actual_types = [token.type for token in tokens]
        assert actual_types == expected_types

    def test_identifiers(self):
        """Test identifier tokenization."""
        source = "foo bar_baz _private Class123"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # All should be IDENT tokens
        ident_tokens = [t for t in tokens if t.type == TokenType.IDENT]
        assert len(ident_tokens) == 4
        assert [t.value for t in ident_tokens] == [
            "foo",
            "bar_baz",
            "_private",
            "Class123",
        ]

    def test_dotted_names(self):
        """Test dotted name tokenization - now produces individual IDENT and DOT tokens."""
        source = "json.loads collections.defaultdict"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # Filter out newlines and EOF for easier testing
        relevant_tokens = [t for t in tokens if t.type not in [TokenType.NEWLINE, TokenType.EOF]]

        # Should get: IDENT DOT IDENT IDENT DOT IDENT
        expected_sequence = [
            (TokenType.IDENT, "json"),
            (TokenType.DOT, "."),
            (TokenType.IDENT, "loads"),
            (TokenType.IDENT, "collections"),
            (TokenType.DOT, "."),
            (TokenType.IDENT, "defaultdict"),
        ]

        actual_sequence = [(t.type, t.value) for t in relevant_tokens]
        assert actual_sequence == expected_sequence

    def test_literals(self):
        """Test literal tokenization."""
        source = """42 3.14 1e-4 1. "hello" 'world' True False None"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # Filter out EOF token
        tokens = [t for t in tokens if t.type != TokenType.EOF]

        expected = [
            (TokenType.INT, "42"),
            (TokenType.FLOAT, "3.14"),
            (TokenType.FLOAT, "1e-4"),
            (TokenType.FLOAT, "1."),
            (TokenType.STRING, "hello"),
            (TokenType.STRING, "world"),
            (TokenType.TRUE, "True"),
            (TokenType.FALSE, "False"),
            (TokenType.NONE, "None"),
        ]

        actual = [(t.type, t.value) for t in tokens]
        assert actual == expected

    def test_operators(self):
        """Test operator tokenization."""
        source = "@ = : , . / ( ) [ ] { } + - * ** /"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        expected_types = [
            TokenType.AT,
            TokenType.EQUALS,
            TokenType.COLON,
            TokenType.COMMA,
            TokenType.DOT,
            TokenType.DIVIDE,
            TokenType.LPAREN,
            TokenType.RPAREN,
            TokenType.LBRACKET,
            TokenType.RBRACKET,
            TokenType.LBRACE,
            TokenType.RBRACE,
            TokenType.PLUS,
            TokenType.MINUS,
            TokenType.MULTIPLY,
            TokenType.POWER,
            TokenType.DIVIDE,
            TokenType.EOF,
        ]

        actual_types = [t.type for t in tokens]
        assert actual_types == expected_types

    def test_floor_divide(self):
        """Test that // is one token and a following / stays DIVIDE."""
        source = "a // b / c"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        expected = [
            (TokenType.IDENT, "a"),
            (TokenType.FLOOR_DIVIDE, "//"),
            (TokenType.IDENT, "b"),
            (TokenType.DIVIDE, "/"),
            (TokenType.IDENT, "c"),
            (TokenType.EOF, ""),
        ]
        actual = [(t.type, t.value) for t in tokens]
        assert actual == expected

    def test_builtins(self):
        """Test built-in function recognition."""
        source = "len min max range"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        expected_types = [
            TokenType.LEN,
            TokenType.MIN,
            TokenType.MAX,
            TokenType.RANGE,
            TokenType.EOF,
        ]
        actual_types = [t.type for t in tokens]
        assert actual_types == expected_types


class TestStringHandling:
    """Test string literal parsing."""

    def test_basic_strings(self):
        """Test basic string parsing."""
        source = "\"hello\" 'world'"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        strings = [t for t in tokens if t.type == TokenType.STRING]
        assert len(strings) == 2
        assert strings[0].value == "hello"
        assert strings[1].value == "world"

    def test_escape_sequences(self):
        """Test string escape sequence handling."""
        source = r'"line1\nline2\ttab\"quote\\backslash"'
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        string_token = next(t for t in tokens if t.type == TokenType.STRING)
        assert string_token.value == 'line1\nline2\ttab"quote\\backslash'

    def test_unterminated_string(self):
        """Test error on unterminated string."""
        source = '"unterminated string'
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        assert "Unterminated string literal" in str(exc_info.value)
        assert "test.pfig:1:1" in str(exc_info.value)


class TestNumberHandling:
    """Test numeric literal parsing."""

    def test_integers(self):
        """Test integer parsing."""
        source = "0 42 123"  # Remove negative test since unary minus is handled by parser
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        numbers = [t for t in tokens if t.type in (TokenType.INT, TokenType.FLOAT)]
        assert len(numbers) == 3
        assert numbers[0].value == "0"
        assert numbers[1].value == "42"
        assert numbers[2].value == "123"

    def test_floats(self):
        """Test float parsing."""
        source = "42. 3.14 2.5 0.001"  # Remove negative test since unary minus is handled by parser
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        floats = [t for t in tokens if t.type == TokenType.FLOAT]
        assert len(floats) == 4
        assert floats[0].value == "42."
        assert floats[1].value == "3.14"
        assert floats[2].value == "2.5"
        assert floats[3].value == "0.001"

    def test_scientific_notation(self):
        """Test scientific notation parsing."""
        source = (
            "1e10 2.5e-3 1.23E+5"  # Remove negative test since unary minus is handled by parser
        )
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        sci_numbers = [t for t in tokens if t.type == TokenType.FLOAT]
        assert len(sci_numbers) == 3
        assert sci_numbers[0].value == "1e10"
        assert sci_numbers[1].value == "2.5e-3"
        assert sci_numbers[2].value == "1.23E+5"

    def test_integers_with_underscores(self):
        """Test integer parsing with underscores."""
        source = "1_000_000 42_000 1_234_567_890"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        numbers = [t for t in tokens if t.type == TokenType.INT]
        assert len(numbers) == 3
        assert numbers[0].value == "1_000_000"
        assert numbers[1].value == "42_000"
        assert numbers[2].value == "1_234_567_890"

    def test_floats_with_underscores(self):
        """Test float parsing with underscores."""
        source = "1_000.5 3.14_159 1_234.567_890"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        floats = [t for t in tokens if t.type == TokenType.FLOAT]
        assert len(floats) == 3
        assert floats[0].value == "1_000.5"
        assert floats[1].value == "3.14_159"
        assert floats[2].value == "1_234.567_890"

    def test_scientific_notation_with_underscores(self):
        """Test scientific notation parsing with underscores."""
        source = "1_000e10 2.5e1_000 1.23E+5_000"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        sci_numbers = [t for t in tokens if t.type == TokenType.FLOAT]
        assert len(sci_numbers) == 3
        assert sci_numbers[0].value == "1_000e10"
        assert sci_numbers[1].value == "2.5e1_000"
        assert sci_numbers[2].value == "1.23E+5_000"


class TestComments:
    """Test comment handling."""

    def test_line_comments(self):
        """Test that line comments are ignored."""
        source = """
# This is a comment
batch_size = 64  # End of line comment
# Another comment
learning_rate = 0.001
"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # Should only get the actual code tokens, not comments
        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64", "learning_rate", "=", "0.001"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_basic(self):
        """Test basic multi-line comment with triple double quotes."""
        source = '''
"""
This is a multi-line comment
spanning multiple lines
"""
batch_size = 64
'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # Should only get the actual code tokens, not comments
        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_single_quotes(self):
        """Test multi-line comment with triple single quotes."""
        source = """
'''
This is a multi-line comment
with single quotes
'''
learning_rate = 0.001
"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["learning_rate", "=", "0.001"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_between_code(self):
        """Test multi-line comment between code statements."""
        source = '''
batch_size = 64
"""
This comment is between
two assignments
"""
learning_rate = 0.001
'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64", "learning_rate", "=", "0.001"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_inline(self):
        """Test multi-line comment on same line as code."""
        source = 'batch_size = 64\n"""inline comment"""\nlearning_rate = 0.001'
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64", "learning_rate", "=", "0.001"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_empty(self):
        """Test empty multi-line comment."""
        source = '''
""""""
batch_size = 64
'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_with_special_characters(self):
        """Test multi-line comment containing special characters and symbols."""
        source = '''
"""
This comment has special chars: @#$%^&*()
And operators: = + - * / **
And keywords: import from include override
"""
batch_size = 64
'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_with_code_like_content(self):
        """Test multi-line comment containing code-like content."""
        source = '''
"""
batch_size = 128
learning_rate = 0.01
model = MyModel()
"""
batch_size = 64
'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        # Should only get the real assignment, not the commented one
        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_multiple_in_file(self):
        """Test multiple multi-line comments in same file."""
        source = """
\"""First comment\"""
batch_size = 64
'''
Second comment with single quotes
'''
learning_rate = 0.001
\"""
Third comment
at the end
\"""
"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64", "learning_rate", "=", "0.001"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_at_file_start(self):
        """Test multi-line comment at the very start of file."""
        source = '''"""
Module docstring / file header comment
"""
import torch
batch_size = 64'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["import", "torch", "batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_at_file_end(self):
        """Test multi-line comment at the very end of file."""
        source = '''batch_size = 64
"""
End of file comment
No code after this
"""'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_with_single_quote_inside_double(self):
        """Test multi-line comment with single quotes inside."""
        source = '''
"""
This comment contains 'single quotes'
and "nested double quotes" too
"""
batch_size = 64
'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_with_double_quote_inside_single(self):
        """Test multi-line comment with double quotes inside."""
        source = """
'''
This comment contains "double quotes"
and isn't affected by them
'''
batch_size = 64
"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_unterminated_multiline_comment_double_quotes(self):
        """Test error on unterminated multi-line comment with double quotes."""
        source = '''
"""
This comment is never closed
batch_size = 64
'''
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        assert "Unterminated multi-line comment" in str(exc_info.value)

    def test_unterminated_multiline_comment_single_quotes(self):
        """Test error on unterminated multi-line comment with single quotes."""
        source = """
'''
This comment is never closed
batch_size = 64
"""
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        assert "Unterminated multi-line comment" in str(exc_info.value)

    def test_multiline_comment_wrong_closing_type(self):
        """Test error on multi-line comment with wrong quote type."""
        source = """
\"""
This comment starts with double quotes
but tries to close with single
'''
"""
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        # Should fail because it needs """ to close, not '''
        assert "Unterminated" in str(exc_info.value) or "incomplete" in str(exc_info.value).lower()

    def test_multiline_comment_wrong_closing_count_two_quotes(self):
        """Test error on multi-line comment closed with only 2 quotes."""
        source = '''
"""
This comment
""
batch_size = 64
'''
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        # Should detect that only 2 quotes were used
        error_msg = str(exc_info.value)
        assert "incomplete closing delimiter" in error_msg.lower() or "found 2 quote" in error_msg

    def test_multiline_comment_wrong_closing_count_one_quote(self):
        """Test error on multi-line comment closed with only 1 quote."""
        source = '''
"""
This comment
"
batch_size = 64
'''
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        # Should detect that only 1 quote was used
        error_msg = str(exc_info.value)
        assert "incomplete closing delimiter" in error_msg.lower() or "found 1 quote" in error_msg

    def test_multiline_comment_partial_closing(self):
        """Test that partial closing quotes (1 or 2) don't terminate comment."""
        source = '''
"""
This has a "" but not three quotes
Still in comment
"""
batch_size = 64
'''
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_mixed_single_and_line_comments(self):
        """Test mixing single-line and multi-line comments."""
        source = """
# Single line comment
\"""
Multi-line comment
\"""
batch_size = 64  # Another single line
'''
More multi-line
'''
learning_rate = 0.001
"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64", "learning_rate", "=", "0.001"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values

    def test_multiline_comment_with_newlines(self):
        """Test multi-line comment preserves newlines internally (but they're ignored)."""
        source = """
'''


Multiple blank lines inside


'''
batch_size = 64
"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        non_newline_tokens = [t for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF)]

        expected_values = ["batch_size", "=", "64"]
        actual_values = [t.value for t in non_newline_tokens]
        assert actual_values == expected_values


class TestPositionTracking:
    """Test source position tracking for error reporting."""

    def test_position_tracking(self):
        """Test that tokens track their source positions correctly."""
        source = '''line1 = 42
line2 = "hello"'''
        lexer = Lexer(source, "test.pfig")
        tokens = lexer.tokenize()

        # Find specific tokens and check their positions
        line1_token = next(t for t in tokens if t.value == "line1")
        assert line1_token.position.line == 1
        assert line1_token.position.column == 1
        assert line1_token.position.filename == "test.pfig"

        line2_token = next(t for t in tokens if t.value == "line2")
        assert line2_token.position.line == 2
        assert line2_token.position.column == 1

        hello_token = next(t for t in tokens if t.value == "hello")
        assert hello_token.position.line == 2
        assert hello_token.position.column == 9  # After "line2 = "


class TestComplexExpressions:
    """Test tokenization of complex pfig expressions."""

    def test_provider_syntax(self):
        """Test @ provider syntax."""
        source = "@model @dataset() @config/batch_size"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # Remove EOF
        tokens = [t for t in tokens if t.type != TokenType.EOF]

        expected = [
            TokenType.AT,
            TokenType.IDENT,  # @model
            TokenType.AT,
            TokenType.IDENT,
            TokenType.LPAREN,
            TokenType.RPAREN,  # @dataset()
            TokenType.AT,
            TokenType.IDENT,
            TokenType.DIVIDE,
            TokenType.IDENT,  # @config/batch_size
        ]

        actual = [t.type for t in tokens]
        assert actual == expected

    def test_function_call_syntax(self):
        """Test function call tokenization - now with individual IDENT and DOT tokens."""
        source = "json.loads(s='{\"test\": true}', strict=False)"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # Should have IDENT DOT IDENT sequence, then call syntax
        relevant_tokens = [t for t in tokens if t.type not in [TokenType.NEWLINE, TokenType.EOF]]

        # Check we get json.loads as separate tokens
        assert relevant_tokens[0].type == TokenType.IDENT and relevant_tokens[0].value == "json"
        assert relevant_tokens[1].type == TokenType.DOT and relevant_tokens[1].value == "."
        assert relevant_tokens[2].type == TokenType.IDENT and relevant_tokens[2].value == "loads"

        # Should have proper parentheses and parameter syntax
        lparen = next(t for t in tokens if t.type == TokenType.LPAREN)
        rparen = next(t for t in tokens if t.type == TokenType.RPAREN)
        assert lparen is not None
        assert rparen is not None

    def test_container_literals(self):
        """Test list/tuple/dict tokenization."""
        source = '[1, 2, 3] (a, b) {"key": "value"}'
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        # Check bracket types
        brackets = [
            t
            for t in tokens
            if t.type
            in (
                TokenType.LBRACKET,
                TokenType.RBRACKET,
                TokenType.LPAREN,
                TokenType.RPAREN,
                TokenType.LBRACE,
                TokenType.RBRACE,
            )
        ]

        expected_brackets = [
            TokenType.LBRACKET,
            TokenType.RBRACKET,  # [...]
            TokenType.LPAREN,
            TokenType.RPAREN,  # (...)
            TokenType.LBRACE,
            TokenType.RBRACE,  # {...}
        ]

        actual_brackets = [t.type for t in brackets]
        assert actual_brackets == expected_brackets


class TestErrorHandling:
    """Test lexer error conditions."""

    def test_invalid_characters(self):
        """Test handling of invalid characters."""
        source = "valid = 42\n$invalid"
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        assert "Unexpected character" in str(exc_info.value)
        assert "$" in str(exc_info.value)
        assert "test.pfig:2:" in str(exc_info.value)

    def test_malformed_numbers(self):
        """Test handling of malformed scientific notation."""
        source = "bad_number = 1e"
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        assert "Expected digits after 'e'" in str(exc_info.value)
