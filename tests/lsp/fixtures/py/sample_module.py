"""Sample Python module for testing symbol resolution."""


class SampleClass:
    """A sample class with inspectable signature."""

    def __init__(self, name: str, value: int = 42, **kwargs):
        self.name = name
        self.value = value


def sample_function(x: int, y: int = 0, z: str = "hello") -> int:
    """A sample function."""
    return x + y
