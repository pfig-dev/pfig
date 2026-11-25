"""
Unit tests for the pfig parser.
"""

import sys

import pytest

from pfig.ast_nodes import *
from pfig.lexer import LexerError
from pfig.parser import ParseError, parse_pfig_source


class TestBasicParsing:
    """Test basic parsing functionality."""

    def test_empty_file(self):
        """Test parsing an empty file."""
        ast = parse_pfig_source("", filename="test.pfig")
        assert isinstance(ast, PFigFile)
        assert len(ast.statements) == 0

    def test_comments_only(self):
        """Test parsing a file with only comments."""
        source = """
# This is a comment
# Another comment
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        assert len(ast.statements) == 0

    def test_simple_binding(self):
        """Test parsing a simple binding."""
        source = "batch_size = 64"
        ast = parse_pfig_source(source, filename="test.pfig")

        assert len(ast.statements) == 1
        binding = ast.statements[0]

        assert isinstance(binding, Binding)
        assert binding.name == "batch_size"
        assert isinstance(binding.value, LiteralExpr)
        assert binding.value.value == 64


class TestImportStatements:
    """Test parsing of import statements."""

    def test_simple_import(self):
        """Test parsing 'import module'."""
        source = "import json"
        ast = parse_pfig_source(source, filename="test.pfig")

        assert len(ast.imports()) == 1
        import_stmt = ast.imports()[0]

        assert isinstance(import_stmt, ImportStmt)
        assert import_stmt.module == "json"
        assert import_stmt.alias is None

    def test_import_with_alias(self):
        """Test parsing 'import module as alias'."""
        source = "import collections.abc as abc"
        ast = parse_pfig_source(source, filename="test.pfig")

        import_stmt = ast.imports()[0]
        assert import_stmt.module == "collections.abc"
        assert import_stmt.alias == "abc"

    def test_from_import(self):
        """Test parsing 'from module import names'."""
        source = "from collections import defaultdict, OrderedDict"
        ast = parse_pfig_source(source, filename="test.pfig")

        from_import = ast.imports()[0]
        assert isinstance(from_import, FromImportStmt)
        assert from_import.module == "collections"
        assert len(from_import.names) == 2

        assert from_import.names[0].name == "defaultdict"
        assert from_import.names[0].alias is None

        assert from_import.names[1].name == "OrderedDict"
        assert from_import.names[1].alias is None

    def test_from_import_with_aliases(self):
        """Test parsing 'from module import name as alias'."""
        source = "from json import loads as parse, dumps"
        ast = parse_pfig_source(source, filename="test.pfig")

        from_import = ast.imports()[0]
        assert len(from_import.names) == 2

        assert from_import.names[0].name == "loads"
        assert from_import.names[0].alias == "parse"

        assert from_import.names[1].name == "dumps"
        assert from_import.names[1].alias is None


class TestIncludeStatements:
    """Test parsing of include statements."""

    def test_include_with_alias(self):
        """Test parsing 'include "file" as alias'."""
        source = 'include "./base.pfig" as base'
        ast = parse_pfig_source(source, filename="test.pfig")

        assert len(ast.includes()) == 1
        include_stmt = ast.includes()[0]

        assert isinstance(include_stmt, IncludeStmt)
        assert include_stmt.path == "./base.pfig"
        assert include_stmt.alias == "base"

    # FromInclude functionality removed - only explicit aliasing supported
    def test_explicit_include_only(self):
        """Test that hoisting syntax is no longer supported."""
        source = 'from "./utils.pfig" include make_optimizer'

        with pytest.raises(ParseError):
            # This should fail since we only support 'from module import' for Python imports
            parse_pfig_source(source, filename="test.pfig")


class TestExternalStatements:
    """Test parsing of external declarations."""

    def test_simple_external(self):
        """Test parsing 'external name'."""
        source = "external output_dir"
        ast = parse_pfig_source(source, filename="test.pfig")

        externals = ast.externals()
        assert len(externals) == 1

        external = externals[0]
        assert isinstance(external, ExternalStmt)
        assert isinstance(external.path, PathExpr)
        assert external.path.parts == ["output_dir"]

    def test_namespaced_external(self):
        """Test parsing external with namespace path."""
        source = "external config/batch_size"
        ast = parse_pfig_source(source, filename="test.pfig")

        external = ast.externals()[0]
        assert external.path.parts == ["config", "batch_size"]


class TestOverrideStatements:
    """Test parsing of override statements."""

    def test_simple_override(self):
        """Test parsing 'override name = value'."""
        source = "override batch_size = 128"
        ast = parse_pfig_source(source, filename="test.pfig")

        overrides = ast.overrides()
        assert len(overrides) == 1

        override = overrides[0]
        assert isinstance(override, OverrideStmt)
        assert override.path.parts == ["batch_size"]
        assert isinstance(override.value, LiteralExpr)
        assert override.value.value == 128

    def test_namespaced_override(self):
        """Test parsing override with namespace path."""
        source = "override config/learning_rate = 1e-4"
        ast = parse_pfig_source(source, filename="test.pfig")

        override = ast.overrides()[0]
        assert override.path.parts == ["config", "learning_rate"]
        assert isinstance(override.value, LiteralExpr)
        assert override.value.value == 1e-4


class TestExpressions:
    """Test parsing of various expression types."""

    def test_literals(self):
        """Test parsing literal values."""
        test_cases = [
            ("num_int = 42", 42),
            ("num_float = 3.14", 3.14),
            ('text = "hello"', "hello"),
            ("flag_true = True", True),
            ("flag_false = False", False),
            ("empty = None", None),
            ("large_int = 1_000_000", 1_000_000),
            ("large_float = 1_234.567_890", 1_234.567_890),
        ]

        for source, expected_value in test_cases:
            ast = parse_pfig_source(source, filename="test.pfig")
            binding = ast.bindings()[0]

            assert isinstance(binding.value, LiteralExpr)
            assert binding.value.value == expected_value

    def test_list_expressions(self):
        """Test parsing list literals."""
        source = "numbers = [1, 2, 3]"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, ListExpr)
        assert len(binding.value.elements) == 3

        # Check elements are literal integers
        for i, elem in enumerate(binding.value.elements):
            assert isinstance(elem, LiteralExpr)
            assert elem.value == i + 1

    def test_tuple_expressions(self):
        """Test parsing tuple literals."""
        source = "coords = (10, 20)"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, TupleExpr)
        assert len(binding.value.elements) == 2

    def test_dict_expressions(self):
        """Test parsing dictionary literals."""
        source = 'config = {"batch_size": 64, "lr": 0.001}'
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, DictExpr)
        assert len(binding.value.pairs) == 2

        # Check first key-value pair
        key1, value1 = binding.value.pairs[0]
        assert isinstance(key1, LiteralExpr)
        assert key1.value == "batch_size"
        assert isinstance(value1, LiteralExpr)
        assert value1.value == 64

    def test_provider_references(self):
        """Test parsing provider references (@name)."""
        source = "model_ref = @base_model"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, ProviderRefExpr)
        assert binding.value.path.parts == ["base_model"]

    def test_namespaced_provider_references(self):
        """Test parsing namespaced provider references (@alias/name)."""
        source = "config_val = @config/batch_size"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, ProviderRefExpr)
        assert binding.value.path.parts == ["config", "batch_size"]


class TestFunctionCalls:
    """Test parsing of function/constructor calls."""

    def test_simple_call(self):
        """Test parsing simple function call."""
        source = "result = json.loads(s=data)"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, CallExpr)

        # Check callable
        assert isinstance(binding.value.callable, DottedNameExpr)
        assert binding.value.callable.parts == ["json", "loads"]

        # Check kwargs
        assert len(binding.value.kwargs) == 1
        assert "s" in binding.value.kwargs

        s_arg = binding.value.kwargs["s"]
        assert isinstance(s_arg, DottedNameExpr)
        assert s_arg.parts == ["data"]

    def test_provider_call(self):
        """Test parsing provider call (@name(kwargs))."""
        source = "fresh_model = @base_model(hidden_dim=512)"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, CallExpr)

        # Callable should be a ProviderRefExpr
        assert isinstance(binding.value.callable, ProviderRefExpr)
        assert binding.value.callable.path.parts == ["base_model"]

        # Check kwargs
        assert "hidden_dim" in binding.value.kwargs
        hidden_dim_arg = binding.value.kwargs["hidden_dim"]
        assert isinstance(hidden_dim_arg, LiteralExpr)
        assert hidden_dim_arg.value == 512

    def test_builtin_calls(self):
        """Test parsing built-in function calls."""
        test_cases = [
            ("size = len(items)", "len"),
            ("minimum = min(a, b)", "min"),
            ("maximum = max(values)", "max"),
            ("numbers = range(10)", "range"),
        ]

        for source, expected_func in test_cases:
            ast = parse_pfig_source(source, filename="test.pfig")
            binding = ast.bindings()[0]

            assert isinstance(binding.value, BuiltinCallExpr)
            assert binding.value.function == expected_func


class TestOperatorExpressions:
    """Test parsing of arithmetic expressions."""

    def test_binary_operators(self):
        """Test parsing binary arithmetic operators."""
        source = "result = a + b * c"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, BinaryOpExpr)

        # Should parse as: a + (b * c) due to precedence
        assert binding.value.operator == "+"
        assert isinstance(binding.value.left, DottedNameExpr)
        assert binding.value.left.parts == ["a"]

        # Right side should be b * c
        right = binding.value.right
        assert isinstance(right, BinaryOpExpr)
        assert right.operator == "*"

    def test_operator_precedence(self):
        """Test that operator precedence is handled correctly."""
        source = "result = 2 + 3 * 4"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        # Should parse as 2 + (3 * 4), not (2 + 3) * 4
        assert isinstance(binding.value, BinaryOpExpr)
        assert binding.value.operator == "+"

        # Left should be 2
        assert isinstance(binding.value.left, LiteralExpr)
        assert binding.value.left.value == 2

        # Right should be (3 * 4)
        right = binding.value.right
        assert isinstance(right, BinaryOpExpr)
        assert right.operator == "*"

    def test_floor_divide_precedence(self):
        """Test that // has the same precedence as * and is left-associative."""
        source = "result = a // b * c"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        # Should parse as (a // b) * c
        assert isinstance(binding.value, BinaryOpExpr)
        assert binding.value.operator == "*"

        left = binding.value.left
        assert isinstance(left, BinaryOpExpr)
        assert left.operator == "//"
        assert isinstance(left.left, DottedNameExpr)
        assert left.left.parts == ["a"]
        assert isinstance(left.right, DottedNameExpr)
        assert left.right.parts == ["b"]

        assert isinstance(binding.value.right, DottedNameExpr)
        assert binding.value.right.parts == ["c"]

    def test_power_operator(self):
        """Test power operator (right associative)."""
        source = "result = 2 ** 3 ** 2"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        # Should parse as 2 ** (3 ** 2), not (2 ** 3) ** 2
        assert isinstance(binding.value, BinaryOpExpr)
        assert binding.value.operator == "**"

        # Left should be 2
        assert isinstance(binding.value.left, LiteralExpr)
        assert binding.value.left.value == 2

        # Right should be (3 ** 2)
        right = binding.value.right
        assert isinstance(right, BinaryOpExpr)
        assert right.operator == "**"

    def test_unary_minus(self):
        """Test unary minus operator."""
        source = "result = -42"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, UnaryOpExpr)
        assert binding.value.operator == "-"
        assert isinstance(binding.value.operand, LiteralExpr)
        assert binding.value.operand.value == 42


class TestComplexExpressions:
    """Test parsing of complex nested expressions."""

    def test_nested_containers(self):
        """Test parsing nested lists and dictionaries."""
        source = 'data = [{"name": "item1", "values": [1, 2]}, {"name": "item2"}]'
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, ListExpr)
        assert len(binding.value.elements) == 2

        # First element should be a dict
        first_elem = binding.value.elements[0]
        assert isinstance(first_elem, DictExpr)
        assert len(first_elem.pairs) == 2

    def test_mixed_expressions(self):
        """Test parsing expressions with mixed operators and calls."""
        source = "result = len(items) + max(values) * 2"
        ast = parse_pfig_source(source, filename="test.pfig")

        binding = ast.bindings()[0]
        assert isinstance(binding.value, BinaryOpExpr)
        assert binding.value.operator == "+"

        # Left should be len(items)
        left = binding.value.left
        assert isinstance(left, BuiltinCallExpr)
        assert left.function == "len"

        # Right should be max(values) * 2
        right = binding.value.right
        assert isinstance(right, BinaryOpExpr)
        assert right.operator == "*"


class TestErrorHandling:
    """Test parser error conditions."""

    # --- Bug #1: unbounded recursion → RecursionError DoS ---

    def assert_parses_or_raises_safe_error(self, src):
        try:
            parse_pfig_source(src)
        except RecursionError as exc:
            pytest.fail(f"RecursionError leaked from parser: {exc}")
        except (ParseError, LexerError):
            pass

    @pytest.mark.parametrize("depth", [300, 600])
    def test_deep_list_nesting_does_not_leak_recursion_error(self, depth):
        """Deeply nested lists must not leak RecursionError out of the parser."""
        src = "x = " + "[" * depth + "]" * depth
        self.assert_parses_or_raises_safe_error(src)

    @pytest.mark.parametrize("depth", [200, 400])
    def test_deep_dict_nesting_does_not_leak_recursion_error(self, depth):
        """Deeply nested dicts must not leak RecursionError out of the parser."""
        src = "x = " + '{"k": ' * depth + "1" + "}" * depth
        self.assert_parses_or_raises_safe_error(src)

    @pytest.mark.parametrize("depth", [300, 600])
    def test_deep_paren_nesting_does_not_leak_recursion_error(self, depth):
        """Deeply nested parentheses must not leak RecursionError out of the parser."""
        src = "x = " + "(" * depth + "1" + ")" * depth
        self.assert_parses_or_raises_safe_error(src)

    def test_extreme_nesting_converts_recursion_error_to_parse_error(self):
        """Pathological nesting should fail as ParseError, not leak RecursionError."""
        depth = sys.getrecursionlimit() * 2
        src = "x = " + "[" * depth + "]" * depth
        with pytest.raises(ParseError, match="Maximum nesting depth exceeded"):
            parse_pfig_source(src)

    def test_moderate_nesting_still_parses(self):
        """Reasonable nesting (10 levels) must parse without errors."""
        ast = parse_pfig_source("x = " + "[" * 10 + "]" * 10)
        assert ast is not None

    # --- Bug #2: malformed underscore placement in numeric literals → ValueError ---

    @pytest.mark.parametrize("literal", ["1_", "1__2", "1.2_", "1e_5"])
    def test_malformed_underscore_in_number_raises_parse_error(self, literal):
        """Invalid underscore placement must raise LexerError or ParseError, not ValueError."""
        src = f"x = {literal}"
        with pytest.raises((ParseError, LexerError, ValueError)) as exc_info:
            parse_pfig_source(src)
        assert not isinstance(exc_info.value, ValueError), (
            f"ValueError leaked for numeric literal {literal!r} — "
            "wrap int()/float() conversion in parse_primary with a ParseError"
        )

    def test_syntax_error(self):
        """Test handling of syntax errors."""
        source = "invalid syntax here ="

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        assert "test.pfig" in str(exc_info.value)
        # Error message should mention the issue (either "Unexpected token" or "Expected")
        error_msg = str(exc_info.value)
        assert "Unexpected token" in error_msg or "Expected" in error_msg

    def test_missing_equals(self):
        """Test error when assignment is missing equals sign."""
        source = "name value"

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        assert "Expected EQUALS" in str(exc_info.value)

    def test_missing_closing_bracket(self):
        """Test error when containers are not closed."""
        source = "items = [1, 2, 3"

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        assert "Expected RBRACKET" in str(exc_info.value)

    def test_invalid_provider_syntax(self):
        """Test error when @ is not followed by identifier."""
        source = "bad = @123"

        with pytest.raises(ParseError) as exc_info:
            parse_pfig_source(source, filename="test.pfig")

        # Should expect IDENT after @
        assert "Expected IDENT" in str(exc_info.value)


class TestCompleteFiles:
    """Test parsing of complete pfig files."""

    def test_mixed_statements(self):
        """Test parsing a file with mixed statement types."""
        source = """
# Complete pfig file
import json
from collections import defaultdict

include "./base.pfig" as base
include "./utils.pfig" as utils

external output_dir
external num_gpus

override base/batch_size = 128

# Bindings
model_config = {"layers": 6, "hidden": 512}
counter = defaultdict(default_factory=int)
data = json.loads(s='{"test": true}')
"""

        ast = parse_pfig_source(source, filename="complete.pfig")

        # Check statement counts
        assert len(ast.imports()) == 2
        assert len(ast.includes()) == 2
        assert len(ast.externals()) == 2
        assert len(ast.overrides()) == 1
        assert len(ast.bindings()) == 3

        # Verify we can access different statement types
        imports = ast.imports()
        assert any(stmt.module == "json" for stmt in imports)
        assert any(stmt.module == "collections" for stmt in imports)

        externals = ast.externals()
        external_names = ["/".join(ext.path.parts) for ext in externals]
        assert "output_dir" in external_names
        assert "num_gpus" in external_names

        bindings = ast.bindings()
        binding_names = [b.name for b in bindings]
        assert "model_config" in binding_names
        assert "counter" in binding_names
        assert "data" in binding_names


class TestParserErrorHandling:
    """Test parser error conditions and constraints."""

    def test_slash_rejected_in_binding_names(self):
        """Test that binding names cannot contain slashes."""
        invalid_configs = [
            "my/config = 42",
            "data/loader = DataLoader()",
            "model/params = {}",
            "a/b/c = 'nested'",
        ]

        for config in invalid_configs:
            with pytest.raises(ParseError, match="Expected EQUALS, got DIVIDE"):
                parse_pfig_source(config)

    def test_slash_allowed_in_overrides(self):
        """Test that slashes work correctly in override statements."""
        config = """
include "./base.pfig" as base
override base/learning_rate = 0.01
override base/nested/value = 42
"""
        # Should parse without error
        ast = parse_pfig_source(config)
        overrides = [stmt for stmt in ast.statements if isinstance(stmt, OverrideStmt)]
        assert len(overrides) == 2
        assert overrides[0].path.parts == ["base", "learning_rate"]
        assert overrides[1].path.parts == ["base", "nested", "value"]

    def test_slash_allowed_in_provider_references(self):
        """Test that slashes work correctly in @ provider references."""
        config = """
result = @base/value
nested = @config/deep/nested/param
"""
        # Should parse without error
        ast = parse_pfig_source(config)
        bindings = [stmt for stmt in ast.statements if isinstance(stmt, Binding)]
        assert len(bindings) == 2

        # Check that the provider references have the right path structure
        result_binding = bindings[0]
        assert isinstance(result_binding.value, ProviderRefExpr)
        assert result_binding.value.path.parts == ["base", "value"]

        nested_binding = bindings[1]
        assert isinstance(nested_binding.value, ProviderRefExpr)
        assert nested_binding.value.path.parts == ["config", "deep", "nested", "param"]

    def test_mixed_valid_invalid_slash_usage(self):
        """Test mixing valid and invalid slash usage."""
        # This should fail on the invalid binding name
        invalid_config = """
valid_binding = 42
override base/value = 100
invalid/binding = "fail"
result = @base/value
"""
        with pytest.raises(ParseError, match="Expected EQUALS, got DIVIDE"):
            parse_pfig_source(invalid_config)
