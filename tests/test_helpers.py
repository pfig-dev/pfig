from pfig import entrypoint


def test_entrypoint_wraps_function_and_args():
    calls = []

    def train(run_id: str, *, seed: int) -> str:
        calls.append((run_id, seed))
        return f"{run_id}-{seed}"

    ep = entrypoint(train, run_id="exp1", seed=42)

    # Should be zero-arg callable
    result = ep()

    assert calls == [("exp1", 42)]
    assert result == "exp1-42"


def test_entrypoint_binds_kwargs_at_call_time():
    calls = []

    def fn(x: int) -> int:
        calls.append(x)
        return x * 2

    ep = entrypoint(fn, x=3)
    assert ep() == 6
    assert calls == [3]
