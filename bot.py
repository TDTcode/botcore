"""``BotCore``: the base class both bots subclass.

Minimal bot::

    # <bot>/bot.py
    from .botcore import BotCore
    from . import param

    class MainBot(BotCore):
        activity_name = 'your suggestions'
        @classmethod
        def default_config(cls):
            return param.rc

Everything the two old ``MainBot`` classes had in common lives here; what
differs is expressed as overridable hooks / class attributes:

=========================  ==================================================
``make_intents()``         least-privilege by default (``message_content`` only)
``make_activity()``        presence, from ``activity_name`` / ``activity_type``
``default_config()``       the bot's ``Config`` (usually ``param.rc``)
``sync_app_commands``      sync the app-command tree once at start-up
``emoji_roles_enabled``    handle raw reactions with ``bot.emoji_roles``
``core_cogs``              shared cogs to load, after the bot's own (a bot's own
                           cog module of the same name replaces the shared one;
                           list names in ``exclude_cogs`` to skip any cog)
``call_legacy_async_init`` call ``cog._async_init()`` on first ready (for
                           cogs not yet ported to ``CoreCog.after_ready``)
=========================  ==================================================

Differences from the per-bot ``MainBot``:

* event handlers are real methods (overridable), not ``@self.event`` closures;
* cogs are loaded once in ``setup_hook`` (not on every ``on_ready``), so a
  gateway reconnect no longer raises ``ExtensionAlreadyLoaded``;
* a cog that fails to load is logged and skipped, it no longer silently
  aborts loading of the remaining cogs;
* ``bot_check`` is DM-safe; ``get_or_fetch_user`` returns the user it fetched;
* the old ``tdt()`` is ``home_guild()`` (guild id from ``home_guild_id``).
"""

import asyncio
import datetime
import importlib
import logging
import os
import pkgutil

import discord  # type: ignore
from discord.ext import commands  # type: ignore

from . import async_helpers, helpers
from .emoji_roles import EmojiRoles
from .git_manage import GitManager
from .storage import UserConfig, get_all_user_config_files

logger = logging.getLogger("discord." + __name__)

#: dotted name of this package as imported (``botcore`` or ``<bot>.botcore``)
CORE_PACKAGE = __name__.rpartition(".")[0]


def _utcnow():
    return datetime.datetime.now(datetime.UTC)


class BotCore(commands.Bot):
    """Base class for the bots.  See the module docstring."""

    #: shown as the bot's presence (None: no activity)
    activity_name = None
    activity_type = discord.ActivityType.playing
    sync_app_commands = False
    emoji_roles_enabled = False
    call_legacy_async_init = False
    #: shared cogs (modules in ``botcore/cogs``) loaded by default
    core_cogs = ("git_tools", "admin_tools", "debugging", "privacy")
    #: cog module names (core or the bot's own) that must not be loaded
    exclude_cogs = ()
    #: seconds to let the connection settle before announcing "Reboot complete."
    reissue_delay = 1

    def __init__(self, *args, config=None, package=None, reissue=None, startup=None, notices=None, **kwargs):
        if config is None:
            config = self.default_config()
        self.config = config
        self.paths = config.paths
        self.package = package or type(self).package_name()
        self.git = GitManager(self.paths.root)
        self.reissue = reissue
        self.startup = _utcnow() if startup is None else startup
        #: messages for the log channel, posted once on first ready (the runner uses this
        #: to report an update that happened while no bot was connected)
        self.notices = list(notices or [])
        self.failed_cogs = {}
        self._ready_once = False
        UserConfig.directory = self.paths.users
        # set some defaults for the bot
        kwargs.setdefault("intents", self.make_intents())
        kwargs.setdefault("command_prefix", config("cmd_prefix"))
        activity = self.make_activity()
        if activity is not None:
            kwargs.setdefault("activity", activity)
        kwargs["case_insensitive"] = True
        super().__init__(*args, **kwargs)
        self.emoji_roles = EmojiRoles(self)

    # -- construction hooks ------------------------------------------------
    @classmethod
    def default_config(cls):
        """Return the bot's ``Config`` (override; usually ``param.rc``)."""
        raise NotImplementedError(f"Pass config=... to {cls.__name__}() or override {cls.__name__}.default_config().")

    @classmethod
    def package_name(cls):
        """The bot package: the parent of the module defining the subclass
        (``wit_bot.bot`` -> ``wit_bot``).  Override if ``bot.py`` is nested deeper."""
        return cls.__module__.rpartition(".")[0]

    def make_intents(self):
        """Least-privilege defaults: no members/presences, message content on."""
        intents = discord.Intents.default()
        intents.members = False
        intents.presences = False
        intents.message_content = True
        return intents

    def make_activity(self):
        if not self.activity_name:
            return None
        return discord.Activity(name=self.activity_name, type=self.activity_type)

    # -- cogs ---------------------------------------------------------------
    @staticmethod
    def _discover(package):
        """Module names (not starting with ``_``) directly inside ``package``."""
        try:
            mod = importlib.import_module(package)
        except ModuleNotFoundError as e:
            # only "there is no such cogs package" is fine; a cog that fails to import
            # its own dependency must surface
            if e.name == package or package.startswith(e.name + "."):
                return []
            raise
        path = getattr(mod, "__path__", None)
        if path is None:
            return []
        return sorted(name for _, name, ispkg in pkgutil.iter_modules(path) if not ispkg and not name.startswith("_"))

    def cog_extensions(self):
        """Extension names to load: the bot's own cogs first, then the shared core cogs.

        Own cogs go first so that a command-name collision (e.g. the bot already has a
        ``privacy_policy`` command) costs the shared cog, never the bot's own cog; the
        failure is logged and recorded in ``failed_cogs``.  A core cog is skipped
        entirely if the bot has a cog module of the same name.
        """
        own = self._discover(self.package + ".cogs")
        out = [self.package + ".cogs." + name for name in own if name not in self.exclude_cogs]
        out += [
            CORE_PACKAGE + ".cogs." + name
            for name in self.core_cogs
            if name not in own and name not in self.exclude_cogs
        ]
        return out

    async def load_cogs(self):
        """Load every extension from ``cog_extensions()``; failures are logged, not fatal."""
        for ext in self.cog_extensions():
            try:
                await self.load_extension(ext)
            except commands.ExtensionAlreadyLoaded:
                pass
            except Exception as e:
                self.failed_cogs[ext] = e
                logger.exception("Failed to load cog %s", ext)

    # -- events ---------------------------------------------------------------
    async def setup_hook(self):
        """Runs once per login, before the bot is ready."""
        await self.load_cogs()
        if self.sync_app_commands:
            try:
                synced = await self.tree.sync()
                logger.info("Synced %d app commands.", len(synced))
            except Exception as e:  # noqa: BLE001  (a failed sync must never stop the bot starting)
                logger.error("Error syncing commands: %s", e)

    async def on_ready(self):
        """Do something when the bot is ready and active... like say so."""
        msg = "We have logged in as {0.user}, running discord.py {1.__version__}"
        logger.info(msg.format(self, discord))
        if self._ready_once:  # gateway reconnect: nothing more to do
            return
        self._ready_once = True
        if self.call_legacy_async_init:
            await self._call_legacy_async_init()
        await self._finish_reissue()
        await self._flush_notices()
        self.reissue = None
        self.startup = _utcnow()

    async def _call_legacy_async_init(self):
        for cog in self.cogs.values():
            init = getattr(cog, "_async_init", None)
            if init is None:
                continue
            try:
                await init()
            except Exception:
                logger.exception("_async_init failed in %s", type(cog).__name__)

    async def _finish_reissue(self):
        """After a ``reboot`` command: announce completion (and new git commits)."""
        if self.reissue is None:
            return
        logger.info("Reissue detected.")
        now = _utcnow()
        await asyncio.sleep(self.reissue_delay)
        try:
            look_back = min(now - helpers.hour, helpers.localize(self.startup), self.git.last_updated())
            log = self.git.log_items(look_back=max(look_back, now - helpers.week))
        except Exception:
            logger.exception("Could not read the git log after reboot.")
            log = []
        logger.info("Reboot complete.")
        channel = self.find_channel(self.reissue.channel.id)
        if channel is None:
            logger.warning("Reissue channel not found; not announcing.")
        elif log:
            await channel.send("Reboot complete. Git updates detected:")
            await async_helpers.split_send(channel, log, style="```")
        else:
            await channel.send("Reboot complete.")

    async def announce(self, text):
        """Post ``text`` to the log channel (config ``log_channel``).  Returns True if sent.

        A missing channel or a Discord error is logged, never raised: an announcement must
        not be able to break the caller.
        """
        channel = self.find_channel(self.config("log_channel"))
        if channel is None:
            logger.warning("No log channel to announce in: %s", text)
            return False
        try:
            await async_helpers.split_send(channel, text, style="```")
        except discord.HTTPException:
            logger.exception("Could not announce in the log channel: %s", text)
            return False
        return True

    async def _flush_notices(self):
        notices, self.notices = self.notices, []
        for text in notices:
            await self.announce(text)

    async def pull_code(self, channel=None):
        """Update the bot's own checkout (git runs in a thread, off the event loop).

        A rebase of local commits or a failure is announced in the log channel, unless that
        is ``channel`` (the channel the caller is replying in).  Returns the ``UpdateResult``.
        """
        result = await asyncio.to_thread(self.git.update)
        notice = result.notice()
        if notice:
            log_channel = self.find_channel(self.config("log_channel"))
            if channel is None or getattr(log_channel, "id", None) != getattr(channel, "id", None):
                await self.announce(notice)
        return result

    async def on_command_error(self, ctx, error):
        """Print errors to the discord channel where the command was given"""
        logger.error('Command "%s" failed. Invoked by %s.', ctx.command, ctx.author, exc_info=error)
        try:
            await ctx.send(str(error))
        except discord.HTTPException:
            pass

    async def on_command_completion(self, ctx):
        logger.debug(f'Command "{ctx.command}" invoked by {ctx.author}.')

    async def on_raw_reaction_add(self, payload):
        """Handle emoji reactions (see ``emoji_roles_enabled``)"""
        if self.emoji_roles_enabled:
            await self.emoji_roles.handle(payload)

    async def on_raw_reaction_remove(self, payload):
        """Handle emoji reactions (see ``emoji_roles_enabled``)"""
        if self.emoji_roles_enabled:
            await self.emoji_roles.handle(payload, delete=True)

    async def bot_check(self, ctx):
        """Run a check to see if we should respond to the given command."""
        # DM channels do not have a ``name`` attribute; commands stay available there.
        return getattr(ctx.channel, "name", None) not in (self.config("ignore_list") or [])

    # -- lookups ----------------------------------------------------------------
    def find_channel(self, channel):
        """Attempts to return a Channel type object from different types of inputs"""
        # if int assume it's an id number
        if isinstance(channel, int):
            out = self.get_channel(channel)
            if out is not None:
                return out
            for guild in self.guilds:
                out = guild.get_channel(channel)
                if out is not None:
                    return out
        # if string-like assume it's a channel name in a guild we're in
        if hasattr(channel, "lower"):
            kw = {}
            aliases = self.config.get("channel_aliases")
            if aliases is not None:
                kw["aliases"] = aliases
            out = [helpers.find_channel(i, channel, **kw) for i in self.guilds]
            out = [i for i in out if i]
            if out:
                return out[0]
        # id is an attribute of the channel type, so we should be good
        if hasattr(channel, "id"):
            return channel

    def home_guild(self):
        """The bot's own guild: ``home_guild_id`` from config (or the only guild it is in)."""
        gid = self.config("home_guild_id")
        if gid is not None:
            for g in self.guilds:
                if g.id == gid:
                    return g
            raise RuntimeError(f"Home guild {gid} not found")
        if len(self.guilds) == 1:
            return self.guilds[0]
        raise RuntimeError(f"No home_guild_id configured and the bot is in {len(self.guilds)} guilds")

    async def get_user_configs(self):
        files = get_all_user_config_files()

        async def get_user(fn):
            return await self.fetch_user(int(os.path.basename(fn).split(".")[0]))

        return [UserConfig(await get_user(f)) for f in files]

    async def get_or_fetch_user(self, user_id, guild=None, fallback=False):
        """A Member (if ``guild`` given and found) or User for ``user_id``.

        With ``fallback=True`` return the raw id if nobody was found, else None.
        """
        if isinstance(guild, commands.Context):
            guild = guild.guild
        if isinstance(guild, int):
            try:
                guild = next(i for i in self.guilds if i.id == guild)
            except StopIteration:
                guild = None
        if guild is not None:
            out = guild.get_member(user_id)
            if out is not None:
                return out
            try:
                out = await guild.fetch_member(user_id)
            except discord.errors.NotFound:
                out = None
            if out is not None:
                return out
        out = self.get_user(user_id)
        if out is not None:
            return out
        try:
            out = await self.fetch_user(user_id)
        except discord.errors.NotFound:
            out = None
        if out is None and fallback:
            return user_id
        return out

    async def is_admin(self, member, guild=None):
        """Admin = top role id in ``admin_role_ids``, guild owner, or bot owner."""
        top_role = getattr(member, "top_role", None)
        if getattr(top_role, "id", None) in (self.config("admin_role_ids") or []):
            return True
        if guild is None:
            guild = getattr(member, "guild", None)
        if guild is not None:
            owner = getattr(guild, "owner", None)
            if owner == member or (
                getattr(guild, "owner_id", None) is not None and guild.owner_id == getattr(member, "id", None)
            ):
                return True
        return bool(await self.is_owner(member))

    # -- emoji roles (thin delegates; see emoji_roles.py) -------------------------
    def enroll_emoji_role(self, *args, **kwargs):
        """Enroll a ``{emoji: role}`` rule to handle emoji reactions"""
        self.emoji_roles.enroll(*args, **kwargs)

    async def emoji2role(self, payload, *args, **kwargs):
        """Handle an emoji reaction (see ``EmojiRoles.assign``)"""
        return await self.emoji_roles.assign(payload, *args, **kwargs)

    @property
    def _emoji_dm_targets(self):
        """Deprecated alias of ``bot.emoji_roles.dm_targets`` (TDTbot's direct_messages cog)."""
        return self.emoji_roles.dm_targets

    # -- misc ---------------------------------------------------------------------
    def restart_time(self):
        """When this process/bot (or the ``reboot`` command that caused it) started"""
        t = helpers.localize(self.startup)
        try:
            t = max(t, helpers.localize(self.reissue.message.created_at))
        except AttributeError:
            pass
        return t
