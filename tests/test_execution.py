"""
Unit tests for the pfig execution engine.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from pfig.execution import *
from pfig.parser import parse_pfig_source


class TestLiteralValues:
    """Test materialization of literal values."""

    def test_simple_literals(self):
        """Test that simple literals materialize correctly."""
        source = """
batch_size = 64
learning_rate = 0.001
model_name = "transformer"
use_cuda = True
placeholder = None
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # Test each binding individually using CLI entrypoint override
        result = get_materialized_bindings(ast, entrypoint="batch_size")
        assert result["batch_size"] == 64

        result = get_materialized_bindings(ast, entrypoint="learning_rate")
        assert result["learning_rate"] == 0.001

        result = get_materialized_bindings(ast, entrypoint="model_name")
        assert result["model_name"] == "transformer"

        result = get_materialized_bindings(ast, entrypoint="use_cuda")
        assert result["use_cuda"] is True

        result = get_materialized_bindings(ast, entrypoint="placeholder")
        assert result["placeholder"] is None

    def test_container_literals(self):
        """Test that container literals materialize correctly."""
        source = """
numbers = [1, 2, 3]
coords = (10, 20)
config = {"batch_size": 64, "lr": 0.001}
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # Test each binding individually using CLI entrypoint override
        result = get_materialized_bindings(ast, entrypoint="numbers")
        assert result["numbers"] == [1, 2, 3]

        result = get_materialized_bindings(ast, entrypoint="coords")
        assert result["coords"] == (10, 20)

        result = get_materialized_bindings(ast, entrypoint="config")
        assert result["config"] == {"batch_size": 64, "lr": 0.001}


class TestImportsAndCalls:
    """Test import processing and function calls."""

    def test_simple_import_and_call(self):
        """Test import and provider reference."""
        source = """
import json

# Provider definition
config_provider = json.dumps(obj={"test": True})

# Provider reference (materialize the provider)
config_str = @config_provider
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="config_str")

        # Should have called json.dumps with the dict
        assert result["config_str"] == '{"test": true}'

    def test_from_import_and_call(self):
        """Test from-import and provider reference."""
        source = """
from collections import defaultdict

# Provider definition
counter_provider = defaultdict(default_factory=int)

# Provider reference (materialize the provider)
counter = @counter_provider
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="counter")

        # Should be a defaultdict instance
        counter = result["counter"]
        assert isinstance(counter, defaultdict)
        assert counter["nonexistent_key"] == 0  # Default behavior

    def test_import_with_alias(self):
        """Test import with alias and provider reference."""
        source = """
import json as js

# Provider definition
data_provider = js.loads(s='{"value": 42}')

# Provider reference (materialize the provider)
data = @data_provider
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="data")

        assert result["data"] == {"value": 42}

    def test_submodule_imports(self):
        """Test importing sub-modules like 'from os import path'."""
        source = """
import json
from os import environ
from urllib import parse

# Test os.environ sub-module usage (keyword arguments)
home_provider = environ.get(key="HOME", default="/default/home")

# Test urllib.parse sub-module usage (keyword arguments)
url_provider = parse.urljoin(base="https://example.com/", url="api/v1/data")

# Test urllib.parse quote function (keyword argument)
quoted_provider = parse.quote(string="hello world")

# Test combining results
result = {
    "home_dir": @home_provider,
    "full_url": @url_provider,
    "quoted_string": @quoted_provider
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        # Test os.environ functionality
        assert isinstance(config["home_dir"], str)

        # Test urllib.parse functionality
        from urllib import parse

        expected_url = parse.urljoin("https://example.com/", "api/v1/data")
        assert config["full_url"] == expected_url

        # Test quote functionality
        assert config["quoted_string"] == parse.quote("hello world")

    def test_nested_submodule_imports(self):
        """Test deeper nested sub-module imports."""
        source = """
import json
from xml.etree import ElementTree

# Test XML parsing with ElementTree (keyword argument)
xml_string = "<root><item id='1'>test</item></root>"
xml_provider = ElementTree.fromstring(text=xml_string)

# Test that the XML element was created successfully
result = {
    "xml_element": @xml_provider,
    "xml_string_used": xml_string
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        # Test that we got an XML element
        assert hasattr(config["xml_element"], "tag")
        assert config["xml_element"].tag == "root"
        assert config["xml_string_used"] == "<root><item id='1'>test</item></root>"

    def test_multiple_submodule_from_same_package(self):
        """Test importing multiple sub-modules from the same package."""
        source = """
import json
from os import environ
from collections import defaultdict, Counter

# Use os.environ (keyword arguments)
home_provider = environ.get(key="HOME", default="/default/home")
user_provider = environ.get(key="USER", default="unknown")

# Use multiple collections (keyword arguments)
dict_provider = defaultdict(default_factory=list)

# Counter with no arguments creates an empty counter
counter_provider = Counter()

result = {
    "home_dir": @home_provider,
    "username": @user_provider,
    "default_dict": @dict_provider,
    "counter_dict": @counter_provider
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        # Test os.environ access
        assert isinstance(config["home_dir"], str)
        assert isinstance(config["username"], str)

        # Test collections
        assert isinstance(config["default_dict"], defaultdict)
        assert config["default_dict"]["missing_key"] == []  # Should return empty list

        from collections import Counter

        assert isinstance(config["counter_dict"], Counter)
        assert len(config["counter_dict"]) == 0  # Empty counter

    def test_submodule_with_aliases(self):
        """Test sub-module imports with aliases."""
        source = """
import json
from os import environ as osenv
from urllib import parse as urlparse

# Use aliased imports (keyword arguments)
home_provider = osenv.get(key="HOME", default="/default/home")
url_provider = urlparse.quote(string="hello world")

result = {
    "home_path": @home_provider,
    "quoted_url": @url_provider
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert isinstance(config["home_path"], str)

        from urllib import parse

        assert config["quoted_url"] == parse.quote("hello world")

    def test_complex_submodule_usage(self):
        """Test complex real-world sub-module usage patterns."""
        source = """
import json
from datetime import datetime, timedelta
from urllib import parse

# Date/time calculations (no args needed for datetime.now())
now_provider = datetime.now()

# Create a timedelta for future calculation (keyword argument)
week_delta = timedelta(days=7)
future_provider = @now_provider + @week_delta

# URL parsing and manipulation (keyword arguments)
base_url = "https://api.example.com/"
endpoint = "v1/users"
full_url_provider = parse.urljoin(base=base_url, url=endpoint)

result = {
    "current_time": @now_provider,
    "future_time": @future_provider,
    "full_url": @full_url_provider,
    "base_url_used": base_url,
    "endpoint_used": endpoint
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        # Test datetime functionality
        from datetime import datetime, timedelta

        assert isinstance(config["current_time"], datetime)
        assert isinstance(config["future_time"], datetime)

        # Future should be later than current
        assert config["future_time"] > config["current_time"]

        # Test URL parsing functionality
        from urllib import parse

        expected_url = parse.urljoin("https://api.example.com/", "v1/users")
        assert config["full_url"] == expected_url
        assert config["base_url_used"] == "https://api.example.com/"
        assert config["endpoint_used"] == "v1/users"

    def test_provider_method_references(self):
        """Test @provider.method syntax for getting bound methods."""
        source = """
import json
from collections import defaultdict

# Create a provider with callable methods
counter_provider = defaultdict(default_factory=int)

# Reference methods on the provider
clear_method = @counter_provider.clear
keys_method = @counter_provider.keys
get_method = @counter_provider.get

# Test that we can get the methods without calling them
result = {
    "counter": @counter_provider,
    "clear_callable": @clear_method,
    "keys_callable": @keys_method,
    "get_callable": @get_method
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        # Test that we got the actual instance and methods
        from collections import defaultdict

        assert isinstance(config["counter"], defaultdict)

        # Test that the methods are callable
        assert callable(config["clear_callable"])
        assert callable(config["keys_callable"])
        assert callable(config["get_callable"])

        # Test that they are bound methods of the same instance
        assert config["clear_callable"].__self__ is config["counter"]
        assert config["keys_callable"].__self__ is config["counter"]
        assert config["get_callable"].__self__ is config["counter"]

        # Test that the methods work when called
        counter = config["counter"]
        counter["test"] = 42
        keys = list(config["keys_callable"]())
        assert "test" in keys

        config["clear_callable"]()
        assert len(counter) == 0

    def test_provider_method_as_entrypoint(self):
        """Test using @provider.method as an entrypoint."""
        source = """
from collections import defaultdict

# Create a counter
counter = defaultdict(default_factory=int)

# Get the clear method as a binding we can test
clear_method = @counter.clear

# Use the clear method as the entrypoint
__entrypoint__ = @clear_method
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # Test that we can get the method
        result = get_materialized_bindings(ast, entrypoint="clear_method")
        clear_callable = result["clear_method"]

        # Verify it's callable and bound to a defaultdict
        assert callable(clear_callable)
        assert hasattr(clear_callable, "__self__")

        from collections import defaultdict

        assert isinstance(clear_callable.__self__, defaultdict)

        # Test that executing the entrypoint works (calls the method)
        from pfig.execution import execute_pfig

        # This should execute without error - the method will be called
        execute_pfig(ast)

    def test_provider_method_error_cases(self):
        """Test error handling for provider method references."""
        # Test non-existent attribute
        with pytest.raises(ExecutionError, match="has no attribute 'nonexistent'"):
            source = """
import json
from collections import defaultdict

counter = defaultdict(default_factory=int)
bad_method = @counter.nonexistent

__entrypoint__ = json.loads
"""
            ast = parse_pfig_source(source, filename="test.pfig")
            get_materialized_bindings(ast, entrypoint="bad_method")

        # Non-callable attributes are allowed (attribute-value refs).
        source = """
from types import SimpleNamespace

ns = SimpleNamespace(label="ok")
value = @ns.label
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        out = get_materialized_bindings(ast, entrypoint="value")
        assert out["value"] == "ok"

        # Test method on non-existent provider
        # With ProviderMethodRef now tracked as a dependency, this fails earlier
        # in the planning phase with an "Unknown binding" validation error.
        with pytest.raises(ValidationError, match="Unknown binding"):
            source = """
import json
bad_ref = @nonexistent.method
__entrypoint__ = json.loads
"""
            ast = parse_pfig_source(source, filename="test.pfig")
            get_materialized_bindings(ast, entrypoint="bad_ref")


class TestProviderReferences:
    """Test @ reference functionality: shared vs fresh instances."""

    def test_shared_instances(self):
        """Test that @name returns the same instance (shared)."""
        source = """
from decimal import Decimal
from collections import defaultdict

# Provider definition
model = Decimal(value="42.5")

# Multiple shared references in same context
result = [@model, @model, @model]
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")

        instances = result["result"]

        # All references should return the same object
        assert instances[0] is instances[1]
        assert instances[1] is instances[2]
        assert len(set(id(inst) for inst in instances)) == 1  # Only one unique id

    def test_fresh_instances(self):
        """Test that @name() creates new instances (fresh)."""
        source = """
from decimal import Decimal

# Provider definition
model = Decimal(value="42.5")

# Multiple fresh references
result = [@model(), @model(), @model()]
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")

        instances = result["result"]

        # All references should return different objects
        assert instances[0] is not instances[1]
        assert instances[1] is not instances[2]
        assert instances[0] is not instances[2]
        assert len(set(id(inst) for inst in instances)) == 3  # Three unique ids

    def test_shared_vs_fresh_mixed(self):
        """Test mixing shared and fresh instances."""
        source = """
from decimal import Decimal

# Provider definition
model = Decimal(value="42.5")

# Mix shared and fresh references
result = {
    "shared1": @model,
    "shared2": @model,
    "fresh1": @model(),
    "fresh2": @model()
}
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")

        instances = result["result"]

        # Shared instances should be the same
        assert instances["shared1"] is instances["shared2"]

        # Fresh instances should be different from each other
        assert instances["fresh1"] is not instances["fresh2"]

        # Fresh instances should be different from shared
        assert instances["shared1"] is not instances["fresh1"]
        assert instances["shared1"] is not instances["fresh2"]

    def test_fresh_with_kwargs(self):
        """Test fresh instances with different behaviors."""
        source = """
from collections import defaultdict

# Base provider
counter = defaultdict(default_factory=int)

# Test different fresh instances
result = {
    "base": @counter,
    "fresh1": @counter(),
    "fresh2": @counter()
}
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")

        instances = result["result"]

        # Check that we have defaultdict objects
        assert all(isinstance(inst, defaultdict) for inst in instances.values())

        # Shared instance should be reused
        base = instances["base"]

        # Fresh instances should be different objects
        fresh1 = instances["fresh1"]
        fresh2 = instances["fresh2"]

        assert fresh1 is not base  # Different objects
        assert fresh2 is not base  # Different objects
        assert fresh1 is not fresh2  # Different objects

        # But they should have the same behavior (same default_factory)
        assert base.default_factory is int
        assert fresh1.default_factory is int
        assert fresh2.default_factory is int

    def test_value_binding_with_inline_providers_is_cached(self):
        """Test that value bindings containing inline providers are cached.

        When a value like `nodes = [Decimal(...), Decimal(...)]` is referenced
        multiple times via @nodes, the same list (and same instances inside)
        should be returned each time. This is a regression test for a bug where
        inline ProviderSpecs inside containers were re-instantiated on each reference.
        """
        source = """
from decimal import Decimal

# Value binding with inline provider calls (not named providers)
decimals = [Decimal(value="1"), Decimal(value="2")]

# Reference the value multiple times
result = {"a": @decimals, "b": @decimals}
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")

        instances = result["result"]

        # Both references should return the exact same list object
        assert instances["a"] is instances["b"], (
            f"Value binding should return same list object on multiple references. "
            f"Got different lists: {id(instances['a'])} vs {id(instances['b'])}"
        )

        # And therefore the same Decimal instances inside
        assert instances["a"][0] is instances["b"][0], (
            f"Inline providers in value should be same instance. "
            f"Got: {id(instances['a'][0])} vs {id(instances['b'][0])}"
        )
        assert instances["a"][1] is instances["b"][1]

    def test_value_binding_tuple_with_inline_providers_is_cached(self):
        """Test that tuple value bindings with inline providers are cached."""
        source = """
from decimal import Decimal

# Tuple value binding with inline provider calls
decimals = (Decimal(value="1"), Decimal(value="2"))

# Reference the value multiple times
result = {"a": @decimals, "b": @decimals}
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")

        instances = result["result"]

        # Both references should return the exact same tuple object
        assert instances["a"] is instances["b"]
        # And same instances inside
        assert instances["a"][0] is instances["b"][0]
        assert instances["a"][1] is instances["b"][1]

    def test_value_binding_dict_with_inline_providers_is_cached(self):
        """Test that dict value bindings with inline providers are cached."""
        source = """
from decimal import Decimal

# Dict value binding with inline provider calls
decimals = {"x": Decimal(value="1"), "y": Decimal(value="2")}

# Reference the value multiple times
result = {"a": @decimals, "b": @decimals}
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")

        instances = result["result"]

        # Both references should return the exact same dict object
        assert instances["a"] is instances["b"]
        # And same instances inside
        assert instances["a"]["x"] is instances["b"]["x"]
        assert instances["a"]["y"] is instances["b"]["y"]


class TestProviderSpecs:
    """Test provider specification creation and handling."""

    def test_provider_with_missing_kwargs(self):
        """Test that providers with missing kwargs are detected."""
        import inspect  # Import real inspect module

        # Mock a class that requires arguments
        with patch("json.JSONEncoder") as mock_encoder:
            # Configure the mock to require 'skipkeys' argument
            mock_sig = Mock()
            mock_param = Mock()

            # FIX: Use the REAL sentinel, not a mock attribute
            mock_param.default = inspect.Parameter.empty

            # FIX: Explicitly set the kind to ensure it passes the filter check
            mock_param.kind = inspect.Parameter.POSITIONAL_OR_KEYWORD

            mock_param.name = "skipkeys"
            mock_sig.parameters = {"skipkeys": mock_param}

            source = """
from json import JSONEncoder

# Missing required 'skipkeys' parameter
encoder = JSONEncoder()
"""

            with patch("inspect.signature", return_value=mock_sig):
                ast = parse_pfig_source(source, filename="test.pfig")

                # Should compile without error but note missing kwargs
                compiler = ASTCompiler()
                compiled = compiler.compile_file(ast)

                encoder_spec = compiled["providers"]["encoder"]
                assert "skipkeys" in encoder_spec.missing_kwargs


class TestDependencyResolution:
    """Test dependency graph construction and resolution."""

    def test_dependency_detection(self):
        """Test that dependencies are correctly identified."""
        # Create some test intermediate representations
        ref_a = ProviderRef("model_a")
        ref_b = ProviderRef("model_b")

        # Test getting dependencies
        deps = get_dependencies(ref_a)
        assert deps == ["model_a"]

        # Test nested dependencies
        spec = ProviderSpec(callable_obj=Mock(), args=[], kwargs={"dep1": ref_a, "dep2": ref_b})
        deps = get_dependencies(spec)
        assert set(deps) == {"model_a", "model_b"}

    def test_topological_sort(self):
        """Test topological sorting of dependency graph."""
        # Create a simple dependency graph: c -> b -> a
        graph = {
            "a": [],  # No dependencies
            "b": ["a"],  # Depends on a
            "c": ["b"],  # Depends on b
        }

        result, issues = topological_sort(graph)

        # Should be in dependency order: a, then b, then c
        assert result == ["a", "b", "c"]
        assert issues == []

    def test_cycle_detection(self):
        """Test that dependency cycles are detected."""
        # Create a cycle: a -> b -> c -> a
        graph = {"a": ["c"], "b": ["a"], "c": ["b"]}

        result, issues = topological_sort(graph)

        # Should have cycle detected as an issue
        assert len(issues) > 0
        assert any("cycle" in issue.message.lower() for issue in issues)

    def test_unknown_dependency(self):
        """Test error when referencing unknown provider."""
        graph = {"a": ["nonexistent"]}

        result, issues = topological_sort(graph)

        # Should have unknown binding issue
        assert len(issues) > 0
        assert any("Unknown binding" in issue.message for issue in issues)


class TestMaterializer:
    """Test the materializer component."""

    def test_literal_materialization(self):
        """Test materialization of literal values."""
        materializer = Materializer()

        literal = LiteralValue(42)
        result = materializer.materialize_value(literal)
        assert result == 42

    def test_list_materialization(self):
        """Test materialization of lists."""
        materializer = Materializer()

        # List with mixed literal values
        test_list = [LiteralValue(1), LiteralValue("hello"), LiteralValue(True)]
        result = materializer.materialize_value(test_list)
        assert result == [1, "hello", True]

    def test_dict_materialization(self):
        """Test materialization of dictionaries."""
        materializer = Materializer()

        # Dict with literal keys and values
        test_dict = {
            "key1": LiteralValue(42),
            "key2": LiteralValue("value2"),  # String keys, LiteralValue values
        }
        result = materializer.materialize_value(test_dict)
        assert result == {"key1": 42, "key2": "value2"}


class TestNestedMaterialization:
    """Test materialization of nested objects (ProviderSpec inside containers)."""

    def test_function_calls_in_lists(self):
        """Test that function calls inside lists are properly materialized."""
        source = """
from collections import defaultdict

# List containing function calls
containers = [
    defaultdict(default_factory=int),
    defaultdict(default_factory=str),
    defaultdict(default_factory=list)
]
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="containers")

        # Verify we get a list of properly materialized defaultdict objects
        containers = result["containers"]
        assert len(containers) == 3
        assert all(isinstance(c, defaultdict) for c in containers)

        # Test that the defaultdicts work correctly
        assert containers[0]["test"] == 0  # int default
        assert containers[1]["test"] == ""  # str default
        assert containers[2]["test"] == []  # list default

    def test_function_calls_in_dicts(self):
        """Test that function calls inside dictionaries are properly materialized."""
        source = """
from collections import defaultdict

# Dictionary containing function calls
config = {
    "counter": defaultdict(default_factory=int),
    "lookup": defaultdict(default_factory=str),
    "nested": {
        "inner_counter": defaultdict(default_factory=list)
    }
}
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="config")

        config = result["config"]
        assert isinstance(config["counter"], defaultdict)
        assert isinstance(config["lookup"], defaultdict)
        assert isinstance(config["nested"]["inner_counter"], defaultdict)

        # Test functionality
        assert config["counter"]["test"] == 0
        assert config["lookup"]["test"] == ""
        assert config["nested"]["inner_counter"]["test"] == []

    def test_complex_nested_structures(self):
        """Test materialization in deeply nested structures."""
        source = """
from collections import defaultdict

# Complex nested structure with function calls at various levels
data = {
    "models": [
        {"counter": defaultdict(default_factory=int)},
        {"counter": defaultdict(default_factory=str)}
    ],
    "config": {
        "metrics": [
            defaultdict(default_factory=list),
            defaultdict(default_factory=dict)
        ]
    }
}
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="data")

        data = result["data"]

        # Test nested function calls in lists within dicts
        assert isinstance(data["models"][0]["counter"], defaultdict)
        assert isinstance(data["models"][1]["counter"], defaultdict)
        assert data["models"][0]["counter"]["test"] == 0
        assert data["models"][1]["counter"]["test"] == ""

        # Test nested function calls in lists within nested dicts
        assert isinstance(data["config"]["metrics"][0], defaultdict)
        assert isinstance(data["config"]["metrics"][1], defaultdict)
        assert data["config"]["metrics"][0]["test"] == []
        assert isinstance(data["config"]["metrics"][1]["test"], dict)

    def test_mixed_literals_and_function_calls(self):
        """Test containers mixing literal values and function calls."""
        source = """
from collections import defaultdict

mixed_list = [
    42,
    "literal_string",
    defaultdict(default_factory=int),
    True,
    defaultdict(default_factory=str)
]

mixed_dict = {
    "literal_int": 123,
    "counter": defaultdict(default_factory=list),
    "literal_bool": False,
    "lookup": defaultdict(default_factory=dict)
}
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # Test mixed list
        result = get_materialized_bindings(ast, entrypoint="mixed_list")
        mixed_list = result["mixed_list"]
        assert mixed_list[0] == 42
        assert mixed_list[1] == "literal_string"
        assert isinstance(mixed_list[2], defaultdict)
        assert mixed_list[3] is True
        assert isinstance(mixed_list[4], defaultdict)

        # Test functionality of materialized objects
        assert mixed_list[2]["test"] == 0
        assert mixed_list[4]["test"] == ""

        # Test mixed dict
        result = get_materialized_bindings(ast, entrypoint="mixed_dict")
        mixed_dict = result["mixed_dict"]
        assert mixed_dict["literal_int"] == 123
        assert isinstance(mixed_dict["counter"], defaultdict)
        assert mixed_dict["literal_bool"] is False
        assert isinstance(mixed_dict["lookup"], defaultdict)

        # Test functionality
        assert mixed_dict["counter"]["test"] == []
        assert isinstance(mixed_dict["lookup"]["test"], dict)

    def test_provider_references_in_containers(self):
        """Test containers with @ references to providers."""
        source = """
from collections import defaultdict

# Define individual providers
int_counter = defaultdict(default_factory=int)
str_counter = defaultdict(default_factory=str)

# Use @ references in containers
counters_list = [@int_counter, @str_counter]
counters_dict = {
    "ints": @int_counter,
    "strings": @str_counter
}
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # Test list with @ references
        result = get_materialized_bindings(ast, entrypoint="counters_list")
        counters_list = result["counters_list"]
        assert len(counters_list) == 2
        assert isinstance(counters_list[0], defaultdict)
        assert isinstance(counters_list[1], defaultdict)

        # Test dict with @ references
        result = get_materialized_bindings(ast, entrypoint="counters_dict")
        counters_dict = result["counters_dict"]
        assert isinstance(counters_dict["ints"], defaultdict)
        assert isinstance(counters_dict["strings"], defaultdict)

        # Test that @ references work (shared vs fresh instance behavior tested elsewhere)

    def test_pytorch_style_transforms(self):
        """Test PyTorch-style transforms composition (the original use case)."""
        source = """
from collections import defaultdict

# Simulate transforms.Compose with function calls in a list
# This mimics the exact pattern that was failing in the MNIST example
mock_transforms = [
    defaultdict(default_factory=int),    # Simulates transforms.ToTensor()
    defaultdict(default_factory=str),    # Simulates transforms.Normalize()
    defaultdict(default_factory=list)    # Additional transform
]

# Nested in a dict like transforms.Compose(transforms=[...])
compose_like = {
    "transforms": [
        defaultdict(default_factory=dict),
        defaultdict(default_factory=set)
    ]
}
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # Test the transforms list
        result = get_materialized_bindings(ast, entrypoint="mock_transforms")
        transforms = result["mock_transforms"]
        assert len(transforms) == 3
        assert all(isinstance(t, defaultdict) for t in transforms)

        # Test the compose-like structure
        result = get_materialized_bindings(ast, entrypoint="compose_like")
        compose = result["compose_like"]
        assert len(compose["transforms"]) == 2
        assert all(isinstance(t, defaultdict) for t in compose["transforms"])

        # Test functionality - this proves the nested objects are properly materialized
        assert compose["transforms"][0]["key"] == {}  # dict default
        assert compose["transforms"][1]["key"] == set()  # set default

    def test_error_cases_nested_materialization(self):
        """Test error handling in nested materialization scenarios."""
        # Test invalid import in nested function calls
        source = """
# This import doesn't exist and should fail
from nonexistent_module import NonexistentClass

# This should fail during materialization of nested objects
problematic_list = [
    42,
    NonexistentClass()  # This will fail to import
]
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # This should fail during compilation due to import error
        with pytest.raises((ExecutionError, ValidationError), match="module|import"):
            get_materialized_bindings(ast, entrypoint="problematic_list")


class TestBuiltinFunctions:
    """Test built-in function handling."""

    def test_builtin_calls_in_ast(self):
        """Test that built-in calls are recognized in AST."""
        source = """
items = [1, 2, 3, 4, 5]
count = len(items)
minimum = min(items)
maximum = max(items)
numbers = range(3)
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # Check that builtin calls are parsed correctly
        bindings = {b.name: b.value for b in ast.bindings()}

        assert isinstance(bindings["count"], BuiltinCallExpr)
        assert bindings["count"].function == "len"

        assert isinstance(bindings["minimum"], BuiltinCallExpr)
        assert bindings["minimum"].function == "min"

        assert isinstance(bindings["maximum"], BuiltinCallExpr)
        assert bindings["maximum"].function == "max"

        assert isinstance(bindings["numbers"], BuiltinCallExpr)
        assert bindings["numbers"].function == "range"


class TestArithmeticOperations:
    """Test unary and binary arithmetic operations."""

    def test_unary_operations(self):
        """Test unary operations like -42."""
        source = """
import json

# Compile-time unary operations
neg_int = -42
neg_float = -3.14
neg_zero = -0

# Runtime unary operations
base = 100
neg_base = -base

result = {
    "neg_int": neg_int,
    "neg_float": neg_float,
    "neg_zero": neg_zero,
    "neg_base": neg_base
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["neg_int"] == -42
        assert config["neg_float"] == -3.14
        assert config["neg_zero"] == 0
        assert config["neg_base"] == -100

    def test_binary_operations_compile_time(self):
        """Test compile-time binary operations with literals."""
        source = """
import json

# All binary operators with literals (computed at compile time)
addition = 10 + 5
subtraction = 20 - 8
multiplication = 6 * 7
division = 15 / 3
exponentiation = 2 ** 4

result = {
    "add": addition,
    "sub": subtraction,
    "mul": multiplication,
    "div": division,
    "pow": exponentiation
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["add"] == 15
        assert config["sub"] == 12
        assert config["mul"] == 42
        assert config["div"] == 5.0
        assert config["pow"] == 16

    def test_binary_operations_runtime(self):
        """Test runtime binary operations with variables."""
        source = """
import json

# Variables for runtime evaluation
left = 10
right = 3

# Runtime binary operations
addition = left + right
subtraction = left - right
multiplication = left * right
division = left / right
exponentiation = left ** right

result = {
    "add": addition,
    "sub": subtraction,
    "mul": multiplication,
    "div": division,
    "pow": exponentiation
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["add"] == 13
        assert config["sub"] == 7
        assert config["mul"] == 30
        assert config["div"] == 10 / 3  # 3.3333...
        assert config["pow"] == 1000

    def test_operator_precedence(self):
        """Test that operator precedence is respected."""
        source = """
import json

# Test precedence: * before +, ** before *, parentheses override
expr1 = 2 + 3 * 4          # Should be 2 + 12 = 14
expr2 = (2 + 3) * 4        # Should be 5 * 4 = 20
expr3 = 2 ** 3 * 4         # Should be 8 * 4 = 32
expr4 = 2 * 3 ** 4         # Should be 2 * 81 = 162
expr5 = 10 - 5 + 3         # Left-to-right: (10 - 5) + 3 = 8
expr6 = 20 / 4 * 2         # Left-to-right: (20 / 4) * 2 = 10

result = {
    "mult_before_add": expr1,
    "parens_override": expr2,
    "power_before_mult": expr3,
    "power_right_assoc": expr4,
    "left_to_right_add_sub": expr5,
    "left_to_right_mult_div": expr6
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["mult_before_add"] == 14
        assert config["parens_override"] == 20
        assert config["power_before_mult"] == 32
        assert config["power_right_assoc"] == 162
        assert config["left_to_right_add_sub"] == 8
        assert config["left_to_right_mult_div"] == 10.0

    def test_floor_division(self):
        """Test integer (floor) division at compile time and runtime."""
        source = """
import json

# Compile-time floor division
compile_int = 7 // 2
compile_neg = -7 // 2
compile_float = 7.5 // 2

# Runtime floor division
left = 7
right = 2
neg = -7
runtime_int = left // right
runtime_neg = neg // right
runtime_float = 7.5 // right

# Same precedence as * and /, left-associative
with_mul = 20 // 4 * 2
with_div = 20 // 4 / 2

result = {
    "compile_int": compile_int,
    "compile_neg": compile_neg,
    "compile_float": compile_float,
    "runtime_int": runtime_int,
    "runtime_neg": runtime_neg,
    "runtime_float": runtime_float,
    "with_mul": with_mul,
    "with_div": with_div
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["compile_int"] == 3
        assert config["compile_neg"] == -4
        assert config["compile_float"] == 3.0
        assert config["runtime_int"] == 3
        assert config["runtime_neg"] == -4
        assert config["runtime_float"] == 3.0
        assert config["with_mul"] == 10
        assert config["with_div"] == 2.5

    def test_mixed_types(self):
        """Test arithmetic with mixed numeric types."""
        source = """
import json

# Integer and float operations
int_plus_float = 10 + 3.5
float_plus_int = 2.5 + 7
int_mult_float = 4 * 2.25
float_div_int = 9.0 / 3

result = {
    "int_plus_float": int_plus_float,
    "float_plus_int": float_plus_int,
    "int_mult_float": int_mult_float,
    "float_div_int": float_div_int
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["int_plus_float"] == 13.5
        assert config["float_plus_int"] == 9.5
        assert config["int_mult_float"] == 9.0
        assert config["float_div_int"] == 3.0

    def test_string_concatenation(self):
        """Test string concatenation with + operator."""
        source = """
import json

# String operations
simple_concat = "Hello" + " World"
multi_concat = "A" + "B" + "C"
string_var1 = "pfig"
string_var2 = "DSL"
var_concat = string_var1 + " " + string_var2

result = {
    "simple": simple_concat,
    "multi": multi_concat,
    "variables": var_concat
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["simple"] == "Hello World"
        assert config["multi"] == "ABC"
        assert config["variables"] == "pfig DSL"

    def test_complex_expressions(self):
        """Test complex nested arithmetic expressions."""
        source = """
import json

# Complex expressions mixing literals and variables
base = 10
multiplier = 2
offset = 5

# Complex expression with multiple operators and precedence
complex1 = base * multiplier + offset       # 10 * 2 + 5 = 25
complex2 = base + multiplier * offset       # 10 + 2 * 5 = 20
complex3 = (base + multiplier) * offset     # (10 + 2) * 5 = 60
complex4 = base ** 2 - multiplier * offset  # 100 - 10 = 90
complex5 = -base + multiplier               # -10 + 2 = -8

result = {
    "expr1": complex1,
    "expr2": complex2,
    "expr3": complex3,
    "expr4": complex4,
    "expr5": complex5
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["expr1"] == 25
        assert config["expr2"] == 20
        assert config["expr3"] == 60
        assert config["expr4"] == 90
        assert config["expr5"] == -8

    def test_arithmetic_error_handling(self):
        """Test error handling in arithmetic operations."""
        # Test compile-time division by zero
        with pytest.raises(ExecutionError, match="division by zero"):
            source = """
import json
divide_by_zero = 10 / 0
__entrypoint__ = json.loads
"""
            ast = parse_pfig_source(source, filename="test.pfig")
            get_materialized_bindings(ast, entrypoint="divide_by_zero")

        # Test runtime division by zero
        with pytest.raises(ExecutionError, match="division by zero"):
            source = """
import json
zero = 0
ten = 10
divide_by_zero = ten / zero
__entrypoint__ = json.loads
"""
            ast = parse_pfig_source(source, filename="test.pfig")
            get_materialized_bindings(ast, entrypoint="divide_by_zero")

        # Test compile-time floor division by zero
        with pytest.raises(ExecutionError, match="integer division or modulo by zero"):
            source = """
import json
divide_by_zero = 10 // 0
__entrypoint__ = json.loads
"""
            ast = parse_pfig_source(source, filename="test.pfig")
            get_materialized_bindings(ast, entrypoint="divide_by_zero")

        # Test runtime floor division by zero
        with pytest.raises(ExecutionError, match="integer division or modulo by zero"):
            source = """
import json
zero = 0
ten = 10
divide_by_zero = ten // zero
__entrypoint__ = json.loads
"""
            ast = parse_pfig_source(source, filename="test.pfig")
            get_materialized_bindings(ast, entrypoint="divide_by_zero")

    def test_nested_expressions_with_containers(self):
        """Test arithmetic expressions within containers."""
        source = """
import json

base = 10

# Arithmetic in lists
arithmetic_list = [base + 5, base * 2, base - 3]

# Arithmetic in dictionaries
arithmetic_dict = {
    "add": base + 15,
    "multiply": base * 3,
    "power": base ** 2
}

# Arithmetic in tuples
arithmetic_tuple = (base / 2, base + base, -base)

result = {
    "list": arithmetic_list,
    "dict": arithmetic_dict,
    "tuple": arithmetic_tuple
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="result")
        config = result["result"]

        assert config["list"] == [15, 20, 7]
        assert config["dict"] == {"add": 25, "multiply": 30, "power": 100}
        assert config["tuple"] == (5.0, 20, -10)

    def test_real_world_arithmetic_examples(self):
        """Test real-world use cases for arithmetic in config files."""
        source = """
import json

# Machine learning hyperparameters with derived values
base_learning_rate = 1e-3
batch_size = 32
num_epochs = 100
hidden_size = 256
input_dim = 784
output_dim = 10

# Derived calculations
effective_learning_rate = base_learning_rate * batch_size
total_training_steps = num_epochs * 1000  # Assuming 1000 steps per epoch
model_parameters = input_dim * hidden_size + hidden_size * output_dim
memory_per_batch = batch_size * input_dim * 4  # 4 bytes per float

# Configuration with computed values
ml_config = {
    "base_lr": base_learning_rate,
    "batch_size": batch_size,
    "effective_lr": effective_learning_rate,
    "total_steps": total_training_steps,
    "model_params": model_parameters,
    "memory_mb": memory_per_batch / 1024 / 1024
}

__entrypoint__ = json.loads
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        result = get_materialized_bindings(ast, entrypoint="ml_config")
        config = result["ml_config"]

        assert config["base_lr"] == 0.001
        assert config["batch_size"] == 32
        assert config["effective_lr"] == 0.032
        assert config["total_steps"] == 100000
        assert config["model_params"] == 203264  # 784 * 256 + 256 * 10
        assert abs(config["memory_mb"] - 0.095703125) < 1e-6  # 32 * 784 * 4 / 1024 / 1024


class TestErrorConditions:
    """Test various error conditions in execution."""

    def test_unknown_import(self):
        """Test error when importing unknown module."""
        source = """
import nonexistent_module

result = nonexistent_module.function()
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises((ExecutionError, ValidationError)) as exc_info:
            execute_pfig(ast)

        # Should fail during import processing
        assert "import" in str(exc_info.value).lower() or "module" in str(exc_info.value).lower()

    def test_unknown_attribute(self):
        """Test error when accessing unknown attribute."""
        source = """
import json

result = json.nonexistent_function()
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ExecutionError) as exc_info:
            execute_pfig(ast)

        # Should fail when trying to get nonexistent attribute
        assert (
            "nonexistent_function" in str(exc_info.value)
            or "attribute" in str(exc_info.value).lower()
        )

    def test_construction_failure(self):
        """Test error when constructor call fails."""
        # Mock a class that raises an exception
        mock_class = Mock(side_effect=ValueError("Construction failed"))

        with patch("json.JSONDecoder", mock_class):
            source = """
from json import JSONDecoder

# Provider definition
decoder_provider = JSONDecoder()

# Provider reference (will fail during materialization)
decoder = @decoder_provider
"""
            ast = parse_pfig_source(source, filename="test.pfig")

            with pytest.raises(ExecutionError) as exc_info:
                get_materialized_bindings(ast, entrypoint="decoder")

            assert "Failed to construct" in str(exc_info.value)
            assert "decoder_provider" in str(exc_info.value)


class TestComplexExecution:
    """Test execution of complex configurations."""

    def test_chained_dependencies(self):
        """Test execution with chained dependencies."""
        source = """
import json

# Create nested data structure
base_config = {"model": "transformer", "layers": 6}

# Provider definitions
config_str_provider = json.dumps(obj=base_config)
parsed_back_provider = json.loads(s=@config_str_provider)

# Provider references
config_str = @config_str_provider
parsed_back = @parsed_back_provider
"""

        ast = parse_pfig_source(source, filename="test.pfig")
        # Use parsed_back as entrypoint - it depends on config_str which depends on base_config
        result = get_materialized_bindings(ast, entrypoint="parsed_back")

        # Should have properly executed the chain
        assert result["base_config"] == {"model": "transformer", "layers": 6}
        assert result["parsed_back"] == {"model": "transformer", "layers": 6}

        # Test the intermediate step separately
        config_result = get_materialized_bindings(ast, entrypoint="config_str")
        assert config_result["config_str"] == '{"model": "transformer", "layers": 6}'

    def test_multiple_imports(self):
        """Test execution with multiple imports."""
        source = """
import json
from collections import defaultdict, OrderedDict

# Provider definitions
data_provider = json.dumps(obj={"test": True})
counter_provider = defaultdict(default_factory=int)
ordered_provider = OrderedDict()

# Provider references
data = @data_provider
counter = @counter_provider
ordered = @ordered_provider
"""

        ast = parse_pfig_source(source, filename="test.pfig")

        from collections import OrderedDict, defaultdict

        # Test each binding individually
        result = get_materialized_bindings(ast, entrypoint="counter")
        assert isinstance(result["counter"], defaultdict)

        result = get_materialized_bindings(ast, entrypoint="ordered")
        assert isinstance(result["ordered"], OrderedDict)

        result = get_materialized_bindings(ast, entrypoint="data")
        assert result["data"] == '{"test": true}'


class TestDispatcher:
    """Test the dispatcher utility."""

    def test_dispatcher_registration(self):
        """Test dispatcher method registration."""

        class TestTarget:
            @Dispatcher.register(str)
            def handle_string(self, value):
                return f"string: {value}"

            @Dispatcher.register(int)
            def handle_int(self, value):
                return f"int: {value}"

            @Dispatcher.register(list)
            @Dispatcher.register(tuple)
            def handle_sequence(self, value):
                return type(value)(f"sequence: {item}" for item in value)

        target = TestTarget()
        dispatcher = Dispatcher(target)

        # Test dispatch by type
        result1 = dispatcher(str, "hello")
        assert result1 == "string: hello"

        result2 = dispatcher(int, 42)
        assert result2 == "int: 42"

        result3 = dispatcher(list, [1, 2, 3])
        assert result3 == ["sequence: 1", "sequence: 2", "sequence: 3"]

        result4 = dispatcher(tuple, (1, 2, 3))
        assert result4 == ("sequence: 1", "sequence: 2", "sequence: 3")

    def test_dispatcher_missing_handler(self):
        """Test error when no handler is registered."""

        class TestTarget:
            @Dispatcher.register(str)
            def handle_string(self, value):
                return f"string: {value}"

        target = TestTarget()
        dispatcher = Dispatcher(target)

        # Should raise error for unregistered type
        with pytest.raises(NoDispatcherError):
            dispatcher(int, 42)


class TestASTCompiler:
    """Test the AST compiler component."""

    def test_import_processing(self):
        """Test that imports are processed correctly."""
        source = """
import json
from collections import defaultdict as dd
"""

        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        # Check import table
        import_table = compiled["import_table"]
        assert "json" in import_table
        assert "dd" in import_table

        # Should be the actual modules
        import json as actual_json
        from collections import defaultdict as actual_dd

        assert import_table["json"] is actual_json
        assert import_table["dd"] is actual_dd

    def test_binding_compilation(self):
        """Test that bindings are compiled correctly."""
        source = """
import json

# Simple value
batch_size = 64

# Function call
config = json.dumps(obj={"test": True})
"""

        ast = parse_pfig_source(source, filename="test.pfig")
        compiler = ASTCompiler()
        compiled = compiler.compile_file(ast)

        # Check compiled bindings
        assert "batch_size" in compiled["values"]
        assert "config" in compiled["providers"]

        # batch_size should be a LiteralValue
        batch_size_val = compiled["values"]["batch_size"]
        assert isinstance(batch_size_val, LiteralValue)
        assert batch_size_val.value == 64

        # config should be a ProviderSpec
        config_spec = compiled["providers"]["config"]
        assert isinstance(config_spec, ProviderSpec)
        assert config_spec.name == "config"


class TestEndToEnd:
    """End-to-end integration tests."""

    def test_complete_workflow(self):
        """Test complete parse -> compile -> materialize workflow."""
        source = """
# Complete pfig example
import json
from collections import defaultdict

# Configuration values
model_config = {
    "type": "transformer",
    "layers": 6,
    "hidden_dim": 512
}

# Provider definitions
config_str_provider = json.dumps(obj=model_config)
counter_provider = defaultdict(default_factory=int)
layer_count_provider = len([1, 2, 3, 4, 5, 6])
max_val_provider = max([100, 200, 50])

# Provider references
config_str = @config_str_provider
counter = @counter_provider
layer_count = @layer_count_provider
max_val = @max_val_provider
"""

        # Parse
        ast = parse_pfig_source(source, filename="complete.pfig")
        assert len(ast.statements) > 0

        # Execute and test each binding individually
        import json

        # Test model_config
        result = get_materialized_bindings(ast, entrypoint="model_config")
        assert result["model_config"] == {
            "type": "transformer",
            "layers": 6,
            "hidden_dim": 512,
        }

        # Test config_str (depends on model_config)
        result = get_materialized_bindings(ast, entrypoint="config_str")
        parsed = json.loads(result["config_str"])
        assert parsed == result["model_config"]

        # Test counter
        result = get_materialized_bindings(ast, entrypoint="counter")
        assert isinstance(result["counter"], defaultdict)

        # Test computed values
        result = get_materialized_bindings(ast, entrypoint="layer_count")
        assert result["layer_count"] == 6

        result = get_materialized_bindings(ast, entrypoint="max_val")
        assert result["max_val"] == 200


class StrictClass:
    """Helper class for validation tests."""

    def __init__(self, alpha=1, beta=2):
        self.alpha = alpha
        self.beta = beta


class TestValidationFailures:
    """
    Tests for the 'Silent Failure' and 'Unknown Kwarg' bugs.
    These tests verify that invalid configs are caught at compile time.
    """

    def test_unknown_kwarg_raises_validation_error(self):
        """
        Standard Test: A typo in a kwarg should trigger a validation error.
        The compiler now collects issues instead of raising immediately.
        """
        compiler = ASTCompiler()
        compiler.import_table["StrictClass"] = StrictClass

        # Typo: 'allpha' instead of 'alpha'
        source = "obj = StrictClass(allpha=99)"
        ast = parse_pfig_source(source, filename="test_validation.pfig")

        # Compile collects issues, then we can check them or call raise_if_issues()
        compiler.compile_file(ast)

        # Check that an unknown kwargs issue was collected
        assert len(compiler.validation_issues) > 0
        issue_messages = [str(issue) for issue in compiler.validation_issues]
        assert any("Unknown kwargs" in msg or "allpha" in msg for msg in issue_messages)

        # Calling raise_if_issues should raise ValidationError
        with pytest.raises(ValidationError) as excinfo:
            compiler.raise_if_issues()

        error_msg = str(excinfo.value)
        assert "allpha" in error_msg

    def test_signature_inspection_failure_raises_error(self):
        """
        Standard Test: If inspect.signature fails (e.g. C-ext), do NOT fail silently.
        """
        # ENABLE STRICT MODE here to force an error on signature failure
        compiler = ASTCompiler(strict=True)
        compiler.import_table["StrictClass"] = StrictClass

        source = "obj = StrictClass(alpha=1)"
        ast = parse_pfig_source(source, filename="test_validation.pfig")

        # Simulating a C-extension/Builtin that crashes inspect.signature
        with patch("inspect.signature", side_effect=ValueError("no signature found")):
            # In strict mode, the compiler should raise immediately for signature failures
            with pytest.raises(ExecutionError) as excinfo:
                compiler.compile_file(ast)

        assert "Could not inspect signature" in str(excinfo.value)


def test_load_profile_returns_overrides_and_file_origins(tmp_path):
    profile = tmp_path / "env.pfig"
    profile.write_text("override some_ext = 123")
    overrides, file_origins = load_profile(profile)
    assert overrides == {"some_ext": 123}
    assert file_origins == {}


class TestRunFunction:
    """Test the run() convenience function."""

    def test_basic_run(self):
        """Test basic usage of run() with a config file."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
data = OrderedDict(a=1, b=2, c=3)
__entrypoint__ = @data.keys
"""
            )

            result = run(config)
            assert list(result) == ["a", "b", "c"]

    def test_run_with_string_path(self):
        """Test that run() accepts string paths."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
data = OrderedDict(x=42)
__entrypoint__ = @data.keys
"""
            )

            # Pass string instead of Path
            result = run(str(config))
            assert list(result) == ["x"]

    def test_run_with_externals(self):
        """Test run() with external values."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
external batch_size
data = OrderedDict(batch_size=@batch_size)
__entrypoint__ = @data.copy
"""
            )

            result = run(config, externals={"batch_size": 128})
            assert result == {"batch_size": 128}

    def test_run_with_entrypoint_override(self):
        """Test run() with entrypoint override."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
data_a = OrderedDict(value=10)
data_b = OrderedDict(value=20)
get_a = @data_a.copy
get_b = @data_b.copy
__entrypoint__ = @get_a
"""
            )

            # Default entrypoint returns data_a
            result = run(config)
            assert result == {"value": 10}

            # Override to use get_b
            result = run(config, entrypoint="get_b")
            assert result == {"value": 20}

    def test_run_with_method_path_entrypoint_override(self):
        """CLI/API entrypoint may be provider.method, not only a binding name."""
        with TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
data_a = OrderedDict(value=10)
data_b = OrderedDict(value=20)
__entrypoint__ = @data_a.copy
"""
            )

            result = run(config, entrypoint="data_b.copy")
            assert result == {"value": 20}

    def test_run_with_nested_method_path_entrypoint_override(self):
        """CLI/API entrypoint walks a nested attribute chain, then calls it."""
        with TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
from types import SimpleNamespace
inner = OrderedDict(value=20)
outer = SimpleNamespace(data=@inner)
__entrypoint__ = @inner.copy
"""
            )

            result = run(config, entrypoint="outer.data.copy")
            assert result == {"value": 20}

    def test_run_with_snapshot_dir(self):
        """Test run() creates snapshot when snapshot_dir is provided."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import Counter
counter = Counter()
__entrypoint__ = @counter.copy
"""
            )

            snapshot_dir = tmp_path / "snapshots"
            result = run(config, snapshot_dir=snapshot_dir)

            # Should have created the snapshot directory with a timestamped subfolder
            assert snapshot_dir.exists()
            subdirs = list(snapshot_dir.iterdir())
            assert len(subdirs) == 1
            assert subdirs[0].is_dir()

            # Snapshot directory should contain log.json
            log_file = subdirs[0] / "log.json"
            assert log_file.exists()

    @pytest.mark.parametrize("remote", [False, True], ids=["local", "uri"])
    def test_run_snapshot_bundle_entry_path(self, tmp_path, remote):
        """Only local config paths are passed to the local snapshot bundler."""
        from pfig import fs

        memory_fs = pytest.importorskip("fsspec").filesystem("memory") if remote else None
        config = (
            "memory://pfig-snapshot-entry-path/config.pfig" if remote else tmp_path / "config.pfig"
        )
        fs.write_text(
            config,
            "from collections import Counter\n"
            "counter = Counter()\n"
            "__entrypoint__ = @counter.copy\n",
        )
        try:
            with patch("pfig.snapshot.create_snapshot") as create_snapshot:
                assert run(config, snapshot_dir=tmp_path / "snapshots") == {}
            assert create_snapshot.call_args.kwargs["entry_path"] == (None if remote else config)
        finally:
            if memory_fs is not None:
                memory_fs.rm(str(config))

    @pytest.mark.parametrize(
        "snapshot_binding",
        [
            "__snapshot__ = @snapshot_path",
            "__snapshot__ = snapshot_directory(context=@context)",
        ],
        ids=["provider-reference", "direct-provider"],
    )
    def test_run_snapshot_constructs_shared_provider_once(self, snapshot_binding):
        """Direct and referenced snapshot providers share the entrypoint's cache."""
        module_name = "pfig_snapshot_shared_probe"
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / f"{module_name}.py").write_text(
                """
from pathlib import Path

class RunContext:
    constructions = 0

    def __init__(self, root):
        RunContext.constructions += 1
        self.directory = Path(root) / f"run-{RunContext.constructions}"
        self.directory.mkdir(parents=True)

def snapshot_directory(context):
    return str(context.directory / "snapshots")

class Trainer:
    def __init__(self, context):
        self.context = context

    def run(self):
        return str(self.context.directory)
"""
            )
            root = tmp_path / "runs"
            config = tmp_path / "train.pfig"
            config.write_text(
                f"""
from {module_name} import RunContext, Trainer, snapshot_directory

context = RunContext(root="{root}")
snapshot_path = snapshot_directory(context=@context)
trainer = Trainer(context=@context)

{snapshot_binding}
__entrypoint__ = @trainer.run
"""
            )

            sys.modules.pop(module_name, None)
            sys.path.insert(0, str(tmp_path))
            try:
                result = run(config)
                probe = sys.modules[module_name]
                assert probe.RunContext.constructions == 1
                snapshot_root = Path(result) / "snapshots"
                assert snapshot_root.is_dir()
                assert any(snapshot_root.iterdir())
            finally:
                sys.path.remove(str(tmp_path))
                sys.modules.pop(module_name, None)

    def test_run_without_snapshot_by_default(self):
        """Test that run() does NOT create snapshots by default."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
data = OrderedDict(value=42)
__entrypoint__ = @data.copy
"""
            )

            # No snapshot_dir provided
            result = run(config)
            assert result == {"value": 42}

            # No snapshots directory should be created
            snapshot_dir = tmp_path / "snapshots"
            assert not snapshot_dir.exists()

    def test_run_with_profile_paths(self):
        """Test run() with profile files."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create profile
            profile = tmp_path / "profile.pfig"
            profile.write_text("override learning_rate = 0.01")

            # Create config
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
external learning_rate
data = OrderedDict(lr=@learning_rate)
__entrypoint__ = @data.copy
"""
            )

            result = run(config, profile_paths=[profile])
            assert result == {"lr": 0.01}

    def test_run_file_not_found(self):
        """Test run() raises error for non-existent file."""
        with pytest.raises(FileNotFoundError):
            run("/nonexistent/path/config.pfig")

    def test_run_parse_error(self):
        """Test run() raises ParseError for invalid config."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from pfig import LexerError

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "invalid.pfig"
            config.write_text("this is not valid pfig syntax !!!")

            with pytest.raises(LexerError):
                run(config)

    def test_run_missing_external_error(self):
        """Test run() raises ValidationError for missing externals."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
external required_value
data = OrderedDict(val=@required_value)
__entrypoint__ = @data.copy
"""
            )

            with pytest.raises(ValidationError, match="Missing external value"):
                run(config)

    def test_run_with_includes(self):
        """Test run() with included files."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create included file
            lib = tmp_path / "lib.pfig"
            lib.write_text("multiplier = 10")

            # Create main config
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
include "./lib.pfig" as lib
value = 5
result = @value * @lib/multiplier
data = OrderedDict(result=@result)
__entrypoint__ = @data.copy
"""
            )

            result = run(config)
            assert result == {"result": 50}

    def test_run_with_relative_include(self):
        """Test run() with an explicitly located config library."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create library in a separate location
            lib_dir = tmp_path / "libs"
            lib_dir.mkdir()
            lib_file = lib_dir / "utils.pfig"
            lib_file.write_text("helper_value = 999")

            # Create main config using a relative include
            config = tmp_path / "config.pfig"
            config.write_text(
                """
from collections import OrderedDict
include "./libs/utils.pfig" as utils
data = OrderedDict(val=@utils/helper_value)
__entrypoint__ = @data.copy
"""
            )

            result = run(config)
            assert result == {"val": 999}


class TestValidationWithoutMaterialization:
    """Tests for validation that should NOT call constructors.

    These tests verify that pfig validate performs structural validation
    (syntax, dependencies, required args) without materializing objects.
    """

    def test_validation_should_not_call_constructors(self):
        """Verify that validation does not materialize objects.

        This is a regression test. Validation should check config correctness
        without side effects from calling constructors.
        """
        # Track how many times the constructor is called
        call_count = {"value": 0}

        class InstantiationTracker:
            def __init__(self):
                call_count["value"] += 1

            def __call__(self):
                pass

        # Patch the tracker into a known module
        with patch.dict(
            "sys.modules",
            {"test_tracker": Mock(InstantiationTracker=InstantiationTracker)},
        ):
            import sys

            sys.modules["test_tracker"] = Mock(InstantiationTracker=InstantiationTracker)

            source = """
from test_tracker import InstantiationTracker

obj = InstantiationTracker()

__entrypoint__ = @obj
"""
            ast = parse_pfig_source(source, filename="test.pfig")

            # Reset counter before validation
            call_count["value"] = 0

            # Validate - this should NOT call the constructor
            result = validate_pfig(ast)

            # EXPECTED: Constructor should NOT have been called during validation
            # CURRENT BUG: Constructor IS called during validation
            assert call_count["value"] == 0, (
                f"Validation should not call constructors, but InstantiationTracker.__init__ "
                f"was called {call_count['value']} time(s)"
            )

    def test_validation_catches_missing_kwargs_without_materializing(self):
        """Verify validation detects missing required args without constructing.

        The signature introspection happens at compile time, so we should be
        able to detect missing kwargs without actually calling the constructor.
        """
        source = """
from collections import namedtuple

# namedtuple requires 'typename' and 'field_names' arguments
Point = namedtuple()

__entrypoint__ = @Point
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        # validate_pfig catches missing kwargs statically (no constructor called)
        with pytest.raises((ExecutionError, ValidationError), match="missing"):
            validate_pfig(ast)

    def test_validation_detects_non_callable_entrypoint(self):
        """Verify validation detects non-callable entrypoint statically.

        We should be able to tell that a literal value (like an int) is not
        callable without materializing the entire config.
        """
        source = """
x = 42

__entrypoint__ = @x
"""
        ast = parse_pfig_source(source, filename="test.pfig")

        with pytest.raises(ExecutionError, match="must be callable"):
            validate_pfig(ast)


class TestCompilePfig:
    """Tests for compile_pfig — compilation without requiring an entrypoint."""

    def test_valid_config_no_entrypoint(self):
        """compile_pfig succeeds on a file without __entrypoint__."""
        source = """
from collections import OrderedDict
data = OrderedDict(a=1, b=2)
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiled, issues = compile_pfig(ast)

        assert compiled, "compiled dict should not be empty"
        assert issues == [], f"expected no issues, got {issues}"

    def test_valid_config_with_entrypoint(self):
        """compile_pfig also works fine when __entrypoint__ is present."""
        source = """
from collections import OrderedDict
data = OrderedDict(a=1, b=2)
__entrypoint__ = @data.keys
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiled, issues = compile_pfig(ast)

        assert compiled
        assert issues == []

    def test_returns_issues_for_duplicate_binding(self):
        """Duplicate-binding issues are collected, not raised."""
        source = """
x = 1
x = 2
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiled, issues = compile_pfig(ast)

        assert len(issues) > 0
        codes = [i.code for i in issues]
        assert "duplicate_binding" in codes, f"expected duplicate_binding, got {codes}"

    def test_returns_issues_for_missing_externals(self):
        """Missing externals are reported as issues, not raised."""
        source = """
external batch_size
x = @batch_size
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiled, issues = compile_pfig(ast)

        codes = [i.code for i in issues]
        assert "missing_external" in codes

    def test_allow_missing_externals_suppresses(self):
        """allow_missing_externals=True suppresses missing-external issues."""
        source = """
external batch_size
x = @batch_size
"""
        ast = parse_pfig_source(source, filename="test.pfig")
        compiled, issues = compile_pfig(ast, allow_missing_externals=True)

        codes = [i.code for i in issues]
        assert "missing_external" not in codes
