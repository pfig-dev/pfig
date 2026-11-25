"""Smoke-test runnable documentation examples against the public APIs."""

import re
import shlex
import sys
from pathlib import Path
from types import ModuleType

import pytest

from pfig import ParseError, instantiate, parse_pfig_source, run
from pfig.cli import create_parser

ROOT = Path(__file__).resolve().parents[1]


def fenced_blocks(path, language):
    text = (ROOT / path).read_text()
    return re.findall(rf"```{language}\n(.*?)\n```", text, flags=re.DOTALL)


def test_getting_started_hello(tmp_path):
    source = fenced_blocks("docs/guides/getting-started.md", "pfig")[0]
    config = tmp_path / "hello.pfig"
    config.write_text(source)
    # The tutorial's entrypoint prints the Decimal and returns None.
    assert run(config, no_snapshot=True) is None


@pytest.mark.parametrize(
    "path",
    ["docs/guides/troubleshooting.md", "docs/developer/development_workflow.md"],
)
def test_documented_test_functions(path):
    examples = [block for block in fenced_blocks(path, "python") if block.startswith("def test_")]
    assert examples
    for source in examples:
        namespace = {}
        exec(compile(source, path, "exec"), namespace)
        for name, function in namespace.items():
            if name.startswith("test_"):
                function()


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/reference/cli.md",
        "docs/guides/getting-started.md",
        "docs/guides/patterns.md",
    ],
)
def test_documented_cli_arguments(path):
    parser = create_parser()
    checked = 0
    for block in fenced_blocks(path, "bash"):
        for line in block.splitlines():
            if not line.startswith("pfig ") or any(
                placeholder in line for placeholder in ("<agent>", "[options]")
            ):
                continue
            args = shlex.split(line, comments=True)[1:]
            parser.parse_args(args)
            checked += 1
    assert checked


@pytest.mark.parametrize("path", ["docs/guides/getting-started.md", "docs/guides/patterns.md"])
def test_guide_configs_parse(path):
    for source in fenced_blocks(path, "pfig"):
        parse_pfig_source(source)


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("2 ** 3 ** 2", 512),
        ("-2 ** 2", 4),
        ("2 ** -2", 0.25),
        ("(1,)", (1,)),
        ("()", ()),
        ("[len([1, 2,]), max(1, 3,)]", [2, 3]),
        ("dict(args=[1, 2])", {"args": [1, 2]}),
    ],
)
def test_documented_grammar_semantics(expression, expected):
    assert instantiate("value", source=f"value = {expression}") == expected


@pytest.mark.parametrize(
    "expression",
    ["+2", "dict(1)", "@model.encoder()", "@model().encoder"],
)
def test_documented_grammar_rejections(expression):
    with pytest.raises(ParseError):
        parse_pfig_source(f"value = {expression}")


def test_documented_reference_division():
    from pfig.ast_nodes import BinaryOpExpr, ProviderRefExpr

    division = parse_pfig_source("result = (@a) / b").statements[0].value
    reference = parse_pfig_source("result = @a / b").statements[0].value
    assert isinstance(division, BinaryOpExpr)
    assert isinstance(reference, ProviderRefExpr)
    assert reference.path.parts == ["a", "b"]


def test_patterns_plain_function(tmp_path, monkeypatch):
    # Supply minimal application code so the documented factory and config can
    # run without requiring the ML dependencies used by the larger examples.
    module = ModuleType("train")
    module.Model = lambda **kwargs: kwargs
    module.train = lambda **kwargs: kwargs
    factory = fenced_blocks("docs/guides/patterns.md", "python")[0]
    exec(compile(factory, "train.py", "exec"), module.__dict__)
    monkeypatch.setitem(sys.modules, "train", module)

    sources = fenced_blocks("docs/guides/patterns.md", "pfig")
    (tmp_path / "consts.pfig").write_text(sources[0])
    source = next(source for source in sources if "training = training_entrypoint(" in source)
    config = tmp_path / "train.pfig"
    config.write_text(source)
    assert run(config, externals={"device": "cpu"}, no_snapshot=True) == {
        "model": {"hidden_size": 512, "device": "cpu"},
        "epochs": 10,
    }
