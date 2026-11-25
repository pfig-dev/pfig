"""Tests for the include system."""

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
from pfig.execution import execute_pfig_with_metadata


class TestIncludeSystem:
    """Test the include system functionality."""

    def test_basic_include(self):
        """Test basic include functionality."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
# Base configuration
batch_size = 64
learning_rate = 0.001

default_optimizer = dict(
    type="adam",
    lr=@learning_rate
)
"""
            )

            # Create main config that includes base
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

epochs = 10

# Use included values
training_config = {
    "batch_size": @base/batch_size,
    "learning_rate": @base/learning_rate,
    "epochs": @epochs,
    "optimizer": @base/default_optimizer
}
"""
            )

            # Test instantiation
            result = instantiate("training_config", path=main_config)

            assert result["batch_size"] == 64
            assert result["learning_rate"] == 0.001
            assert result["epochs"] == 10
            assert result["optimizer"]["type"] == "adam"
            assert result["optimizer"]["lr"] == 0.001

    def test_nested_includes(self):
        """Test nested includes (A includes B, B includes C)."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create deepest config (C)
            config_c = tmp_path / "c.pfig"
            config_c.write_text(
                """
# Deep config
deep_value = "from_c"
"""
            )

            # Create middle config (B) that includes C
            config_b = tmp_path / "b.pfig"
            config_b.write_text(
                """
include "./c.pfig" as c

middle_value = "from_b"
combined = @c/deep_value + "_and_" + @middle_value
"""
            )

            # Create top config (A) that includes B
            config_a = tmp_path / "a.pfig"
            config_a.write_text(
                """
include "./b.pfig" as b

top_value = "from_a"
final_result = @top_value + "_" + @b/combined
"""
            )

            # Test instantiation
            result = instantiate("final_result", path=config_a)
            assert result == "from_a_from_c_and_from_b"

    def test_include_cycle_detection(self):
        """Test that include cycles are detected and reported."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create files that include each other
            config_a = tmp_path / "a.pfig"
            config_a.write_text(
                """
include "./b.pfig" as b
value_a = "from_a"
"""
            )

            config_b = tmp_path / "b.pfig"
            config_b.write_text(
                """
include "./a.pfig" as a
value_b = "from_b"
"""
            )

            # Test that cycle is detected
            with pytest.raises(ValidationError, match=r"Include cycle detected"):
                instantiate("value_a", path=config_a)

    def test_include_file_not_found(self):
        """Test error handling when included file doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            config = tmp_path / "main.pfig"
            config.write_text(
                """
include "./nonexistent.pfig" as missing
value = @missing/something
"""
            )

            with pytest.raises(ValidationError, match=r"Include file not found"):
                instantiate("value", path=config)

    def test_include_namespace_isolation(self):
        """Test that includes create proper namespace isolation."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config with common names
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
name = "base_name"
value = 100
"""
            )

            # Create main config with same names
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

name = "main_name"
value = 200

# Should access the included values via namespace
result = {
    "main_name": @name,
    "main_value": @value,
    "base_name": @base/name,
    "base_value": @base/value
}
"""
            )

            result = instantiate("result", path=main_config)

            assert result["main_name"] == "main_name"
            assert result["main_value"] == 200
            assert result["base_name"] == "base_name"
            assert result["base_value"] == 100

    def test_include_with_execute_pfig(self):
        """Test that includes work with execute_pfig."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
counter = 0
"""
            )

            # Create main config with callable entrypoint
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

# Import a callable
my_dict = dict()

__entrypoint__ = @my_dict.clear
"""
            )

            # Parse and execute
            ast = parse_pfig_source(main_config.read_text(), filename=str(main_config))
            result = execute_pfig(ast, filename=str(main_config))

            # Should execute without error (clear method doesn't return anything)
            assert result is None

    def test_include_with_get_materialized_bindings(self):
        """Test that includes work with get_materialized_bindings."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
shared_value = "shared"
"""
            )

            # Create main config
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

local_value = "local"
combined = @base/shared_value + "_" + @local_value
"""
            )

            # Parse and get materialized bindings
            ast = parse_pfig_source(main_config.read_text(), filename=str(main_config))
            bindings = get_materialized_bindings(
                ast, entrypoint="combined", filename=str(main_config)
            )

            # Arithmetic references participate in reachability like other dependencies.
            assert bindings == {
                "combined": "shared_local",
                "base/shared_value": "shared",
                "local_value": "local",
            }

    def test_duplicate_binding_after_include(self):
        """Test error handling for duplicate bindings after include."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
common_name = "from_base"
"""
            )

            # Create second config with same binding
            second_config = tmp_path / "second.pfig"
            second_config.write_text(
                """
common_name = "from_second"
"""
            )

            # Create main config that includes both with same alias (should error)
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base
include "./second.pfig" as base  # Same alias, should cause duplicate

value = @base/common_name
"""
            )

            with pytest.raises(
                ValidationError,
                match=r"Duplicate.*(after include|include alias)",
            ):
                instantiate("value", path=main_config)

    def test_include_caching(self):
        """Test that included files are cached and not recompiled."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create shared config
            shared_config = tmp_path / "shared.pfig"
            shared_config.write_text(
                """
shared_value = "shared"
"""
            )

            # Create two configs that both include shared
            config_a = tmp_path / "a.pfig"
            config_a.write_text(
                """
include "./shared.pfig" as shared
value_a = @shared/shared_value + "_a"
"""
            )

            config_b = tmp_path / "b.pfig"
            config_b.write_text(
                """
include "./shared.pfig" as shared
value_b = @shared/shared_value + "_b"
"""
            )

            # Create main that includes both
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./a.pfig" as a
include "./b.pfig" as b

result = {
    "a": @a/value_a,
    "b": @b/value_b
}
"""
            )

            result = instantiate("result", path=main_config)

            assert result["a"] == "shared_a"
            assert result["b"] == "shared_b"

    def test_include_caching_with_alias(self):
        """Test that included files are cached and not recompiled."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            config_a = tmp_path / "a.pfig"
            config_a.write_text(
                """
from collections import Counter
counter = Counter()
"""
            )

            config_b = tmp_path / "b.pfig"
            config_b.write_text(
                """
include "./a.pfig" as a
counter = @a/counter
"""
            )

            # Create main that includes both
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./a.pfig" as a
include "./b.pfig" as b

result = {
    "a": @a/counter,
    "b": @b/a/counter,
}
"""
            )

            result = instantiate("result", path=main_config)
            assert id(result["a"]) == id(result["b"])

    def test_same_file_different_aliases_shared_instances(self):
        """Test that the same file included with different aliases returns shared instances.

        This is critical: if components.pfig is included as 'components' in one file and
        'comps' in another, both should get the same shared instance of each provider.
        The canonical_key system ensures this works correctly.
        """
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create a components file with providers
            components = tmp_path / "components.pfig"
            components.write_text(
                """
from collections import Counter
image_encoder = Counter()
pose_encoder = Counter()
"""
            )

            # Include components with alias "components"
            policy_config = tmp_path / "policy.pfig"
            policy_config.write_text(
                """
include "./components.pfig" as components

# Use the encoders in policy
policy_image_enc = @components/image_encoder
policy_pose_enc = @components/pose_encoder
"""
            )

            # Include components with alias "comps" (different alias!)
            train_config = tmp_path / "train.pfig"
            train_config.write_text(
                """
include "./components.pfig" as comps
include "./policy.pfig" as policy

# Reference via different alias
optimizer_image_enc = @comps/image_encoder
optimizer_pose_enc = @comps/pose_encoder

# Also reference from policy (which used 'components' alias)
policy_image_from_policy = @policy/policy_image_enc
policy_pose_from_policy = @policy/policy_pose_enc

result = {
    "optimizer_image": @optimizer_image_enc,
    "optimizer_pose": @optimizer_pose_enc,
    "policy_image": @policy_image_from_policy,
    "policy_pose": @policy_pose_from_policy,
}
"""
            )

            result = instantiate("result", path=train_config)

            # Critical assertions: Despite different aliases ('components' vs 'comps'),
            # all references should point to the SAME instances
            assert result["optimizer_image"] is result["policy_image"], (
                f"image_encoder should be the same instance regardless of alias. "
                f"optimizer: {id(result['optimizer_image'])}, "
                f"policy: {id(result['policy_image'])}"
            )

            assert result["optimizer_pose"] is result["policy_pose"], (
                f"pose_encoder should be the same instance regardless of alias. "
                f"optimizer: {id(result['optimizer_pose'])}, "
                f"policy: {id(result['policy_pose'])}"
            )


@pytest.mark.parametrize("path", ["std://models.pfig", "package://my_models/base.pfig"])
def test_protocol_includes_are_rejected(path):
    with pytest.raises(ValidationError, match="Protocol-based includes are not supported"):
        instantiate("result", source=f'include "{path}" as lib\nresult = @lib/value')


class TestIncludedFilesTracking:
    """Test that included files are tracked and returned in metadata."""

    def test_no_includes_returns_empty_list(self):
        """Test that config without includes returns empty included_files."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create a simple config with no includes
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
batch_size = 64
__entrypoint__ = print
"""
            )

            ast = parse_pfig_source(main_config.read_text(), filename=str(main_config))
            metadata = execute_pfig_with_metadata(ast, filename=str(main_config))

            assert metadata.included_files == []

    def test_single_include_tracked(self):
        """Test that a single include is tracked."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config
            base_config = tmp_path / "base.pfig"
            base_config.write_text(
                """
batch_size = 64
"""
            )

            # Create main config that includes base
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./base.pfig" as base

config = @base/batch_size
__entrypoint__ = print
"""
            )

            ast = parse_pfig_source(main_config.read_text(), filename=str(main_config))
            metadata = execute_pfig_with_metadata(ast, filename=str(main_config))

            # Should have exactly one included file
            assert len(metadata.included_files) == 1
            assert str(base_config.resolve()) in metadata.included_files

    def test_nested_includes_tracked(self):
        """Test that nested includes (A -> B -> C) are all tracked."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create deepest config (C)
            config_c = tmp_path / "c.pfig"
            config_c.write_text(
                """
deep_value = "from_c"
"""
            )

            # Create middle config (B) that includes C
            config_b = tmp_path / "b.pfig"
            config_b.write_text(
                """
include "./c.pfig" as c

middle_value = @c/deep_value
"""
            )

            # Create top config (A) that includes B
            config_a = tmp_path / "a.pfig"
            config_a.write_text(
                """
include "./b.pfig" as b

result = @b/middle_value
__entrypoint__ = print
"""
            )

            ast = parse_pfig_source(config_a.read_text(), filename=str(config_a))
            metadata = execute_pfig_with_metadata(ast, filename=str(config_a))

            # Should have both B and C in included files
            assert len(metadata.included_files) == 2
            included_set = set(metadata.included_files)
            assert str(config_b.resolve()) in included_set
            assert str(config_c.resolve()) in included_set

    def test_diamond_includes_tracked_once(self):
        """Test that files included via multiple paths are tracked only once."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create shared config (included by both A and B)
            shared_config = tmp_path / "shared.pfig"
            shared_config.write_text(
                """
shared_value = "shared"
"""
            )

            # Create A that includes shared
            config_a = tmp_path / "a.pfig"
            config_a.write_text(
                """
include "./shared.pfig" as shared
value_a = @shared/shared_value
"""
            )

            # Create B that also includes shared
            config_b = tmp_path / "b.pfig"
            config_b.write_text(
                """
include "./shared.pfig" as shared
value_b = @shared/shared_value
"""
            )

            # Main includes both A and B
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./a.pfig" as a
include "./b.pfig" as b

result = @a/value_a + @b/value_b
__entrypoint__ = print
"""
            )

            ast = parse_pfig_source(main_config.read_text(), filename=str(main_config))
            metadata = execute_pfig_with_metadata(ast, filename=str(main_config))

            # Should have A, B, and shared - but shared only once (deduplicated by caching)
            assert len(metadata.included_files) == 3
            included_set = set(metadata.included_files)
            assert str(config_a.resolve()) in included_set
            assert str(config_b.resolve()) in included_set
            assert str(shared_config.resolve()) in included_set

    def test_library_includes_tracked(self):
        """Explicit library includes are tracked for bundling."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create library directory
            lib_dir = tmp_path / "lib"
            lib_dir.mkdir()
            lib_file = lib_dir / "config.pfig"
            lib_file.write_text(
                """
lib_value = 42
"""
            )

            # Create main config using an explicit relative include
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
include "./lib/config.pfig" as lib

result = @lib/lib_value
__entrypoint__ = print
"""
            )

            ast = parse_pfig_source(main_config.read_text(), filename=str(main_config))
            metadata = execute_pfig_with_metadata(ast, filename=str(main_config))

            # Should have the lib file tracked
            assert len(metadata.included_files) == 1
            assert str(lib_file.resolve()) in metadata.included_files
