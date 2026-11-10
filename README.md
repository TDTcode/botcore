# botcore

Shared foundation for the TDT Discord bots ([TDTbot](https://github.com/TDTcode/TDTbot),
[wit_bot](https://github.com/TDTcode/wit_bot)): a `BotCore` base class, a `CoreCog`
base class, configuration, storage, git self-update and a shared `__main__` runner.

Requires Python >= 3.11 and discord.py >= 2.4 (discord.py 1.x is not supported).

## How it is used

botcore lives **inside a bot's package directory** as `<bot>/botcore/` and is imported
relatively, so the bot's self-update (`git pull`) and module reload keep working:

```python
# <bot>/bot.py
import discord
from . import param
from .botcore import BotCore


class MainBot(BotCore):
    activity_name = "your suggestions"
    activity_type = discord.ActivityType.listening

    @classmethod
    def default_config(cls):
        return param.rc
```

```python
# <bot>/param.py  (data only: ids and defaults)
from pathlib import Path
from .botcore.config import Config

rc = Config(
    {"admin_role_ids": [123], "home_guild_id": 456, "ignore_list": ["lfg"]}, root=Path(__file__).parent, name="tdt"
)
```

```python
# <bot>/__main__.py
from .botcore.runner import run

run(__package__)
```

Run it as before, from the parent directory: `python -m <bot>` (`-h` for options).
**Keep every import inside the core relative**, so it works from any `<bot>/botcore/`
(and as plain `botcore` for its own tests).

## Adding botcore to a bot

botcore is its own repository: <https://github.com/TDTcode/botcore>
(`git@github.com:TDTcode/botcore.git`, branch `main`). A bot uses it as a git submodule at
`<bot>/botcore/`; from the bot's repository root:

```sh
git submodule add ../botcore.git botcore
```

The relative URL resolves next to the bot's own remote. Clone a bot with
`git clone --recurse-submodules`, or run `git submodule update --init` in an existing
checkout. A bot pins one specific botcore commit; to take a newer one, update the
submodule (`git -C botcore pull`) and commit the changed pointer in the bot repository.
The bot's self-update takes care of the rest: `GitManager.update()` ends with
`git submodule update --init --recursive`.

## What is in the box

| Module | What it gives you |
|---|---|
| `bot.py` | `BotCore(commands.Bot)`: overridable `make_intents()` (least privilege by default), `make_activity()`, `default_config()`; cogs loaded once in `setup_hook`; `bot_check` (DM-safe), `find_channel`, `get_or_fetch_user`, `is_admin`, `home_guild()`, `restart_time()`; `emoji_roles`; `git` (`GitManager`) |
| `cog.py` | `CoreCog` (`after_ready()` hook, `channel()`, `role()`, `log_channel`) and `AdminCog` (admin-only `cog_check`) |
| `config.py` | `Config` (callable dict: `rc('key', default)`; layered defaults, json file, token from CLI/env/file) and `Paths` (`config`, `data`, `users`, `logs`; `BOT_DATA_DIR` moves mutable state out of the checkout) |
| `storage.py` | `DataContainer`, `UserConfig`, `PermaDict`, `IntPermaDict` (formats unchanged; json written atomically) |
| `emoji_roles.py` | react-for-a-role (`bot.enroll_emoji_role(...)`, enabled with `emoji_roles_enabled = True`) |
| `git_manage.py` | `GitManager`: `update()` (fetch + **rebase** onto upstream, then submodule sync; returns an `UpdateResult`), `head()`, `rollback(sha)`, `log_items()`, `last_updated()` |
| `runner.py` | the shared `__main__`: argparse, restart loop, update + purge-and-reimport, rollback of a failed update, hand-over of log-channel notices |
| `helpers.py`, `async_helpers.py` | `find_channel`, `find_role`, time helpers, `split_send`, `admin_check`, `parse_payload`, ... |
| `log_init.py`, `reloader.py` | logging set-up; recursive package reload |
| `cogs/` | shared cogs: `git_tools`, `admin_tools`, `debugging`, `privacy` |
| `testing.py` | helpers for bot tests: `make_bot`, `add_guild`, `assert_least_privilege` |

### Self-update and the log channel

The runner updates the bot's checkout between runs: `git fetch`, then `git rebase` onto the
upstream branch (independent of the host's `pull.rebase` setting), then a submodule sync.
Local commits on a deployed checkout are replayed on top of the new upstream commits.
If the new code fails to import, the checkout is rolled back (`git reset --keep`) and the
old code runs again. Nothing is destroyed: a conflicting rebase is aborted, and a tracked file with
uncommitted edits makes the update fail instead of being overwritten.

The bot tells you in its **log channel** (config `log_channel`, normally `debugging`) when an
update rebased local commits (with their old hashes), failed, or was rolled back. Plain
fast-forward updates are not announced; the reboot message already lists new commits.
The update happens while no bot is connected, so the runner passes the text to the next bot
(`MainBot(notices=[...])`), which posts it once it is ready. The `git_pull` and
`reload_cogs pull` commands announce right away through `bot.pull_code()`. Use
`await bot.announce(text)` for your own messages to that channel.

### Class attributes on `BotCore`

| Attribute | Default | Meaning |
|---|---|---|
| `activity_name` / `activity_type` | `None` / `playing` | presence |
| `sync_app_commands` | `False` | sync the app-command tree once at start-up |
| `emoji_roles_enabled` | `False` | handle raw reactions with `bot.emoji_roles` |
| `core_cogs` | `git_tools, admin_tools, debugging, privacy` | shared cogs to load (after the bot's own) |
| `exclude_cogs` | `()` | cog module names (own or shared) not to load |
| `call_legacy_async_init` | `False` | call `cog._async_init()` once on first ready (cogs not yet ported to `after_ready`) |
| `reissue_delay` | `1` | seconds before announcing "Reboot complete." |

### Config keys the core reads

`cmd_prefix` (default derived from `name`: `WIT$ Wit$ wit$`), `ignore_list`, `log_channel`,
`admin_role_ids` (top-role ids that count as admin), `home_guild_id`, `channel_aliases`
(optional name->id mapping for `find_channel`), `privacy_url` / `privacy_file`,
`emoji_debug_user`, `config_file`, `token_file`, `logfile`, `timezone`, `event_reminders`.

Token lookup: `-t` (file or literal) > `<NAME>_TOKEN` / `DISCORD_TOKEN` env > `config/token.txt`.

### Cogs: bot-specific vs shared

Cogs are discovered with `pkgutil` from `<package>.cogs` (modules starting with `_` are
skipped). The bot's own cogs load first, then the shared ones. A bot cog module with the
same name as a shared one (e.g. `admin_tools.py`) replaces it; a command-name collision
costs the shared cog (logged, listed in `bot.failed_cogs`), never the bot's own.
A cog that fails to load is logged and skipped.

```python
# <bot>/cogs/hello.py
from discord.ext import commands
from ..botcore import CoreCog


class Hello(CoreCog):
    async def after_ready(self):  # replaces on_ready + sleep(5) + _async_init
        await self.log_channel.send("hello is up")

    @commands.command()
    async def hi(self, ctx):
        await ctx.send("hi")


async def setup(bot):
    await bot.add_cog(Hello(bot))
```

## Tests

From this directory, with the dependencies from `pyproject.toml` plus the test tools
installed, e.g. with [uv](https://docs.astral.sh/uv/):

```sh
uv venv
uv pip install discord.py gitpython humanize pytz pytest pytest-asyncio
.venv/bin/python -m pytest
```

`pyproject.toml` puts the parent directory on `sys.path`, so `import botcore` works.
Use `botcore.testing` in a bot's own tests:

```python
from .botcore import testing

bot = testing.make_bot(MainBot)
testing.assert_least_privilege(bot, members=True)  # TDT; wit: members=False
```

## Compatibility rule

Different bots will run different pins of botcore. A change must stay backward compatible
for one release: add a deprecation shim (e.g. `bot._emoji_dm_targets`) instead of a breaking
rename. Bump `__version__` in `__init__.py` when behaviour changes.
