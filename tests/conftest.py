"""
Pytest configuration and fixtures for pfig tests.
"""

import os
import sys

# Add the pfig module to Python path so tests can import it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
