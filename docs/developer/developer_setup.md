# Development Setup

This guide covers setting up your development environment for contributing to pfig.

## Prerequisites

- Python 3.10+
- Node.js and npm, for graph viewer frontend changes
- Basic understanding of Python imports and class instantiation
- Familiarity with compiler/interpreter concepts (helpful but not required)

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/pfig-dev/pfig.git
   cd pfig
   ```

2. **Install the package in editable mode:**
   ```bash
   pip install -e ".[dev,remote,lsp]"
   ```

   This installs pfig in development mode with development dependencies, so changes to the source code are immediately available without reinstalling.

3. **Verify installation:**
   ```bash
   pfig run examples/hello_world.pfig
   pytest  # Run test suite
   pre-commit run --all-files
   ```

4. **Run type checking directly when needed:**
   ```bash
   pyright
   ```

   Pyright is included in the `dev` extra and enforced by CI through pre-commit. The
   pre-commit hook uses `language: system`, so run it from an environment where
   `pyright` is on `PATH` (for example, an activated virtual environment after
   installing `.[dev,remote,lsp]`).

## Graph viewer frontend setup

The graph viewer frontend source lives in `webview/`. Its production build is
checked in under `pfig/vis/assets/` so installed Python packages can serve the
viewer without requiring Node.js.

Install frontend dependencies from the lockfile when working on graph UI code:

```bash
cd webview
npm ci
```

After editing files in `webview/src/`, run:

```bash
npm run test
npm run build
```

Commit both the source changes in `webview/` and the rebuilt output in
`pfig/vis/assets/`. Do not commit `webview/node_modules/`.

## Building documentation locally

```bash
pip install -e ".[docs]"
mkdocs serve          # Live preview
mkdocs build --strict # Check links, API rendering, and site generation
```

The docs extra keeps `pymdown-extensions` below 12 because its changed
highlighting API is not yet compatible with the mkdocstrings renderer.

## Publishing documentation

Documentation is built with MkDocs and deployed to [pfig.dev](https://pfig.dev/) via Cloudflare Pages on pushes to `main`.

1. In Cloudflare, use the Pages project named `pfig` with `main` as its production
   branch and `pfig.dev` attached as a custom domain. Reuse the existing project
   if present. If it was connected to GitHub, disable automatic Git deployments
   so Wrangler is the only publisher.
2. Under **Settings → Secrets and variables → Actions** in `pfig-dev/pfig`, add:
   - `CLOUDFLARE_API_TOKEN` — a scoped token with **Account → Cloudflare Pages → Edit** for the account that owns the project
   - `CLOUDFLARE_ACCOUNT_ID` — account ID from the Cloudflare dashboard
3. Merge to `main` or manually run [Deploy docs](https://github.com/pfig-dev/pfig/actions/workflows/docs.yml)
   on `main`. The workflow builds with `mkdocs build --strict` and uploads `site/`
   with Wrangler. Verify the production deployment in Cloudflare and at `pfig.dev`.

## Next Steps

- Read the [Architecture](architecture.md) guide to understand pfig internals
- Check out the [Development Workflow](development_workflow.md) for how to work on pfig
- See [Contributing](contributing.md) for contribution guidelines
