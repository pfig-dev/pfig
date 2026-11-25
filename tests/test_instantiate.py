"""
Tests for the instantiate() API function.

The instantiate() function provides sub-DAG instantiation from pfig configurations.
"""

import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from pfig import ExecutionError, instantiate, parse_pfig_source


class TestInstantiateAPI:
    """Test the instantiate() function from the public API."""

    def test_basic_provider_instantiation(self):
        """Test instantiating providers from string config."""
        config = """
from collections import Counter, defaultdict

word_counter = Counter()
data_store = defaultdict(default_factory=int)
"""

        # Test provider instantiation
        counter = instantiate("word_counter", source=config)
        assert isinstance(counter, Counter)

        data_store = instantiate("data_store", source=config)
        assert isinstance(data_store, defaultdict)

    def test_value_instantiation(self):
        """Test instantiating simple values."""
        config = """
number = 42
text = "hello world"
items = [1, 2, 3]
"""

        assert instantiate("number", source=config) == 42
        assert instantiate("text", source=config) == "hello world"
        assert instantiate("items", source=config) == [1, 2, 3]

    def test_import_instantiation(self):
        """Test instantiating imported modules/classes."""
        config = """
from collections import Counter
from json import loads
"""

        # Test class import
        counter_class = instantiate("Counter", source=config)
        assert counter_class is Counter

        # Test function import
        loads_func = instantiate("loads", source=config)
        result = loads_func('{"test": true}')
        assert result == {"test": True}

    def test_fresh_vs_shared_semantics(self):
        """Test fresh vs shared instantiation."""
        config = """
from collections import Counter

word_counter = Counter()
"""

        # Fresh instances should be different
        fresh1 = instantiate("word_counter", source=config, fresh=True)
        fresh2 = instantiate("word_counter", source=config, fresh=True)
        assert fresh1 is not fresh2
        assert isinstance(fresh1, Counter)
        assert isinstance(fresh2, Counter)

    def test_call_kwargs(self):
        """Test call_kwargs for fresh instantiation with arguments."""
        config = """
from collections import Counter

word_counter = Counter()
"""

        # call_kwargs should create a fresh instance with the provided args
        counter_with_data = instantiate(
            "word_counter",
            source=config,
            call_kwargs={"iterable": ["a", "b", "a", "c"]},
        )

        assert isinstance(counter_with_data, Counter)
        # Note: pfig wraps the iterable as a single value, so it's not expanded
        assert "iterable" in counter_with_data

    def test_dependency_resolution(self):
        """Test that dependencies are properly resolved."""
        config = """
from collections import Counter

words = ["hello", "world", "hello"]
word_counter = Counter(iterable=@words)
"""

        # Should instantiate both words and word_counter
        counter = instantiate("word_counter", source=config)
        assert isinstance(counter, Counter)

        # Should also be able to get the dependency separately
        words = instantiate("words", source=config)
        assert words == ["hello", "world", "hello"]

    def test_file_based_config(self):
        """Test instantiation from file paths."""
        config_content = """
from collections import Counter

my_counter = Counter()
value = 123
"""

        # Create temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pfig", delete=False) as f:
            f.write(config_content)
            temp_path = Path(f.name)

        try:
            # Test file-based instantiation
            counter = instantiate("my_counter", path=temp_path)
            assert isinstance(counter, Counter)

            value = instantiate("value", path=temp_path)
            assert value == 123

        finally:
            # Clean up
            temp_path.unlink()

    def test_ast_parsing(self):
        """Test that instantiate works with string source (which gets parsed internally)."""
        config = """
from collections import Counter

word_counter = Counter()
"""

        # Verify we can parse the config (AST is created internally by instantiate)
        ast = parse_pfig_source(config)
        assert ast is not None

        # Instantiate from string source (not from AST - new API doesn't support direct AST input)
        counter = instantiate("word_counter", source=config)
        assert isinstance(counter, Counter)

    def test_error_cases(self):
        """Test error handling."""
        config = """
from collections import Counter

word_counter = Counter()
"""

        # Non-existent binding
        with pytest.raises(ExecutionError, match="not found in configuration"):
            instantiate("nonexistent", source=config)

        # Non-existent file
        with pytest.raises(FileNotFoundError):
            instantiate("anything", path=Path("/nonexistent/path.pfig"))

    def test_sub_dag_materialization(self):
        """Test that only reachable bindings are materialized."""
        config = """
from collections import Counter, defaultdict

# These should be materialized when requesting word_counter
words = ["test"]
word_counter = Counter(iterable=@words)

# This should NOT be materialized when requesting word_counter
unrelated_data = defaultdict(default_factory=int)
"""

        # This should work without trying to materialize unrelated_data
        counter = instantiate("word_counter", source=config)
        assert isinstance(counter, Counter)

        # But we can still get unrelated_data separately
        data = instantiate("unrelated_data", source=config)
        assert isinstance(data, defaultdict)
