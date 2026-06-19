"""Ensure the repository root is importable during tests.

pytest.ini uses ``pythonpath = .`` (pytest 7+); this root conftest guarantees the
same behavior on older pytest versions where that option is ignored.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
