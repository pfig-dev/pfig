# ![pfig logo](https://pfig.dev/assets/pfig-text-192.png)

[![CI](https://github.com/pfig-dev/pfig/actions/workflows/ci.yml/badge.svg)](https://github.com/pfig-dev/pfig/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/pfig-dev/pfig/branch/main/graph/badge.svg)](https://codecov.io/gh/pfig-dev/pfig)
[![PyPI version](https://img.shields.io/pypi/v/pfig.svg)](https://pypi.org/project/pfig/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/pfig-dev/pfig/blob/main/LICENSE)

**A tiny, strict DSL for declarative Python object configuration**

pfig separates *what* you build (algorithm) from *how* you run it (environment). Built for ML workflows.

Our paper, [ML Configuration Artifacts Should Be Closed Under Review](https://openreview.net/forum?id=1l0lbV2pC2), was accepted to the **NeurIPS 2026 Position Paper Track**. See [Citation](#citation) for BibTeX.

## Quick Start

Requires Python 3.10 or newer.

```bash
pip install pfig
```

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

pfig configs are **trusted inputs**, not a sandboxed data format. Running `pfig validate`, `pfig run`, or loading a bundle/replay snapshot can import Python modules, instantiate objects, clone git repositories, and install dependencies from lock files. Only run configs, bundles, and snapshots from sources you trust.

## Core Syntax

| Syntax | Description |
|--------|-------------|
| `name = value` | Literal binding |
| `provider = Class(args)` | Lazy provider recipe |
| `@name` | Shared instance (memoized) |
| `@name()` | Fresh instance |
| `@provider.attr[.attr...]` | Attribute or bound method |
| `include "./file.pfig" as alias` | Include with namespace |
| `override alias/name = value` | Override included binding |
| `external name` | Runtime environment parameter |
| `__entrypoint__` | What to execute |

## Key Features

- **Providers**: Lazy recipes with `@name` (shared) and `@name()` (fresh) instances
- **Includes**: Modular configs with explicit filesystem paths: `include "./base.pfig" as base`. Copy/vendor shared configs into your project; no Python-package config discovery.
- **Externals**: Runtime environment separation
- **Overrides**: Config layering with `override base/param = value`
- **Snapshots**: Best-effort reproducibility with config bundles, Git patches, and environment capture
- **CLI**: Validate, run, diff, fmt, bundle, replay, view, diff-view, and install-rules

## Formatting

`pfig fmt` rewrites `.pfig` files to a canonical style — 4-space indent, 100-char lines, black-style magic trailing comma, sorted imports within blank-line groups. Comments are preserved.

```bash
pfig fmt config.pfig           # format in place
pfig fmt path/to/configs/      # recurse into a directory
pfig fmt --check config.pfig   # exit non-zero if it would change
pfig fmt --diff config.pfig    # show a unified diff
pfig fmt -                     # read stdin, write stdout
```

## Editor Language Server

The pfig LSP server is included as an optional extra. For VS Code, Neovim, or local
editor integration, install the extra and run the server as a module:

```bash
python -m pip install "pfig[lsp]"
python -m pfig.lsp
```

The server supports diagnostics, go-to-definition, hover, completion, signature
help, and semantic tokens for provider/value highlighting.

## Graph Viewer

The graph viewer is included with `pfig`. Open it in a browser with:

```bash
pfig view config.pfig --mode reachable
pfig diff-view base.pfig proposed.pfig
```

It serves a local React viewer backed by the same graph view model used by the
language server. Use `--mode all` to show every binding instead of only bindings
reachable from `__entrypoint__`. In standalone CLI views, clicking a reference
zooms to its graph node, opening its file in Overview and revealing the binding
if it was folded. Editor-integrated views use source navigation: VS Code's
webview or a Neovim `--reveal-url` callback.

The editable frontend source lives in `webview/`. The packaged runtime serves
the built files from `pfig/vis/assets/`, so frontend changes should be rebuilt
before committing:

```bash
cd webview
npm ci
npm run test
npm run build
```

## Documentation

📚 **[Full Documentation](https://pfig.dev/)** - Complete guide, API reference, and examples

- [Getting Started](https://pfig.dev/guides/getting-started/) - Quick start guide and tutorials
- [Reference](https://pfig.dev/reference/) - API reference, syntax, and CLI documentation
- [Developer Guide](https://pfig.dev/developer/developer_guide/) - Contributing guide and development docs

**Editor / AI agent support:**
- **AI agent rules:** Run `pfig install-rules <agent>` in your project to install rules that teach AI coding agents how to write `.pfig` files and pfig-friendly Python. Supported targets:
  - `cursor` — writes `.cursor/rules/*.mdc` (per-file rules with `globs:` for conditional loading)
  - `claude` — writes `.claude/rules/*.md` (per-file rules with `paths:` for conditional loading)
  - `codex` — writes a single concatenated `AGENTS.md` at the repo root
- **Editor integration:** See the [language server reference](https://pfig.dev/reference/lsp_api/#bundled-language-server) for setup and capabilities.
- **VS Code extension:** Coming soon.

*pfig: Explicit wiring, zero magic*

## Citation

If you use pfig in your research, please cite our paper:

```bibtex
@inproceedings{rudy2026ml,
  title     = {{ML} Configuration Artifacts Should Be Closed Under Review},
  author    = {Jan Rudy and Mohamad Khalil},
  booktitle = {The Fortieth Annual Conference on Neural Information Processing Systems Position Paper Track},
  year      = {2026},
  url       = {https://openreview.net/forum?id=1l0lbV2pC2}
}
```

## Community

- [Contributing](https://github.com/pfig-dev/pfig/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/pfig-dev/pfig/blob/main/SECURITY.md)
- [Code of conduct](https://github.com/pfig-dev/pfig/blob/main/CODE_OF_CONDUCT.md)
- [Changelog](https://github.com/pfig-dev/pfig/blob/main/CHANGELOG.md)

## License

pfig is licensed under [Apache-2.0](https://github.com/pfig-dev/pfig/blob/main/LICENSE). The bundled graph viewer
includes components under other licenses; see the
[third-party notices](https://github.com/pfig-dev/pfig/blob/main/pfig/vis/assets/THIRD_PARTY_NOTICES.txt).

## Honorable mentions

`pfig` originated during work at **Ocado Group plc** and is now personally maintained. **Ocado Group plc** is gratefully acknowledged for supporting the open-sourcing of the project.
