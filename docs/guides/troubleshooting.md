# Troubleshooting

## Testing pfig Configs

```python
def test_my_config():
    config = '''
    import json
    data = json.loads(s='{"test": true}')
    result = @data
    '''

    from pfig import instantiate
    result = instantiate("result", source=config)
    assert result == {"test": True}
```

## Common Error Types

- **ParseError**: Syntax errors in pfig source
- **ValidationError**: Static validation errors (unknown bindings, missing kwargs, cycles)
- **ExecutionError**: Runtime errors during materialization
- **LexerError**: Tokenization errors

## Validation Errors

pfig collects multiple validation issues where possible and reports them together,
sorted by source position. Some failures (including parsing and profile-loading
errors) stop processing immediately:

```
Validation failed (2 issues):
  - config.pfig:15:1 - Unknown binding: 'learning_rat' (referenced by 'optimizer') - did you mean 'learning_rate'?
  - config.pfig:24:1 - Provider 'trainer' is missing required kwargs ['model'] and is used via @trainer.fit
```

The "did you mean" suggestions help catch typos in binding names.

## Common Issues

1. **Parse Errors**: Check syntax against examples, ensure proper indentation
2. **Validation Errors**: Review all reported issues - they include file/line positions and suggestions
3. **Execution Errors**: Look for runtime issues during object instantiation
4. **Provider Issues**: Verify shared vs fresh semantics match expectations (`@provider` vs `@provider()`)
5. **Include Problems**: Check file paths are correct and aliases are used properly

## Error Location Tracking

Errors include source position information where available:

```
config.pfig:15:8: Cannot resolve provider reference '@missing_provider'
```

## Debugging

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Inspect AST
ast = parse_pfig_source(config)
print(ast)

# Inspect compiled form
from pfig.execution import ASTCompiler
compiler = ASTCompiler()
compiled = compiler.compile_file(ast)
print(compiled["providers"])
print(compiled["values"])
```

