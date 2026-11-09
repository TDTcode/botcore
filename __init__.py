"""botcore: shared foundation for the TDT Discord bots (TDTbot, wit_bot).

This package is designed to live *inside* a bot's package directory
(``<bot>/botcore/``), imported with a relative import
(``from .botcore import BotCore``), so that the bot's self-update and module
reload keep working.  It is also importable as plain ``botcore`` for its own
tests.  Keep every import inside the core relative.

See README.md for the API overview.
"""
__version__ = '0.1.0'

from .config import Config, Paths  # noqa: F401
from .storage import DataContainer, PermaDict, IntPermaDict, UserConfig  # noqa: F401
from .git_manage import GitManager  # noqa: F401
from .cog import CoreCog, AdminCog  # noqa: F401
from .bot import BotCore  # noqa: F401
