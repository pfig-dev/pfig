from functools import partial
from typing import Any, Callable


def entrypoint(fn: Callable[..., Any], /, **bound_kwargs: Any) -> Callable[[], Any]:
    """
    Wrap a callable and bound kwargs into a zero-argument entrypoint.

    This is a small convenience for pfig configs that want to wire arguments
    into a function while still satisfying pfig's expectation that
    `__entrypoint__` is a zero-argument callable.

    Args:
        fn: The callable to wrap.
        **bound_kwargs: Keyword arguments to bind to the callable.

    Returns:
        A zero-argument callable that invokes `fn(**bound_kwargs)`.

    Example:
        In Python:

        ```python
        from pfig import entrypoint

        def train(run_id: str, *, seed: int) -> None:
            ...

        ep = entrypoint(train, run_id="exp1", seed=42)
        ep()  # calls train(run_id="exp1", seed=42)
        ```

        The ``fn`` parameter is positional-only. The keyword-only pfig DSL
        cannot call this helper directly; expose a Python adapter instead:

        ```python
        def training_entrypoint(*, run_id, seed):
            return entrypoint(train, run_id=run_id, seed=seed)
        ```

        Then wire ``training_entrypoint(run_id=@run_id, seed=@consts/seed)``
        as the config's ``__entrypoint__``.
    """

    return partial(fn, **bound_kwargs)
