---
description: Writing *.pfig files or writing example pfig file text in tests or to the command line
pattern: **/*.pfig
---

# pfig Spec (Agent Reference)

Wiring-only DSL: constructors + kwargs. No instance method calls, conditionals, or loops.

## Syntax

**@ sigil:** `@name` = shared (memoized), `@name()` / `@name(k=v)` = fresh with kwargs overlay, `@name.attr[.attr...]` = attribute or bound method (any depth). Provider = recipe. `@name.attr()` is not a call.

**Keywords:** `import` / `from ... import ...` | `include "path" as alias` (explicit filesystem path, relative to containing file) | `override path = expr` | `external path` (path = name or alias/name)

**Bindings:** `name = value` or `name = Class(kwarg=@other)`. Bare identifiers only (no `/`, no `-`, no leading digit).

## Namespace Resolution

| Form | Meaning |
|------|---------|
| `name` | Local binding |
| `alias/symbol` | From include |
| `module.Symbol` | Python import (no `/`) |
| `@name` | Config provider/value ref |

Slash `/` is ONLY for include namespaces. Cannot define `foo/bar = ...`.

## Reference Rules

**Use @ for:** pfig bindings (`@model`, `@consts/batch`), fresh calls (`@base_dl(shuffle=True)`), attribute and method chains (`@trainer.fit`, `@model.encoder.weight`, `@outer.data.copy`)

**No @ for:** Python symbols (`torch.optim.Adam`, `nn.Linear`, `helpers.get_params`). Use helpers for instance methods: `params=helpers.get_params(@model)`.

## Values & Expressions

**Supported:** primitives (incl. `1_000_000`), `[list]`, `(tuple)`, `{dict}`, `+ - * / // **`, unary `-`, string `+`, `len`/`min`/`max`/`range`

**Not supported:** conditionals, lambdas, comprehensions, instance method calls, positional args (use kwargs)

## Externals & Profiles

`external path` = runtime knob (path = name or alias/name), set via `--set` or `--profile`. Missing → validation error.

Profiles: `override path = expr` (externals only). Profiles cannot override normal values.

## Reserved / Magic

`__entrypoint__` = zero-argument callable for run. `__snapshot__` = snapshot destination (CLI supports string literals/direct refs; Python `run()` also evaluates expressions).

## Declarative Resolution

Each file must explicitly include the dependencies it references. Parent/sibling
bindings cannot satisfy unresolved references in an included file.

Aliases of the same source binding share one effective definition, including
values and externals. Overrides propagate through every alias. Inclusion ancestors
win over dependency overrides; siblings have no depth-based precedence. Each file
may author one override per canonical binding: duplicates through the same or
different aliases fail even when identical. Multiple sibling overrides need an
explicit ancestor override, even when identical. Reaching one authored override
through several include paths is not a duplicate. Statement order never selects
a winner. Self-reference cycles are errors; fresh calls do not retain an overridden recipe.

Supply repeated external aliases once. Later profiles win canonically, followed by
CLI/API inputs; conflicting assignments in one layer fail. Profiles target externals only.

## File Order

Recommended layout (not precedence): imports → includes → externals → values → providers → `__entrypoint__`

## Patterns

**Partial provider:** `base_dl = DataLoader(batch_size=64)` then `@base_dl(dataset=@train_ds, shuffle=True)`

**Fresh variant:** `loader = @base/dl(num_workers=8)` (a separate binding; do not assume self-overrides preserve the old recipe)

**Include:** `include "./consts.pfig" as consts` → `@consts/batch`

## Rules

- All includes need `as alias`
- Shadowing requires `override path = expr` (not `path = expr`)
- Includes use explicit filesystem paths; no config discovery via Python packages
- No instance method calls

## Validation Checklist

Imports first; includes have `as alias`; externals declared; shadowing uses `override`; bare identifiers only; `@` for config refs not Python symbols; no instance method calls; `__entrypoint__` or `--entrypoint`

## Common Errors

```pfig
# ❌ data/batch = 64  # no slash in bindings
# ❌ batch = 128      # if base/batch exists, use override base/batch = 128
# ❌ @torch.optim.Adam  # no @ for Python symbols
# ❌ @model.forward(x)  # no instance method calls
# ❌ override output_dir = "./runs"  # in main config — only profiles override externals
# ❌ override learning_rate = 0.01  # in profile — profiles only override externals
```

## CLI

`pfig view`, `pfig validate`, `pfig run`, `pfig diff`, `pfig bundle`, `pfig fmt`, `pfig replay`, `pfig install-rules`. Global verbosity precedes the command: `pfig -v run config.pfig`.

`--entrypoint` overrides `__entrypoint__`. It may be a binding name (`trainer`) or an attribute path (`trainer.fit`, `dataset/dataset_manager.upload`, `outer.data.copy`). The longest prefix that names a binding is the object; remaining segments use the normal attribute chain, and the last one must be callable.

`--profile profiles/local.pfig` applies a profile at the root namespace. `--profile train::profiles/local.pfig` applies it at the `train/` prefix (every `override` inside is prefixed with `train/` before validation).
