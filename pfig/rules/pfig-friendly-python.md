---
description: How to structure and write Python code for use with pfig
pattern: **/*.py
---

# pfig-Friendly Python Code

Structure and write Python so pfig configs can wire it cleanly. pfig uses **constructor + kwargs only**; no instance method calls in config.

## Constructor Design

**All configuration via keyword arguments.** pfig does not support positional args.

```python
# ✅ Good: everything as kwargs
class MLP(nn.Module):
    def __init__(self, input_size=784, hidden_sizes=[512, 256], num_classes=10, dropout=0.2):
        ...

class Trainer:
    def __init__(self, model, optimizer, train_loader, val_loader, device="cpu", epochs=10, run_root=""):
        ...
```

```python
# ❌ Avoid: positional-only or *args-heavy signatures
def train(model, epochs, /):  # positional-only — cannot wire directly
    ...

def build(x, *layers):        # pfig cannot supply the positional layers
    ...
```

**Accept externals as constructor kwargs.** Device, paths, workers — things set via `--set` or profiles — should be injectable:

```python
# ✅ Good: runtime knobs as kwargs
def __init__(self, ..., device="cpu", run_root="", num_workers=4):
```

## Entrypoint Design

Expose a **zero-argument callable** that pfig can use as `__entrypoint__`. Two patterns:

**1. Bound method** — simplest when the class owns the full workflow:

```python
class Trainer:
    def fit(self):
        """Train for self.epochs. No args — uses self."""
        for epoch in range(self.epochs):
            ...
        return results

# In pfig: __entrypoint__ = @trainer.fit
```

**2. Factory function** — when you need to wrap or compose:

```python
def train_main(job_name, trainer):
    """Return a zero-arg callable that runs training."""
    def _run():
        return trainer.fit()
    return _run

# In pfig: __entrypoint__ = train_main(job_name=@job_name, trainer=@trainer)
```

For plain functions, expose a keyword-friendly Python adapter:

```python
from functools import partial

def training_entrypoint(*, model, epochs):
    return partial(train, model=model, epochs=epochs)

# In pfig: __entrypoint__ = training_entrypoint(model=@model, epochs=@epochs)
```

`pfig.entrypoint(train, **kwargs)` is convenient in Python, but its positional-only
`fn` parameter cannot currently be supplied directly from the keyword-only DSL.

## Helper Functions

pfig **cannot call instance methods** (e.g. `model.parameters()`). Use **pure, module-level helpers**:

```python
# helpers.py
def create_optimizer(optimizer_class, model, **kwargs):
    """Create optimizer with model parameters. Called from pfig."""
    return optimizer_class(model.parameters(), **kwargs)

def param_groups(model, **kwargs):
    """Return param groups for optimizer. model is instantiated by pfig."""
    return list(model.parameters())
```

```pfig
# In pfig config
optimizer = create_optimizer(optimizer_class=optim.Adam, model=@model, lr=@lr)
# Or: params=helpers.param_groups(model=@model) for custom param groups
```

Helpers should be **side-effect free** during the call pfig makes. pfig cannot enforce this; it's a convention.

## Dependency Injection

**Inject dependencies via constructor.** Avoid globals and module-level state. pfig wires objects by passing them as kwargs:

```python
# ✅ Good: deps injected
class Trainer:
    def __init__(self, model, optimizer, train_loader, val_loader, device, epochs):
        self.model = model
        ...
```

```python
# ❌ Avoid: pulling from globals or creating deps inside
class Trainer:
    def __init__(self):
        self.model = MLP()  # hardcoded — pfig can't override
        self.optimizer = Adam(self.model.parameters())  # same
```

## Module Layout

Structure modules to match how pfig configs organize:

```
myproj/
  models.py    # MLP, ResNet, etc. — model architecture
  data.py      # dataset/loader factories
  train.py     # Trainer, train_main — training orchestration
  helpers.py   # create_optimizer, param_groups — bridge to pfig
```

Configs then import: `from myproj import models, train, helpers`.

## Avoid Side Effects in Constructors

Constructors are called during pfig's lazy materialization. Keep them **light**:

- ✅ Set attributes, build submodules
- ❌ Avoid heavy I/O, network calls, or global registration in `__init__`
- ✅ Defer I/O to the entrypoint (e.g. `fit()`)

## See Also

- [pfig-developer-guide](__RULES_DIR__/pfig-developer-guide__RULES_EXT__) — pfig config syntax and best practices
- [pfig-spec](__RULES_DIR__/pfig-spec__RULES_EXT__) — Full DSL reference
