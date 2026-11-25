# Design TODOs

## Reconsider explicit sharing of config files between projects

Mounts and `package://` includes have been removed. For now, copy/vendor shared
`.pfig` files into each project and use explicit relative includes. Python imports
resolve Python code, not configuration files.

Reconsider named config-source roots only when real cross-project workflows show
that manually propagating changes is a material problem. Do not add implicit
search paths or discover config files through Python's package/import environment.

Before proposing a replacement, establish:

- Concrete use cases that copying/vendoring and relative includes cannot serve well.
- How shared config revisions and compatibility are managed; location aliases alone
  do not solve distribution or versioning.
- Explicit path anchoring, scope, conflict handling, and override precedence.
- Consistent resolution across the API, CLI, editor, snapshots, and replay.
- Provenance and portable bundling of the entire relative include tree.

Keep the smallest useful mechanism; do not assume sharing needs a new keyword,
provider references, or a virtual filesystem.
