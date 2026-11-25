"""
Tests for pfig error handling and error reporting.
"""

import pytest

from pfig.execution import (
    ExecutionError,
    ValidationError,
    ValidationIssue,
    execute_pfig,
    get_materialized_bindings,
)
from pfig.lexer import Lexer, LexerError
from pfig.parser import ParseError, parse_pfig_source


class TestLexerErrors:
    """Test lexer error conditions and reporting."""

    def test_unterminated_string_error(self):
        """Test error for unterminated string literals."""
        source = 'name = "unterminated string'
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        error = exc_info.value
        assert "Unterminated string literal" in error.message
        assert error.position.filename == "test.pfig"
        assert error.position.line == 1
        assert "test.pfig:1:" in str(error)

    def test_invalid_character_error(self):
        """Test error for invalid characters."""
        source = "valid = 42\n$invalid_char"
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        error = exc_info.value
        assert "Unexpected character" in error.message
        assert "$" in error.message
        assert error.position.line == 2
        assert "test.pfig:2:" in str(error)

    def test_malformed_scientific_notation(self):
        """Test error for malformed scientific notation."""
        source = "number = 1e"
        lexer = Lexer(source, "test.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        error = exc_info.value
        assert "Expected digits after 'e'" in error.message

    def test_position_tracking_accuracy(self):
        """Test that error positions are accurate."""
        source = """line1 = 42
line2 = "ok"
line3 = $bad"""

        lexer = Lexer(source, "multiline.pfig")

        with pytest.raises(LexerError) as exc_info:
            lexer.tokenize()

        error = exc_info.value
        assert error.position.line == 3
        assert error.position.column == 9  # Position of '$'
        assert "multiline.pfig:3:9" in str(error)


class TestParserErrors:
    """Test parser error conditions and reporting."""

    def test_unexpected_token_error(self):
        """Test error for unexpected tokens."""
        source = "name 42"  # Missing '='

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Expected EQUALS" in error.message
        assert "test.pfig" in str(error)

    def test_missing_closing_bracket(self):
        """Test error for missing closing brackets."""
        source = "items = [1, 2, 3"

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Expected RBRACKET" in error.message

    def test_missing_closing_paren(self):
        """Test error for missing closing parenthesis."""
        source = "coords = (10, 20"

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Expected RPAREN" in error.message

    def test_missing_closing_brace(self):
        """Test error for missing closing brace."""
        source = 'config = {"key": "value"'

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Expected RBRACE" in error.message

    def test_invalid_provider_syntax(self):
        """Test error for invalid @ syntax."""
        source = "bad = @123"  # @ must be followed by identifier

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Expected IDENT" in error.message

    def test_missing_dict_colon(self):
        """Test error for missing colon in dictionary."""
        source = 'config = {"key" "value"}'  # Missing ':'

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Expected COLON" in error.message

    def test_invalid_function_kwargs(self):
        """Test error for invalid function call syntax."""
        source = "result = func(123)"  # Positional args not allowed, must use kwargs

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        # Should now get helpful error about positional arguments
        error = exc_info.value
        assert "Positional arguments are not supported" in error.message
        assert "keyword arguments" in error.message

    def test_provider_call_with_positional_args_numbers(self):
        """Test error for provider call with numeric positional arguments."""
        source = "model = @base_model(784, 10)"  # Should use keyword arguments

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Positional arguments are not supported" in error.message
        assert "keyword arguments" in error.message

    def test_provider_call_with_positional_args_strings(self):
        """Test error for provider call with string positional arguments."""
        source = 'config = @loader("data.json", "utf-8")'

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Positional arguments are not supported" in error.message
        assert "keyword arguments" in error.message

    def test_provider_call_with_positional_identifier(self):
        """Test error for provider call with identifier as positional argument."""
        source = """
batch_size = 64
model = @create_model(batch_size)
"""

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Positional arguments are not supported" in error.message
        assert "batch_size=" in error.message  # Should suggest the correct syntax

    def test_provider_call_mixed_args(self):
        """Test error for provider call with mixed positional and keyword arguments."""
        source = "model = @base_model(784, hidden_dim=256)"  # First arg is positional

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Positional arguments are not supported" in error.message

    def test_regular_function_call_with_positional_args(self):
        """Test error for regular function call with positional arguments."""
        source = """
import json
result = json.loads("text", True)
"""

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error = exc_info.value
        assert "Positional arguments are not supported" in error.message
        assert "keyword arguments" in error.message

    def test_multiple_errors_context(self):
        """Test error reporting with surrounding context."""
        source = """
import json
batch_size = 64
invalid syntax here
learning_rate = 0.001
"""

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="context.pfig")

        error = exc_info.value
        # Should indicate the problematic line
        assert "context.pfig" in str(error)
        # Line number should be around line 4 where the error occurs
        line_num_found = any(str(i) in str(error) for i in range(3, 6))
        assert line_num_found


class TestExecutionErrors:
    """Test execution engine error conditions."""

    def test_unknown_import_error(self):
        """Test error when importing non-existent module."""
        source = """
import nonexistent_module

result = nonexistent_module.function()
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises((ExecutionError, ValidationError, ModuleNotFoundError)) as exc_info:
            execute_pfig(ast)

        # Should mention the problematic module
        error_msg = str(exc_info.value)
        assert "nonexistent_module" in error_msg

    def test_unknown_attribute_error(self):
        """Test error when accessing non-existent attribute."""
        source = """
import json

result = json.nonexistent_function()
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises((ExecutionError, ValidationError, AttributeError)) as exc_info:
            execute_pfig(ast)

        error_msg = str(exc_info.value)
        assert "nonexistent_function" in error_msg or "attribute" in error_msg.lower()

    def test_constructor_failure_error(self):
        """Test error when constructor call fails."""
        source = """
import json

# Provider definition - will fail when materialized
result_provider = json.loads(s="invalid json {")

# Provider reference (will fail during materialization)
result = @result_provider
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ExecutionError) as exc_info:
            get_materialized_bindings(ast, entrypoint="result")

        error = exc_info.value
        # Should mention the provider that failed
        assert "Failed to construct" in error.message or "result_provider" in error.message

    def test_missing_kwarg_error(self):
        """Test error when required kwargs are missing."""
        # Note: This test depends on having a provider with missing kwargs
        # The current implementation may not fully validate this yet
        source = """
# This would test missing required arguments
# But our current implementation may not catch this at execution time
placeholder = 42
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="placeholder")

        # For now, just verify this doesn't crash
        assert result["placeholder"] == 42

    def test_provider_dependency_error(self):
        """Test error for unknown provider dependencies."""
        # This test represents future functionality when @ references work
        source = """
# This would reference a non-existent provider
# current_model = @nonexistent_provider
placeholder = 42
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="placeholder")

        # Currently just a placeholder
        assert result["placeholder"] == 42


class TestErrorRecovery:
    """Test error recovery and handling strategies."""

    def test_early_error_detection(self):
        """Test that errors are detected as early as possible."""
        # Lexer should catch this before parser even runs
        source = 'name = "unterminated'

        with pytest.raises(LexerError):
            # Should fail at lexing stage
            parse_pfig_source(source, filename="test.pfig")

    def test_error_doesn_not_crash_parser(self):
        """Test that parser errors don't crash the system."""
        invalid_sources = [
            "invalid syntax",
            "name = ",
            "= value",
            "[unclosed list",
            "{'unclosed': dict",
            "@",
            "@123invalid",
        ]

        for source in invalid_sources:
            with pytest.raises((LexerError, ParseError)):
                parse_pfig_source(source, filename="test.pfig")
            # The important thing is we get a proper exception, not a crash

    def test_error_messages_are_helpful(self):
        """Test that error messages provide useful information."""
        test_cases = [
            ('name = "unterminated', "Unterminated string"),
            ("name 42", "Expected EQUALS"),
            ("items = [1, 2", "Expected RBRACKET"),
            ("bad = @123", "Expected IDENT"),
        ]

        for source, expected_phrase in test_cases:
            with pytest.raises((LexerError, ParseError)) as exc_info:
                parse_pfig_source(source, filename="test.pfig")

            error_msg = str(exc_info.value)
            assert expected_phrase in error_msg, (
                f"Expected '{expected_phrase}' in error for '{source}'"
            )


class TestErrorPositioning:
    """Test accuracy of error position reporting."""

    def test_multiline_error_positions(self):
        """Test error positions in multiline files."""
        source = """# Line 1: comment
import json  # Line 2: import
batch_size = 64  # Line 3: valid binding
invalid syntax here  # Line 4: ERROR
learning_rate = 0.001  # Line 5: would be valid
"""

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="multiline.pfig")

        error = exc_info.value
        # Error should be reported on line 4
        assert ":4:" in str(error) or "4:" in str(error)
        assert "multiline.pfig" in str(error)

    def test_column_accuracy(self):
        """Test that column positions are reasonably accurate."""
        source = "name = $invalid"  # Error at column 8

        with pytest.raises(LexerError) as exc_info:
            lexer = Lexer(source, "test.pfig")
            lexer.tokenize()

        error = exc_info.value
        # Column should be around 8 (where '$' appears)
        assert error.position.column == 8

    def test_nested_error_positions(self):
        """Test error positions in nested structures."""
        source = """
config = {
    "valid_key": "valid_value",
    "bad_key": $invalid_value,
    "another_key": "another_value"
}
"""

        with pytest.raises(LexerError) as exc_info:
            lexer = Lexer(source, "nested.pfig")
            lexer.tokenize()

        error = exc_info.value
        # Error should be on line 4 (where $invalid_value is)
        assert error.position.line == 4


class TestErrorIntegration:
    """Test error handling in integration scenarios."""

    def test_parse_error_doesn_prevent_retry(self):
        """Test that after a parse error, we can try parsing again."""
        bad_source = "invalid syntax"
        good_source = "valid_name = 42"

        # First parse should fail
        with pytest.raises(ParseError):
            parse_pfig_source(bad_source, filename="bad.pfig")

        # Second parse should succeed
        ast = parse_pfig_source(good_source, filename="good.pfig")
        result = get_materialized_bindings(ast, entrypoint="valid_name")
        assert result["valid_name"] == 42

    def test_execution_error_preserves_partial_results(self):
        """Test behavior when execution fails partway through."""
        source = """
import json

# These should succeed
good_value1 = 42
good_value2 = "hello"

# This should fail
bad_value = json.loads(s="invalid json")

# This won't be reached due to dependency order
good_value3 = 123
"""

        ast = parse_pfig_source(source, filename="partial.pfig")

        with pytest.raises(ExecutionError):
            execute_pfig(ast)

        # The behavior here depends on execution order and error handling strategy
        # For now, we just verify that proper exceptions are raised

    def test_cascading_errors(self):
        """Test handling of multiple related errors."""
        source = """
# Multiple syntax errors
invalid1 =
invalid2 = $bad_char
invalid3 = [unclosed
"""

        # Should fail on the first error encountered
        with pytest.raises((LexerError, ParseError)):
            parse_pfig_source(source, filename="cascade.pfig")

        # The parser should stop at the first error, not try to continue


class TestErrorMessages:
    """Test quality and usefulness of error messages."""

    def test_error_message_completeness(self):
        """Test that error messages include all necessary information."""
        source = 'bad = "unterminated string'

        with pytest.raises(LexerError) as exc_info:
            lexer = Lexer(source, "complete.pfig")
            lexer.tokenize()

        error_msg = str(exc_info.value)

        # Should include:
        # - Filename
        # - Line number
        # - Column number (optional but nice)
        # - Description of the error
        assert "complete.pfig" in error_msg
        assert "1" in error_msg  # Line number
        assert "Unterminated" in error_msg

    def test_suggested_fixes_in_errors(self):
        """Test that some errors suggest fixes."""
        # This is aspirational - good error messages often suggest solutions
        source = "name 42"  # Missing '='

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        error_msg = str(exc_info.value)
        # At minimum, should be clear about what was expected
        assert "Expected EQUALS" in error_msg
        # Ideally would suggest adding '=' but that's advanced

    def test_context_in_error_messages(self):
        """Test that errors provide sufficient context."""
        source = """
import json
from collections import defaultdict

# Many lines of valid code to provide context
value1 = 42
value2 = "hello"
value3 = [1, 2, 3]
value4 = {"key": "value"}

# Error on this line
invalid syntax here

value5 = 123
"""

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="context.pfig")

        error_msg = str(exc_info.value)
        # Should mention the line where error occurs
        # Exact line number depends on parsing, but should be around 11-12
        has_line_info = any(str(i) in error_msg for i in range(10, 14))
        assert has_line_info


class TestValidationIssue:
    """Test ValidationIssue dataclass."""

    def test_create_validation_issue(self):
        """Test creating a ValidationIssue."""
        issue = ValidationIssue(
            code="unknown_binding",
            message="Unknown binding: 'foo'",
            binding="foo",
        )
        assert issue.code == "unknown_binding"
        assert issue.message == "Unknown binding: 'foo'"
        assert issue.binding == "foo"
        assert issue.position is None

    def test_validation_issue_str(self):
        """Test ValidationIssue string representation."""
        issue = ValidationIssue(
            code="duplicate_binding",
            message="Binding 'bar' redefines existing binding.",
        )
        assert str(issue) == "Binding 'bar' redefines existing binding."


class TestValidationError:
    """Test ValidationError exception."""

    def test_single_issue(self):
        """Test ValidationError with a single issue."""
        issues = [
            ValidationIssue(
                code="unknown_binding",
                message="Unknown binding: 'foo'",
            )
        ]
        error = ValidationError(issues)

        assert len(error.issues) == 1
        assert "Validation failed (1 issue):" in str(error)
        assert "Unknown binding: 'foo'" in str(error)

    def test_multiple_issues(self):
        """Test ValidationError with multiple issues."""
        issues = [
            ValidationIssue(code="unknown_binding", message="Unknown binding: 'foo'"),
            ValidationIssue(code="unknown_binding", message="Unknown binding: 'bar'"),
            ValidationIssue(code="duplicate_binding", message="Duplicate: 'baz'"),
        ]
        error = ValidationError(issues)

        assert len(error.issues) == 3
        assert "Validation failed (3 issues):" in str(error)
        assert "Unknown binding: 'foo'" in str(error)
        assert "Unknown binding: 'bar'" in str(error)
        assert "Duplicate: 'baz'" in str(error)

    def test_error_message_format(self):
        """Test that error message has proper format."""
        issues = [
            ValidationIssue(code="test1", message="First error"),
            ValidationIssue(code="test2", message="Second error"),
        ]
        error = ValidationError(issues)

        msg = str(error)
        lines = msg.split("\n")
        assert lines[0] == "Validation failed (2 issues):"
        assert lines[1] == "  - First error"
        assert lines[2] == "  - Second error"


class TestValidationErrorCollection:
    """Test that validation errors are collected and reported together."""

    def test_multiple_unknown_bindings(self):
        """Test that multiple unknown binding errors are collected."""
        source = """
import json

# Provider that references multiple non-existent bindings
# The dict container makes all refs part of the same provider
container = {"a": @nonexistent1, "b": @nonexistent2}

__entrypoint__ = @container
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ValidationError) as exc_info:
            execute_pfig(ast)

        error = exc_info.value
        # Should have collected multiple unknown binding issues
        assert len(error.issues) >= 2
        # All should be unknown binding errors
        messages = [issue.message for issue in error.issues]
        assert any("nonexistent1" in m for m in messages)
        assert any("nonexistent2" in m for m in messages)

    def test_duplicate_bindings_collected(self):
        """Test that duplicate binding errors are collected."""
        source = """
from collections import OrderedDict

foo = 42
foo = 43
bar = 1
bar = 2

result = OrderedDict(foo=@foo, bar=@bar)
__entrypoint__ = @result
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ValidationError) as exc_info:
            execute_pfig(ast)

        error = exc_info.value
        # Should have at least 2 duplicate binding errors
        assert len(error.issues) >= 2
        assert any("foo" in issue.message for issue in error.issues)
        assert any("bar" in issue.message for issue in error.issues)

    def test_mixed_error_types_collected(self):
        """Test that different types of validation errors are collected together."""
        source = """
# First: duplicate binding
value = 42
value = 43

# Second: reference to unknown binding
result = @nonexistent

__entrypoint__ = @result
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ValidationError) as exc_info:
            execute_pfig(ast)

        error = exc_info.value
        # Should have collected both types of errors
        assert len(error.issues) >= 2
        codes = [issue.code for issue in error.issues]
        # Should have at least duplicate and unknown binding errors
        assert "duplicate_binding" in codes or any(
            "redefines" in issue.message.lower() for issue in error.issues
        )

    def test_unknown_kwarg_issue_type_exists(self):
        """Test that unknown_kwarg issues can be created.

        Note: Most Python builtins are C extensions without inspectable signatures,
        so unknown kwargs are often only caught at runtime rather than validation.
        This test verifies the ValidationIssue type works correctly.
        """
        # Directly verify the unknown_kwarg issue type is supported
        issue = ValidationIssue(
            code="unknown_kwarg",
            message="Unknown kwargs for 'test.Func': invalid_arg. Valid parameters are: ['x', 'y']",
            binding="test_binding",
        )

        error = ValidationError([issue])

        assert len(error.issues) == 1
        assert error.issues[0].code == "unknown_kwarg"
        assert "invalid_arg" in str(error)
        assert "Unknown kwargs" in str(error)

    def test_dependency_cycle_detection(self):
        """Test that dependency cycles are detected and reported."""
        source = """
# Circular dependency
a = @b
b = @c
c = @a

__entrypoint__ = @a
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ValidationError) as exc_info:
            execute_pfig(ast)

        error = exc_info.value
        # Should detect the cycle
        assert any("cycle" in issue.message.lower() for issue in error.issues)

    def test_unknown_binding_suggests_similar(self):
        """Test that unknown bindings suggest similar names."""
        source = """
learning_rate = 0.001
batch_size = 32

# Typo: learningrate instead of learning_rate
result = {"lr": @learningrate, "bs": @batch_size}

__entrypoint__ = @result
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ValidationError) as exc_info:
            execute_pfig(ast)

        error = exc_info.value
        error_str = str(error)
        # Should suggest the correct binding
        assert "learningrate" in error_str
        assert "did you mean" in error_str.lower()
        assert "learning_rate" in error_str

    def test_unknown_binding_no_suggestion_when_not_similar(self):
        """Test that no suggestion is made when nothing is similar."""
        source = """
foo = 1
bar = 2

# Completely different name
result = @completely_different_name

__entrypoint__ = @result
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ValidationError) as exc_info:
            execute_pfig(ast)

        error = exc_info.value
        error_str = str(error)
        # Should NOT have a suggestion
        assert "completely_different_name" in error_str
        assert "did you mean" not in error_str.lower()

    def test_error_message_lists_all_issues(self):
        """Test that the error message includes all collected issues."""
        source = """
from collections import OrderedDict

x = 1
x = 2
y = 1
y = 2

result = OrderedDict(x=@x, y=@y)
__entrypoint__ = @result
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ValidationError) as exc_info:
            execute_pfig(ast)

        error_str = str(exc_info.value)
        # Count the number of issue lines (lines starting with "  - ")
        issue_lines = [line for line in error_str.split("\n") if line.startswith("  - ")]
        # Should have at least 2 issues listed
        assert len(issue_lines) >= 2


class TestValidationErrorInCLI:
    """Test ValidationError handling in CLI context."""

    def test_validation_error_import(self):
        """Test that ValidationError can be imported from pfig package."""
        from pfig import ValidationError, ValidationIssue

        # Should be able to create instances
        issue = ValidationIssue(code="test", message="Test message")
        error = ValidationError([issue])
        assert len(error.issues) == 1

    def test_validation_error_is_exception(self):
        """Test that ValidationError is a proper exception."""
        from pfig import ValidationError, ValidationIssue

        issues = [ValidationIssue(code="test", message="Test")]
        error = ValidationError(issues)

        # Should be raisable
        with pytest.raises(ValidationError):
            raise error

        # Should be catchable as Exception
        try:
            raise error
        except Exception as e:
            assert isinstance(e, ValidationError)


class TestValidationErrorSorting:
    """Test that validation errors are sorted by position."""

    def test_errors_sorted_by_line(self):
        """Errors should be sorted by line number."""
        from pfig import ValidationError, ValidationIssue
        from pfig.lexer import Position

        issues = [
            ValidationIssue(
                code="test",
                message="Error on line 30",
                position=Position("test.pfig", 30, 1),
            ),
            ValidationIssue(
                code="test",
                message="Error on line 10",
                position=Position("test.pfig", 10, 1),
            ),
            ValidationIssue(
                code="test",
                message="Error on line 20",
                position=Position("test.pfig", 20, 1),
            ),
        ]

        error = ValidationError(issues)

        # Should be sorted by line number
        assert error.issues[0].position.line == 10
        assert error.issues[1].position.line == 20
        assert error.issues[2].position.line == 30

    def test_errors_sorted_by_file_then_line(self):
        """Errors should be sorted by filename first, then line."""
        from pfig import ValidationError, ValidationIssue
        from pfig.lexer import Position

        issues = [
            ValidationIssue(
                code="test",
                message="b.pfig line 5",
                position=Position("b.pfig", 5, 1),
            ),
            ValidationIssue(
                code="test",
                message="a.pfig line 20",
                position=Position("a.pfig", 20, 1),
            ),
            ValidationIssue(
                code="test",
                message="a.pfig line 10",
                position=Position("a.pfig", 10, 1),
            ),
        ]

        error = ValidationError(issues)

        # Should be sorted by filename first, then line
        assert error.issues[0].position.filename == "a.pfig"
        assert error.issues[0].position.line == 10
        assert error.issues[1].position.filename == "a.pfig"
        assert error.issues[1].position.line == 20
        assert error.issues[2].position.filename == "b.pfig"
        assert error.issues[2].position.line == 5

    def test_errors_without_position_sorted_last(self):
        """Errors without position should appear at the end."""
        from pfig import ValidationError, ValidationIssue
        from pfig.lexer import Position

        issues = [
            ValidationIssue(code="test", message="No position"),
            ValidationIssue(
                code="test",
                message="Has position",
                position=Position("test.pfig", 10, 1),
            ),
        ]

        error = ValidationError(issues)

        # Error with position should come first
        assert error.issues[0].position is not None
        assert error.issues[1].position is None


class TestMissingKwargsValidation:
    """Test that missing kwargs are caught for providers used without overlays."""

    def test_check_missing_kwargs_usage_with_shared_ref(self):
        """Missing kwargs should be flagged when provider is used via @name."""
        from pfig.execution import (
            LiteralValue,
            ProviderRef,
            ProviderSpec,
            check_missing_kwargs_usage,
        )

        # Simulate compiled data: provider with missing kwargs used via shared ref
        compiled_data = {
            "providers": {
                "trainer": ProviderSpec(
                    callable_obj=dict,  # Doesn't matter for this test
                    args=[],
                    kwargs={"optimizer": LiteralValue("adam")},
                    missing_kwargs=["model"],  # Missing 'model'
                    name="trainer",
                ),
            },
            "values": {
                "result": ProviderRef("trainer"),  # Shared reference
            },
        }
        reachable = {"trainer", "result"}

        issues = check_missing_kwargs_usage(compiled_data, reachable)

        assert len(issues) == 1
        assert "missing" in issues[0].message.lower()
        assert "model" in issues[0].message

    def test_check_missing_kwargs_usage_with_fresh_call_ok(self):
        """Partial providers used via @name(...) with overlay should NOT error."""
        from pfig.execution import (
            FreshProviderCall,
            LiteralValue,
            ProviderSpec,
            check_missing_kwargs_usage,
        )

        # Provider missing 'dataset', but fresh call supplies it
        compiled_data = {
            "providers": {
                "loader": ProviderSpec(
                    callable_obj=dict,
                    args=[],
                    kwargs={"batch_size": LiteralValue(32)},
                    missing_kwargs=["dataset"],
                    name="loader",
                ),
            },
            "values": {
                "train_loader": FreshProviderCall(
                    "loader",
                    {"dataset": LiteralValue("train_data")},  # Supplies missing arg
                ),
            },
        }
        from pfig.execution import find_reachable_bindings

        reachable = find_reachable_bindings(compiled_data, "train_loader")
        assert reachable == {"train_loader"}

        issues = check_missing_kwargs_usage(compiled_data, reachable)

        # No issues - overlay supplies the missing kwarg
        assert len(issues) == 0

    def test_check_missing_kwargs_usage_with_fresh_call_still_missing(self):
        """Should error if overlay doesn't supply all missing kwargs."""
        from pfig.execution import (
            FreshProviderCall,
            LiteralValue,
            ProviderSpec,
            check_missing_kwargs_usage,
        )

        # Provider missing 'a' and 'b', overlay only supplies 'a'
        compiled_data = {
            "providers": {
                "thing": ProviderSpec(
                    callable_obj=dict,
                    args=[],
                    kwargs={"c": LiteralValue("c_value")},
                    missing_kwargs=["a", "b"],
                    name="thing",
                ),
            },
            "values": {
                "result": FreshProviderCall(
                    "thing",
                    {"a": LiteralValue("a_value")},  # Only supplies 'a', missing 'b'
                ),
            },
        }
        from pfig.execution import find_reachable_bindings

        reachable = find_reachable_bindings(compiled_data, "result")
        assert reachable == {"result"}

        issues = check_missing_kwargs_usage(compiled_data, reachable)

        assert len(issues) == 1
        assert "missing" in issues[0].message.lower()
        assert "['b']" in issues[0].message  # Only 'b' is still missing
        assert "['a']" not in issues[0].message  # 'a' was supplied

    def test_check_missing_kwargs_usage_with_method_ref(self):
        """Missing kwargs should error when provider is used via @name.method."""
        from pfig.execution import (
            LiteralValue,
            ProviderMethodRef,
            ProviderSpec,
            check_missing_kwargs_usage,
        )

        # Provider with missing kwargs used via method reference
        compiled_data = {
            "providers": {
                "trainer": ProviderSpec(
                    callable_obj=dict,
                    args=[],
                    kwargs={"optimizer": LiteralValue("adam")},
                    missing_kwargs=["model"],
                    name="trainer",
                ),
            },
            "values": {
                "entrypoint": ProviderMethodRef("trainer", "fit"),
            },
        }
        reachable = {"trainer", "entrypoint"}

        issues = check_missing_kwargs_usage(compiled_data, reachable)

        assert len(issues) == 1
        assert "missing" in issues[0].message.lower()
        assert issues[0].binding == "trainer"
        assert "model" in issues[0].message

    def test_check_missing_kwargs_not_reachable_no_error(self):
        """Providers not in reachable set should not be checked."""
        from pfig.execution import (
            LiteralValue,
            ProviderRef,
            ProviderSpec,
            check_missing_kwargs_usage,
        )

        # Provider with missing kwargs, but NOT reachable
        compiled_data = {
            "providers": {
                "unused_trainer": ProviderSpec(
                    callable_obj=dict,
                    args=[],
                    kwargs={},
                    missing_kwargs=["model", "optimizer"],
                    name="unused_trainer",
                ),
            },
            "values": {
                "result": LiteralValue(42),  # Doesn't reference the trainer
            },
        }
        reachable = {"result"}  # trainer is NOT reachable

        issues = check_missing_kwargs_usage(compiled_data, reachable)

        # No issues - unreachable provider is not checked
        assert len(issues) == 0
