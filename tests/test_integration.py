"""
Integration tests for pfig - testing complete end-to-end workflows.
"""

import os
import tempfile
from pathlib import Path

import pytest

from pfig.execution import (
    ExecutionError,
    ProfileSpec,
    execute_pfig,
    get_materialized_bindings,
    validate_pfig,
)
from pfig.parser import parse_config_file, parse_pfig_source


class TestBasicIntegration:
    """Test basic end-to-end pfig functionality."""

    def test_simple_config_execution(self):
        """Test parsing and executing a simple configuration."""
        config = """
# Simple pfig configuration
import json
from collections import defaultdict

# Basic values
batch_size = 64
learning_rate = 0.001
model_name = "transformer"

# Provider definitions
config_dict_provider = json.loads(s='{"hidden_dim": 512, "num_layers": 6}')
counter_provider = defaultdict(default_factory=int)

# Provider references
config_dict = @config_dict_provider
counter = @counter_provider

# Container values
layer_sizes = [784, 512, 256, 10]
hyperparams = {"lr": 0.001, "batch_size": 64}
"""

        # Parse and test each binding individually
        ast = parse_pfig_source(config, filename="simple.pfig")

        # Test literal values
        result = get_materialized_bindings(ast, entrypoint="batch_size")
        assert result["batch_size"] == 64

        result = get_materialized_bindings(ast, entrypoint="learning_rate")
        assert result["learning_rate"] == 0.001

        result = get_materialized_bindings(ast, entrypoint="model_name")
        assert result["model_name"] == "transformer"

        # Test function calls and containers
        result = get_materialized_bindings(ast, entrypoint="config_dict")
        assert result["config_dict"] == {"hidden_dim": 512, "num_layers": 6}

        result = get_materialized_bindings(ast, entrypoint="layer_sizes")
        assert result["layer_sizes"] == [784, 512, 256, 10]

        result = get_materialized_bindings(ast, entrypoint="hyperparams")
        assert result["hyperparams"] == {"lr": 0.001, "batch_size": 64}

        # Test types
        from collections import defaultdict

        result = get_materialized_bindings(ast, entrypoint="counter")
        assert isinstance(result["counter"], defaultdict)

    def test_mathematical_expressions(self):
        """Test parsing and execution of mathematical expressions."""
        config = """
# Mathematical computations
base = 10
multiplier = 2.5
power = 3

# Arithmetic operations
doubled = base * multiplier
cubed = base ** power
combined = doubled + cubed - 5

# Using built-ins
numbers = [1, 5, 3, 8, 2]
count = len(numbers)
minimum = min(numbers)
maximum = max(numbers)
"""

        ast = parse_pfig_source(config, filename="math.pfig")

        # Note: Currently our execution engine doesn't evaluate arithmetic expressions
        # This test documents the current limitation and will need updating when
        # expression evaluation is implemented

        # Test basic literal values
        result = get_materialized_bindings(ast, entrypoint="base")
        assert result["base"] == 10

        result = get_materialized_bindings(ast, entrypoint="multiplier")
        assert result["multiplier"] == 2.5

        result = get_materialized_bindings(ast, entrypoint="power")
        assert result["power"] == 3

        result = get_materialized_bindings(ast, entrypoint="numbers")
        assert result["numbers"] == [1, 5, 3, 8, 2]

        # These expressions will fail until we implement expression evaluation
        # TODO: Implement proper arithmetic evaluation


class TestFileStructure:
    """Test pfig file structure and organization."""

    def test_statement_types_recognition(self):
        """Test that different statement types are correctly parsed."""
        config = """
# All types of pfig statements
import json
from collections import defaultdict

include "./base.pfig" as base
include "./utils.pfig" as utils

external output_dir
external num_gpus

override base/batch_size = 128
override base/learning_rate = 1e-4

# Regular bindings
model_config = {"layers": 6}
data_loader = json.loads
"""

        ast = parse_pfig_source(config, filename="complete.pfig")

        # Verify statement type counts
        assert len(ast.imports()) == 2
        assert len(ast.includes()) == 2
        assert len(ast.externals()) == 2
        assert len(ast.overrides()) == 2
        assert len(ast.bindings()) == 2

        # Verify import details
        imports = ast.imports()
        import_modules = [stmt.module for stmt in imports]
        assert "json" in import_modules
        assert "collections" in import_modules

        # Verify external details
        externals = ast.externals()
        external_paths = ["/".join(ext.path.parts) for ext in externals]
        assert "output_dir" in external_paths
        assert "num_gpus" in external_paths

        # Verify override details
        overrides = ast.overrides()
        override_paths = ["/".join(over.path.parts) for over in overrides]
        assert "base/batch_size" in override_paths
        assert "base/learning_rate" in override_paths

    def test_comments_and_whitespace(self):
        """Test that comments and whitespace are handled correctly."""
        config = """
# This is a header comment
import json  # Import comment


# Section comment
batch_size = 64    # Value comment

# Multi-line comments:
# - First point
# - Second point
learning_rate = 0.001

        # Indented comment
model_name = "transformer"
"""

        ast = parse_pfig_source(config, filename="comments.pfig")

        # Comments should be ignored, only actual code should be parsed
        result = get_materialized_bindings(ast, entrypoint="batch_size")
        assert result["batch_size"] == 64

        result = get_materialized_bindings(ast, entrypoint="learning_rate")
        assert result["learning_rate"] == 0.001

        result = get_materialized_bindings(ast, entrypoint="model_name")
        assert result["model_name"] == "transformer"


class TestComplexConfigurations:
    """Test more complex pfig configurations."""

    def test_nested_data_structures(self):
        """Test complex nested data structures."""
        config = """
import json

# Deeply nested configuration
model_config = {
    "architecture": {
        "type": "transformer",
        "layers": [
            {"type": "embedding", "dim": 512},
            {"type": "attention", "heads": 8},
            {"type": "feedforward", "dim": 2048}
        ],
        "output": {"dim": 10, "activation": "softmax"}
    },
    "training": {
        "optimizer": "adam",
        "lr": 0.001,
        "batch_size": 32
    }
}

# Provider definitions
config_json_provider = json.dumps(obj=model_config)
parsed_config_provider = json.loads(s=@config_json_provider)

# Provider references
config_json = @config_json_provider
parsed_config = @parsed_config_provider
"""

        ast = parse_pfig_source(config, filename="nested.pfig")

        # Use parsed_config as entrypoint since it depends on the whole chain
        result = get_materialized_bindings(ast, entrypoint="parsed_config")

        # Verify nested structure is preserved
        model_config = result["model_config"]
        assert model_config["architecture"]["type"] == "transformer"
        assert len(model_config["architecture"]["layers"]) == 3
        assert model_config["architecture"]["layers"][0]["type"] == "embedding"
        assert model_config["training"]["lr"] == 0.001

        # Verify serialization roundtrip
        import json

        original = result["model_config"]
        deserialized = result["parsed_config"]

        # Should be identical after roundtrip
        assert original == deserialized

        # Test intermediate step separately
        json_result = get_materialized_bindings(ast, entrypoint="config_json")
        serialized = json_result["config_json"]

        # Verify serialization worked
        assert isinstance(serialized, str)
        assert json.loads(serialized) == original

    def test_multiple_import_styles(self):
        """Test different import styles work together."""
        config = """
# Different import styles
import json
import collections as colls
from math import sqrt, pi
from os.path import join as path_join

# Provider definitions
data_provider = json.dumps(obj={"test": True})
counter_provider = colls.defaultdict(default_factory=int)

# Provider references
data = @data_provider
counter = @counter_provider
"""

        ast = parse_pfig_source(config, filename="imports.pfig")

        # Verify imports work by testing each binding individually
        result = get_materialized_bindings(ast, entrypoint="data")
        assert result["data"] == '{"test": true}'

        from collections import defaultdict

        result = get_materialized_bindings(ast, entrypoint="counter")
        assert isinstance(result["counter"], defaultdict)


class TestErrorScenarios:
    """Test error handling in integration scenarios."""

    def test_parse_error_reporting(self):
        """Test that parse errors include proper context."""
        config = """
import json

# This line has a syntax error
bad_binding =
learning_rate = 0.001
"""

        with pytest.raises(Exception) as exc_info:
            parse_pfig_source(config, filename="error.pfig")

        error_msg = str(exc_info.value)

        # Should include filename and line info
        assert "error.pfig" in error_msg
        assert any(char.isdigit() for char in error_msg)  # Should have line number

    def test_execution_error_reporting(self):
        """Test that execution errors include proper context."""
        config = """
import json

# This will fail - invalid JSON
bad_data = json.loads(s="invalid json syntax")
"""

        ast = parse_pfig_source(config, filename="exec_error.pfig")

        with pytest.raises(Exception) as exc_info:
            execute_pfig(ast)

        error_msg = str(exc_info.value)

        # Should indicate which provider failed
        # The exact error depends on implementation details
        assert len(error_msg) > 0  # At least some error message

    def test_missing_import_error(self):
        """Test error when using undefined imports."""
        config = """
# Try to use something that wasn't imported
result = undefined_module.function()
"""

        ast = parse_pfig_source(config, filename="missing.pfig")

        with pytest.raises(Exception) as exc_info:
            execute_pfig(ast)

        error_msg = str(exc_info.value)
        assert "undefined_module" in error_msg or "Unknown import" in error_msg


class TestSpecificationCompliance:
    """Test compliance with pfig specification features."""

    def test_reserved_names_parsing(self):
        """Test that reserved names are parsed (even if not implemented)."""
        config = """
import sys

# Reserved magic names (parsing only - not yet executed)
__entrypoint__ = sys.exit
__snapshot__ = "./snapshots/run.json"

# Regular bindings
regular_value = 42
"""

        ast = parse_pfig_source(config, filename="reserved.pfig")

        # Should parse without error
        bindings = ast.bindings()
        binding_names = [b.name for b in bindings]

        assert "__entrypoint__" in binding_names
        assert "__snapshot__" in binding_names
        assert "regular_value" in binding_names

    def test_namespace_path_parsing(self):
        """Test parsing of namespace paths (even without execution)."""
        config = """
# Namespace paths in various contexts
external config/batch_size
external training/learning_rate

override base/model_type = "transformer"
override data/split_ratio = 0.8
"""

        ast = parse_pfig_source(config, filename="namespace.pfig")

        # Check external paths
        externals = ast.externals()
        assert len(externals) == 2

        ext_paths = ["/".join(ext.path.parts) for ext in externals]
        assert "config/batch_size" in ext_paths
        assert "training/learning_rate" in ext_paths

        # Check override paths
        overrides = ast.overrides()
        assert len(overrides) == 2

        over_paths = ["/".join(over.path.parts) for over in overrides]
        assert "base/model_type" in over_paths
        assert "data/split_ratio" in over_paths

    def test_provider_syntax_parsing(self):
        """Test parsing of @ provider syntax (even without execution)."""
        config = """
# Provider references and calls (parsing only)
# model_ref = @base_model
# fresh_model = @base_model(hidden_dim=512)
# config_val = @config/batch_size

# For now, just test that the syntax is recognized in comments
placeholder = 42
"""

        # This currently just tests that files with provider syntax comments
        # can still be parsed successfully
        ast = parse_pfig_source(config, filename="providers.pfig")

        assert len(ast.bindings()) == 1
        assert ast.bindings()[0].name == "placeholder"


class TestPerformance:
    """Test performance characteristics."""


def test_large_config_parsing():
    """Test parsing of reasonably large configurations."""
    # Generate a larger config programmatically
    lines = ["import json", "from collections import defaultdict", ""]

    # Add many bindings
    for i in range(100):
        lines.append(f"value_{i} = {i}")
        lines.append(f'string_{i} = "item_{i}"')

    # Add some complex structures
    for i in range(10):
        lines.append(f"dict_{i} = {{'key_{i}': {i}, 'nested': {{'inner': {i * 2}}}}}")
        lines.append(f"list_{i} = [{', '.join(str(j) for j in range(i, i + 5))}]")

    config = "\n".join(lines)

    # Should parse without issues
    ast = parse_pfig_source(config, filename="large.pfig")

    # Verify some results by testing individual bindings
    result = get_materialized_bindings(ast, entrypoint="value_50")
    assert result["value_50"] == 50

    result = get_materialized_bindings(ast, entrypoint="string_25")
    assert result["string_25"] == "item_25"

    result = get_materialized_bindings(ast, entrypoint="dict_5")
    assert result["dict_5"]["key_5"] == 5

    result = get_materialized_bindings(ast, entrypoint="list_3")
    assert result["list_3"] == [3, 4, 5, 6, 7]

    # Note: With entrypoint-based execution, we only materialize reachable bindings
    # So we can't assert the total count anymore


class TestRegressionTests:
    """Regression tests for previously fixed bugs."""

    def test_empty_containers(self):
        """Test that empty containers are handled correctly."""
        config = """
empty_list = []
empty_tuple = ()
empty_dict = {}
"""

        ast = parse_pfig_source(config, filename="empty.pfig")

        # Test each empty container individually
        result = get_materialized_bindings(ast, entrypoint="empty_list")
        assert result["empty_list"] == []

        result = get_materialized_bindings(ast, entrypoint="empty_tuple")
        assert result["empty_tuple"] == ()

        result = get_materialized_bindings(ast, entrypoint="empty_dict")
        assert result["empty_dict"] == {}

    def test_trailing_commas(self):
        """Test that trailing commas in containers are handled."""
        config = """
list_with_trailing = [1, 2, 3,]
tuple_with_trailing = (1, 2,)
dict_with_trailing = {"a": 1, "b": 2,}
"""

        ast = parse_pfig_source(config, filename="trailing.pfig")

        # Test each container with trailing commas individually
        result = get_materialized_bindings(ast, entrypoint="list_with_trailing")
        assert result["list_with_trailing"] == [1, 2, 3]

        result = get_materialized_bindings(ast, entrypoint="tuple_with_trailing")
        assert result["tuple_with_trailing"] == (1, 2)

        result = get_materialized_bindings(ast, entrypoint="dict_with_trailing")
        assert result["dict_with_trailing"] == {"a": 1, "b": 2}

    def test_string_escapes(self):
        """Test that string escapes work correctly."""
        config = r"""
newline_string = "line1\nline2"
tab_string = "col1\tcol2"
quote_string = "He said \"Hello\""
backslash_string = "path\\to\\file"
"""

        ast = parse_pfig_source(config, filename="strings.pfig")

        # Test each string with escapes individually
        result = get_materialized_bindings(ast, entrypoint="newline_string")
        assert result["newline_string"] == "line1\nline2"

        result = get_materialized_bindings(ast, entrypoint="tab_string")
        assert result["tab_string"] == "col1\tcol2"

        result = get_materialized_bindings(ast, entrypoint="quote_string")
        assert result["quote_string"] == 'He said "Hello"'

        result = get_materialized_bindings(ast, entrypoint="backslash_string")
        assert result["backslash_string"] == "path\\to\\file"


class TestBackwardsCompatibility:
    """Test backwards compatibility with existing configs."""

    def test_python_style_config(self):
        """Test that Python-style configs still work."""
        # This is the kind of config the old main.py could handle
        config = """
import json
from collections import defaultdict

batch_size = 64
learning_rate = 0.001

# Provider definitions
config_dict_provider = json.dumps(obj={"layers": 6})
counter_provider = defaultdict(default_factory=int)

# Provider references
config_dict = @config_dict_provider
counter = @counter_provider
"""

        ast = parse_pfig_source(config, filename="compat.pfig")

        # Should work identically to old parser for this subset
        result = get_materialized_bindings(ast, entrypoint="batch_size")
        assert result["batch_size"] == 64

        result = get_materialized_bindings(ast, entrypoint="learning_rate")
        assert result["learning_rate"] == 0.001

        result = get_materialized_bindings(ast, entrypoint="config_dict")
        assert '"layers": 6' in result["config_dict"]

        from collections import defaultdict

        result = get_materialized_bindings(ast, entrypoint="counter")
        assert isinstance(result["counter"], defaultdict)


class TestProfilePrefixIntegration:
    """End-to-end tests for --profile train::profiles/local.pfig feature."""

    def test_validate_nested_with_prefix(self, tmp_path):
        train = tmp_path / "train.pfig"
        train.write_text(
            "external output_dir\n"
            "external bf16\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict(o=@output_dir, b=@bf16)\n"
            "__entrypoint__ = print\n"
        )

        root = tmp_path / "train_lora.pfig"
        root.write_text(f'include "{train}" as train\n__entrypoint__ = @train/result\n')

        profile = tmp_path / "local.pfig"
        profile.write_text('override output_dir = "./runs/local"\noverride bf16 = False\n')

        spec = ProfileSpec(path=profile, prefix=("train",))
        ast = parse_config_file(root)
        metadata = validate_pfig(ast, profile_paths=[spec])
        assert metadata.externals["train/output_dir"] == "./runs/local"
        assert metadata.externals["train/bf16"] is False

    def test_cli_set_overrides_prefixed_profile(self, tmp_path):
        """CLI --set train/output_dir=... should override --profile train::local.pfig."""
        train = tmp_path / "train.pfig"
        train.write_text(
            "external output_dir\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict(o=@output_dir)\n"
            "__entrypoint__ = print\n"
        )

        root = tmp_path / "root.pfig"
        root.write_text(f'include "{train}" as train\n__entrypoint__ = @train/result\n')

        profile = tmp_path / "local.pfig"
        profile.write_text('override output_dir = "./from_profile"\n')

        spec = ProfileSpec(path=profile, prefix=("train",))
        ast = parse_config_file(root)
        # CLI --set takes precedence over profile
        metadata = validate_pfig(
            ast,
            profile_paths=[spec],
            externals={"train/output_dir": "./from_cli"},
        )
        assert metadata.externals["train/output_dir"] == "./from_cli"

    def test_bad_prefix_error_message(self, tmp_path):
        """Typo in prefix should mention known external prefixes."""
        from pfig.execution import ExecutionError

        train = tmp_path / "train.pfig"
        train.write_text(
            "external output_dir\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict()\n"
            "__entrypoint__ = print\n"
        )

        root = tmp_path / "root.pfig"
        root.write_text(f'include "{train}" as train\n__entrypoint__ = @train/result\n')

        profile = tmp_path / "local.pfig"
        profile.write_text('override output_dir = "./x"\n')

        spec = ProfileSpec(path=profile, prefix=("trian",))  # typo
        ast = parse_config_file(root)
        with pytest.raises(ExecutionError) as exc_info:
            validate_pfig(ast, profile_paths=[spec])
        msg = str(exc_info.value)
        assert "trian" in msg
        assert "train" in msg  # should suggest the correct namespace


class TestMissingProfilePrefixSuggestion:
    """Integration tests for the 'did you mean train::profile' suggestion feature."""

    def _write_train_pfig(self, tmp_path):
        p = tmp_path / "train.pfig"
        p.write_text(
            "external output_dir\n"
            "external bf16\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict(o=@output_dir, b=@bf16)\n"
            "__entrypoint__ = print\n"
        )
        return p

    def _write_root_pfig(self, tmp_path, train_path):
        p = tmp_path / "root.pfig"
        p.write_text(f'include "{train_path}" as train\n__entrypoint__ = @train/result\n')
        return p

    def _write_local_profile(self, tmp_path):
        p = tmp_path / "local.pfig"
        p.write_text('override output_dir = "./runs/local"\noverride bf16 = False\n')
        return p

    def test_unprefixed_profile_fails_with_train_suggestion(self, tmp_path):
        """High-confidence case: all failed overrides match one prefix."""
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_pfig(tmp_path, train)
        profile = self._write_local_profile(tmp_path)

        ast = parse_config_file(root)
        with pytest.raises(ExecutionError) as exc_info:
            validate_pfig(ast, profile_paths=[profile])
        msg = str(exc_info.value)
        assert "train/output_dir" in msg
        assert "train/bf16" in msg
        assert "--profile train::" in msg
        assert "Did you mean" in msg

    def test_prefixed_profile_succeeds(self, tmp_path):
        """Sanity check: the suggested command actually works."""
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_pfig(tmp_path, train)
        profile = self._write_local_profile(tmp_path)

        spec = ProfileSpec(path=profile, prefix=("train",))
        ast = parse_config_file(root)
        metadata = validate_pfig(ast, profile_paths=[spec])
        assert metadata.externals["train/output_dir"] == "./runs/local"
        assert metadata.externals["train/bf16"] is False

    def test_ambiguous_multiple_includes_lists_both_candidates(self, tmp_path):
        """When the same config is included twice, both candidate commands appear."""
        train = self._write_train_pfig(tmp_path)

        root = tmp_path / "root.pfig"
        root.write_text(
            f'include "{train}" as train_a\ninclude "{train}" as train_b\n__entrypoint__ = print\n'
        )

        profile = tmp_path / "local.pfig"
        profile.write_text('override output_dir = "./x"\n')

        ast = parse_config_file(root)
        with pytest.raises(ExecutionError) as exc_info:
            validate_pfig(ast, profile_paths=[profile])
        msg = str(exc_info.value)
        assert "--profile train_a::" in msg
        assert "--profile train_b::" in msg

    def test_no_suffix_match_gives_plain_error(self, tmp_path):
        """When no declared external matches, no suggestion is emitted."""
        config = tmp_path / "config.pfig"
        config.write_text(
            "external project_id\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict(p=@project_id)\n"
            "__entrypoint__ = print\n"
        )

        profile = tmp_path / "local.pfig"
        profile.write_text("override learning_rate = 1e-4\n")

        ast = parse_config_file(config)
        with pytest.raises(ExecutionError) as exc_info:
            validate_pfig(ast, profile_paths=[profile])
        msg = str(exc_info.value)
        assert "not declared externals" in msg
        assert "Did you mean" not in msg
        assert "--profile" not in msg

    def test_partial_match_shows_unmatched_overrides(self, tmp_path):
        """Medium-confidence: some overrides match a prefix, one does not."""
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_pfig(tmp_path, train)

        profile = tmp_path / "mixed.pfig"
        profile.write_text(
            'override output_dir = "./runs/local"\n'
            "override bf16 = False\n"
            'override nonexistent_key = "oops"\n'
        )

        ast = parse_config_file(root)
        with pytest.raises(ExecutionError) as exc_info:
            validate_pfig(ast, profile_paths=[profile])
        msg = str(exc_info.value)
        assert "--profile train::" in msg
        assert "nonexistent_key" in msg

    def test_root_profile_still_works_unchanged(self, tmp_path):
        """Existing root-level profiles are unaffected by the new logic."""
        config = self._write_train_pfig(tmp_path)
        profile = self._write_local_profile(tmp_path)

        ast = parse_config_file(config)
        metadata = validate_pfig(ast, profile_paths=[profile])
        assert metadata.externals["output_dir"] == "./runs/local"
        assert metadata.externals["bf16"] is False
