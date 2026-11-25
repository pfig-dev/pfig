"""
Tests for chained fresh provider calls (Fresh-on-Fresh).
"""

import pytest

from pfig import ExecutionError, instantiate


class MockLayer:
    def __init__(self, name="base", size=10, activation="relu"):
        self.name = name
        self.size = size
        self.activation = activation

    def __repr__(self):
        return f"MockLayer(name={self.name}, size={self.size}, act={self.activation})"


class TestFreshChaining:
    """
    Reproduces the 'Fresh-on-Fresh' crash where a fresh call cannot target
    another binding that was itself created via a fresh call.
    """

    def test_fresh_call_on_fresh_call_fails(self):
        """
        Scenario:
        1. 'base' is a standard provider.
        2. 'middle' is a fresh call: @base(size=20).
           Crucially, 'middle' is stored in 'values', not 'providers'.
        3. 'final' is a fresh call on 'middle': @middle(activation='tanh').

        Current Behavior: Crash (KeyError) because execution engine looks for 'middle' in 'providers'.
        Expected Behavior: Success (recursive resolution of fresh calls).
        """
        config = """
import tests.test_fresh_chaining as t

# 1. Root Provider
base = t.MockLayer(name="root", size=10, activation="relu")

# 2. First Fresh Call (Stored as a Value/FreshProviderCall)
middle = @base(size=20, name="middle")

# 3. Second Fresh Call (Chained on 'middle')
# This is the point of failure in the current implementation
final = @middle(activation="tanh", name="final")
"""
        # This should fail with KeyError: 'middle' in the current implementation
        # Or eventually pass when fixed.
        # We use a broad Exception catch to demonstrate the crash,
        # or specific failure modes depending on how python handles the dict lookup.

        try:
            result = instantiate("final", source=config)

            # If it somehow passes (once fixed), verify correctness
            assert result.size == 20  # Inherited from 'middle'
            assert result.activation == "tanh"  # Overridden in 'final'
            assert result.name == "final"

        except KeyError as e:
            pytest.fail(
                f"CRASH: Failed to lookup intermediate provider in fresh chain. Missing key: {e}"
            )
        except Exception as e:
            # It might raise ExecutionError if wrapped, or raw KeyError
            pytest.fail(f"CRASH: {type(e).__name__}: {e}")

    def test_deep_fresh_chaining(self):
        """Test deeper nesting levels."""
        config = """
import tests.test_fresh_chaining as t

l1 = t.MockLayer(size=1)
l2 = @l1(size=2)
l3 = @l2(size=3)
l4 = @l3(size=4)

result = @l4()
"""
        try:
            result = instantiate("result", source=config)
            assert result.size == 4
        except Exception as e:
            pytest.fail(f"Deep chaining crashed: {e}")
