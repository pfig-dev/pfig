---
description: pfig syntax, limitations, mental model, and best practices
pattern: **/*.pfig
---

# pfig Developer Guide

## Mental Model

Wiring-only DSL: constructors + kwargs. No instance method calls, conditionals, or loops. Execution traces from `__entrypoint__` backward; objects instantiated lazily.

- **WHAT** (algorithm, hyperparams) → values and providers
- **HOW** (device, paths, workers) → `external`, set via `--set` or `--profile`

**Minimal example:**
```pfig
from train import Model, Trainer
include "./consts.pfig" as consts
external device

model = Model(hidden_size=@consts/hidden_size)
trainer = Trainer(model=@model, device=@device, epochs=@consts/epochs)
__entrypoint__ = @trainer.fit
```

## Syntax Cheat Sheet

| Syntax | Use |
|--------|-----|
| `name = value` | Value binding (primitives, containers, expressions) |
| `provider = Class(kwarg=@other)` | Provider recipe (lazy, memoized when referenced) |
| `@name` | Shared instance (same object every time) |
| `@name()` | Fresh instance (new object each time) |
| `@name(k=v)` | Fresh instance with kwarg overlay |
| `@provider.attr[.attr...]` | Attribute or bound method, any depth |
| `include "./file.pfig" as alias` | Include with namespace; reference as `@alias/symbol` |
| `override alias/name = value` | Replace included binding (explicit only) |
| `external name` | Runtime knob — must be set via `--set` or profile |
| `__entrypoint__` | Callable for `pfig run` |

**Critical:** `@` only for pfig bindings, never for Python symbols.
```pfig
# ✅ Correct
optimizer = torch.optim.Adam(params=helpers.get_params(@model), lr=@lr)

# ❌ Wrong - no `@` for python symbols, no instance method calls on providers
optimizer = @torch.optim.Adam(params=@model.parameters(), lr=@lr)
```

## Limitations

- No conditionals, lambdas, loops, list comprehensions
- No instance method calls — use `@provider.attr.method` (the method itself) or `@provider.attr` (a field), not `@provider.method()`
- No positional args — keyword only: `Model(input_dim=784, output_dim=10)`
- No bindings with `/` — slash is for namespace refs only
- Profiles: only override externals

## File Order

imports → includes → externals → values → providers → `__entrypoint__`

## Best Practices

### 1. Externals vs values

`external` for runtime (device, paths, workers); values for algorithm params.

### 2. Includes

Split by concern: `consts.pfig`, `data.pfig`, `model.pfig`, `main.pfig`. Use explicit relative includes; copy/vendor config files for cross-project sharing.

```pfig
include "./consts.pfig" as consts
trainer = Trainer(model=@model, lr=@consts/lr)
```

### 3. Profiles

Override externals only. Example: `override device = "cpu"`. Run: `pfig run main.pfig --profile profiles/local.pfig`

### 4. Experiments

Include base, override values. Providers in base use overridden values automatically.

```pfig
include "../main.pfig" as base
override base/consts/learning_rate = 0.01
__entrypoint__ = @base/__entrypoint__
```

### 5. Partial providers

Base recipe missing args; close with fresh calls:
```pfig
base_dl = torch.utils.data.DataLoader(batch_size=64)  # missing dataset
train_dl = @base_dl(dataset=@train_ds, shuffle=True)
val_dl = @base_dl(dataset=@val_ds, shuffle=False)
```

### 6. Helper functions

pfig cannot call instance methods. Use module-level helpers: `create_optimizer(optimizer_class=optim.Adam, model=@model, lr=@lr)`

### 7. Plain function entrypoint

Expose a keyword-friendly Python factory returning a zero-argument callable,
e.g. `def training_entrypoint(*, model, epochs): return partial(train, model=model, epochs=epochs)`.
Then `__entrypoint__ = training_entrypoint(model=@model, epochs=@epochs)`.
`pfig.entrypoint` itself has a positional-only `fn` parameter and is Python-only
until the DSL supports positional arguments.

### 8. Snapshots

For CLI runs, use `__snapshot__ = @output_dir` with a string external, or
`--snapshot ./snapshots/experiment`. Python `run()` also supports computed directive
expressions such as `"./snapshots/" + @experiment`. Validate before run; keep Git clean.

## Common Mistakes

- `@torch.optim.Adam` → wrong. Use `torch.optim.Adam` (no `@` for Python symbols)
- Shadowing: use `override base/lr = 0.01`, not `lr = 0.01`
- Profiles cannot override non-externals

## CLI

`pfig validate`, `pfig run`, `pfig diff`, `pfig bundle`, `pfig fmt`

`--entrypoint` overrides `__entrypoint__`. Pass a binding name (`trainer`) or an attribute path (`trainer.fit`, `outer.data.copy`). pfig uses the longest binding-name prefix, then walks the remaining attributes. The last attribute must be callable.

## See Also

- [pfig-spec](__RULES_DIR__/pfig-spec__RULES_EXT__) — Full syntax, validation rules
- [pfig-friendly-python](__RULES_DIR__/pfig-friendly-python__RULES_EXT__) — Python code structure
