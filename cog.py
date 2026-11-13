"""Base classes for cogs.

``CoreCog``   a ``commands.Cog`` that knows its bot and offers:

* ``after_ready()``: override it instead of the old ``on_ready`` +
  ``asyncio.sleep(5)`` + ``_async_init`` dance.  It runs once the bot is ready
  (immediately, if the cog is loaded/reloaded while the bot is already ready);
* ``channel(ref)`` / ``role(ref)`` / ``log_channel``: lookups that use the bot's
  config and home guild instead of hard-coded ids.

``AdminCog``  a ``CoreCog`` whose commands are admin-only (``cog_check`` uses
``BotCore.is_admin``).  It is *not* hidden automatically, because discord.py
does not inherit ``command_attrs`` through subclasses; write

    class Mine(AdminCog, command_attrs=dict(hidden=True)): ...

to hide the commands from the default help (the core's own admin cogs do).

Each cog module still ends with the two-line v2 setup function::

    async def setup(bot):
        await bot.add_cog(Mine(bot))
"""

import asyncio
import logging

from discord import app_commands  # type: ignore
from discord.ext import commands  # type: ignore

from . import helpers
from .async_helpers import admin_check


class CoreCog(commands.Cog):
    """Base class for cogs of a ``BotCore`` bot."""

    #: seconds to wait after the bot is ready before ``after_ready()`` runs
    #: (the old cogs used ``asyncio.sleep(5)`` to let caches fill).
    ready_delay = 0

    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("discord." + type(self).__module__)
        self._after_ready_task = None

    # -- lifecycle -------------------------------------------------------
    async def cog_load(self):
        self._after_ready_task = asyncio.ensure_future(self._run_after_ready())

    async def cog_unload(self):
        task, self._after_ready_task = self._after_ready_task, None
        if task is not None and not task.done():
            task.cancel()

    async def _wait_for_bot(self):
        """``wait_until_ready``, but tolerate being loaded before the bot has logged in
        (discord.py raises RuntimeError until then)."""
        while True:
            try:
                await self.bot.wait_until_ready()
                return
            except RuntimeError:
                await asyncio.sleep(0.25)

    async def _run_after_ready(self):
        await self._wait_for_bot()
        if self.ready_delay:
            await asyncio.sleep(self.ready_delay)
        try:
            await self.after_ready()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("after_ready failed in %s", type(self).__name__)

    async def after_ready(self):
        """Override: one-time start-up work that needs a connected bot."""

    # -- lookups ---------------------------------------------------------
    def channel(self, ref=None):
        """A channel by id/name/alias; default: the configured ``log_channel``."""
        if ref is None:
            ref = self.bot.config("log_channel")
        return self.bot.find_channel(ref)

    @property
    def log_channel(self):
        return self.channel()

    def role(self, ref, guild=None):
        """A role (by id or name) in ``guild`` (default: the bot's home guild)."""
        if guild is None:
            guild = self.bot.home_guild()
        return helpers.find_role(guild, ref)


#: Hides a hybrid command's *slash* version from everyone but members with the Administrator
#: permission (server owners can still re-grant it in Server Settings > Integrations).  This only
#: tidies the slash picker: it is not security.  ``AdminCog.cog_check`` is what refuses non-admins,
#: so put this decorator *below* ``@commands.hybrid_command()`` on every command of an ``AdminCog``.
slash_admin_only = app_commands.default_permissions(administrator=True)


class AdminCog(CoreCog):
    """A ``CoreCog`` whose commands only admins may run."""

    async def cog_check(self, ctx):
        """Don't allow everyone to access this cog"""
        return await admin_check(ctx)
