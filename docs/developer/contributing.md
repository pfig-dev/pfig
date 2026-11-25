# Contributing

This guide covers how to contribute to pfig. For the short OSS onboarding
path, see the [CONTRIBUTING.md](https://github.com/pfig-dev/pfig/blob/main/CONTRIBUTING.md)
file in the repository root.

## Code Style

- Follow PEP 8
- Use type hints for all public APIs
- Add docstrings for public functions and classes
- Keep lines under 100 characters when reasonable

Code formatting is enforced via pre-commit hooks using:

- **Ruff** - Code formatter and import sorter
- **Pyright** - Type checker for the `pfig` package

## Pre-commit Setup

Pre-commit hooks automatically format and check your code before commits. Set them up:

1. **Install pre-commit** (if not already installed):
   ```bash
   pip install -e ".[dev,remote,lsp]"
   ```

2. **Install the hooks**:
   ```bash
   pre-commit install
   ```

3. **Test the hooks** (optional):
   ```bash
   pre-commit run --all-files
   ```

The hooks will automatically run on `git commit`. To manually run them:
```bash
pre-commit run --all-files
```

CI runs the same pre-commit hook set, including pyright.

The pyright hook is a local `language: system` hook. If `pre-commit run --all-files`
reports `Executable 'pyright' not found`, activate the virtual environment where you
installed `.[dev,remote,lsp]` or otherwise ensure `pyright` is available on `PATH`.

## Commit Message Format

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <description>
```

Common types:

- `build` - Changes to build system or dependencies
- `ci` - Changes to CI configuration
- `chore` - Maintenance tasks
- `docs` - Documentation changes
- `feat` - New features
- `fix` - Bug fixes
- `perf` - Performance improvements
- `refactor` - Code refactoring
- `revert` - Revert previous commit
- `style` - Code style changes (formatting, etc.)
- `test` - Test additions or changes

Examples:

```
feat: add syntax highlighting for provider references
fix: correct pattern matching order
docs: update contributing guidelines
test: add tarball extraction safety regression tests
```

Breaking changes use `feat!:` or `fix!:` and should include a `BREAKING CHANGE:` footer when the impact is not obvious from the subject line.

## Releases

Version numbers follow [Semantic Versioning](https://semver.org/). Maintainers cut releases from tagged commits on `main` and publish release notes in [CHANGELOG.md](https://github.com/pfig-dev/pfig/blob/main/CHANGELOG.md).

## Pull Request Process

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/my-feature`
3. **Write tests** for your changes
4. **Ensure all tests pass**: `pytest`
5. **Update documentation** if needed
6. **Submit pull request** with clear description

## Areas for Contribution

- **Performance optimizations** in execution engine
- **Better error messages** and diagnostics
- **IDE integrations** - [VS Code extension](https://github.com/pfig-dev/vscode-pfig)
- **Additional CLI features** per specification
- **Documentation improvements**
- **Example configurations** for different ML frameworks

## Further Reading

- [Development Setup](developer_setup.md) - Setting up your environment
- [Development Workflow](development_workflow.md) - How to work on pfig
- [Architecture](architecture.md) - Understanding pfig internals
- [Specification](spec.md) - Complete language specification
