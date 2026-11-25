"""Regression coverage for callable provider roots and references."""

import pytest

from pfig import parse_pfig_source, validate_pfig
from pfig.execution import (
    ExecutionError,
    execute_pfig_with_metadata,
    get_materialized_bindings,
    run,
)
from pfig.utils import Printer


@pytest.fixture(params=["direct", "reference", "override"])
def provider_entrypoint(request):
    if request.param == "direct":
        return '__entrypoint__ = Printer(object="hello")', None
    if request.param == "reference":
        return 'printer = Printer(object="hello")\n__entrypoint__ = @printer', None
    return 'printer = Printer(object="hello")', "printer"


def test_callable_provider_entrypoint(provider_entrypoint, capsys):
    binding, override = provider_entrypoint
    ast = parse_pfig_source(f"from pfig.utils import Printer\n{binding}\n")
    validate_pfig(ast, entrypoint=override)
    assert capsys.readouterr().out == ""

    metadata = execute_pfig_with_metadata(ast, entrypoint=override)

    assert metadata.result is None
    assert capsys.readouterr().out == "'hello'\n"
    provider = metadata.materialized[metadata.entrypoint_name]
    assert isinstance(provider, Printer)
    assert provider.object == "hello"

    bindings = get_materialized_bindings(ast, entrypoint=override)
    assert isinstance(bindings[metadata.entrypoint_name], Printer)
    assert capsys.readouterr().out == ""  # Inspection does not invoke the callable.


@pytest.mark.parametrize("snapshot", [False, True])
def test_run_callable_provider_entrypoint(provider_entrypoint, snapshot, tmp_path, capsys):
    binding, override = provider_entrypoint
    config = tmp_path / "config.pfig"
    snapshot_binding = f'__snapshot__ = "{tmp_path / "snapshots"}"\n' if snapshot else ""
    config.write_text(f"from pfig.utils import Printer\n{binding}\n{snapshot_binding}")

    assert run(config, entrypoint=override) is None
    assert capsys.readouterr().out == "'hello'\n"


def test_direct_provider_entrypoint_keeps_overlay_template_lazy(capsys):
    ast = parse_pfig_source("""
from pfig.utils import Printer
template = Printer()
__entrypoint__ = Printer(object=@template(object="hello"))
""")
    validate_pfig(ast)

    metadata = execute_pfig_with_metadata(ast)

    assert isinstance(metadata.materialized["__entrypoint__"].object, Printer)
    assert "template" not in metadata.materialized
    assert "Printer object" in capsys.readouterr().out


@pytest.mark.parametrize(
    "binding",
    ["__entrypoint__ = dict()", "value = dict()\n__entrypoint__ = @value"],
    ids=["direct", "reference"],
)
def test_noncallable_provider_entrypoint(binding):
    ast = parse_pfig_source(binding)
    with pytest.raises(ExecutionError, match="resolved to dict, but entrypoints must be callable"):
        execute_pfig_with_metadata(ast)
