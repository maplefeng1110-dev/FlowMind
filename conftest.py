"""Ensure the repository root is importable during tests.

pytest.ini uses ``pythonpath = .`` (pytest 7+); this root conftest guarantees the
same behavior on older pytest versions where that option is ignored.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The project targets Python 3.9+ (e.g. registry/db.py imports zoneinfo). On older
# interpreters, provide a minimal stub so the suite can import modules that only
# need zoneinfo at runtime (not exercised by these tests).
try:  # pragma: no cover
    import zoneinfo  # noqa: F401
except ImportError:  # pragma: no cover
    import types

    _zoneinfo_stub = types.ModuleType("zoneinfo")

    class ZoneInfo:  # minimal placeholder
        def __init__(self, key=None):
            self.key = key

    _zoneinfo_stub.ZoneInfo = ZoneInfo
    sys.modules["zoneinfo"] = _zoneinfo_stub
