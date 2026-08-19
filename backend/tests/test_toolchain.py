import sys


def test_python_is_312() -> None:
    assert sys.version_info[:2] == (3, 12)
