"""botcore: shared foundation for the TDT Discord bots (TDTbot, wit_bot).

This package is designed to live *inside* a bot's package directory
(``<bot>/botcore/``), imported with a relative import
(``from .botcore import BotCore``), so that the bot's self-update and module
reload keep working.  It is also importable as plain ``botcore`` for its own
tests.  Keep every import inside the core relative.

See README.md for the API overview.
"""

__version__ = "0.1.0"

from .bot import BotCore
from .cog import AdminCog, CoreCog
from .config import Config, Paths
from .git_manage import GitManager
from .storage import DataContainer, IntPermaDict, PermaDict, UserConfig
