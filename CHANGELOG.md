# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Floor division (`//`) in config expressions, with the same precedence as `*` and `/`.
- Open-source launch readiness: docs, CI, security hardening, examples, and publish workflows.

### Changed
- Execution and targeted validation require only effective reachable externals; rootless compilation keeps whole-file diagnostics. Fresh overlays no longer require or construct replaced arguments. Snapshot roots are checked before construction, and CLI computed snapshots preserve shared identity with the entrypoint. Scoped snapshots record effective fresh recipes for replay.
- Include aliases now share effective values, providers, and externals. Overrides resolve by inclusion ancestry, not statement order. Duplicate same-file overrides and unresolved sibling overrides fail before construction, even when identical; diamond includes may reuse one authored declaration.
- References must resolve in their authoring file's explicit dependency scope. Parent roots no longer supply ambient sibling names.
- Runtime inputs target canonical bindings. Supply repeated externals once; ordered profiles retain precedence across aliases, while conflicting assignments in one input layer fail. Runtime comparison supports cyclic containers without invoking opaque Python object equality. Repeated override paths within a profile are errors even when identical.
- Snapshot schema is now 2.0. Replay rejects older schemas; use the recorded pfig version for older snapshots. Logs remain readable for inspection.

### Removed
- Mount statements, `--mount`, mount API/settings parameters, and `package://` config discovery. Use explicit filesystem includes and copy/vendor shared `.pfig` files instead.
- Mount mappings in newly generated snapshots and bundles. Bundle format v2 preserves filesystem include trees; filesystem-only v1 bundles remain readable, while mounted v1 bundles must be recreated.

[Unreleased]: https://github.com/pfig-dev/pfig/compare/main...HEAD
