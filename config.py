"""Configuration and filesystem layout for a bot.

``Config`` replaces the per-bot ``param.Parameters`` / ``param.rc`` singleton.
It is a ``dict`` subclass that is callable (``rc('key', default)``), so existing
``param.rc(...)`` call sites keep working when a bot's ``param.py`` builds one::

    # <bot>/param.py
    from .botcore.config import Config
    rc = Config(dict(cmd_prefix=['X$'], ...), root=_root, name='x')

Layers, lowest to highest precedence:
    core defaults < the bot's defaults < ``config/<name>.json`` < CLI / ``update()``

The Discord token is *not* part of the json layers.  ``read_token`` resolves it
from (highest first): an explicit CLI argument (a file path, or the token
itself), the ``<NAME>_TOKEN`` / ``DISCORD_TOKEN`` environment variables, then
``token_file`` (default ``config/token.txt``).

``Paths`` locates the bot's directories.  By default everything lives inside
the bot checkout (the historical layout).  Set ``BOT_DATA_DIR`` to keep mutable
state (dbm files, per-user json, logs) somewhere else, e.g. outside a git
checkout.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger('discord.' + __name__)

DATA_DIR_ENV = 'BOT_DATA_DIR'
GENERIC_TOKEN_ENV = 'DISCORD_TOKEN'


class Paths:
    """Where a bot keeps its files.

    root    the bot checkout (the package directory)
    config  ``root/config``: static + hand-edited config (json, md templates, token file)
    data    mutable state (``.dbm`` files); ``config`` unless ``BOT_DATA_DIR`` is set
    users   per-user json files: ``data/users``
    logs    ``root/logs`` unless ``BOT_DATA_DIR`` is set (then ``$BOT_DATA_DIR/logs``)
    """

    def __init__(self, root, data_dir=None):
        self.root = Path(root).resolve()
        if data_dir is None:
            data_dir = os.environ.get(DATA_DIR_ENV) or None
        self.config = self.root / 'config'
        if data_dir is None:
            self.overridden = False
            self.data = self.config
            self.logs = self.root / 'logs'
        else:
            self.overridden = True
            self.data = Path(data_dir).expanduser().resolve()
            self.logs = self.data / 'logs'
        self.users = self.data / 'users'

    def ensure(self):
        """Create the mutable directories (only needed when ``BOT_DATA_DIR`` moved them)."""
        for p in (self.data, self.users, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def __repr__(self):
        return f'Paths(root={self.root!s}, data={self.data!s}, logs={self.logs!s})'


def default_prefixes(name):
    """``wit`` -> ``['WIT$', 'Wit$', 'wit$']`` (the prefix style both bots use)."""
    return [name.upper() + '$', name.title() + '$', name.lower() + '$']


class Config(dict):
    """Callable, layered configuration (dict subclass).

    ``defaults`` are the bot's defaults; core defaults fill in anything missing.
    ``root`` is the bot checkout, ``name`` a short lowercase bot name (``'tdt'``,
    ``'wit'``) used for the default prefix, ``config/<name>.json``,
    ``logs/<name>.log`` and the ``<NAME>_TOKEN`` environment variable.
    """

    def __init__(self, defaults=None, *, root=None, name=None, data_dir=None, config=None):
        super().__init__()
        if root is None:
            raise ValueError('Config needs the bot root directory (root=...)')
        self.name = name or Path(root).resolve().name.lower()
        self.paths = Paths(root, data_dir)
        self.defaults = self._core_defaults()
        if defaults:
            self.defaults.update(defaults)
        self.update(self.defaults)
        self.read_config(config)

    def _core_defaults(self):
        p = self.paths
        return {
            'channel':         None,
            'cmd_prefix':      default_prefixes(self.name),
            'config_file':     str(p.config / (self.name + '.json')),
            'token_file':      str(p.config / 'token.txt'),
            'add_bots':        [],
            'ignore_list':     [],       # channel names where commands are ignored
            'log_channel':     'debugging',
            'event_reminders': [360, 60, 0],  # in minutes
            'timezone':        'US/Pacific',
            'logfile':         str(p.logs / (self.name + '.log')),
            'bot_key':         self.name,
            'admin_role_ids':  [],       # role ids whose *top role* grants admin commands
            'home_guild_id':   None,     # the bot's "own" guild (BotCore.home_guild)
            'privacy_url':     None,     # link shown by the shared privacy cog
        }

    def __call__(self, key, default=None):
        """Make this callable, allowing us to avoid/handle key errors"""
        return self.dget(key, default)

    def dget(self, key, default=None):
        """Like get, but check the defaults if we don't have the key"""
        return self.get(key, self.defaults.get(key, default))

    def read_config(self, fn=None):
        """Parse the json config file (default: ``config_file``) and update self.

        A missing file is fine (defaults apply); invalid json raises.
        """
        if fn is None:
            fn = self.dget('config_file')
        if not fn:
            return
        try:
            with open(fn) as f:
                out = json.load(f)
        except OSError:
            return
        self.update(out)

    @property
    def token_env_names(self):
        return [self.name.upper() + '_TOKEN', GENERIC_TOKEN_ENV]

    def read_token(self, fn=None):
        """Resolve the Discord token, store it in ``self['token']`` and return it.

        ``fn`` is the CLI value: a file path, or (if no such file) the token itself.
        Without ``fn``, the environment is consulted before ``token_file``.
        Returns None (and stores None) if nothing is configured.
        """
        if fn is not None:
            return self._token_from_file(fn, literal_ok=True)
        for var in self.token_env_names:
            value = os.environ.get(var, '').strip()
            if value:
                self['token'] = value
                return value
        fn = self.dget('token_file')
        if fn and os.path.exists(fn):
            return self._token_from_file(fn)
        self['token'] = None
        return None

    def _token_from_file(self, fn, literal_ok=False):
        try:
            with open(fn) as f:
                lines = filter(None, [i.split('#')[0].strip() for i in f])
                lines = list(lines)
        except OSError:
            if literal_ok:
                self['token'] = fn
                return self['token']
            raise
        if len(lines) != 1:
            raise ValueError('Token file not properly formatted.')
        self['token'] = lines[0]
        return self['token']

    def public_items(self):
        """Items that are safe to print to chat/logs (no token/secret-ish keys)."""
        return {k: v for k, v in self.items()
                if k != 'roasts' and not any(s in str(k).lower()
                                                    for s in ('token', 'secret', 'password'))}
