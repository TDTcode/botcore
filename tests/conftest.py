import logging
import sys

import pytest


@pytest.fixture
def fake_package(tmp_path, monkeypatch):
    """Create an importable package ``fakepkg_<n>``; returns ``(name, root_path)``."""
    counter = fake_package.counter = getattr(fake_package, 'counter', 0) + 1
    name = 'fakepkg_{0}'.format(counter)
    root = tmp_path / 'pkgs' / name
    root.mkdir(parents=True)
    (root / '__init__.py').write_text('')
    monkeypatch.syspath_prepend(str(root.parent))
    yield name, root
    for mod in [m for m in sys.modules if m == name or m.startswith(name + '.')]:
        del sys.modules[mod]


@pytest.fixture(autouse=True)
def _restore_discord_logger():
    """Runner tests call init_logging, which edits the global 'discord' logger."""
    logger = logging.getLogger('discord')
    handlers, propagate = list(logger.handlers), logger.propagate
    yield
    logger.handlers[:] = handlers
    logger.propagate = propagate
