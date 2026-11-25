# Syntax Reference

## The `@` Sigil

The `@` sigil is used to **reference** things defined in pfig files (values and providers), but **not** when defining them:

```pfig
# Defining values and providers - no @
batch_size = 64                  # Define value
model = MyModel()                # Define provider

# Referencing values and providers - use @
result = @batch_size * 10        # Reference pfig value
trainer = Trainer(model=@model)  # Reference pfig provider

# Python symbols from imports - no @ (neither when importing nor referencing)
import torch.nn as nn
layer = nn.Linear(in_features=10, out_features=5)  # Python symbol call
```

**Rules**:

- **Defining**: No `@` when defining values/providers (`batch_size = 64`)
- **Referencing**: Use `@` when referencing pfig values/providers (`@batch_size`)
- **Imports**: No `@` for Python symbols (neither import nor reference)

## Imports

```pfig
# Import module
import torch
import torch.nn as nn

# Import symbols
from torch import nn, optim
from train import Trainer
import utils  # Helper functions
```

**Note**: Python symbols (imported modules/classes/functions) are used directly without `@`. The `@` sigil is only for config references.

## Includes

Modularize configs by including other `.pfig` files with explicit namespaces:

```pfig
# Relative path includes
include "./base.pfig" as base
include "./consts.pfig" as consts

# Reference included bindings using namespace
model = Model(hidden_size=@base/hidden_size, lr=@consts/learning_rate)
```

**Rules:**

- **Relative paths**: Paths are relative to the current file (e.g., `"./base.pfig"`)
- Includes use explicit filesystem paths; there are no search paths or Python-package discovery.
- Explicit `as alias` is required (no implicit includes)
- Use `@alias/name` syntax to reference included bindings
- Includes create a namespace, not a merge

For cross-project reuse, copy/vendor `.pfig` files into the project and include them by relative path. Python imports locate code, not configuration files.

## Externals

`external name` declares a runtime input supplied through profiles, CLI `--set`,
or Python `externals`. A declaration is required only when the selected execution
needs it after overrides and runtime layers resolve. Fresh calls require their
merged arguments, not arguments replaced by an overlay. Shared uses of the base
provider retain their original requirements.

Unused declarations remain intact and may still be supplied. `None` is a supplied
value, not a missing input. Rootless `compile_pfig()` retains whole-file missing-input
diagnostics; `allow_missing_externals=True` permits incomplete validation, not
execution with unresolved dependencies.

## Values

Simple bindings for constants and configuration values:


```pfig
# Primitives
batch_size = 64                    # int
large_number = 1_000_000          # int with underscores for readability
learning_rate = 0.001              # float
precise_float = 1_234.567_890     # float with underscores
name = "experiment_1"              # str
enabled = True                     # bool
nothing = None                     # None

# Containers
hidden_sizes = [128, 64, 32]       # list
input_shape = (28, 28, 1)          # tuple
config = {"key": "value", "nested": {"a": 1}}  # dict

# Expressions
total = @batch_size * @epochs
path = @base_dir + "/checkpoints"
```

**Supported Types:**

- **Primitives**: `int`, `float`, `str`, `bool`, `None`
- **Containers**: `list`, `tuple`, `dict`

**Allowed Expressions:**

- **Arithmetic operators**: `+`, `-`, `*`, `/`, `//`, `**` (addition, subtraction, multiplication, division, floor division, exponentiation)
- **Unary operators**: `-` (negation, e.g., `-5`, `-@value`)
- **String concatenation**: `+` (e.g., `@base_dir + "/checkpoints"`)
- **Builtin functions**: `len()`, `min()`, `max()`, `range()` (these accept positional arguments, e.g. `range(3)`, unlike provider calls)
- **Provider references**: `@name` or `@namespace/name` (to reference other values/providers)
- **Parenthesized expressions**: `(expr)` for grouping

**Note**: Values are lazy - they're compiled into intermediate representations and only materialized when referenced. If a value is overridden later, the override replaces the compiled representation and will be used when the value is materialized. Providers (constructor calls) are also lazy recipes that are instantiated only when referenced.

## Providers

Providers are recipes for creating objects, defined by constructor or function calls:

```pfig
# Provider definition (recipe)
model = nn.Linear(in_features=784, out_features=10)
optimizer = optim.Adam(params=utils.get_params(@model), lr=0.001)

# Provider references
shared_model = @model              # Shared instance (memoized)
fresh_model = @model()             # Fresh instance (new object)
custom_model = @model(out_features=20)  # Fresh with overrides

# Attribute / method references
forward_fn = @model.forward        # Bound method reference
fit_method = @trainer.fit          # Can be used as entrypoint
topic = @manager.topics.cmd_pose   # Nested attribute values
```

| Syntax | Behavior | Use Case |
|--------|----------|----------|
| `@provider` | Shared instance (memoized) | Default - share objects |
| `@provider()` | Fresh instance (new object) | When you need separate instances |
| `@provider.attr` | Attribute or bound method | Values, properties, callbacks |
| `@provider.a.b.c` | Nested getattr chain | Drill into returned objects |
| `@namespace/provider.attr` | Namespaced attribute reference | Attrs from included configs |
| `@provider(arg=val)` | Fresh with overrides | Override specific parameters |

**Important**: Providers are **recipes** (lazy construction specifications), not instantiated objects. This means:

- ✅ You can reference attributes and methods: `@provider.attr` returns the attribute value using Python `getattr`, including a bound method when appropriate
- ✅ Nested chains work: `@provider.topics.cmd_arms_left_tcp_pose`. Each segment is read once in order, so properties may run Python code during materialization
- ❌ You **cannot** call methods: `@provider.method()` is invalid syntax
- ✅ Use keyword-friendly helper functions if you need to call methods: `helper_func(obj=@provider)` where the helper receives the materialized object and calls the method

## Namespace Resolution

| Syntax | Meaning | Example |
|--------|---------|---------|
| `name` | Local binding definition (bare identifier, no `@`) | `batch_size = 64` |
| `alias/name` | Namespaced path (used in overrides, externals, and provider references) | `override consts/batch_size = 64`, `external consts/device`, `@consts/batch_size` |
| `module.Symbol` | Python symbol from import (no `@`) | `torch.nn.Linear` |
| `@name` | Config provider/value **reference** (use `@` when referencing, not defining) | `@model`, `@consts/batch` |

**Rules:**

- **Defining**: No `@` when defining bindings (`batch_size = 64`, not `@batch_size = 64`)
- **Referencing**: Use `@` when referencing pfig values/providers (`@batch_size`, `@model`)
- **Slash `/`** is ONLY for namespace scoping from **includes**
- **Dotted names** (no `@`) refer to Python symbols from **imports**
- **`@name`** or **`@alias/name`** resolves only in the config binding namespace
- Cannot define bindings with `/` (e.g., `data/batch = 64` is invalid)

## Limitations

pfig is intentionally minimal. The following are **not supported**:

- ❌ Conditionals: `x = 10 if condition else 20`
- ❌ Lambdas: `fn = lambda x: x * 2`
- ❌ Loops/list comprehensions: `items = [x for x in range(10)]`
- ❌ Instance method calls: `result = @model.forward(x)` (use `@model.forward` for bound method reference)
- ❌ Positional arguments: `Model(784, 10)` (use `Model(input_dim=784, output_dim=10)`)

## Magic Variables

Magic variables provide defaults that would otherwise need to be passed to the CLI or Python API. Unlike statements such as `include`, `override`, and `external`, runner directives such as `__entrypoint__` and `__snapshot__` are ordinary bindings. They reuse the same expression and reference machinery without adding grammar rules; the runner interprets their values to choose what to execute and where to save snapshots:

```pfig
__entrypoint__ = @trainer.fit    # Callable entrypoint for `pfig run`
```

Without `__entrypoint__`, you would need to specify the entrypoint via CLI:

```bash
pfig run config.pfig --entrypoint fit
```

Here `fit` must be a binding in the config, for example `fit = @trainer.fit`.
Alternatively, use `--entrypoint trainer.fit` to select a callable attribute on a
binding. Arbitrary Python import paths are not resolved. See
[Specifying Entrypoint](cli.md#specifying-entrypoint) for resolution rules.


### `__snapshot__`

Specify the default directory for snapshot output:

```pfig
__snapshot__ = "./experiments/snapshots"
```

Snapshots capture configuration, Git state, and environment for reproducibility. Override via `--snapshot` or disable with `--no-snapshot`.

Both runners also accept `external __snapshot__` for a runtime-supplied destination.
Its value must be a nonempty path string and is required unless snapshotting is
disabled or an explicit destination overrides it.

See the [Snapshots Guide](../guides/snapshots.md) for full details.

## Positional Arguments

pfig only supports keyword arguments. Positional arguments are not allowed:

```pfig
# Invalid - positional arguments
model = Model(784, 10)

# Valid - keyword arguments
model = Model(input_dim=784, output_dim=10)
```

`args=[...]` is not a positional-argument escape hatch: it is passed as an ordinary
keyword named `args`. Wrap positional-only Python callables in a keyword-friendly
Python adapter instead.

## Grammar

This EBNF-style summary follows the recursive-descent parser in `pfig/parser.py`.
Braces denote repetition, brackets denote optional syntax, and quoted text is
literal syntax. `IDENT`, `INT`, `FLOAT`, and `STRING` are lexer tokens; keywords
(including builtin names) are reserved rather than identifiers. Whitespace,
newlines, and `#` line comments are omitted here. Newlines are skipped by the
parser, including inside expressions.

```text
file          := { statement }
statement     := import_stmt | from_stmt | include_stmt | external_stmt
               | override_stmt | binding

import_stmt   := "import" dotted_name [ "as" IDENT ]
from_stmt     := "from" dotted_name "import" import_item { "," import_item }
import_item   := IDENT [ "as" IDENT ]
include_stmt  := "include" STRING "as" IDENT
external_stmt := "external" path
override_stmt := "override" path "=" expr
binding       := IDENT "=" expr

path          := IDENT { "/" IDENT }
dotted_name   := IDENT { "." IDENT }

expr          := term { ("+" | "-") term }
term          := power { ("*" | "/" | "//") power }
power         := unary [ "**" power ]
unary         := "-" unary | primary
primary       := literal | list | tuple | dict | group
               | provider_expr | symbol_expr | builtin_call
literal       := INT | FLOAT | STRING | "True" | "False" | "None"

list          := "[" [ expr { "," expr } [ "," ] ] "]"
tuple         := "(" ")" | "(" expr "," [ expr { "," expr } [ "," ] ] ")"
group         := "(" expr ")"
dict          := "{" [ pair { "," pair } [ "," ] ] "}"
pair          := expr ":" expr

provider_expr := "@" path [ attr_chain | kwargs ]
attr_chain    := "." IDENT { "." IDENT }
symbol_expr   := dotted_name [ kwargs ]
kwargs        := "(" [ kwarg { "," kwarg } [ "," ] ] ")"
kwarg         := IDENT "=" expr
builtin_call  := builtin "(" [ expr { "," expr } [ "," ] ] ")"
builtin       := "len" | "min" | "max" | "range"
```

Important parsing details:

- Only builtin calls accept positional arguments. `args=[...]` in an ordinary
  call is just a keyword argument, not a separate syntax feature.
- A provider reference may have an attribute chain **or** fresh-call kwargs,
  not both: `@model.encoder.weight` and `@model(width=32)` are valid;
  `@model.encoder()` is not.
- Exponentiation associates to the right: `2 ** 3 ** 2` means `2 ** (3 ** 2)`.
  Unary minus binds more tightly than exponentiation in the current parser:
  `-2 ** 2` means `(-2) ** 2`, unlike Python. Unary `+` is not supported.
- Slash-separated paths are consumed as part of provider references:
  `@a / b` is a reference to the binding `a/b`, not division. Parenthesize the
  reference to express division: `(@a) / b`.
- Parsing describes syntax, not validity of a configuration: import resolution,
  binding lookup, callable signatures, and other semantic checks happen later.

## Putting It All Together

Here's a complete example showing all syntax elements working together:

```pfig
# Imports
import torch
from torch import nn, optim
import torchvision
from train import Trainer
# Helper functions (e.g., utils.get_params for extracting model parameters)
import utils

# Includes - modularize configs with explicit namespaces
include "./base.pfig" as base

# External parameters - runtime values set via CLI or profiles
external device
external data_dir

# Overrides - replace values from included configs
override base/learning_rate = 0.01

# Bindings (providers) - recipes for creating objects
model = nn.Linear(
    in_features=@base/input_size,
    out_features=@base/n_classes,
)
# Note: Method calls on providers are not allowed (providers are recipes,
# not instantiated objects). Use a helper function to extract
# parameters: utils.get_params(@model)
optimizer = optim.Adam(
    params=utils.get_params(@model),
    lr=@base/learning_rate,
)
# Partial providers - define base recipe missing required args,
# then close with fresh calls
base_dataset = torchvision.datasets.MNIST(root=@data_dir, download=True)
train_dataset = @base_dataset(train=True)
val_dataset = @base_dataset(train=False)

base_dataloader = torch.utils.data.DataLoader(batch_size=@base/batch_size)
train_dl = @base_dataloader(dataset=@train_dataset, shuffle=True)
val_dl = @base_dataloader(dataset=@val_dataset, shuffle=False)

trainer = Trainer(
    model=@model,
    optimizer=@optimizer,
    device=@device,
    train_dataloader=@train_dl,
    val_dataloader=@val_dl,
)

# Entrypoint
__entrypoint__ = @trainer.fit
```

