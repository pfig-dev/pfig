# Development Workflow

This guide covers how to work on pfig code, add features, and write tests.

## Code Structure Guidelines

- **Keep AST nodes simple**: They should only represent structure, no business logic. Simple convenience methods (like `PFigFile.imports()` or `__str__()` for formatting) are acceptable, but complex logic belongs elsewhere.
- **Put logic in execution**: Business logic (dependency resolution, materialization, cycle detection, etc.) belongs in `execution.py`
- **Maintain position tracking**: All AST nodes inherit from `ASTNode` which tracks `position: Position` for error reporting
- **Add comprehensive tests**: Every feature should have positive and negative test cases
- **Follow error message conventions**: Clear, actionable error messages with source positions. Use `ExecutionError(message, position=...)` to include position information.

## Adding New Features

When adding a new feature, follow this order:

1. **Update lexer** (if new syntax) in `lexer.py` - Add new tokens/keywords
2. **Update AST nodes** (if needed) in `ast_nodes.py` - Define new node types (depends on lexer for `Position`)
3. **Update parser** to handle new syntax in `parser.py` - Parse tokens into AST nodes (depends on lexer and AST nodes)
4. **Update execution** to handle new semantics in `execution.py` - Implement runtime behavior (depends on AST nodes and parser)
5. **Add tests** in appropriate `test_*.py` file
6. **Update documentation**

**Note**: If you're only adding new semantics (not new syntax), you may skip steps 1-3 and go directly to execution. If you're only adding new AST node types without new syntax, you may skip step 1.

## Running Tests

Make sure your virtual environment is activated and dev dependencies are installed (`pip install -e ".[dev,remote,lsp]"`):

```bash
# All tests
pytest

# Specific test file
pytest tests/test_execution.py

# Specific test function
pytest tests/test_execution.py::TestLiteralValues::test_simple_literals

# With verbose output
pytest -v

# With coverage (requires pytest-cov from dev dependencies)
pytest --cov=pfig tests/
```

For graph viewer frontend changes, run the frontend checks from `webview/`:

```bash
npm ci
npm run test
npm run build
```

`webview/` contains the editable React/TypeScript source. `npm run build`
updates `pfig/vis/assets/`, which is the static viewer served by
`pfig view` and included in Python package data. Commit the rebuilt
assets with the frontend source so the packaged viewer matches the source.

## Test Structure

The test suite covers the following components:

**Core Components:**

- `tests/test_lexer.py` - Tokenization tests
- `tests/test_parser.py` - AST parsing tests
- `tests/test_execution.py` - Execution and materialization tests
- `tests/test_errors.py` - Error handling and validation tests

**Features:**

- `tests/test_integration.py` - End-to-end workflow tests
- `tests/test_includes.py` - Include system tests
- `tests/test_overrides.py` - Override system tests
- `tests/test_externals.py` - External parameters tests
- `tests/test_namespaced_method_refs.py` - Provider method references tests
- `tests/test_fresh_chaining.py` - Fresh instance chaining tests
- `tests/test_builtin_namespace_separation.py` - Namespace separation tests

**Infrastructure:**

- `tests/test_cli.py` - CLI command tests
- `tests/test_diff.py` - Configuration diffing tests
- `tests/test_instantiate.py` - Instantiate function tests

## Writing Tests

Follow existing patterns:

**Most common pattern - using `get_materialized_bindings`:**

```python
def test_my_feature():
    config = '''
    import json
    data = json.loads(s='{"test": true}')
    result = @data
    '''

    from pfig import parse_pfig_source, get_materialized_bindings
    ast = parse_pfig_source(config, filename="test.pfig")
    result = get_materialized_bindings(ast, entrypoint="result")
    assert result["result"] == {'test': True}
```

**Using `execute_pfig` with a method reference:**

```python
def test_my_feature():
    config = '''
    from collections import defaultdict

    counter = defaultdict(default_factory=int)
    # Use a method as entrypoint
    __entrypoint__ = @counter.clear
    '''

    from pfig import parse_pfig_source, execute_pfig
    ast = parse_pfig_source(config, filename="test.pfig")
    # Method will be called, returns None for clear()
    result = execute_pfig(ast)
    assert result is None
```

**Testing error conditions:**

```python
def test_my_feature_error():
    config = '''
    import nonexistent_module
    result = nonexistent_module.function()
    '''

    from pfig import parse_pfig_source, execute_pfig, ValidationError
    import pytest

    ast = parse_pfig_source(config, filename="test.pfig")
    with pytest.raises(ValidationError):
        execute_pfig(ast)
```

## Further Reading

- [Architecture](architecture.md) - Understanding pfig internals
- [Contributing](contributing.md) - Contribution guidelines
- [Developer Debugging](developer_debugging.md) - Debugging tools for developers
