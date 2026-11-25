# Patterns & Best Practices

Once you have run your [first config](getting-started.md), use these patterns to
keep larger configurations explicit and easy to change. The examples below use
application-defined `Model`, `Trainer`, and training functions; see the
[MNIST example](https://github.com/pfig-dev/pfig/tree/main/examples/mnist_mlp)
for a complete training application.

## Separate parameters, wiring, and environment

Use this structure when a workflow needs several experiment variants or runs in
multiple environments:

- **Algorithm parameters** belong in ordinary bindings, optionally in a separate file.
- **Object wiring** belongs in the main config.
- **Environment settings**, such as device and data paths, are declared externals
  supplied by profiles or the CLI.

`consts.pfig`:

```pfig
# consts.pfig
hidden_size = 512
epochs = 10
```

`config.pfig`:

```pfig
# config.pfig
from train import Model, Trainer

include "./consts.pfig" as consts
external device
external data_dir

model = Model(hidden_size=@consts/hidden_size)
trainer = Trainer(
    model=@model,
    epochs=@consts/epochs,
    device=@device,
    data_dir=@data_dir,
)
__entrypoint__ = @trainer.fit
```

Keep Python behavior in Python: the config should show what depends on what,
not implement the training algorithm. Includes use explicit filesystem paths
relative to the file containing the include.

## Keep full defaults and derive subsets

Keep the complete graph as the baseline; variants should contain only deviations.

`full.pfig`:

```pfig
from collections import OrderedDict
external nvidia_root
external render_root
inputs = [@nvidia_root, @render_root]
experiment = OrderedDict(inputs=@inputs)
__entrypoint__ = @experiment.copy
```

`smoke.pfig`:

```pfig
include "./full.pfig" as full
override full/inputs = [@full/nvidia_root]
__entrypoint__ = @full/__entrypoint__
```

```bash
pfig run smoke.pfig --set full/nvidia_root=/data/nvidia
```

Smoke requires only its selected root. Full still requires both inputs. The unused
declaration remains valid and can still be supplied by a profile. A profile with
`override nvidia_root = "/data/nvidia"` applies explicitly through
`--profile full::profiles/local.pfig`; include namespaces are not flattened.

## Use profiles for different environments

Use profiles when the same algorithm should run on different machines. For the
config above, create these files in your project's `profiles/` directory.

`profiles/gpu.pfig`:

```pfig
# profiles/gpu.pfig
override device = "cuda"
override data_dir = "/shared_data/my_model"
```

`profiles/cpu.pfig`:

```pfig
# profiles/cpu.pfig
override device = "cpu"
override data_dir = "./data"
```

```bash
pfig run config.pfig --profile profiles/gpu.pfig
pfig run config.pfig --profile profiles/cpu.pfig

# CLI values take precedence over the profile
pfig run config.pfig --profile profiles/gpu.pfig --set data_dir=./local_data
```

**Pitfall:** profiles may override only declared externals, not ordinary values
such as `epochs`. Use an experiment config for algorithm changes instead.
Later profiles take precedence over earlier ones; `--set` values win over all
profiles. See [profile prefixes](../reference/cli.md#profile-application-prefixes)
when applying a profile to an included config.

## Layer experiments with includes and overrides

Use an experiment config when you want to change parameters without copying the
whole object graph. Building on `config.pfig` above:

`experiment.pfig`:

```pfig
# experiment.pfig
include "./config.pfig" as base

override base/consts/hidden_size = 1024
override base/consts/epochs = 20

__entrypoint__ = @base/__entrypoint__
```

Reuse the same GPU profile, applying it to the included `base` config:

```bash
pfig run experiment.pfig --profile base::profiles/gpu.pfig
```

`base::` matches the include alias: the profile's `device` and `data_dir`
overrides now target `base/device` and `base/data_dir`. The profile file itself
stays unchanged. Running `config.pfig` directly still uses
`--profile profiles/gpu.pfig`, without a prefix.

The application prefix follows the include structure, so renaming `base` or
adding another wrapper also changes the prefix. pfig keeps this target explicit
rather than guessing which included config should receive an environment profile.

**Best practice:** expose tunable parameters as named bindings in the base config.
Overrides replace entire bindings; they do not patch fields or deeply nested
constructor kwargs. Use `override base/consts/hidden_size`, not
`override base/model.hidden_size`. This keeps experiment configs coupled to the
base config's explicit parameter interface rather than its internal object
structure, so internal refactoring need not break experiment overrides.

Include aliases name shared source bindings, not isolated instances. In a diamond
include graph, overriding `geometry/values/width` also changes the same source
binding reached as `backbone/geometry/values/width`. Both paths keep sharing the
geometry object. Each provider file must include its own dependencies; including
a sibling at the experiment root does not make it visible inside another file.

Ancestor overrides win over dependency overrides. Author only one override per
canonical binding in each file; repeating it through any alias is an error, even
with an identical expression. Multiple sibling overrides need an explicit ancestor
override, even when identical. Diamond includes may reach the same authored
override more than once. Use fresh calls for variants, not repeated includes with
conflicting settings. See the [language spec](../developer/spec.md#overrides).

## Reuse recipes with shared and fresh instances

Use a shared reference when several consumers should receive the same object:

```pfig
model = Model(hidden_size=512)
trainer = Trainer(model=@model)
evaluator = Evaluator(model=@model)
```

Use fresh calls when you need separate instances or variants of a common recipe:

```pfig
# A partial recipe: dataset is supplied by each fresh call
base_loader = DataLoader(batch_size=64)
train_loader = @base_loader(dataset=@train_dataset, shuffle=True)
val_loader = @base_loader(dataset=@val_dataset, shuffle=False)
```

These are wiring fragments: import the application callables and define their
dependencies in your complete config.

**Pitfalls:**

- A partial provider cannot be used as shared until all required kwargs are supplied.
- Fresh construction is not a deep copy: dependencies referenced with `@name`
  remain shared.
- Give variants their own binding names rather than relying on a self-referential
  override to retain an old recipe.

## Wiring a plain function

Use this pattern when your workflow is a Python function that needs arguments,
rather than an object with a zero-argument method such as `trainer.fit`.

Entrypoints must be zero-argument callables. Expose a keyword-friendly Python
adapter that returns one:

```python
# train.py (alongside your Model and train definitions)
from functools import partial


def training_entrypoint(*, model, epochs):
    return partial(train, model=model, epochs=epochs)
```

Using the `consts.pfig` file from the first pattern:

```pfig
# train.pfig
from train import Model, training_entrypoint

include "./consts.pfig" as consts
external device

model = Model(hidden_size=@consts/hidden_size, device=@device)
training = training_entrypoint(
    model=@model,
    epochs=@consts/epochs,
)
__entrypoint__ = @training
```

```bash
pfig run train.pfig --set device=cpu
```

`pfig.entrypoint(train, model=model, epochs=epochs)` is a convenience for Python
callers. Its `fn` parameter is positional-only, so it cannot currently be called
directly in the keyword-only DSL. Do not replace the factory call above with
`entrypoint(train, ...)` in a `.pfig` file.

For other instance method calls, expose Python helpers with keyword-friendly
arguments. Attribute and bound-method references such as `@trainer.fit` and
`@model.encoder.weight` are supported, but method invocation inside the config
is not.

## Capture reproducible runs

Validate before running, and keep source changes committed and pushed where
possible:

```bash
pfig validate config.pfig --profile profiles/cpu.pfig
pfig run config.pfig --profile profiles/cpu.pfig --snapshot ./experiments/baseline
```

CLI runs create snapshots by default. A timestamped child directory is created
inside the selected destination; use a separate parent directory to group related
experiments.

**Pitfalls:** snapshots are best-effort, not a guarantee of identical results.
External datasets, services, and Python non-determinism are not captured. Remote
snapshot destinations currently omit replay payloads; write locally and upload
the complete directory when replay is required. See the [Snapshots Guide](snapshots.md)
for warnings, replay, and the differences between CLI and Python snapshot behavior.

## Keep configs reviewable

- Prefer named providers for objects reused across the graph.
- Put behavior and branching in Python helpers, not configuration expressions.
- Format configs with `pfig fmt`; use `pfig fmt --check` in CI.
- Use `pfig view` to inspect dependencies and `pfig diff` to compare experiment configs.

## Further reading

- [Syntax Reference](../reference/syntax_reference.md) — supported language constructs
- [CLI Reference](../reference/cli.md) — validation, formatting, visualization, and other commands
- [Python API](../reference/api_reference.md) — programmatic execution and instantiation
