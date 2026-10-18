"""Make the bundled pure-Python packages (scripts/_vendor) importable when they are not installed.

Lets the report build on locked-down machines where `pip install` is not allowed: only Python is needed.
An installed openpyxl always takes precedence over the bundled copy.
"""
import os
import sys

if sys.version_info < (3, 8):
    sys.exit(f"Python 3.8 or newer is required (found {sys.version.split()[0]}).")

_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")

try:
    import openpyxl  # noqa: F401
except ImportError:
    if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
        sys.path.append(_VENDOR)


def has_matplotlib():
    try:
        import matplotlib  # noqa: F401
        return True
    except Exception:
        return False
