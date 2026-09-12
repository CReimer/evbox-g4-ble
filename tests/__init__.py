"""Shared helpers keeping legacy dependency stubs isolated from runtime tests."""

from contextlib import contextmanager
import sys


@contextmanager
def isolated_framework_stubs():
    prefixes = ("homeassistant", "bleak_retry_connector")

    def framework(name):
        return any(
            name == prefix or name.startswith(prefix + ".") for prefix in prefixes
        )

    saved = {name: module for name, module in sys.modules.items() if framework(name)}
    try:
        yield
    finally:
        for name in list(sys.modules):
            if framework(name):
                del sys.modules[name]
        sys.modules.update(saved)
