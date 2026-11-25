# Getting Started with pfig

This guide will help you install pfig, run your first config, and learn the core
concepts. For larger workflows, continue to [Patterns & Best Practices](patterns.md).

## Installation

```bash
pip install pfig
```

Until the first PyPI release is published, install from the repository:

```bash
pip install git+https://github.com/pfig-dev/pfig.git
```

If you are contributing to pfig, see the [Developer Setup](../developer/developer_setup.md)
guide for editable installation and development dependencies.

## Your First pfig Config

Create `hello.pfig`:

```pfig
# hello.pfig
from decimal import Decimal
from pfig.utils import Printer

# A provider (lazy recipe)
pi = Decimal(value="3.14159")

printer = Printer(object=@pi)
# Printer instance is callable, so it can be used as an entrypoint
__entrypoint__ = @printer
```

Run it:

```bash
pfig run hello.pfig
```

This prints `3.14159`. pfig builds the objects needed by `__entrypoint__`, then
calls it with no arguments. CLI runs also create a reproducibility snapshot by
default; see the [Snapshots Guide](snapshots.md).

Only run configs from sources you trust: imports and object construction execute
Python code.

## Core Concepts

### 1. Providers (Lazy Recipes)

Providers are recipes for creating objects, defined by function or constructor
calls. Execution starts from the entrypoint and instantiates only what's needed:

```pfig
model = MyModel(hidden_size=128)
```

The fragments below illustrate the syntax; in a complete config, import the
Python callables and define their dependencies as in `hello.pfig` above.

### 2. Provider References

Use `@name` for shared instances (the same object for every reference within a
run) or `@name()` for fresh construction:

```pfig
shared = @model
fresh = @model()
```

Python symbols from imports do not use `@`. Attributes and methods on providers
can be referenced with `@model.encoder.weight` or `@trainer.fit`.

### 3. Includes

Modularize configs with explicit namespaces.

`base.pfig`:

```pfig
# base.pfig
epochs = 10
learning_rate = 0.001
```

A config can include those values:

```pfig
# config.pfig
include "./base.pfig" as base
model = Model(epochs=@base/epochs, lr=@base/learning_rate)
```

Paths are relative to the including file. The alias keeps included bindings in
their own namespace; `@base/epochs` refers to the value in `base.pfig`.

### 4. Externals

Externals separate the runtime environment from algorithm parameters:

```pfig
external device
external data_dir

trainer = Trainer(device=@device, data_dir=@data_dir)
```

Supply them when running your complete config:

```bash
pfig run config.pfig --set device=cuda data_dir=/path/to/data
```

### 5. Overrides

Layer configurations by including a base file and replacing named bindings.
Using `base.pfig` above, an experiment config can change the epoch count:

```pfig
# experiment.pfig
include "./base.pfig" as base
override base/epochs = 20
# Add the rest of your wiring here, including __entrypoint__.
```

Overrides replace whole bindings, not object fields. For a complete layering
example, see [Layer experiments](patterns.md#layer-experiments-with-includes-and-overrides).

## Next Steps

- [Patterns & Best Practices](patterns.md) — structure projects, layer experiments,
  reuse recipes, wire functions, and choose environment profiles
- [Syntax Reference](../reference/syntax_reference.md) — supported language constructs
- [CLI Reference](../reference/cli.md) — available commands, including
  [formatting](../reference/cli.md#fmt) and [AI agent rules](../reference/cli.md#install-rules)
- [Snapshots Guide](snapshots.md) — reproducibility, warnings, and replay
- [Examples](https://github.com/pfig-dev/pfig/tree/main/examples) — complete workflows
- [Editor support](../reference/lsp_api.md#bundled-language-server) — optional language server;
  syntax highlighting is available via the [VS Code extension](https://github.com/pfig-dev/vscode-pfig)
