#!/usr/bin/env python3
"""
Unit test for namespaced provider method references.

Tests both working cases and the current limitation with @namespace/provider.method syntax.
"""

import pytest

from pfig import ParseError, parse_pfig_source
from pfig.ast_nodes import ProviderMethodRefExpr, ProviderRefExpr


class TestNamespacedProviderMethodRefs:
    """Test provider method reference parsing in different contexts."""

    def test_simple_provider_method_works(self):
        """Test that simple @provider.method works (Path 1)."""
        config = """
model = dict(x=1)
result = @model.get
"""
        ast = parse_pfig_source(config)
        result_binding = ast.bindings()[1]

        # Should create ProviderMethodRefExpr
        assert isinstance(result_binding.value, ProviderMethodRefExpr)
        assert result_binding.value.path.parts == ["model"]
        assert result_binding.value.attrs == ["get"]
        assert result_binding.value.method == "get"

    def test_namespaced_provider_ref_works(self):
        """Test that @namespace/provider works (Path 2 without method)."""
        config = """
include "./examples/mnist_mlp/consts.pfig" as consts
result = @consts/device
"""
        ast = parse_pfig_source(config)
        result_binding = [b for b in ast.bindings() if b.name == "result"][0]

        # Should create ProviderRefExpr with namespaced path
        assert isinstance(result_binding.value, ProviderRefExpr)
        assert result_binding.value.path.parts == ["consts", "device"]

    def test_namespaced_provider_method_works_now(self):
        """Test that @namespace/provider.method now works after eliminating DOTTED_NAME tokens."""
        config = """
include "./examples/mnist_mlp/consts.pfig" as consts
result = @consts/device.upper
"""

        # This should now work!
        ast = parse_pfig_source(config)
        result_binding = [b for b in ast.bindings() if b.name == "result"][0]

        # Should create ProviderMethodRefExpr with namespaced path
        assert isinstance(result_binding.value, ProviderMethodRefExpr)
        assert result_binding.value.path.parts == ["consts", "device"]
        assert result_binding.value.attrs == ["upper"]
        assert result_binding.value.method == "upper"

    def test_lexer_tokenization_behavior(self):
        """Document the new lexer behavior after eliminating DOTTED_NAME tokens."""
        from pfig.lexer import Lexer, TokenType

        # Simple case - now uses individual tokens
        lexer1 = Lexer("@model.get")
        tokens1 = lexer1.tokenize()
        token_types1 = [
            (t.type, t.value) for t in tokens1 if t.type not in [TokenType.NEWLINE, TokenType.EOF]
        ]

        expected1 = [
            (TokenType.AT, "@"),
            (TokenType.IDENT, "model"),
            (TokenType.DOT, "."),
            (TokenType.IDENT, "get"),
        ]
        assert token_types1 == expected1

        # Namespaced case - now works consistently
        lexer2 = Lexer("@consts/device.upper")
        tokens2 = lexer2.tokenize()
        token_types2 = [
            (t.type, t.value) for t in tokens2 if t.type not in [TokenType.NEWLINE, TokenType.EOF]
        ]

        # All dots are now individual DOT tokens - much cleaner!
        expected2 = [
            (TokenType.AT, "@"),
            (TokenType.IDENT, "consts"),
            (TokenType.DIVIDE, "/"),
            (TokenType.IDENT, "device"),
            (TokenType.DOT, "."),
            (TokenType.IDENT, "upper"),
        ]
        assert token_types2 == expected2

    def test_deep_namespaced_paths_work(self):
        """Test that deep namespace paths work without methods."""
        config = """
include "./examples/mnist_mlp/consts.pfig" as consts
include "./examples/simple/consts.pfig" as simple
result = @consts/input_size
"""
        ast = parse_pfig_source(config)
        result_binding = [b for b in ast.bindings() if b.name == "result"][0]

        # Should work fine for namespace paths without methods
        assert isinstance(result_binding.value, ProviderRefExpr)
        assert result_binding.value.path.parts == ["consts", "input_size"]


class TestImplementedSolution:
    """Test the implemented solution: eliminating DOTTED_NAME tokens entirely."""

    def test_solution_eliminates_dotted_name_tokens(self):
        """
        Implemented solution: Remove DOTTED_NAME tokens entirely.

        All dots are now individual DOT tokens, making parsing consistent:
        - @model.get → AT IDENT:"model" DOT:"." IDENT:"get"
        - @consts/device.upper → AT IDENT:"consts" DIVIDE:"/" IDENT:"device" DOT:"." IDENT:"upper"
        """
        import pfig
        from pfig.lexer import Lexer, TokenType

        # Test that both cases parse successfully
        simple_config = "result = @model.get"
        namespaced_config = """
include "./examples/mnist_mlp/consts.pfig" as consts
result = @consts/device.upper
"""

        # Both should parse without errors
        ast1 = pfig.parse_pfig_source(simple_config)
        ast2 = pfig.parse_pfig_source(namespaced_config)

        # Both should create ProviderMethodRefExpr
        result1 = ast1.bindings()[0]
        result2 = [b for b in ast2.bindings() if b.name == "result"][0]

        assert isinstance(result1.value, ProviderMethodRefExpr)
        assert isinstance(result2.value, ProviderMethodRefExpr)

        # Check the paths and methods
        assert result1.value.path.parts == ["model"]
        assert result1.value.attrs == ["get"]

        assert result2.value.path.parts == ["consts", "device"]
        assert result2.value.attrs == ["upper"]


def test_current_working_examples():
    """Test real examples from the codebase that work correctly."""

    # From mnist example
    config = """
from train import Trainer
trainer = Trainer(model=None)
__entrypoint__ = @trainer.fit
"""
    ast = parse_pfig_source(config)
    entrypoint_binding = [b for b in ast.bindings() if b.name == "__entrypoint__"][0]

    assert isinstance(entrypoint_binding.value, ProviderMethodRefExpr)
    assert entrypoint_binding.value.path.parts == ["trainer"]
    assert entrypoint_binding.value.method == "fit"


if __name__ == "__main__":
    # Run the tests to demonstrate current behavior
    test = TestNamespacedProviderMethodRefs()

    print("✅ Testing simple @provider.method...")
    test.test_simple_provider_method_works()
    print("   PASS: Simple provider method references work")

    print("\n✅ Testing @namespace/provider...")
    test.test_namespaced_provider_ref_works()
    print("   PASS: Namespaced provider references work")

    print("\n✅ Testing @namespace/provider.method...")
    try:
        test.test_namespaced_provider_method_works_now()
        print("   PASS: Now works correctly!")
    except Exception as e:
        print(f"   UNEXPECTED: {e}")

    print("\n📋 Testing lexer tokenization...")
    test.test_lexer_tokenization_behavior()
    print("   PASS: Lexer behavior documented")

    print("\n✅ Testing deep namespace paths...")
    test.test_deep_namespaced_paths_work()
    print("   PASS: Deep namespaced paths work")

    print("\n🎯 Summary:")
    print("  • Simple method refs (@provider.method) ✅ work")
    print("  • Namespaced refs (@ns/provider) ✅ work")
    print("  • Namespaced method refs (@ns/provider.method) ✅ now work!")
    print("  • Solution: Eliminated DOTTED_NAME tokens entirely, unified parser paths")
