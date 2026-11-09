import logging
import sys
import types

import pytest

from ..config import Config


@pytest.fixture
def config(tmp_path):
    return Config({'cmd_prefix': ['t$']}, root=tmp_path, name='test')


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


class FakeChannel:
    """Collects what is sent to it."""
    def __init__(self, id=1, name='chan'):
        self.id = id
        self.name = name
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        return types.SimpleNamespace(content=content, kwargs=kwargs)


class FakeGuild:
    def __init__(self, id=1, channels=(), roles=(), members=(), owner=None):
        self.id = id
        self.channels = list(channels)
        self.roles = list(roles)
        self._members = {m.id: m for m in members}
        self.owner = owner
        self.owner_id = getattr(owner, 'id', None)

    def get_channel(self, cid):
        return {c.id: c for c in self.channels}.get(cid)

    def get_member(self, uid):
        return self._members.get(uid)

    async def fetch_member(self, uid):
        return None
