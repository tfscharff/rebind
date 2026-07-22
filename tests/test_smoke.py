import sys

import rebind


def test_package_imports():
    assert rebind.__version__ == "0.0.1"


def test_python_version_is_pinned():
    """Phase 0 constraint: 3.12 only. 3.14 lacks wheels for the CV/ML stack."""
    assert sys.version_info[:2] == (3, 12), f"expected 3.12, got {sys.version_info[:2]}"
