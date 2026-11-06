import tomllib
from pathlib import Path

import evalharness


def test_package_version_has_one_value():
    """The version lives in two places; every stored result records it, so they must agree."""
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert evalharness.__version__ == pyproject["project"]["version"]
