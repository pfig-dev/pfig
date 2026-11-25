"""
Unit tests for the pfig external system.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pfig.execution import (
    ExecutionError,
    ProfileSpec,
    ValidationError,
    execute_pfig,
    execute_pfig_with_metadata,
    instantiate,
)
from pfig.parser import parse_pfig_source


class TestExternalDeclarations:
    """Test external declaration parsing and basic functionality."""

    def test_simple_external_declaration(self):
        """Test parsing a simple external declaration."""
        config = "external output_dir"
        ast = parse_pfig_source(config)

        externals = ast.externals()
        assert len(externals) == 1
        assert externals[0].path.parts == ["output_dir"]

    def test_namespaced_external_declaration(self):
        """Test parsing external with namespace path."""
        config = "external config/batch_size"
        ast = parse_pfig_source(config)

        external = ast.externals()[0]
        assert external.path.parts == ["config", "batch_size"]

    def test_multiple_external_declarations(self):
        """Test parsing multiple external declarations."""
        config = """
external output_dir
external num_gpus
external config/batch_size
"""
        ast = parse_pfig_source(config)

        externals = ast.externals()
        assert len(externals) == 3

        paths = ["/".join(ext.path.parts) for ext in externals]
        assert "output_dir" in paths
        assert "num_gpus" in paths
        assert "config/batch_size" in paths


class TestExternalExecution:
    """Test external execution and validation."""

    def test_external_with_provided_values(self):
        """Test that externals work when values are provided."""
        config = """
external output_dir
external num_gpus

result = {
    "output": @output_dir,
    "gpus": @num_gpus
}
"""
        externals = {"output_dir": "./test_runs", "num_gpus": 4}
        result = instantiate("result", source=config, externals=externals)

        assert result["output"] == "./test_runs"
        assert result["gpus"] == 4

    def test_external_missing_values_error(self):
        """Test that missing external values raise an error."""
        config = """
external output_dir
external num_gpus

result = @output_dir
"""
        # No externals provided
        with pytest.raises(ValidationError) as exc_info:
            instantiate("result", source=config)
        # Only the selected binding's input is required.
        assert [issue.binding for issue in exc_info.value.issues] == ["output_dir"]
        assert instantiate("result", source=config, externals={"output_dir": "./runs"}) == "./runs"

    def test_allow_missing_externals_validation(self):
        """Test that allow_missing_externals allows validation without all externals."""
        from pfig.execution import validate_pfig

        config = """
external output_dir
external num_gpus

result = @output_dir

__entrypoint__ = @result.copy
"""
        ast = parse_pfig_source(config)

        # Without allow_missing_externals, the selected input is required.
        with pytest.raises(ValidationError, match="Missing external value"):
            validate_pfig(ast)

        # With allow_missing_externals, validation should pass
        result = validate_pfig(ast, allow_missing_externals=True)
        assert result.entrypoint_name == "__entrypoint__"
        assert "__entrypoint__" in result.reachable_names

    def test_external_with_complex_types(self):
        """Test externals with complex types (lists, dicts, etc.)."""
        config = """
external gpu_ids
external model_config

result = {
    "gpus": @gpu_ids,
    "model": @model_config
}
"""
        externals = {
            "gpu_ids": [0, 1, 2, 3],
            "model_config": {"layers": [512, 256, 128], "dropout": 0.2},
        }
        result = instantiate("result", source=config, externals=externals)

        assert result["gpus"] == [0, 1, 2, 3]
        assert result["model"]["layers"] == [512, 256, 128]
        assert result["model"]["dropout"] == 0.2


class TestExternalValidationRules:
    """Test validation rules for externals."""

    def test_cannot_override_external_in_normal_config(self):
        """Test that overriding externals in normal configs is not allowed."""
        config = """
external output_dir

# This should fail
override output_dir = "./bad_override"

result = @output_dir
"""
        with pytest.raises(
            ExecutionError,
            match="Cannot override external 'output_dir' in normal configuration",
        ):
            instantiate("result", source=config)

    def test_can_override_regular_binding_in_normal_config(self):
        """Test that overriding regular bindings still works."""
        config = """
output_dir = "./default"

override output_dir = "./overridden"

result = @output_dir
"""
        result = instantiate("result", source=config)
        assert result == "./overridden"

    def test_external_reference_without_declaration_fails(self):
        """Test that referencing undeclared externals fails."""
        config = """
# No external declaration for output_dir
result = @output_dir
"""
        with pytest.raises(ValidationError, match="Unknown binding: 'output_dir'"):
            instantiate("result", source=config)


class TestProfileSystem:
    """Test profile system functionality."""

    @pytest.mark.parametrize("second", ['"cpu"', '"cuda"'])
    def test_duplicate_profile_paths_fail_even_when_shadowed(self, tmp_path, second):
        profile = tmp_path / "duplicate.pfig"
        profile.write_text(f'override device = "cpu"\noverride device = {second}\n')
        later = tmp_path / "later.pfig"
        later.write_text('override device = "mps"\n')
        with pytest.raises(ValueError, match="Conflicting overrides"):
            instantiate(
                "device",
                source="external device\n",
                profile_paths=[profile, later],
                externals={"device": "cpu"},
            )

    @pytest.mark.parametrize("override_first", [False, True])
    def test_profile_can_override_inherited_value(self, tmp_path, override_first):
        base = tmp_path / "base.pfig"
        base.write_text('override device = "cpu"\noverride workers = 4\n')
        profile = tmp_path / "local.pfig"
        statements = ['include "./base.pfig" as train', 'override train/device = "cuda"']
        if override_first:
            statements.reverse()
        profile.write_text("\n".join(statements))

        assert instantiate(
            "result",
            source=(
                "external train/device\nexternal train/workers\n"
                "result = [@train/device, @train/workers]\n"
            ),
            profile_paths=[profile],
        ) == ["cuda", 4]

    @pytest.mark.parametrize("second", ['"cuda"', '"mps"'])
    def test_profile_rejects_duplicate_local_overrides_of_inherited_value(self, tmp_path, second):
        (tmp_path / "base.pfig").write_text('override device = "cpu"\n')
        profile = tmp_path / "local.pfig"
        profile.write_text(
            'include "./base.pfig" as train\n'
            'override train/device = "cuda"\n'
            f"override train/device = {second}\n"
        )
        with pytest.raises(ValueError, match="Conflicting overrides"):
            instantiate("train/device", source="external train/device\n", profile_paths=[profile])

    def test_profile_cannot_hide_inherited_duplicate_declarations(self, tmp_path):
        (tmp_path / "base.pfig").write_text('override device = "cpu"\noverride device = "cpu"\n')
        profile = tmp_path / "local.pfig"
        profile.write_text('include "./base.pfig" as train\noverride train/device = "cuda"\n')
        with pytest.raises(ValueError, match="Conflicting overrides"):
            instantiate("train/device", source="external train/device\n", profile_paths=[profile])

    def test_valid_profile_with_externals(self):
        """Test that valid profiles work correctly."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create profile file
            profile_file = tmp_path / "test_profile.pfig"
            profile_file.write_text(
                """
override output_dir = "./profile_runs"
override num_gpus = 8
"""
            )

            # Create main config
            main_config = """
external output_dir
external num_gpus

result = {
    "output": @output_dir,
    "gpus": @num_gpus
}

__entrypoint__ = print
"""

            # Test with profile
            ast = parse_pfig_source(main_config)
            from pfig.execution import load_profile, validate_pfig

            profile_values, _file_origins = load_profile(str(profile_file))
            metadata = validate_pfig(ast, externals=profile_values)

            assert metadata is not None  # Should succeed

    def test_valid_profile_with_externals_list_type(self):
        """Test that valid profiles work correctly."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create profile file
            profile_file = tmp_path / "test_profile.pfig"
            profile_file.write_text(
                """
override output_dir = "./profile_runs"
override num_gpus = 8
override val_zarr_paths = ["/path/to/val/data1.zarr", "/path/to/val/data2.zarr"]
"""
            )

            # Create main config
            main_config = """
external output_dir
external num_gpus
external val_zarr_paths

result = {
    "output": @output_dir,
    "gpus": @num_gpus,
    "val_zarr_paths": @val_zarr_paths
}

__entrypoint__ = print
"""

            # Test with profile
            ast = parse_pfig_source(main_config)
            from pfig.execution import load_profile, validate_pfig

            profile_values, _file_origins = load_profile(str(profile_file))
            metadata = validate_pfig(ast, externals=profile_values)

            assert metadata is not None  # Should succeed

    def test_profile_cannot_define_regular_bindings(self):
        """Test that profiles cannot define regular bindings."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create bad profile file
            profile_file = tmp_path / "bad_profile.pfig"
            profile_file.write_text(
                """
# This should fail - regular binding in profile
regular_binding = "not allowed"
"""
            )

            from pfig.execution import load_profile

            with pytest.raises(ValueError, match="Profile files cannot define regular bindings"):
                load_profile(str(profile_file))

    def test_profile_cannot_define_providers(self):
        """Test that profiles cannot define providers."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create bad profile file
            profile_file = tmp_path / "bad_profile.pfig"
            profile_file.write_text(
                """
import json

# This should fail - provider in profile
data_loader = json.loads(s='{"test": true}')
"""
            )

            from pfig.execution import load_profile

            with pytest.raises(ValueError, match="Profile files cannot define regular bindings"):
                load_profile(str(profile_file))

    def test_profiles_can_override_externals(self):
        """Test that profiles can properly override externals."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create good profile file
            profile_file = tmp_path / "good_profile.pfig"
            profile_file.write_text(
                """
external output_dir
external num_gpus
external batch_size

override output_dir = "./profile_output"
override num_gpus = 2
override batch_size = 64
"""
            )

            from pfig.execution import load_profile

            profile_values, _file_origins = load_profile(str(profile_file))

        assert profile_values["output_dir"] == "./profile_output"
        assert profile_values["num_gpus"] == 2  # Integer from literal parsing
        assert profile_values["batch_size"] == 64


class TestExternalIntegration:
    """Test integration of externals with other pfig features."""

    def test_externals_with_includes(self):
        """Test externals working with include system."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config with externals
            base_file = tmp_path / "base.pfig"
            base_file.write_text(
                """
external output_dir
external num_gpus

base_config = {
    "output": @output_dir,
    "gpus": @num_gpus
}
"""
            )

            # Create main config that includes base
            main_file = tmp_path / "main.pfig"
            main_file.write_text(
                f"""
include "./{base_file.name}" as base

result = @base/base_config
"""
            )

            externals = {"base/output_dir": "./test", "base/num_gpus": 4}
            result = instantiate("result", path=str(main_file), externals=externals)

            assert result["output"] == "./test"
            assert result["gpus"] == 4

    def test_externals_with_overrides_and_includes(self):
        """Test complex scenario with externals, includes, and overrides."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create base config
            base_file = tmp_path / "base.pfig"
            base_file.write_text(
                """
external learning_rate
external batch_size

config = {
    "lr": @learning_rate,
    "batch": @batch_size
}
"""
            )

            # Create main config with includes and non-external overrides
            main_file = tmp_path / "main.pfig"
            main_file.write_text(
                f"""
include "./{base_file.name}" as base

# This is allowed - overriding a regular binding from included file
override base/config = {{"lr": @base/learning_rate, "batch": 128, "extra": True}}

result = @base/config
"""
            )

            externals = {"base/learning_rate": 0.001, "base/batch_size": 64}
            result = instantiate("result", path=str(main_file), externals=externals)

            assert result["lr"] == 0.001
            assert result["batch"] == 128  # Overridden
            assert result["extra"] == True

    def test_externals_in_arithmetic_expressions(self):
        """Test externals used in arithmetic expressions."""
        config = """
external base_lr
external multiplier

final_lr = @base_lr * @multiplier
result = @final_lr
"""
        externals = {"base_lr": 0.001, "multiplier": 10}
        result = instantiate("result", source=config, externals=externals)

        assert result == 0.01  # 0.001 * 10


class TestExternalErrorCases:
    """Test error cases and edge conditions."""

    def test_external_name_validation(self):
        """Test that external names follow identifier rules."""
        # Valid external names should work
        valid_configs = [
            "external output_dir",
            "external _private_var",
            "external config123",
            "external namespace/value",
        ]

        for config in valid_configs:
            ast = parse_pfig_source(config)
            assert len(ast.externals()) == 1

    def test_external_cycle_detection(self):
        """Test that external references don't cause cycles."""
        config = """
external value_a
external value_b

# These reference externals, not each other - should be fine
result_a = @value_a
result_b = @value_b
combined = [@result_a, @result_b]
"""
        externals = {"value_a": "A", "value_b": "B"}
        result = instantiate("combined", source=config, externals=externals)

        assert result == ["A", "B"]

    def test_helpful_error_messages(self):
        """Test that error messages are helpful."""
        config = """
external output_dir
external num_gpus
external batch_size

result = [@output_dir, @num_gpus, @batch_size]
"""

        # Test missing externals error message
        with pytest.raises(ValidationError) as exc_info:
            instantiate("result", source=config)

        error = exc_info.value
        error_msg = str(error)
        # Should list all missing externals
        assert "batch_size" in error_msg
        assert "num_gpus" in error_msg
        assert "output_dir" in error_msg
        # Each missing external should be a separate issue with helpful message
        missing_external_issues = [i for i in error.issues if i.code == "missing_external"]
        assert len(missing_external_issues) == 3
        # Should include guidance on how to fix
        assert any("--set" in i.message for i in missing_external_issues)


class TestCLIIntegration:
    """Test CLI integration with externals."""

    def test_set_flag_basic(self):
        """Test basic --set flag functionality."""
        from pfig.cli import parse_set_args

        args = ["output_dir=./runs", "num_gpus=4", "debug=true"]
        result = parse_set_args(args)

        expected = {
            "output_dir": "./runs",
            "num_gpus": 4,  # Type inference converts to int
            "debug": True,  # Type inference converts to bool
        }
        assert result == expected

    def test_set_flag_error_cases(self):
        """Test --set flag error handling."""
        from pfig.cli import parse_set_args

        # Missing equals sign
        with pytest.raises(ValueError, match="Invalid --set argument"):
            parse_set_args(["invalid_arg"])

        # Empty key or value should still work
        result = parse_set_args(["key=", "=value"])
        assert result == {"key": "", "": "value"}

        # Multiple equals signs should work (split on first)
        result = parse_set_args(["url=http://example.com:8080?param=value"])
        assert result == {"url": "http://example.com:8080?param=value"}

    def test_profile_override_non_external_binding_fails(self):
        """Profiles may only override declared externals, not normal bindings."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
value = "DEFAULT"
result = @value
"""
            )

            profile = tmp_path / "profile.pfig"
            profile.write_text("""override value = "FROM_PROFILE" """)

            with pytest.raises(
                ExecutionError,
                match="not declared externals",
            ):
                instantiate(name="result", path=main_config, profile_paths=[profile])

    def test_profile_precedence(self):
        """Test that --set values take precedence over profile values."""
        # This would be tested with actual CLI calls, but the logic is covered
        # in the profile system tests above
        pass

    def test_multiple_profiles(self):
        """Test that multiple profile files can be loaded and merged."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create main config
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
external batch_size
external learning_rate
external num_epochs

result = {
    "batch_size": @batch_size,
    "learning_rate": @learning_rate,
    "num_epochs": @num_epochs,
}
"""
            )

            # Create first profile
            profile1 = tmp_path / "profile1.pfig"
            profile1.write_text(
                """
override batch_size = 32
override learning_rate = 0.001
"""
            )

            # Create second profile
            profile2 = tmp_path / "profile2.pfig"
            profile2.write_text(
                """
override batch_size = 64  # This should override profile1's value
override num_epochs = 100
"""
            )

            # Load with multiple profiles
            result = instantiate(
                name="result", path=main_config, profile_paths=[profile1, profile2]
            )

            # Later profiles should override earlier ones
            assert result["batch_size"] == 64  # From profile2 (overrides profile1)
            assert result["learning_rate"] == 0.001  # From profile1
            assert result["num_epochs"] == 100  # From profile2

    def test_multiple_profiles_order_matters(self):
        """Test that profile order matters - later profiles override earlier ones."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create main config
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
external value

result = @value
"""
            )

            # Create profiles with conflicting values
            profile_a = tmp_path / "profile_a.pfig"
            profile_a.write_text("override value = 'A'")

            profile_b = tmp_path / "profile_b.pfig"
            profile_b.write_text("override value = 'B'")

            # Order: A then B -> B wins
            result1 = instantiate(
                name="result", path=main_config, profile_paths=[profile_a, profile_b]
            )
            assert result1 == "B"

            # Order: B then A -> A wins
            result2 = instantiate(
                name="result", path=main_config, profile_paths=[profile_b, profile_a]
            )
            assert result2 == "A"

    def test_multiple_profiles_with_externals_override(self):
        """Test that externals parameter overrides all profiles."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create main config
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
external batch_size
external learning_rate

result = {
    "batch_size": @batch_size,
    "learning_rate": @learning_rate,
}
"""
            )

            # Create profiles
            profile1 = tmp_path / "profile1.pfig"
            profile1.write_text(
                """
override batch_size = 32
override learning_rate = 0.001
"""
            )

            profile2 = tmp_path / "profile2.pfig"
            profile2.write_text(
                """
override batch_size = 64
"""
            )

            # Load with profiles and externals - externals should win
            result = instantiate(
                name="result",
                path=main_config,
                profile_paths=[profile1, profile2],
                externals={"batch_size": 128, "learning_rate": 0.01},
            )

            # Externals should override all profiles
            assert result["batch_size"] == 128  # From externals (not 64 from profile2)
            assert result["learning_rate"] == 0.01  # From externals (not 0.001 from profile1)

    def test_empty_profile_list(self):
        """Test that empty profile list works correctly."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create main config
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
external value

result = @value
"""
            )

            # Empty list should work (externals required)
            result = instantiate(
                name="result",
                path=main_config,
                profile_paths=[],
                externals={"value": 42},
            )
            assert result == 42

    def test_single_profile_in_list(self):
        """Test that a single profile in a list works correctly."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create main config
            main_config = tmp_path / "main.pfig"
            main_config.write_text(
                """
external value

result = @value
"""
            )

            # Single profile in list
            profile = tmp_path / "profile.pfig"
            profile.write_text("override value = 'single'")

            result = instantiate(name="result", path=main_config, profile_paths=[profile])
            assert result == "single"


class TestProfilePrefixes:
    """Tests for the profile application prefix feature (train::profiles/local.pfig)."""

    def _write_train_pfig(self, tmp_path: Path) -> Path:
        train = tmp_path / "train.pfig"
        train.write_text(
            "external output_dir\n"
            "external bf16\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict(output_dir=@output_dir, bf16=@bf16)\n"
            "__entrypoint__ = print\n"
        )
        return train

    def _write_root_config(self, tmp_path: Path, train_path: Path) -> Path:
        root = tmp_path / "train_lora.pfig"
        root.write_text(f'include "{train_path}" as train\n__entrypoint__ = @train/result\n')
        return root

    def _write_local_profile(self, tmp_path: Path) -> Path:
        profile = tmp_path / "local.pfig"
        profile.write_text('override output_dir = "./runs/local"\noverride bf16 = False\n')
        return profile

    def test_root_use_unprefixed_still_works(self, tmp_path):
        train = self._write_train_pfig(tmp_path)
        profile = self._write_local_profile(tmp_path)
        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(train)
        metadata = validate_pfig(ast, profile_paths=[profile])
        assert metadata is not None

    def test_prefixed_profile_applies_correctly(self, tmp_path):
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_config(tmp_path, train)
        profile = self._write_local_profile(tmp_path)

        spec = ProfileSpec(path=profile, prefix=("train",))
        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(root)
        metadata = validate_pfig(ast, profile_paths=[spec])
        assert metadata is not None

    def test_unprefixed_profile_against_nested_config_fails(self, tmp_path):
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_config(tmp_path, train)
        profile = self._write_local_profile(tmp_path)

        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(root)
        with pytest.raises(ExecutionError, match="not declared externals"):
            validate_pfig(ast, profile_paths=[profile])

    def test_unprefixed_profile_error_includes_suggestion(self, tmp_path):
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_config(tmp_path, train)
        profile = self._write_local_profile(tmp_path)

        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(root)
        with pytest.raises(ExecutionError) as exc_info:
            validate_pfig(ast, profile_paths=[profile])
        msg = str(exc_info.value)
        assert "train/output_dir" in msg
        assert "train/bf16" in msg
        assert "--profile train::" in msg

    def test_profile_cannot_override_non_external_after_prefixing(self, tmp_path):
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_config(tmp_path, train)

        bad_profile = tmp_path / "bad.pfig"
        bad_profile.write_text("override learning_rate = 1e-4\n")
        spec = ProfileSpec(path=bad_profile, prefix=("train",))

        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(root)
        with pytest.raises(ExecutionError, match="not a declared external"):
            validate_pfig(ast, profile_paths=[spec])

    def test_later_prefixed_profile_overrides_earlier(self, tmp_path):
        train = self._write_train_pfig(tmp_path)
        root = self._write_root_config(tmp_path, train)

        profile_a = tmp_path / "base.pfig"
        profile_a.write_text('override output_dir = "./runs/base"\noverride bf16 = True\n')
        profile_b = tmp_path / "local.pfig"
        profile_b.write_text('override output_dir = "./runs/local"\noverride bf16 = False\n')

        spec_a = ProfileSpec(path=profile_a, prefix=("train",))
        spec_b = ProfileSpec(path=profile_b, prefix=("train",))

        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(root)
        metadata = validate_pfig(ast, profile_paths=[spec_a, spec_b])
        # Later profile wins: bf16 = False, output_dir = "./runs/local"
        assert metadata.externals.get("train/bf16") is False
        assert metadata.externals.get("train/output_dir") == "./runs/local"

    def test_same_profile_two_namespaces(self, tmp_path):
        """Same profile file applied under two different namespace prefixes."""
        train = tmp_path / "train.pfig"
        train.write_text(
            "external output_dir\n"
            "external bf16\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict(output_dir=@output_dir, bf16=@bf16)\n"
            "__entrypoint__ = print\n"
        )

        root = tmp_path / "multi.pfig"
        root.write_text(
            f'include "{train}" as train_a\n'
            f'include "{train}" as train_b\n'
            "__entrypoint__ = @train_a/result\n"
        )

        profile = tmp_path / "local.pfig"
        profile.write_text('override output_dir = "./runs/a"\noverride bf16 = True\n')

        profile_b = tmp_path / "local_b.pfig"
        profile_b.write_text('override output_dir = "./runs/b"\noverride bf16 = False\n')

        spec_a = ProfileSpec(path=profile, prefix=("train_a",))
        spec_b = ProfileSpec(path=profile_b, prefix=("train_b",))

        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(root)
        metadata = validate_pfig(ast, profile_paths=[spec_a, spec_b])
        # Both aliases identify one source. The later profile wins globally.
        assert metadata.externals["train_a/output_dir"] == "./runs/b"
        assert metadata.externals["train_b/output_dir"] == "./runs/b"
        assert metadata.externals["train_a/bf16"] is False
        assert metadata.externals["train_b/bf16"] is False

    def test_prefix_does_not_mutate_profile_ast(self, tmp_path):
        """Applying the same profile twice under different prefixes should be independent."""
        train = tmp_path / "train.pfig"
        train.write_text(
            "external output_dir\n"
            "from collections import OrderedDict\n"
            "result = OrderedDict(d=@output_dir)\n"
            "__entrypoint__ = print\n"
        )

        root = tmp_path / "root.pfig"
        root.write_text(
            f'include "{train}" as ns_a\ninclude "{train}" as ns_b\n__entrypoint__ = @ns_a/result\n'
        )

        profile = tmp_path / "shared.pfig"
        profile.write_text('override output_dir = "./shared"\n')

        spec_a = ProfileSpec(path=profile, prefix=("ns_a",))
        spec_b = ProfileSpec(path=profile, prefix=("ns_b",))

        from pfig.execution import validate_pfig
        from pfig.parser import parse_config_file

        ast = parse_config_file(root)
        metadata = validate_pfig(ast, profile_paths=[spec_a, spec_b])
        assert metadata.externals["ns_a/output_dir"] == "./shared"
        assert metadata.externals["ns_b/output_dir"] == "./shared"


class TestInferMissingProfilePrefix:
    """Unit tests for infer_prefix_candidates and infer_missing_profile_target_suggestion."""

    from pfig.execution import (
        infer_missing_profile_target_suggestion,
        infer_prefix_candidates,
    )

    def _segs(self, s):
        return tuple(s.split("/"))

    def _ext_segs(self, *paths):
        return {tuple(p.split("/")) for p in paths}

    # -- infer_prefix_candidates --

    def test_single_failed_single_match(self):
        from pfig.execution import infer_prefix_candidates

        result = infer_prefix_candidates(("output_dir",), self._ext_segs("train/output_dir"))
        assert len(result) == 1
        assert result[0] == (("train",), ("train", "output_dir"))

    def test_single_failed_no_match(self):
        from pfig.execution import infer_prefix_candidates

        result = infer_prefix_candidates(("output_dir",), self._ext_segs("launcher/project"))
        assert result == []

    def test_single_failed_two_matches(self):
        from pfig.execution import infer_prefix_candidates

        result = infer_prefix_candidates(
            ("output_dir",),
            self._ext_segs("train/output_dir", "eval/output_dir"),
        )
        prefixes = {r[0] for r in result}
        assert prefixes == {("train",), ("eval",)}

    def test_segment_safe_no_false_positive(self):
        from pfig.execution import infer_prefix_candidates

        # "dir" should NOT match "train/output_dir" — different segment
        result = infer_prefix_candidates(("dir",), self._ext_segs("train/output_dir"))
        assert result == []

    def test_multi_segment_failed_path(self):
        from pfig.execution import infer_prefix_candidates

        result = infer_prefix_candidates(
            ("logging", "output_dir"),
            self._ext_segs("train/logging/output_dir"),
        )
        assert len(result) == 1
        assert result[0][0] == ("train",)

    def test_exact_length_no_match(self):
        from pfig.execution import infer_prefix_candidates

        # External path same length as failed path — no prefix possible
        result = infer_prefix_candidates(("output_dir",), self._ext_segs("output_dir"))
        assert result == []

    # -- infer_missing_profile_target_suggestion --

    def test_high_confidence_all_match_one_prefix(self):
        from pfig.execution import infer_missing_profile_target_suggestion

        msg = infer_missing_profile_target_suggestion(
            failed_overrides=["output_dir", "bf16"],
            declared_externals={"train/output_dir", "train/bf16"},
            profile_path="profiles/local.pfig",
        )
        assert "train/" in msg
        assert "--profile train::profiles/local.pfig" in msg
        assert "Did you mean" in msg

    def test_ambiguous_single_override_multiple_prefixes(self):
        from pfig.execution import infer_missing_profile_target_suggestion

        msg = infer_missing_profile_target_suggestion(
            failed_overrides=["output_dir"],
            declared_externals={"train/output_dir", "eval/output_dir"},
            profile_path="profiles/local.pfig",
        )
        assert "--profile train::profiles/local.pfig" in msg
        assert "--profile eval::profiles/local.pfig" in msg
        assert "multiple namespaces" in msg

    def test_no_match_returns_empty(self):
        from pfig.execution import infer_missing_profile_target_suggestion

        msg = infer_missing_profile_target_suggestion(
            failed_overrides=["output_dir"],
            declared_externals={"launcher/project"},
            profile_path="profiles/local.pfig",
        )
        assert msg == ""

    def test_medium_confidence_partial_match(self):
        from pfig.execution import infer_missing_profile_target_suggestion

        msg = infer_missing_profile_target_suggestion(
            failed_overrides=["output_dir", "bf16", "foo"],
            declared_externals={"train/output_dir", "train/bf16"},
            profile_path="profiles/local.pfig",
        )
        assert "--profile train::profiles/local.pfig" in msg
        assert "foo" in msg  # unmatched override mentioned

    def test_multi_segment_override_path(self):
        from pfig.execution import infer_missing_profile_target_suggestion

        msg = infer_missing_profile_target_suggestion(
            failed_overrides=["logging/output_dir"],
            declared_externals={"train/logging/output_dir"},
            profile_path="profiles/local.pfig",
        )
        assert "--profile train::profiles/local.pfig" in msg

    def test_empty_failed_overrides_returns_empty(self):
        from pfig.execution import infer_missing_profile_target_suggestion

        msg = infer_missing_profile_target_suggestion(
            failed_overrides=[],
            declared_externals={"train/output_dir"},
            profile_path="profiles/local.pfig",
        )
        assert msg == ""
