# Developer Debugging

For general troubleshooting of pfig configurations, see the [Troubleshooting](../guides/troubleshooting.md) guide.

## Developer Debugging Tools

When working on pfig itself, these tools can help:

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Inspect AST
from pfig import parse_pfig_source
ast = parse_pfig_source(config_text)
print(ast)

# Inspect compiled representation
from pfig.execution import ASTCompiler
compiler = ASTCompiler()
compiled = compiler.compile_file(ast)
print(compiled)

# Get materialized objects for inspection
from pfig import get_materialized_bindings
result = get_materialized_bindings(ast, entrypoint="my_binding")
```

## Further Reading

- [Development Workflow](development_workflow.md) - How to work on pfig
- [Architecture](architecture.md) - Understanding pfig internals
- [Troubleshooting](../guides/troubleshooting.md) - User-facing troubleshooting guide

