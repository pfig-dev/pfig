# Contributing to pfig

Thanks for your interest in contributing! pfig is an open-source project and we welcome bug reports, feature requests, and pull requests.

## Quick start

```bash
git clone https://github.com/pfig-dev/pfig.git
cd pfig
pip install -e ".[dev,remote,lsp]"
pre-commit install
pre-commit run --all-files
pytest
```

## Reporting bugs

File an issue at https://github.com/pfig-dev/pfig/issues with:

- A minimal reproducing `.pfig` snippet (and any Python it depends on).
- What you expected to happen, what actually happened.
- pfig version (`pfig --version`) and Python version.

## Submitting changes

1. Fork the repo and create a topic branch from `main`.
2. Make your change, including tests.
3. Run `pre-commit run --all-files` and `pytest` locally. The pre-commit checks include
   formatting, import sorting, and pyright type checking for the `pfig` package.
4. Open a pull request against `main`. Reference any related issue.

By submitting a pull request you agree that your contribution is licensed under the project's Apache 2.0 license.

## Full developer guide

For architecture, debugging, code style, and the full development workflow, see [docs/developer/developer_guide.md](docs/developer/developer_guide.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you agree to abide by its terms.

## Security

Please **do not** file security issues publicly. See [SECURITY.md](SECURITY.md) for how to report vulnerabilities.
