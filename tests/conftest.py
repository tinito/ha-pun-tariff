"""Test setup: import the integration's pure modules without Home Assistant.

The package __init__ imports Home Assistant, so the modules are loaded from their files under a
stand-alone package name. Templates are rendered with plain Jinja2, like Home Assistant does.
"""
import importlib.util
import sys
import types
from pathlib import Path

import jinja2
import pytest

ROOT = Path(__file__).parent.parent / "custom_components" / "pun_tariff"
PACKAGE = "pun_tariff_pure"
sys.modules.setdefault(PACKAGE, types.ModuleType(PACKAGE)).__path__ = [str(ROOT)]
for name in ("bands", "gme", "contract", "meter"):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

_ENV = jinja2.Environment()


def jinja_render(value, variables):
    if isinstance(value, (int, float)):
        return float(value)
    return float(_ENV.from_string(value).render(**variables))


@pytest.fixture
def render():
    return jinja_render
