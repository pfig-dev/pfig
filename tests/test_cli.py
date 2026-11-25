import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pfig import ExecutionError, ParseError, ProfileSpec
from pfig.cli import (
    _style_unified_diff,
    cmd_run,
    cmd_validate,
    cmd_view,
    create_parser,
    main,
    parse_profile_arg,
    parse_set_args,
)
from pfig.diff import NamespaceAlignment
from pfig.lexer import Position


def _mock_metadata(entrypoint=None, externals=None, **kwargs):
    mock_result = MagicMock()
    mock_result.compiled = {
        "values": {},
        "providers": {},
        "runtime_inputs": externals or {},
        "runtime_sources": {},
    }
    entrypoint_name = entrypoint or "__entrypoint__"
    mock_result.entrypoint_name = entrypoint_name
    mock_result.reachable_names = set()
    mock_result.materialized = {entrypoint_name: lambda: None}
    mock_result.externals = externals or {}
    mock_result.included_files = []
    mock_result.result = None
    return mock_result


# ---------- unit: --set parser ----------


@pytest.mark.parametrize("values", [["x=1", "x=2"], ["x=1", "x=True"], ["x=[1]", "x=[True]"]])
def test_parse_set_args_conflicts(values):
    with pytest.raises(ValueError, match="Conflicting --set assignments"):
        parse_set_args(values)


def test_parse_set_args_identical_assignments():
    assert parse_set_args(["x=2", "x=2"]) == {"x": 2}


def test_parse_set_args_types():
    vals = parse_set_args(
        [
            "bool_true=true",
            "bool_false=False",
            "none_val=None",
            "int_val=42",
            "float_val=3.14",
            "str_val=hello",
            "spaces=  7  ",
        ]
    )
    assert vals == {
        "bool_true": True,
        "bool_false": False,
        "none_val": None,
        "int_val": 42,
        "float_val": 3.14,
        "str_val": "hello",
        "spaces": 7,
    }


def test_parse_set_args_invalid():
    with pytest.raises(ValueError):
        parse_set_args(["novalue"])


def test_style_unified_diff_uses_terminal_palette_roles():
    lines = [
        "--- a.pfig\n",
        "+++ b.pfig\n",
        "@@ -1 +1 @@\n",
        "-old\n",
        "+new\n",
        " context\n",
    ]

    assert list(_style_unified_diff(lines, color=True)) == [
        "\x1b[31m--- a.pfig\n\x1b[0m",
        "\x1b[32m+++ b.pfig\n\x1b[0m",
        "\x1b[36m@@ -1 +1 @@\n\x1b[0m",
        "\x1b[31m-old\n\x1b[0m",
        "\x1b[32m+new\n\x1b[0m",
        " context\n",
    ]


def test_style_unified_diff_leaves_lines_plain_when_color_disabled():
    lines = ["--- a.pfig\n", "+new\n"]

    assert list(_style_unified_diff(lines, color=False)) == lines


def _args(namespace_dict):
    ns = types.SimpleNamespace(**namespace_dict)
    # default fields expected by cmd_* paths
    ns.verbose = getattr(ns, "verbose", 0)
    ns.set_args = getattr(ns, "set_args", None)
    ns.set_values = getattr(ns, "set_values", {})
    ns.profiles = getattr(ns, "profiles", None)
    ns.entrypoint = getattr(ns, "entrypoint", None)
    return ns


# ---------- wiring/args via create_parser + main ----------


def test_validate_wires_profiles_and_set(monkeypatch):
    from unittest.mock import MagicMock

    # capture args passed to validate_pfig
    seen = {}
    monkeypatch.setattr("pfig.cli.load_config_file", lambda p: "src")
    monkeypatch.setattr("pfig.cli.parse_pfig_source", lambda s, filename=None: "AST")

    def recorder(
        ast,
        entrypoint=None,
        filename=None,
        externals=None,
        profile_paths=None,
        strict=False,
        allow_missing_externals=False,
        **kwargs,
    ):
        seen["entrypoint"] = entrypoint
        seen["externals"] = externals
        seen["profiles"] = profile_paths
        seen["allow_missing_externals"] = allow_missing_externals
        # Return mock ExecutionMetadata
        mock_metadata = MagicMock()
        mock_metadata.entrypoint_name = "ep"
        mock_metadata.reachable_names = set()
        mock_metadata.included_files = []
        return mock_metadata

    monkeypatch.setattr("pfig.cli.validate_pfig", recorder)

    parser = create_parser()
    args = parser.parse_args(
        [
            "validate",
            "cfg.pfig",
            "--profile",
            "p1.pfig",
            "--profile",
            "p2.pfig",
            "--set",
            "device=cuda",
            "epochs=2",
            "--entrypoint",
            "trainer.fit",
        ]
    )
    # emulate main() preprocessing
    args.set_values = parse_set_args(args.set_args or [])
    rc = args.func(args)
    assert rc == 0
    assert seen["profiles"] == ["p1.pfig", "p2.pfig"]
    assert seen["externals"] == {"device": "cuda", "epochs": 2}
    assert seen["entrypoint"] == "trainer.fit"
    assert seen["allow_missing_externals"] is False


def test_validate_allow_missing_externals_flag(monkeypatch):
    """Test that --allow-missing-externals flag is passed to validate_pfig."""
    from unittest.mock import MagicMock

    seen = {}
    monkeypatch.setattr("pfig.cli.load_config_file", lambda p: "src")
    monkeypatch.setattr("pfig.cli.parse_pfig_source", lambda s, filename=None: "AST")

    def recorder(
        ast,
        entrypoint=None,
        filename=None,
        externals=None,
        profile_paths=None,
        strict=False,
        allow_missing_externals=False,
        **kwargs,
    ):
        seen["allow_missing_externals"] = allow_missing_externals
        mock_metadata = MagicMock()
        mock_metadata.entrypoint_name = "ep"
        mock_metadata.reachable_names = set()
        mock_metadata.included_files = []
        return mock_metadata

    monkeypatch.setattr("pfig.cli.validate_pfig", recorder)

    parser = create_parser()
    args = parser.parse_args(
        [
            "validate",
            "cfg.pfig",
            "--allow-missing-externals",
        ]
    )
    # emulate main() preprocessing
    args.set_values = parse_set_args(args.set_args or [])
    rc = args.func(args)
    assert rc == 0
    assert seen["allow_missing_externals"] is True


def test_view_parser_wires_graph_options():
    parser = create_parser()
    args = parser.parse_args(
        [
            "view",
            "cfg.pfig",
            "--mode",
            "all",
            "--no-open",
            "--host",
            "localhost",
            "--port",
            "8123",
            "--reveal-url",
            "http://127.0.0.1:9000/reveal?token=abc",
            "--profile",
            "p1.pfig",
            "--set",
            "device=cuda",
            "--strict",
            "--allow-missing-externals",
        ]
    )

    assert args.func is cmd_view
    assert args.config == "cfg.pfig"
    assert args.mode == "all"
    assert args.no_open is True
    assert args.host == "localhost"
    assert args.port == 8123
    assert args.reveal_url == "http://127.0.0.1:9000/reveal?token=abc"
    assert args.profiles == ["p1.pfig"]
    assert args.set_args == ["device=cuda"]
    assert args.strict is True
    assert args.allow_missing_externals is True


@pytest.mark.parametrize(
    ("flags", "expected"),
    [([], 0), (["--no-align-namespace"], 3), (["--align-namespace", "wrapped=/"], 0)],
)
def test_diff_cli_namespace_alignment(monkeypatch, tmp_path, flags, expected):
    (tmp_path / "shared.pfig").write_text(
        "from collections import OrderedDict\nrun = OrderedDict()\n"
    )
    base = tmp_path / "base.pfig"
    proposed = tmp_path / "proposed.pfig"
    (tmp_path / "wrapper.pfig").write_text('include "shared.pfig" as module\n')
    base.write_text('include "wrapper.pfig" as wrapped\n__entrypoint__ = @wrapped/module/run\n')
    proposed.write_text('include "shared.pfig" as module\n__entrypoint__ = @module/run\n')
    monkeypatch.setattr(sys, "argv", ["pfig", "diff", str(base), str(proposed), *flags])
    assert main() == expected


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("x = 1", "x = 1\nexternal new_input", 3),
        ("x = 1", "x = True", 3),
        ("x = 1\ny = @x + 1", "# comment\nx = 1\ny = @x + 1", 0),
    ],
)
def test_diff_exact_paths_preserve_semantics(monkeypatch, tmp_path, before, after, expected):
    base, proposed = tmp_path / "base.pfig", tmp_path / "proposed.pfig"
    base.write_text(before + "\n")
    proposed.write_text(after + "\n")
    monkeypatch.setattr(
        sys, "argv", ["pfig", "diff", str(base), str(proposed), "--no-align-namespace"]
    )
    assert main() == expected


@pytest.mark.parametrize(
    ("command", "base", "alignment"),
    [
        (["view", "cfg.pfig"], None, "auto"),
        (
            ["diff-view", "old.pfig", "cfg.pfig", "--align-namespace", "wrapped=/"],
            "old.pfig",
            NamespaceAlignment.parse("wrapped=/"),
        ),
    ],
)
def test_cmd_view_calls_vis_server(monkeypatch, capsys, tmp_path, command, base, alignment):
    import importlib.util

    seen = {}
    module_path = tmp_path / "view_local_module.py"
    module_path.write_text("VALUE = 42\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Console scripts do not automatically put the working directory on sys.path.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p not in ("", str(tmp_path))])
    assert importlib.util.find_spec("view_local_module") is None

    class DummyServer:
        url = "http://127.0.0.1:1234/"

        def shutdown(self):
            seen["shutdown"] = True

    def fake_serve_graph(*args, **kwargs):
        spec = importlib.util.find_spec("view_local_module")
        assert spec is not None
        assert spec.origin == str(module_path)
        seen["args"] = args
        seen["kwargs"] = kwargs
        return DummyServer()

    class DummyEvent:
        def wait(self):
            seen["waited"] = True

    monkeypatch.setattr("pfig.vis.serve_graph", fake_serve_graph)
    monkeypatch.setattr("threading.Event", lambda: DummyEvent())

    args = create_parser().parse_args(
        [
            *command,
            "--mode",
            "all",
            "--host",
            "localhost",
            "--port",
            "8123",
            "--no-open",
            "--profile",
            "p1.pfig",
            "--set",
            "device=cuda",
            "--allow-missing-externals",
            "--strict",
            "--reveal-url",
            "http://127.0.0.1:9000/reveal?token=abc",
        ]
    )
    args.set_values = parse_set_args(args.set_args)
    assert args.func(args) == 0
    assert capsys.readouterr().out == "http://127.0.0.1:1234/\n"
    assert seen["args"] == ("cfg.pfig",)
    assert seen["kwargs"] == {
        "mode": "all",
        "host": "localhost",
        "port": 8123,
        "open_browser": False,
        "block": False,
        "profiles": ["p1.pfig"],
        "set_overrides": {"device": "cuda"},
        "allow_missing_externals": True,
        "strict_signatures": True,
        "reveal_url": "http://127.0.0.1:9000/reveal?token=abc",
        "compare_to": base,
        "alignment": alignment,
    }
    assert seen["waited"] is True
    assert seen["shutdown"] is True


def test_run_wires_profiles_and_set(monkeypatch):
    seen = {}
    monkeypatch.setattr("pfig.cli.load_config_file", lambda p: "src")
    monkeypatch.setattr("pfig.cli.parse_pfig_source", lambda s, filename=None: "AST")

    def validate_recorder(ast, **kwargs):
        seen["entrypoint"] = kwargs.get("entrypoint")
        seen["externals"] = kwargs.get("externals")
        seen["profiles"] = kwargs.get("profile_paths")
        return _mock_metadata(**kwargs)

    def execute_recorder(metadata, materializer):
        seen["executed"] = metadata.entrypoint_name

    monkeypatch.setattr("pfig.cli.validate_pfig", validate_recorder)
    monkeypatch.setattr("pfig.cli._execute_validated", execute_recorder)

    parser = create_parser()
    args = parser.parse_args(
        [
            "run",
            "cfg.pfig",
            "--profile",
            "p1.pfig",
            "--profile",
            "p2.pfig",
            "--set",
            "device=cuda",
            "epochs=2",
            "--entrypoint",
            "trainer.fit",
            "--no-snapshot",
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])
    rc = args.func(args)
    assert rc == 0
    assert seen["executed"] == "trainer.fit"
    assert seen["profiles"] == ["p1.pfig", "p2.pfig"]
    assert seen["externals"] == {"device": "cuda", "epochs": 2}
    assert seen["entrypoint"] == "trainer.fit"


def test_cmd_run_parse_error_code_and_message(monkeypatch, capsys):
    monkeypatch.setattr("pfig.cli.load_config_file", lambda p: "source")

    def raise_parse(*a, **k):
        raise ParseError("boom", Position("<test>", 1, 1))

    monkeypatch.setattr("pfig.cli.parse_pfig_source", raise_parse)

    args = _args({"config": "cfg.pfig"})
    rc = cmd_run(args)
    err = capsys.readouterr().err
    assert rc == 1


def test_cmd_run_unexpected_exception_exit_code(monkeypatch, capsys):
    def raise_unexpected(*a, **k):
        raise RuntimeError("oops")

    monkeypatch.setattr("pfig.cli.load_config_file", raise_unexpected)

    args = _args({"config": "cfg.pfig"})
    rc = cmd_run(args)
    err = capsys.readouterr().err
    assert rc == 3


def test_parse_set_args_scientific_and_negative():
    vals = parse_set_args(["lr=1e-3", "neg=-5"])
    assert vals["lr"] == pytest.approx(1e-3)
    assert vals["neg"] == -5


def test_main_invalid_set_arg_surfaces_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pfig", "validate", "cfg.pfig", "--set", "novalue"])
    rc = main()
    err = capsys.readouterr().err
    assert rc == 1


def test_multiple_set_values_single_flag():
    """Test parsing multiple key=value pairs after a single --set flag."""
    parser = create_parser()
    args = parser.parse_args(
        [
            "validate",
            "cfg.pfig",
            "--set",
            "device=cuda",
            "lr=0.001",
            "batch_size=32",
            "epochs=10",
            "use_dropout=true",
        ]
    )
    set_values = parse_set_args(args.set_args)
    assert set_values == {
        "device": "cuda",
        "lr": 0.001,
        "batch_size": 32,
        "epochs": 10,
        "use_dropout": True,
    }


def test_multiple_set_flags():
    """Test using multiple --set flags with one key=value pair each."""
    parser = create_parser()
    # Note: With nargs="+", each --set consumes all following values until the next flag
    # So we need to carefully structure the args
    args = parser.parse_args(
        [
            "run",
            "cfg.pfig",
            "--set",
            "device=cuda",
            "--set",
            "lr=0.001",
            "--set",
            "batch_size=32",
        ]
    )
    # When using multiple --set flags, only the last one is captured with nargs="+"
    # This is expected argparse behavior - we should use a single --set with multiple values
    set_values = parse_set_args(args.set_args)
    # With the current implementation, only the last --set flag's values are kept
    # This test documents the current behavior
    assert "batch_size" in set_values
    assert set_values["batch_size"] == 32


def test_multiple_set_values_mixed_types():
    """Test multiple --set values with different types including negative numbers and scientific notation."""
    vals = parse_set_args(
        [
            "lr=1e-4",
            "weight_decay=5e-5",
            "dropout=0.5",
            "hidden_size=256",
            "layers=3",
            "use_cuda=true",
            "seed=-1",
            "model_name=resnet50",
            "checkpoint=None",
        ]
    )
    assert vals == {
        "lr": pytest.approx(1e-4),
        "weight_decay": pytest.approx(5e-5),
        "dropout": 0.5,
        "hidden_size": 256,
        "layers": 3,
        "use_cuda": True,
        "seed": -1,
        "model_name": "resnet50",
        "checkpoint": None,
    }


def test_run_with_multiple_set_values(monkeypatch):
    """Integration test: run command with multiple --set values."""
    seen = {}
    monkeypatch.setattr("pfig.cli.load_config_file", lambda p: "src")
    monkeypatch.setattr("pfig.cli.parse_pfig_source", lambda s, filename=None: "AST")

    def validate_recorder(ast, **kwargs):
        seen["externals"] = kwargs.get("externals")
        return _mock_metadata(**kwargs)

    def execute_recorder(metadata, materializer):
        seen["executed"] = metadata.entrypoint_name

    monkeypatch.setattr("pfig.cli.validate_pfig", validate_recorder)
    monkeypatch.setattr("pfig.cli._execute_validated", execute_recorder)

    parser = create_parser()
    args = parser.parse_args(
        [
            "run",
            "cfg.pfig",
            "--set",
            "device=cuda",
            "lr=1e-3",
            "batch_size=64",
            "epochs=100",
            "use_wandb=false",
            "seed=42",
            "--no-snapshot",
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])
    rc = args.func(args)
    assert rc == 0
    assert seen["executed"] == "__entrypoint__"
    assert seen["externals"] == {
        "device": "cuda",
        "lr": pytest.approx(1e-3),
        "batch_size": 64,
        "epochs": 100,
        "use_wandb": False,
        "seed": 42,
    }


def test_set_values_mixing_externals_and_regular_overrides(tmp_path):
    """
    Test providing --set with both externals and regular value overrides.

    Per spec §12, --set can supply externals, override values, or rebind providers.
    This test verifies that --set can handle both externals and regular value overrides
    in the same command.
    """
    # Create a test pfig file with some externals and some regular values
    config_file = tmp_path / "test.pfig"
    config_file.write_text(
        """
external device
external batch_size

# Regular values (not externals)
lr = 0.01
epochs = 50

result = {
    "device": @device,
    "batch_size": @batch_size,
    "lr": @lr,
    "epochs": @epochs
}

__entrypoint__ = print
"""
    )

    parser = create_parser()
    # Provide --set with both externals (device, batch_size) and regular overrides (lr, epochs)
    args = parser.parse_args(
        [
            "validate",
            str(config_file),
            "--set",
            "device=cuda",  # external
            "batch_size=32",  # external
            "lr=0.001",  # regular value override
            "epochs=100",  # regular value override
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])

    # Execute and check what happens
    rc = args.func(args)

    # Should succeed - --set can override both externals and regular values
    assert rc == 0


def test_set_only_regular_values_no_externals(tmp_path):
    """
    Test providing --set to override regular values when there are NO externals.

    Per spec §12, --set can override any value, not just externals.
    This should work fine even when no externals are declared.
    """
    config_file = tmp_path / "test.pfig"
    config_file.write_text(
        """
# No externals declared - just regular values
lr = 0.01
batch_size = 16
epochs = 50

result = {
    "lr": @lr,
    "batch_size": @batch_size,
    "epochs": @epochs
}

__entrypoint__ = print
"""
    )

    parser = create_parser()
    args = parser.parse_args(
        [
            "validate",
            str(config_file),
            "--set",
            "lr=0.001",
            "batch_size=32",
            "epochs=100",
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])

    rc = args.func(args)

    # Should succeed - --set can override regular values
    assert rc == 0


def test_set_only_externals_with_regular_values_present(tmp_path):
    """
    Test providing --set ONLY for externals when regular values also exist.

    CURRENT BEHAVIOR: This SUCCEEDS because we're only setting declared externals
    and not trying to override regular values. The regular values use their
    default values from the config file.
    """
    config_file = tmp_path / "test.pfig"
    config_file.write_text(
        """
external device
external num_workers

# Regular values
lr = 0.01
batch_size = 16

result = {
    "device": @device,
    "num_workers": @num_workers,
    "lr": @lr,
    "batch_size": @batch_size
}

__entrypoint__ = print
"""
    )

    parser = create_parser()
    # Only set the externals, don't try to override regular values
    args = parser.parse_args(
        [
            "validate",
            str(config_file),
            "--set",
            "device=cuda",
            "num_workers=4",
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])

    rc = args.func(args)

    # This succeeds since we're only providing declared externals
    assert rc == 0


def test_set_values_all_declared_externals_succeeds(tmp_path):
    """Test that providing --set values that are all declared externals succeeds."""
    # Create a test pfig file with all externals declared
    config_file = tmp_path / "test.pfig"
    config_file.write_text(
        """
external device
external batch_size
external lr
external epochs

result = {
    "device": @device,
    "batch_size": @batch_size,
    "lr": @lr,
    "epochs": @epochs
}

__entrypoint__ = print
"""
    )

    parser = create_parser()
    args = parser.parse_args(
        [
            "validate",
            str(config_file),
            "--set",
            "device=cuda",
            "batch_size=32",
            "lr=0.001",
            "epochs=100",
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])

    # This should succeed since all --set values are declared externals
    rc = args.func(args)

    assert rc == 0


def test_set_values_missing_declared_externals_fails(tmp_path, monkeypatch):
    """Test that missing some declared externals produces an error."""
    # Create a test pfig file with externals
    config_file = tmp_path / "test.pfig"
    config_file.write_text(
        """
external device
external batch_size
external lr
external epochs

result = {
    "device": @device,
    "batch_size": @batch_size,
    "lr": @lr,
    "epochs": @epochs
}

__entrypoint__ = @result.copy
"""
    )

    parser = create_parser()
    # Only provide some of the declared externals
    args = parser.parse_args(
        [
            "validate",
            str(config_file),
            "--set",
            "device=cuda",
            "batch_size=32",
            # Missing lr and epochs
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])

    errors: list[str] = []

    def record_error(msg, *args, **kwargs):
        errors.append(str(msg))

    monkeypatch.setattr("pfig.cli.logger.error", record_error)

    # This should fail because lr and epochs are missing
    rc = args.func(args)

    assert rc == 1
    # Each missing external should be reported as a validation issue
    combined = "\n".join(errors)
    assert "Missing external value" in combined
    assert "lr" in combined
    assert "epochs" in combined


def test_set_values_nonexistent_binding_fails(tmp_path):
    """Test that trying to override a non-existent binding with --set fails."""
    config_file = tmp_path / "test.pfig"
    config_file.write_text(
        """
lr = 0.01
batch_size = 16

result = {
    "lr": @lr,
    "batch_size": @batch_size
}

__entrypoint__ = print
"""
    )

    parser = create_parser()
    args = parser.parse_args(
        [
            "validate",
            str(config_file),
            "--set",
            "lr=0.001",
            "nonexistent=42",  # This binding doesn't exist
        ]
    )
    args.set_values = parse_set_args(args.set_args or [])

    # This should fail because 'nonexistent' doesn't exist in the config
    rc = args.func(args)

    assert rc == 1


def test_parse_set_args_complex_structures():
    """
    Test that --set parses complex structures (lists, dicts) correctly.

    This ensures parity between config files and CLI overrides.
    Current behavior (Bug): Returns the string "[1, 2, 3]"
    Desired behavior (Fix): Returns the list [1, 2, 3]
    """
    vals = parse_set_args(
        [
            "list_int=[1, 2, 3]",
            "list_str=['a', 'b']",
            "dict_val={'x': 10, 'y': 20}",
            "nested=[[1, 2], [3, 4]]",
        ]
    )

    # Verify Lists
    assert vals["list_int"] == [1, 2, 3]
    assert isinstance(vals["list_int"], list)

    assert vals["list_str"] == ["a", "b"]

    # Verify Dicts
    assert vals["dict_val"] == {"x": 10, "y": 20}
    assert isinstance(vals["dict_val"], dict)

    # Verify Nested structures
    assert vals["nested"] == [[1, 2], [3, 4]]


@pytest.mark.parametrize("command", ["run", "validate", "bundle", "view"])
def test_mount_option_is_rejected(command):
    with pytest.raises(SystemExit) as error:
        create_parser().parse_args([command, "cfg.pfig", "--mount", "lib=./lib"])
    assert error.value.code == 2


# ===== __snapshot__ directive resolution tests =====


class TestResolveSnapshotDirective:
    """Tests for _resolve_snapshot_directive function."""

    def test_resolve_literal_string(self):
        """Test resolving __snapshot__ = "./snapshots" (literal string)."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from pfig.cli import _resolve_snapshot_directive
        from pfig.execution import LiteralValue

        metadata = MagicMock()
        metadata.materialized = {}
        metadata.compiled = {"values": {"__snapshot__": LiteralValue("./my_snapshots")}}

        result = _resolve_snapshot_directive(metadata)

        assert result == Path("./my_snapshots")

    def test_resolve_materialized_string(self):
        """Test resolving __snapshot__ from materialized values (e.g., os.path.join)."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from pfig.cli import _resolve_snapshot_directive

        metadata = MagicMock()
        metadata.materialized = {"__snapshot__": "/computed/path/snapshots"}
        metadata.compiled = {"values": {}}

        result = _resolve_snapshot_directive(metadata)

        assert result == Path("/computed/path/snapshots")

    def test_resolve_provider_ref_from_externals(self):
        """Test resolving __snapshot__ = @output_dir (external reference)."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from pfig.cli import _resolve_snapshot_directive
        from pfig.execution import ProviderRef

        metadata = MagicMock()
        metadata.materialized = {}
        metadata.externals = {"output_dir": "./runs/snapshots"}
        metadata.compiled = {"values": {"__snapshot__": ProviderRef("output_dir")}}

        result = _resolve_snapshot_directive(metadata)

        assert result == Path("./runs/snapshots")

    def test_resolve_provider_ref_from_materialized(self):
        """Test resolving __snapshot__ = @some_binding (materialized reference)."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from pfig.cli import _resolve_snapshot_directive
        from pfig.execution import ProviderRef

        metadata = MagicMock()
        metadata.materialized = {"snapshot_path": "/resolved/snapshot/dir"}
        metadata.externals = {}
        metadata.compiled = {"values": {"__snapshot__": ProviderRef("snapshot_path")}}

        result = _resolve_snapshot_directive(metadata)

        assert result == Path("/resolved/snapshot/dir")

    def test_resolve_provider_ref_from_compiled_literal(self):
        """Test resolving __snapshot__ = @path where path is a literal value."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from pfig.cli import _resolve_snapshot_directive
        from pfig.execution import LiteralValue, ProviderRef

        metadata = MagicMock()
        metadata.materialized = {}
        metadata.externals = {}
        metadata.compiled = {
            "values": {
                "__snapshot__": ProviderRef("base_path"),
                "base_path": LiteralValue("./base/snapshots"),
            }
        }

        result = _resolve_snapshot_directive(metadata)

        assert result == Path("./base/snapshots")

    def test_resolve_missing_snapshot_directive(self):
        """Test that missing __snapshot__ returns None."""
        from unittest.mock import MagicMock

        from pfig.cli import _resolve_snapshot_directive

        metadata = MagicMock()
        metadata.materialized = {}
        metadata.compiled = {"values": {}}

        result = _resolve_snapshot_directive(metadata)

        assert result is None

    def test_resolve_non_string_value_returns_none(self):
        """Test that non-string materialized values return None."""
        from unittest.mock import MagicMock

        from pfig.cli import _resolve_snapshot_directive

        metadata = MagicMock()
        metadata.materialized = {"__snapshot__": 12345}  # Not a string
        metadata.compiled = {"values": {}}

        result = _resolve_snapshot_directive(metadata)

        assert result is None


# ---------- bundle CLI commands ----------


class TestBundleCLI:
    """Tests for pfig bundle and pfig run --bundle commands."""

    def test_cmd_bundle_creates_tarball(self, tmp_path):
        """Test that cmd_bundle creates a bundle tarball."""
        import tempfile

        from pfig.cli import cmd_bundle, create_parser, parse_set_args

        # Create a simple config file
        config_file = tmp_path / "main.pfig"
        config_file.write_text(
            """
from collections import OrderedDict
data = OrderedDict(a=1)
__entrypoint__ = @data.keys
"""
        )

        output_path = tmp_path / "output.tar.gz"

        parser = create_parser()
        args = parser.parse_args(["bundle", str(config_file), "-o", str(output_path)])
        args.set_values = parse_set_args(args.set_args or [])

        result = cmd_bundle(args)

        assert result == 0
        assert output_path.exists()

    def test_cmd_bundle_creates_directory(self, tmp_path):
        """Test that cmd_bundle creates a bundle directory with -d flag."""
        from pfig.cli import cmd_bundle, create_parser, parse_set_args

        config_file = tmp_path / "main.pfig"
        config_file.write_text(
            """
from collections import OrderedDict
data = OrderedDict(a=1)
__entrypoint__ = @data.keys
"""
        )

        output_path = tmp_path / "bundle_dir"

        parser = create_parser()
        args = parser.parse_args(["bundle", str(config_file), "-o", str(output_path), "-d"])
        args.set_values = parse_set_args(args.set_args or [])

        result = cmd_bundle(args)

        assert result == 0
        assert output_path.is_dir()
        assert (output_path / "bundle_manifest.json").exists()
        assert (output_path / "configs").is_dir()

    def test_cmd_run_bundle_from_directory_writes_snapshot(self, tmp_path, capsys, monkeypatch):
        """Test running from a bundle directory."""
        from pfig.bundle import CONFIGS_DIR, MANIFEST_FILE, BundleManifest
        from pfig.cli import cmd_run, create_parser, parse_set_args

        monkeypatch.chdir(tmp_path)

        # Create bundle directory structure
        bundle_dir = tmp_path / "bundle"
        configs_dir = bundle_dir / CONFIGS_DIR
        configs_dir.mkdir(parents=True)

        # Create entry config using valid pfig syntax
        entry_file = configs_dir / "main.pfig"
        entry_file.write_text(
            """
from collections import OrderedDict
data = OrderedDict(result="hello from bundle")
__entrypoint__ = @data.keys
"""
        )

        # Create manifest
        manifest = BundleManifest(
            bundle_version=1,
            entry=f"{CONFIGS_DIR}/main.pfig",
            files=[f"{CONFIGS_DIR}/main.pfig"],
        )
        (bundle_dir / MANIFEST_FILE).write_text(manifest.to_json())

        parser = create_parser()
        args = parser.parse_args(["run", "--bundle", str(bundle_dir)])
        args.set_values = parse_set_args(args.set_args or [])

        result = cmd_run(args)

        assert result == 0
        snapshots_dir = tmp_path / "snapshots"
        assert snapshots_dir.exists()
        snapshot_subdirs = [p for p in snapshots_dir.iterdir() if p.is_dir()]
        assert len(snapshot_subdirs) == 1
        assert (snapshot_subdirs[0] / "log.json").exists()

    def test_cmd_run_bundle_from_tarball_writes_snapshot(self, tmp_path, capsys, monkeypatch):
        """Test running from a bundle tarball."""
        from pfig.bundle import create_bundle
        from pfig.cli import cmd_run, create_parser, parse_set_args

        monkeypatch.chdir(tmp_path)

        # Create a config file using valid pfig syntax
        config_file = tmp_path / "main.pfig"
        config_file.write_text(
            """
from collections import OrderedDict
data = OrderedDict(result="hello from tarball")
__entrypoint__ = @data.keys
"""
        )

        # Create bundle tarball
        bundle_path = tmp_path / "bundle.tar.gz"
        create_bundle(
            file_origins={},
            entry_path=config_file,
            output_path=bundle_path,
            as_tarball=True,
        )

        parser = create_parser()
        args = parser.parse_args(["run", "--bundle", str(bundle_path)])
        args.set_values = parse_set_args(args.set_args or [])

        result = cmd_run(args)

        assert result == 0
        snapshots_dir = tmp_path / "snapshots"
        assert snapshots_dir.exists()
        snapshot_subdirs = [p for p in snapshots_dir.iterdir() if p.is_dir()]
        assert len(snapshot_subdirs) == 1
        assert (snapshot_subdirs[0] / "log.json").exists()

    def test_cmd_run_bundle_no_snapshot_flag_disables_snapshot(self, tmp_path, capsys, monkeypatch):
        """Test that --no-snapshot disables snapshotting in bundle mode."""
        from pfig.bundle import CONFIGS_DIR, MANIFEST_FILE, BundleManifest
        from pfig.cli import cmd_run, create_parser, parse_set_args

        monkeypatch.chdir(tmp_path)

        # Create bundle directory structure
        bundle_dir = tmp_path / "bundle"
        configs_dir = bundle_dir / CONFIGS_DIR
        configs_dir.mkdir(parents=True)

        # Create entry config using valid pfig syntax
        entry_file = configs_dir / "main.pfig"
        entry_file.write_text(
            """
from collections import OrderedDict
data = OrderedDict(result="hello from bundle")
__entrypoint__ = @data.keys
"""
        )

        # Create manifest
        manifest = BundleManifest(
            bundle_version=1,
            entry=f"{CONFIGS_DIR}/main.pfig",
            files=[f"{CONFIGS_DIR}/main.pfig"],
        )
        (bundle_dir / MANIFEST_FILE).write_text(manifest.to_json())

        parser = create_parser()
        args = parser.parse_args(["run", "--bundle", str(bundle_dir), "--no-snapshot"])
        args.set_values = parse_set_args(args.set_args or [])

        result = cmd_run(args)

        assert result == 0
        assert not (tmp_path / "snapshots").exists()

    def test_cmd_run_requires_config_or_bundle(self):
        """Test that cmd_run requires either config or --bundle."""
        from pfig.cli import cmd_run, create_parser, parse_set_args

        parser = create_parser()
        args = parser.parse_args(["run"])  # No config, no --bundle
        args.set_values = {}

        result = cmd_run(args)

        assert result == 1  # Should fail

    def test_bundle_with_relative_library_includes(self, tmp_path):
        from pfig.bundle import CONFIGS_DIR, BundleManifest
        from pfig.cli import cmd_bundle

        project = tmp_path / "project"
        library = tmp_path / "shared"
        project.mkdir()
        library.mkdir()
        main_file = project / "main.pfig"
        main_file.write_text('include "../shared/base.pfig" as base\nresult = @base/value')
        (library / "base.pfig").write_text("value = 42")
        output = tmp_path / "bundle"
        args = create_parser().parse_args(["bundle", str(main_file), "-o", str(output), "-d"])
        args.set_values = {}
        assert cmd_bundle(args) == 0
        manifest = BundleManifest.from_file(output / "bundle_manifest.json")
        assert manifest.entry == f"{CONFIGS_DIR}/project/main.pfig"
        assert (output / CONFIGS_DIR / "shared/base.pfig").exists()

    def test_cmd_bundle_without_externals(self, tmp_path):
        """Test that bundle creation works without providing externals.

        Bundling only needs to discover files, not execute the config,
        so externals should not be required at bundle-creation time.
        """
        from pfig.cli import cmd_bundle, create_parser, parse_set_args

        # Create a config with declared externals
        config_file = tmp_path / "main.pfig"
        config_file.write_text(
            """
external device
external data_dir

from collections import OrderedDict
settings = OrderedDict(device=@device, data_dir=@data_dir)
__entrypoint__ = @settings.keys
"""
        )

        output_path = tmp_path / "output.tar.gz"

        parser = create_parser()
        # Note: NOT providing --set for device or data_dir
        args = parser.parse_args(["bundle", str(config_file), "-o", str(output_path)])
        args.set_values = parse_set_args(args.set_args or [])

        # Should succeed without providing externals
        result = cmd_bundle(args)

        assert result == 0
        assert output_path.exists()


# ---------- install-rules CLI command ----------


class TestInstallRulesCursor:
    """Tests for pfig install-rules cursor command."""

    def test_installs_cursor_rules(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        args = create_parser().parse_args(["install-rules", "cursor"])
        assert cmd_install_rules(args) == 0

        rules_dir = tmp_path / ".cursor" / "rules"
        installed = sorted(f.name for f in rules_dir.glob("*.mdc"))
        assert "pfig-spec.mdc" in installed
        assert "pfig-developer-guide.mdc" in installed
        assert "pfig-friendly-python.mdc" in installed

    def test_force_overwrites(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        rules_dir = tmp_path / ".cursor" / "rules"
        rules_dir.mkdir(parents=True)
        sentinel = rules_dir / "pfig-spec.mdc"
        sentinel.write_text("custom")

        args = create_parser().parse_args(["install-rules", "cursor", "--force"])
        assert cmd_install_rules(args) == 0
        assert sentinel.read_text() != "custom"


class TestInstallRulesClaude:
    """Tests for pfig install-rules claude command."""

    def test_installs_claude_rules(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        args = create_parser().parse_args(["install-rules", "claude"])
        assert cmd_install_rules(args) == 0

        rules_dir = tmp_path / ".claude" / "rules"
        installed = sorted(f.name for f in rules_dir.glob("*.md"))
        assert "pfig-spec.md" in installed
        assert "pfig-developer-guide.md" in installed
        assert "pfig-friendly-python.md" in installed

    def test_skips_existing_without_force(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        sentinel = rules_dir / "pfig-spec.md"
        sentinel.write_text("custom")

        args = create_parser().parse_args(["install-rules", "claude"])
        assert cmd_install_rules(args) == 0
        assert sentinel.read_text() == "custom"

    def test_installed_files_have_paths_frontmatter(self, tmp_path, monkeypatch):
        """Claude rules use `paths:` frontmatter for conditional loading."""
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        args = create_parser().parse_args(["install-rules", "claude"])
        cmd_install_rules(args)

        rules_dir = tmp_path / ".claude" / "rules"
        for md_file in rules_dir.glob("*.md"):
            content = md_file.read_text()
            assert content.startswith("---"), f"{md_file.name} missing frontmatter"
            assert "paths:" in content, f"{md_file.name} missing paths"


class TestInstallRulesCodex:
    """Tests for pfig install-rules codex command (single-file AGENTS.md)."""

    def test_installs_single_agents_md(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        args = create_parser().parse_args(["install-rules", "codex"])
        assert cmd_install_rules(args) == 0

        agents = tmp_path / "AGENTS.md"
        assert agents.is_file()
        content = agents.read_text()
        # Managed region markers wrap the rendered bundle
        assert "<!-- BEGIN pfig install-rules -->" in content
        assert "<!-- END pfig install-rules -->" in content
        # All three sources concatenated
        assert "pfig Spec (Agent Reference)" in content
        assert "pfig Developer Guide" in content
        assert "pfig-Friendly Python Code" in content
        # No per-agent frontmatter or cross-file placeholders
        assert "globs:" not in content
        assert "paths:" not in content
        assert "__RULES_DIR__" not in content
        assert "__RULES_EXT__" not in content
        # See Also sections stripped (they only made sense as cross-file links)
        assert "## See Also" not in content

    def test_appends_to_existing_file_without_markers(self, tmp_path, monkeypatch):
        """Existing AGENTS.md with user content gets the managed region appended."""
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        agents = tmp_path / "AGENTS.md"
        user_content = "# My Project\n\nProject-specific guidance.\n"
        agents.write_text(user_content)

        args = create_parser().parse_args(["install-rules", "codex"])
        assert cmd_install_rules(args) == 0

        final = agents.read_text()
        assert final.startswith(user_content)
        assert "<!-- BEGIN pfig install-rules -->" in final
        assert "pfig Spec" in final

    def test_replaces_managed_region_in_place(self, tmp_path, monkeypatch):
        """Re-running install updates only the managed region; user content preserved."""
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        agents = tmp_path / "AGENTS.md"
        agents.write_text(
            "# My Project\n\nUser content above.\n\n"
            "<!-- BEGIN pfig install-rules -->\n"
            "stale pfig content from a previous version\n"
            "<!-- END pfig install-rules -->\n"
            "\nUser content below.\n"
        )

        args = create_parser().parse_args(["install-rules", "codex"])
        assert cmd_install_rules(args) == 0

        final = agents.read_text()
        assert "# My Project" in final
        assert "User content above." in final
        assert "User content below." in final
        assert "stale pfig content" not in final
        assert "pfig Spec" in final
        # Region appears exactly once (no duplicate sections on re-install)
        assert final.count("<!-- BEGIN pfig install-rules -->") == 1
        assert final.count("<!-- END pfig install-rules -->") == 1

    def test_unbalanced_markers_returns_error(self, tmp_path, monkeypatch):
        """A BEGIN with no END should fail loudly rather than silently corrupting."""
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# My Project\n<!-- BEGIN pfig install-rules -->\n(no end marker)\n")

        args = create_parser().parse_args(["install-rules", "codex"])
        assert cmd_install_rules(args) == 2
        # File untouched
        assert "(no end marker)" in agents.read_text()
        assert "pfig Spec" not in agents.read_text()


class TestRulesPlaceholderSubstitution:
    """Canonical sources use __RULES_DIR__ / __RULES_EXT__ placeholders that must be rewritten."""

    def test_no_placeholders_leak_into_cursor_output(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        args = create_parser().parse_args(["install-rules", "cursor"])
        cmd_install_rules(args)

        guide = (tmp_path / ".cursor" / "rules" / "pfig-developer-guide.mdc").read_text()
        assert "__RULES_DIR__" not in guide
        assert "__RULES_EXT__" not in guide
        assert "(.cursor/rules/pfig-spec.mdc)" in guide

    def test_no_placeholders_leak_into_claude_output(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        args = create_parser().parse_args(["install-rules", "claude"])
        cmd_install_rules(args)

        guide = (tmp_path / ".claude" / "rules" / "pfig-developer-guide.md").read_text()
        assert "__RULES_DIR__" not in guide
        assert "__RULES_EXT__" not in guide
        assert "(.claude/rules/pfig-spec.md)" in guide


class TestInstallRulesMissingAgent:
    """Calling install-rules without an agent should fail with a helpful error."""

    def test_missing_agent_returns_error(self, tmp_path, monkeypatch):
        from pfig.cli import cmd_install_rules, create_parser

        monkeypatch.chdir(tmp_path)
        args = create_parser().parse_args(["install-rules"])
        assert cmd_install_rules(args) == 2


# ---------- parse_profile_arg ----------


class TestParseProfileArg:
    def test_unprefixed(self):
        spec = parse_profile_arg("profiles/local.pfig")
        assert spec.path == Path("profiles/local.pfig")
        assert spec.prefix == ()

    def test_single_prefix(self):
        spec = parse_profile_arg("train::profiles/local.pfig")
        assert spec.path == Path("profiles/local.pfig")
        assert spec.prefix == ("train",)

    def test_multi_segment_prefix(self):
        spec = parse_profile_arg("train/model::profiles/local.pfig")
        assert spec.path == Path("profiles/local.pfig")
        assert spec.prefix == ("train", "model")

    def test_empty_prefix_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_profile_arg("::profiles/local.pfig")

    def test_empty_path_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_profile_arg("train::")

    def test_double_slash_prefix_rejected(self):
        with pytest.raises(ValueError, match="Invalid profile prefix"):
            parse_profile_arg("train//model::profiles/local.pfig")

    def test_leading_slash_prefix_rejected(self):
        with pytest.raises(ValueError, match="Invalid profile prefix"):
            parse_profile_arg("/train::profiles/local.pfig")

    def test_trailing_slash_prefix_rejected(self):
        with pytest.raises(ValueError, match="Invalid profile prefix"):
            parse_profile_arg("train/::profiles/local.pfig")

    def test_non_identifier_prefix_rejected(self):
        with pytest.raises(ValueError, match="Invalid profile prefix"):
            parse_profile_arg("train.model::profiles/local.pfig")

    def test_double_colon_in_path_allowed(self):
        # Split is on the *first* "::" only
        spec = parse_profile_arg("train::weird::path.pfig")
        assert spec.path == Path("weird::path.pfig")
        assert spec.prefix == ("train",)


class TestMainParsesProfileSpecs:
    """main() should convert --profile strings to ProfileSpec objects."""

    def test_profiles_converted_to_specs(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr("pfig.cli.load_config_file", lambda p: "src")
        monkeypatch.setattr("pfig.cli.parse_pfig_source", lambda s, filename=None: "AST")

        def recorder(ast, **kwargs):
            seen["profiles"] = kwargs.get("profile_paths")
            mock = MagicMock()
            mock.entrypoint_name = "ep"
            mock.reachable_names = set()
            mock.included_files = []
            return mock

        monkeypatch.setattr("pfig.cli.validate_pfig", recorder)
        monkeypatch.setattr("pfig.cli.execute_pfig_with_metadata", recorder)

        monkeypatch.setattr(
            "sys.argv",
            [
                "pfig",
                "run",
                "cfg.pfig",
                "--profile",
                "profiles/local.pfig",
                "--profile",
                "train::profiles/local.pfig",
                "--no-snapshot",
            ],
        )
        main()

        specs = seen["profiles"]
        assert len(specs) == 2
        assert isinstance(specs[0], ProfileSpec)
        assert specs[0].prefix == ()
        assert isinstance(specs[1], ProfileSpec)
        assert specs[1].prefix == ("train",)

    def test_bad_profile_prefix_exits_1(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.argv", ["pfig", "run", "cfg.pfig", "--profile", "::bad.pfig"])
        rc = main()
        assert rc == 1
