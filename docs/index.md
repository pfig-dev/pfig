<img src="assets/pfig-text-192.png" alt="pfig logo" class="pfig-logo-light" />
<img src="assets/pfig-text-192-white.png" alt="pfig logo" class="pfig-logo-dark" />

[![CI](https://github.com/pfig-dev/pfig/actions/workflows/ci.yml/badge.svg)](https://github.com/pfig-dev/pfig/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/pfig.svg)](https://pypi.org/project/pfig/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/pfig-dev/pfig/blob/main/LICENSE)

**A tiny, strict DSL for declarative Python object configuration**

pfig separates *what* you build (algorithm) from *how* you run it (environment). Built for ML workflows.

## Install

```bash
pip install pfig
```

For a complete walkthrough, see **[Getting Started](guides/getting-started.md)**.

## Quick Example

```pfig
# config.pfig
import torch
from train import Model, Trainer

include "./consts.pfig" as consts
external device

model = Model(hidden_size=@consts/hidden_size)
trainer = Trainer(model=@model, epochs=@consts/epochs, device=@device)
__entrypoint__ = @trainer.fit
```

```bash
pfig run config.pfig --set device=cuda
```

## Security

pfig configs are **trusted inputs**. Running `pfig validate`, `pfig run`, or loading a bundle/replay snapshot can import Python modules, instantiate objects, clone git repositories, and install dependencies. See [SECURITY.md](https://github.com/pfig-dev/pfig/blob/main/SECURITY.md).

## Key Features

- **Providers**: Lazy recipes with `@name` (shared) and `@name()` (fresh) instances
- **Includes**: Modular configs with `include "./base.pfig" as base`
- **Externals**: Runtime environment separation
- **Overrides**: Config layering with `override base/param = value`
- **Snapshots**: Automatic reproducibility tracking with Git state and environment capture
- **Graph viewer**: Inspect dependencies with [`pfig view`](reference/cli.md#view) or compare two plans with [`pfig diff-view`](reference/cli.md#diff-view)

## Formatting

`pfig fmt` rewrites `.pfig` files to a canonical style. See the [CLI reference](reference/cli.md#fmt).

## Editor and AI agent support

- **`pfig install-rules`**: Install agent rules for Cursor, Claude Code, or Codex — see [CLI reference](reference/cli.md#install-rules)
- **LSP server**: Diagnostics, go-to-definition, hover, completion, signature help, and semantic tokens — see [LSP API](reference/lsp_api.md)
- **VS Code extension**: Coming soon.

## Get Started

- 👉 **[Installation & Quick Start](guides/getting-started.md)** - Get up and running in minutes
- **[Patterns & Best Practices](guides/patterns.md)** - Structure configs, layer experiments, and reuse recipes
- 📖 **[Syntax Reference](reference/syntax_reference.md)** - Complete syntax reference
- 🔧 **[CLI Reference](reference/cli.md)** - Commands for validate, run, diff, fmt, and more
- 📸 **[Snapshots Guide](guides/snapshots.md)** - Reproducibility and experiment tracking
- 📋 **[Specification](developer/spec.md)** - Complete language specification

## Resources

- 🔗 **[Repository](https://github.com/pfig-dev/pfig)** - Source code and issue tracking
- 📚 **[Examples](https://github.com/pfig-dev/pfig/tree/main/examples)** - Working examples and use cases
- 🐛 **[Issues](https://github.com/pfig-dev/pfig/issues)** - Report bugs or request features
- 🤝 **[Contributing](developer/contributing.md)** - How to contribute to pfig

*pfig: Explicit wiring, zero magic*
