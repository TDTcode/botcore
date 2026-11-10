"""The shared ``__main__``: argument parsing, start-up, restart/update/reload loop.

A bot's ``__main__.py`` becomes::

    from .botcore.runner import run
    run(__package__)

Conventions the bot package must follow:

* ``<package>.param`` exposes ``rc`` (a ``botcore.config.Config``);
* ``<package>.bot`` exposes ``MainBot`` (a ``BotCore`` subclass).

Lifecycle (same idea as the old per-bot ``__main__`` loop)::

    import bot -> run until it stops
      -> git update = fetch + rebase (remembering the old HEAD) -> purge + re-import the bot package
      -> if the new code fails to import: roll the checkout back and import the old code
      -> run the new bot, handing over ``reissue`` / ``startup`` (set by the ``reboot`` command)
         and ``notices`` (a rebase of local commits, a failed update or a rollback: the new bot
         posts them in its log channel once it is ready)

Differences from the old loop:

* the bot is run with ``asyncio.run(bot.start(...))``; discord.py's ``bot.run()``
  swallows Ctrl-C, which made the old loop restart instead of exiting;
* modules are *purged and re-imported* (one pass) instead of two
  ``reload_package`` passes, so a stale base class can't survive a reload; this
  also reloads a nested ``<package>.botcore`` (it lives inside the package);
* a failed update is rolled back (``GitManager.rollback``);
* a missing token is a clear error, not a login failure;
* ``-l/--logfile`` now overrides the config; ``--no-tracemalloc`` turns off the
  allocation tracing the old loop always enabled.
"""

import argparse
import asyncio
import importlib
import os
import sys
import time
import tracemalloc

from aiohttp.client_exceptions import ClientConnectorError  # type: ignore

from . import log_init

logger = log_init.logger

#: a run shorter than this (seconds) is a crash loop: stop instead of restarting
MIN_RUN_SECONDS = 5


def build_parser(extend_parser=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", default=None, type=str, help="Load given config file.")
    parser.add_argument("-t", "--token", default=None, type=str, help="Use provided API token/file.")
    parser.add_argument(
        "-v",
        "--verbose",
        default=False,
        action="store_true",
        help="print all output, timestamps and logging information",
    )
    parser.add_argument("-l", "--logfile", type=str, default=None, help="Set filename of logfile")
    parser.add_argument(
        "--no-tracemalloc",
        default=False,
        action="store_true",
        help="don't trace memory allocations (tracing is on by default, for debugging tracebacks)",
    )
    if extend_parser is not None:
        extend_parser(parser)
    return parser


class Runner:
    def __init__(self, package, argv=None, extend_parser=None, configure=None, bot_class_name="MainBot"):
        """``package``: importable name of the bot (``__package__`` of its ``__main__``).
        ``extend_parser(parser)`` may add bot-specific arguments; ``configure(config, args)``
        may apply them to the config before the bot is built."""
        self.package = package
        self.argv = argv
        self.parser = build_parser(extend_parser)
        self.configure = configure
        self.bot_class_name = bot_class_name
        #: messages for the next bot's log channel (updates happen while no bot is connected)
        self.notices = []

    # -- module management ------------------------------------------------
    def import_bot(self):
        """Import (or re-import) the bot's ``param`` and ``bot`` modules."""
        param = importlib.import_module(self.package + ".param")
        bot_module = importlib.import_module(self.package + ".bot")
        return param, bot_module

    def purge_modules(self):
        """Forget every imported module of the bot package (including ``botcore``)."""
        prefix = self.package + "."
        for name in [n for n in sys.modules if n == self.package or n.startswith(prefix)]:
            del sys.modules[name]
        importlib.invalidate_caches()

    def update_and_reload(self, git):
        """Update the checkout, then import the new code; roll back if that fails.

        Returns ``(param_module, bot_module)``.  ``git`` is a ``GitManager``.
        """
        before = None
        try:
            before = git.head()
            result = git.update()
        except Exception as e:  # noqa: BLE001  (any update failure is logged and reported, never fatal)
            logger.error("Update failed: %s", e)
            self.notices.append(f"Update failed: {e}")
        else:
            logger.info(result.summary())
            notice = result.notice()
            if notice:
                logger.warning(notice)
                self.notices.append(notice)
        self.purge_modules()
        try:
            return self.import_bot()
        except Exception as e:
            logger.exception("The updated code failed to import.")
            if before is None or git.head() == before:
                raise
            failed = git.head()
            error = e
        git.rollback(before)
        self.purge_modules()
        logger.warning("Rolled back to the previous commit.")
        self.notices.append(
            f"The update to {failed[:7]} failed to import "
            f"({type(error).__name__}: {error}); rolled back to {before[:7]}."
        )
        return self.import_bot()

    # -- running ---------------------------------------------------------------
    def log_kwargs(self, args, config):
        kwargs = dict(config)
        kwargs.update({k: v for k, v in vars(args).items() if v not in (None, False)})
        return kwargs

    def make_bot(self, bot_module, reissue, startup):
        notices, self.notices = self.notices, []
        return getattr(bot_module, self.bot_class_name)(reissue=reissue, startup=startup, notices=notices)

    def serve(self, bot, token):
        """Run ``bot`` until it stops.  Ctrl-C propagates as ``KeyboardInterrupt``."""

        async def runner():
            async with bot:
                await bot.start(token)

        asyncio.run(runner())

    def run(self):
        """Run the bot (restarting on ``reboot``); returns a process exit code."""
        args = self.parser.parse_args(self.argv)
        if not args.no_tracemalloc:
            tracemalloc.start()  # for traceback info to debug
        reissue, startup = None, None
        param, bot_module = self.import_bot()
        now = 0
        # keep looping while a run takes longer than MIN_RUN_SECONDS (no crash loops)
        while time.time() - now > MIN_RUN_SECONDS:
            now = time.time()
            config = param.rc
            config.read_config(args.config)
            if self.configure is not None:
                self.configure(config, args)
            if config.paths.overridden:
                config.paths.ensure()
            kwargs = self.log_kwargs(args, config)
            if kwargs.get("logfile"):
                os.makedirs(os.path.dirname(os.path.abspath(kwargs["logfile"])), exist_ok=True)
            # logging first: discord.py installs a NullHandler on the 'discord' logger, so
            # anything logged before init_logging (e.g. "no token") would be lost
            log_init.init_logging(**kwargs)
            token = config.read_token(args.token)
            if not token:
                logger.error(
                    "No Discord token: use -t, set %s, or fill %s.",
                    " / ".join(config.token_env_names),
                    config.dget("token_file"),
                )
                return 2

            bot = self.make_bot(bot_module, reissue, startup)
            try:
                self.serve(bot, token)
            except KeyboardInterrupt:
                raise
            except RuntimeError as e:
                logger.info(e)
            except KeyError as e:
                logger.error(e)
            except ClientConnectorError as e:
                logger.error(e)
                time.sleep(60)
            # if we get here, the bot loop has ended
            reissue, startup = bot.reissue, bot.startup
            git = bot.git
            del bot
            param, bot_module = self.update_and_reload(git)
            logger.info("End of loop.")
        return 0


def run(package, argv=None, **kwargs):
    """Entry point for a bot's ``__main__.py``: ``run(__package__)``."""
    code = Runner(package, argv, **kwargs).run()
    if code:
        sys.exit(code)
