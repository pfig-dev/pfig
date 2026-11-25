"""Tests for the override system."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pfig import (
    ExecutionError,
    ValidationError,
    execute_pfig,
    get_materialized_bindings,
    instantiate,
    parse_pfig_source,
)
from pfig.execution import execute_pfig_with_metadata, validate_pfig


class TestOverrideSystem:
    """Test the override system functionality."""

    def test_basic_value_override(self):
        """Test basic override of simple values."""
        config = """
learning_rate = 0.001
batch_size = 64

override learning_rate = 0.01
override batch_size = 128
"""

        learning_rate = instantiate("learning_rate", source=config)
        batch_size = instantiate("batch_size", source=config)

        assert learning_rate == 0.01
        assert batch_size == 128

    def test_override_with_include(self):
        """Test overriding values from included files."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
learning_rate = 0.001
batch_size = 64
epochs = 5
"""
            )

            # Create main config with overrides
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

override base/learning_rate = 0.01
override base/batch_size = 128

config = {
    "lr": @base/learning_rate,
    "batch": @base/batch_size,
    "epochs": @base/epochs
}
"""
            )

            result = instantiate("config", path=main_config)

            assert result["lr"] == 0.01  # overridden
            assert result["batch"] == 128  # overridden
            assert result["epochs"] == 5  # not overridden

    def test_override_provider(self):
        """Test overriding providers."""
        config = """
model_config = dict(
    type="MLP",
    hidden_size=256
)

override model_config = dict(
    type="CNN",
    hidden_size=512,
    num_layers=3
)

result = @model_config
"""

        result = instantiate("result", source=config)

        assert result["type"] == "CNN"
        assert result["hidden_size"] == 512
        assert result["num_layers"] == 3

    def test_override_provider_with_value(self):
        """Test overriding a provider with a simple value."""
        config = """
model_config = dict(type="MLP")

override model_config = "simple_string"

result = @model_config
"""

        result = instantiate("result", source=config)
        assert result == "simple_string"

    def test_override_value_with_provider(self):
        """Test overriding a simple value with a provider."""
        config = """
model_type = "MLP"

override model_type = dict(
    type="CNN",
    layers=3
)

result = @model_type
"""

        result = instantiate("result", source=config)
        assert result["type"] == "CNN"
        assert result["layers"] == 3

    def test_override_nonexistent_binding(self):
        """Test that overriding a binding that doesn't exist throws an error."""
        config = """
existing_value = 42

override new_value = 100

result = @new_value
"""

        with pytest.raises(ValidationError, match="Override target 'new_value' not found"):
            instantiate("result", source=config)

    def test_override_with_expressions(self):
        """Test overriding with complex expressions."""
        config = """
base_value = 10
multiplier = 2

override base_value = @multiplier * 5

result = @base_value
"""

        result = instantiate("result", source=config)
        assert result == 10  # 2 * 5

    def test_override_namespaced_with_include(self):
        """Test overriding deeply namespaced values."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create nested base config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
hyperparams = dict(
    lr=0.001,
    batch_size=32
)
"""
            )

            # Create main config
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

# Can't override nested dict fields directly, but can override whole object
override base/hyperparams = dict(
    lr=0.01,
    batch_size=128,
    momentum=0.9
)

result = @base/hyperparams
"""
            )

            result = instantiate("result", path=main_config)

            assert result["lr"] == 0.01
            assert result["batch_size"] == 128
            assert result["momentum"] == 0.9

    def test_multiple_overrides_same_binding(self):
        """Conflicting overrides in one file are ambiguous."""
        config = """
value = 1

override value = 2
override value = 3
override value = 4

result = @value
"""

        with pytest.raises(ValidationError, match="Conflicting overrides"):
            instantiate("result", source=config)

    def test_override_with_references(self):
        """Test overrides that reference other bindings."""
        config = """
base_lr = 0.001
factor = 10

# Override with a different name to avoid self-reference
override base_lr = @factor * 0.001

result = @base_lr
"""

        result = instantiate("result", source=config)
        assert result == 0.01  # 10 * 0.001

    def test_override_order_independence(self):
        """Test that overrides work with forward references."""
        config = """
learning_rate = 0.001
base_rate = 0.005

override learning_rate = @base_rate * 2

result = @learning_rate
"""

        result = instantiate("result", source=config)
        assert result == 0.01  # 0.005 * 2

    def test_override_with_cli_commands(self):
        """Test that overrides work with CLI commands."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            config = tmp_path / "test.pfig"
            config.write_text(
                """
value = 42

override value = 100

# Simple callable entrypoint for testing
print_val = print

__entrypoint__ = @print_val
"""
            )

            # Test validation
            ast = parse_pfig_source(config.read_text(), filename=str(config))
            metadata = validate_pfig(ast, filename=str(config))
            # Should not raise an error (metadata returned successfully)

            # Test get_materialized_bindings
            bindings = get_materialized_bindings(ast, entrypoint="value", filename=str(config))
            assert bindings["value"] == 100

    def test_override_error_cases(self):
        """Test error handling in override system."""
        # This test mainly ensures overrides don't break error handling
        # Most override "errors" are actually valid (e.g., creating new bindings)
        config = """
value = 1

override value = @nonexistent_ref

result = @value
"""

        with pytest.raises(ValidationError, match="Unknown binding"):
            instantiate("result", source=config)

    def test_override_with_arithmetic(self):
        """Test overrides with arithmetic expressions."""
        config = """
base = 10
multiplier = 3
extra = 2

# Avoid self-reference by using different variables
override base = @multiplier * @extra + 10

result = @base
"""

        result = instantiate("result", source=config)
        assert result == 16  # 3 * 2 + 10

    def test_override_self_reference_error(self):
        """Test that self-referencing overrides cause recursion errors."""
        config = """
value = 10

# This creates infinite recursion
override value = @value * 2

result = @value
"""

        with pytest.raises(ValidationError, match="Dependency cycle"):
            instantiate("result", source=config)

    def test_complex_override_scenario(self):
        """Test a complex real-world override scenario."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Base ML config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
# Model hyperparameters
learning_rate = 0.001
batch_size = 64
epochs = 10

# Model config
model_config = dict(
    type="MLP",
    hidden_sizes=[256, 128],
    dropout=0.1
)

# Training config
training_config = dict(
    lr=@learning_rate,
    batch_size=@batch_size,
    epochs=@epochs,
    model=@model_config
)
"""
            )

            # Experiment config with overrides
            experiment_config = tmp_path / "experiment.pfig"
            experiment_config.write_text(
                """
include "./base.pfig" as base

# Override hyperparameters for experiment
override base/learning_rate = 0.01
override base/batch_size = 128
override base/epochs = 5

# Override model architecture
override base/model_config = dict(
    type="CNN",
    hidden_sizes=[512, 256, 128],
    dropout=0.2,
    kernel_size=3
)

# Use the base training config (which will use overridden values)
final_config = @base/training_config
"""
            )

            result = instantiate("final_config", path=experiment_config)

            # Check overridden values propagated correctly
            assert result["lr"] == 0.01
            assert result["batch_size"] == 128
            assert result["epochs"] == 5
            assert result["model"]["type"] == "CNN"
            assert result["model"]["hidden_sizes"] == [512, 256, 128]
            assert result["model"]["dropout"] == 0.2
            assert result["model"]["kernel_size"] == 3

    def test_override_nonexistent_binding_behavior(self):
        """Test that overriding a binding that doesn't exist throws an error."""
        config = """
existing_value = 42

# This should throw an error - cannot override non-existent binding
override nonexistent_value = 100

result = @nonexistent_value
"""

        # Should raise an error
        with pytest.raises(ValidationError, match="Override target 'nonexistent_value' not found"):
            instantiate("result", source=config)

        # Original existing binding should still be accessible separately
        valid_config = """
existing_value = 42
result = @existing_value
"""
        existing = instantiate("result", source=valid_config)
        assert existing == 42

    def test_override_vs_shadowing_behavior(self):
        """Test the difference between override and shadowing without override."""

        # Test shadowing without override (should throw error)
        shadow_config = """
value = 42
value = 100  # Shadows previous binding - should error

result = @value
"""

        with pytest.raises(ValidationError, match="redefines existing binding"):
            instantiate("result", source=shadow_config)

        # Test with explicit override (should work)
        override_config = """
value = 42

override value = 100  # Explicit override

result = @value
"""

        result = instantiate("result", source=override_config)
        assert result == 100  # Override works

    def test_duplicate_bindings_without_override(self):
        """Test that duplicate bindings without override throw an error."""
        config = """
learning_rate = 0.001
batch_size = 64
learning_rate = 0.01  # Duplicate without override - should error

result = {
    "lr": @learning_rate,
    "batch": @batch_size
}
"""

        with pytest.raises(ValidationError, match="redefines existing binding"):
            instantiate("result", source=config)

    def test_override_different_types(self):
        """Test overriding bindings with different types."""
        config = """
# Start with a simple value
config_value = 42

# Override with a provider
override config_value = dict(type="new", value=100)

# Override with a different value type
override config_value = "string_value"

result = @config_value
"""

        with pytest.raises(ValidationError, match="Conflicting overrides"):
            instantiate("result", source=config)

    def test_override_with_complex_paths(self):
        """Test overriding with complex namespace paths."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create nested include structure
            deep_config = tmp_path / "deep.pfig"
            deep_config.write_text(
                """
deep_value = "original"
"""
            )

            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
include "./deep.pfig" as deep
base_value = "base"
"""
            )

            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

# Override deeply nested value
override base/deep/deep_value = "overridden"

result = @base/deep/deep_value
"""
            )

            result = instantiate("result", path=main_config)
            assert result == "overridden"

    def test_override_error_cases_comprehensive(self):
        """Test various error cases with overrides."""

        # Test override with invalid reference
        config_with_bad_ref = """
value = 10

override value = @invalid_reference

result = @value
"""

        with pytest.raises(ValidationError, match="Unknown binding"):
            instantiate("result", source=config_with_bad_ref)

        # Test override creating circular dependency
        circular_config = """
a = 1
b = 2

override a = @b
override b = @a

result = @a
"""

        with pytest.raises(ValidationError):
            instantiate("result", source=circular_config)

    def test_override_execution_order(self):
        """Test that overrides are processed after includes and bindings."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
value = "from_base"
dependent = "depends_on_" + @value
"""
            )

            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

# This override should affect the dependent value
override base/value = "overridden"

result = @base/dependent
"""
            )

            result = instantiate("result", path=main_config)
            # The dependent should use the overridden value
            assert result == "depends_on_overridden"

    def test_shadowing_error_behavior(self):
        """Test that shadowing without override throws an error."""
        config = """
original_value = "first"
original_value = "second"  # Should throw error - shadowing not allowed

result = @original_value
"""

        # Should raise a ValidationError (shadowing is now a validation issue)
        with pytest.raises(ValidationError, match="redefines existing binding. Use 'override"):
            instantiate("result", source=config)

    def test_no_error_with_explicit_override(self):
        """Test that explicit override works correctly without errors."""
        config = """
original_value = "first"

override original_value = "second"  # Should work fine

result = @original_value
"""

        result = instantiate("result", source=config)
        assert result == "second"
