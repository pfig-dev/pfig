"""Regression tests for explicit includes and removal of source indirection."""

import inspect
from pathlib import Path

import pytest

from pfig import ParseError, instantiate
from pfig.cli import create_parser
from pfig.execution import ASTCompiler, compile_pfig, load_profile, run, validate_pfig
from pfig.includes import resolve_include_path
from pfig.parser import parse_pfig_source


def test_relative_path_is_anchored_to_containing_file(tmp_path, monkeypatch):
    configs = tmp_path / "configs"
    configs.mkdir()
    target = configs / "base.pfig"
    target.write_text("value = 42")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert resolve_include_path("./base.pfig", configs / "main.pfig").path == target.resolve()


def test_absolute_path(tmp_path):
    target = tmp_path / "base.pfig"
    target.write_text("value = 42")
    assert resolve_include_path(str(target), Path("/other/main.pfig")).path == target.resolve()


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="Include file not found"):
        resolve_include_path("./missing.pfig", tmp_path / "main.pfig")


@pytest.mark.parametrize("path", ["lib://base.pfig", "package://my_lib/base.pfig"])
def test_protocol_rejection_does_not_discover_packages(tmp_path, monkeypatch, path):
    def forbidden(*args, **kwargs):
        pytest.fail("Include resolution must not discover Python packages")

    monkeypatch.setattr("importlib.util.find_spec", forbidden)
    with pytest.raises(ValueError, match="Protocol-based includes are not supported"):
        resolve_include_path(path, tmp_path / "main.pfig")


def test_mount_is_an_ordinary_binding_name():
    assert instantiate("mount", source="mount = 42") == 42


def test_old_mount_statement_rejected_in_config_and_profile(tmp_path):
    source = 'mount lib = "./lib"'
    with pytest.raises(ParseError):
        parse_pfig_source(source)
    profile = tmp_path / "env.pfig"
    profile.write_text(source)
    with pytest.raises(ValueError, match="Failed to load profile"):
        load_profile(profile)


def test_mounts_not_exposed_in_public_signatures():
    for function in (ASTCompiler, compile_pfig, validate_pfig, instantiate, run):
        assert "mounts" not in inspect.signature(function).parameters
    assert "--mount" not in create_parser().format_help()


def test_old_mounts_dunder_is_not_a_resolution_directive():
    compiler = ASTCompiler()
    compiled = compiler.compile_file(parse_pfig_source('__mounts__ = {"lib": "./lib"}'))
    assert "__mounts__" in compiled["values"]
    assert "mounts" not in compiled
