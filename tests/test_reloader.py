import importlib
import sys

from .. import reloader


def test_reload_package_reloads_submodules_under_the_package_dir(fake_package):
    name, root = fake_package
    (root / "child.py").write_text("VALUE = 1\n")
    (root / "__init__.py").write_text("from . import child\n")
    pkg = importlib.import_module(name)
    assert pkg.child.VALUE == 1

    (root / "child.py").write_text("VALUE = 2\n")
    importlib.invalidate_caches()
    reloader.reload_package(pkg)
    assert sys.modules[name + ".child"].VALUE == 2
    assert pkg.child.VALUE == 2


def test_reload_package_ignores_modules_outside_the_package(fake_package):
    name, root = fake_package
    (root / "__init__.py").write_text("import json\n")
    pkg = importlib.import_module(name)
    reloader.reload_package(pkg)  # must not try to reload the stdlib
    assert pkg.json is sys.modules["json"]
