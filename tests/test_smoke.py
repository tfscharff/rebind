import re
import sys

import rebind


def test_package_imports():
    # A valid Semantic Versioning 2.0.0 string (https://semver.org/), not a pinned literal, so a
    # release bump does not break this smoke test. Matches the version in pyproject.toml.
    assert re.match(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$", rebind.__version__)


def test_python_version_is_pinned():
    """Phase 0 constraint: 3.12 only. 3.14 lacks wheels for the CV/ML stack."""
    assert sys.version_info[:2] == (3, 12), f"expected 3.12, got {sys.version_info[:2]}"
