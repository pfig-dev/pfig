"""
Tests for builtin name collision handling and namespace separation.

This module tests that the @ syntax properly disambiguates between:
- Provider references (@name) - should ONLY look in provider namespace
- Bare references (name) - should look in imports, builtins, then binding references
"""

import pytest

from pfig import ExecutionError, ValidationError, instantiate


class TestBuiltinNamespaceCollisions:
    """Test that builtin names can be used as providers without collision."""

    def test_builtin_names_as_providers(self):
        """Providers can use builtin names without collision issues."""
        # These builtin names should work fine as provider names
        builtin_names = ["eval", "str", "int", "list", "dict", "type", "bool"]

        for name in builtin_names:
            config = f"{name} = 42"

            # Should be able to instantiate the provider
            result = instantiate(name, source=config)
            assert result == 42, f"Provider @{name} should resolve to 42, got {result}"

    def test_provider_reference_ignores_builtins(self):
        """@name syntax should NEVER resolve to builtins, only provider namespace."""
        config = """
        eval = "my_provider_value"
        result = @eval
        """

        result = instantiate("result", source=config)

        # Should get the provider value, NOT the builtin eval function
        assert result == "my_provider_value"
        assert not callable(result), "Should not get builtin eval() function"

    def test_original_bug_case(self):
        """Test the specific case that originally failed."""
        config = """
        import json
        eval = json.loads(s='{"test": 42}')
        """

        result = instantiate("eval", source=config)

        # Before fix: this would return <built-in function eval>
        # After fix: this correctly returns the parsed JSON
        expected = {"test": 42}
        assert result == expected, f"Expected {expected}, got {result}"
        assert isinstance(result, dict), "Should be dict, not builtin function"

    def test_dependency_tracking_includes_builtin_names(self):
        """Dependency tracking should include providers even if they have builtin names."""
        config = """
        eval = "base_value"
        dependent = @eval
        """

        # This should work without dependency issues
        result = instantiate("dependent", source=config)
        assert result == "base_value"

    def test_multiple_builtin_providers_in_chain(self):
        """Multiple providers with builtin names should work in dependency chains."""
        config = """
        str = 42
        int = @str
        list = @int
        result = @list
        """

        result = instantiate("result", source=config)
        assert result == 42


class TestNamespaceContextSeparation:
    """Test that different contexts properly separate namespaces."""

    def test_bare_name_vs_provider_reference(self):
        """Bare names and @names should resolve in different namespaces."""
        # Note: This is conceptual - current pfig syntax doesn't allow
        # bare builtin references in expressions, but the separation
        # principle is important for the architecture

        config = """
        eval = "provider_value"
        """

        # @eval should resolve to provider
        result = instantiate("eval", source=config)
        assert result == "provider_value"

    def test_error_message_clarity(self):
        """Error messages should be clear about provider namespace."""
        config = """
        # No provider named 'nonexistent'
        result = @nonexistent
        """

        with pytest.raises(ValidationError) as exc_info:
            instantiate("result", source=config)

        # Error should mention the unknown binding
        error_msg = str(exc_info.value)
        assert "nonexistent" in error_msg or "Unknown binding" in error_msg


if __name__ == "__main__":
    pytest.main([__file__])
