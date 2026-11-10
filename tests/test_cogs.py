import asyncio
import types

import discord
import pytest
from discord.ext import commands

from ..cog import AdminCog, CoreCog
from ..cogs import admin_tools, debugging, git_tools, privacy
from ..git_manage import UpdateResult
from .conftest import FakeChannel, FakeGuild


class FakeCtx(FakeChannel):
    """Stands in for a command Context: ``ctx.send`` collects replies."""

    def __init__(self, bot=None, author=None, guild=None, channel=None):
        super().__init__()
        self.bot, self.author, self.guild = bot, author, guild
        self.channel = channel or FakeChannel(50, "cmds")


class ReadyBot:
    """Just enough bot for CoreCog."""

    def __init__(self, config=None):
        self.ready = asyncio.Event()
        self.config = config or (lambda key, default=None: {"log_channel": "debugging"}.get(key))
        self.looked_up = []
        self.guild = FakeGuild(1, roles=[types.SimpleNamespace(id=5, name="Mod")])

    async def wait_until_ready(self):
        await self.ready.wait()

    def find_channel(self, ref):
        self.looked_up.append(ref)
        return "chan:" + str(ref)

    def home_guild(self):
        return self.guild


class TestCoreCog:
    async def test_after_ready_waits_for_the_bot_and_runs_once(self):
        calls = []

        class C(CoreCog):
            async def after_ready(self):
                calls.append("ready")

        bot = ReadyBot()
        cog = C(bot)
        await cog.cog_load()
        await asyncio.sleep(0)
        assert calls == []  # bot not ready yet
        bot.ready.set()
        await cog._after_ready_task
        assert calls == ["ready"]
        await cog.cog_unload()

    async def test_ready_delay(self, monkeypatch):
        slept = []

        async def fake_sleep(s):
            slept.append(s)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        class C(CoreCog):
            ready_delay = 5

        bot = ReadyBot()
        bot.ready.set()
        cog = C(bot)
        await cog.cog_load()
        await cog._after_ready_task
        assert slept == [5]

    async def test_after_ready_errors_are_logged_not_raised(self, caplog):
        class C(CoreCog):
            async def after_ready(self):
                raise RuntimeError("bad start-up")

        bot = ReadyBot()
        bot.ready.set()
        cog = C(bot)
        await cog.cog_load()
        await cog._after_ready_task  # must not raise
        assert "bad start-up" in caplog.text

    async def test_loading_before_login_waits_instead_of_failing(self, monkeypatch):
        """discord.py raises RuntimeError from wait_until_ready until login has happened."""
        real_sleep = asyncio.sleep
        calls = []

        async def quick_sleep(s):
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", quick_sleep)

        class C(CoreCog):
            async def after_ready(self):
                calls.append("ready")

        bot = ReadyBot()
        attempts = []

        async def flaky_wait():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("Client has not been properly initialised.")
            await bot.ready.wait()

        bot.wait_until_ready = flaky_wait
        bot.ready.set()
        cog = C(bot)
        await cog.cog_load()
        await cog._after_ready_task
        assert calls == ["ready"] and len(attempts) == 3

    async def test_unload_cancels_a_pending_after_ready(self):
        bot = ReadyBot()
        cog = CoreCog(bot)
        await cog.cog_load()
        task = cog._after_ready_task
        await cog.cog_unload()
        with pytest.raises(asyncio.CancelledError):
            await task

    def test_lookups_use_config_and_home_guild(self):
        bot = ReadyBot()
        cog = CoreCog(bot)
        assert cog.log_channel == "chan:debugging"
        assert cog.channel("general") == "chan:general"
        assert cog.role("mod").id == 5
        assert cog.role(5).name == "Mod"
        assert cog.role("x", guild=FakeGuild(2)) is None


class TestAdminCog:
    async def test_cog_check_delegates_to_is_admin(self):
        seen = []

        class B:
            async def is_admin(self, member, guild=None):
                seen.append(member)
                return member == "admin"

        cog = AdminCog(B())
        assert await cog.cog_check(FakeCtx(B(), author="admin")) is True
        assert await cog.cog_check(FakeCtx(B(), author="pleb")) is False
        assert seen == ["admin", "pleb"]


@pytest.fixture
def loaded(bot):
    """The bot with the core cogs added directly (no extension machinery)."""

    async def load():
        await admin_tools.setup(bot)
        await git_tools.setup(bot)
        await debugging.setup(bot)
        await privacy.setup(bot)
        return bot

    return load


class TestSharedCogs:
    async def test_all_four_load_and_admin_ones_are_hidden_and_admin_only(self, loaded):
        bot = await loaded()
        for name in ("AdminTools", "GitTools", "Debugging"):
            cog = bot.cogs[name]
            assert isinstance(cog, AdminCog), name
            assert all(c.hidden for c in cog.get_commands()), name
        assert not isinstance(bot.cogs["Privacy"], AdminCog)
        assert not any(c.hidden for c in bot.cogs["Privacy"].get_commands())

    async def test_reboot_records_the_context_and_closes(self, loaded):
        bot = await loaded()
        closed = []

        async def close():
            closed.append(1)

        bot.close = close
        ctx = FakeCtx(bot)
        await bot.cogs["AdminTools"].reboot.callback(bot.cogs["AdminTools"], ctx)
        assert bot.reissue is ctx and closed == [1]
        assert ctx.sent == ["Ok. I will reboot now."]

    async def test_speak(self, loaded):
        bot = await loaded()
        cog = bot.cogs["AdminTools"]
        ctx = FakeCtx(bot, guild=FakeGuild(1))
        await cog.speak.callback(cog, ctx, "hello there")
        assert ctx.channel.sent == ["hello there"]

    async def test_speak_unknown_guild_replies_with_an_error(self, loaded):
        # regression: the error reply used to be a coroutine that was never awaited
        bot = await loaded()
        cog = bot.cogs["AdminTools"]
        ctx = FakeCtx(bot, guild=FakeGuild(1))
        await cog.speak.callback(cog, ctx, "hi", None, "Nowhere")
        assert ctx.sent == ['ERROR: server "Nowhere" not found.']

    async def test_git_log_and_pull_use_the_bots_git_manager(self, loaded):
        bot = await loaded()
        calls = []

        def update():
            calls.append("pull")
            return UpdateResult("a" * 40, "b" * 40, "origin/main")

        bot.git = types.SimpleNamespace(update=update, log_items=lambda: ["a", "b"])
        cog = bot.cogs["GitTools"]
        ctx = FakeCtx(bot)
        await cog.git_pull.callback(cog, ctx)
        await cog.git_log.callback(cog, ctx)
        assert calls == ["pull"]
        assert ctx.sent == ["Updated aaaaaaa..bbbbbbb.", "```a\nb```"]

    async def test_git_pull_announces_a_rebase_in_the_log_channel(self, loaded):
        bot = await loaded()
        log = FakeChannel(77, "debugging")
        bot.find_channel = lambda ref: log
        bot.git = types.SimpleNamespace(
            update=lambda: UpdateResult("a" * 40, "b" * 40, "origin/main", replayed=[("1234567", "my tweak")])
        )
        cog = bot.cogs["GitTools"]
        ctx = FakeCtx(bot)  # replying in a different channel than the log channel
        await cog.git_pull.callback(cog, ctx)
        assert "replaying 1 local commit(s)" in ctx.sent[0]
        assert len(log.sent) == 1 and "rebased 1 local commit(s)" in log.sent[0]

    async def test_git_pull_reports_failure_without_raising(self, loaded):
        bot = await loaded()
        bot.git = types.SimpleNamespace(
            update=lambda: UpdateResult("a" * 40, "a" * 40, "origin/main", error="merge conflict in a.txt")
        )
        cog = bot.cogs["GitTools"]
        ctx = FakeCtx(bot)
        await cog.git_pull.callback(cog, ctx)
        assert ctx.sent == ["Update failed: merge conflict in a.txt (still on aaaaaaa)."]

    async def test_git_subcommands_exist(self, loaded):
        bot = await loaded()
        group = bot.cogs["GitTools"].git
        assert {c.name for c in group.commands} == {"pull", "log"}

    async def test_debugging_param_hides_secrets(self, loaded):
        bot = await loaded()
        bot.config["token"] = "sekrit"
        cog = bot.cogs["Debugging"]
        ctx = FakeCtx(bot)
        await cog.param.callback(cog, ctx)
        assert "cmd_prefix" in ctx.sent[0] and "sekrit" not in ctx.sent[0]
        assert "config" in cog.param.aliases

    async def test_reload_cogs_awaits_every_reload(self, loaded):
        bot = await loaded()
        reloaded = []
        bot.__dict__["_extensions_stub"] = True

        async def reload_extension(name, **kw):
            reloaded.append(name)

        bot.reload_extension = reload_extension
        type(bot).extensions = property(lambda self: {"x.cogs.a": 1, "x.cogs.b": 2})
        try:
            cog = bot.cogs["Debugging"]
            ctx = FakeCtx(bot)
            await cog.reload_cogs.callback(cog, ctx)
        finally:
            del type(bot).extensions
        assert reloaded == ["x.cogs.a", "x.cogs.b"] and ctx.sent == ["Reloading a, b"]

    async def test_reload_cogs_pull_does_not_reload_after_a_failed_update(self, loaded):
        bot = await loaded()
        reloaded = []

        async def reload_extension(name, **kw):
            reloaded.append(name)

        bot.reload_extension = reload_extension
        bot.git = types.SimpleNamespace(
            update=lambda: UpdateResult("a" * 40, "a" * 40, "origin/main", error="no network")
        )
        cog = bot.cogs["Debugging"]
        ctx = FakeCtx(bot)
        await cog.reload_cogs.callback(cog, ctx, "pull")
        assert reloaded == []
        assert ctx.sent == ["Update failed: no network (still on aaaaaaa)."]

    async def test_reload_cogs_pull_reports_the_update_then_reloads(self, loaded):
        bot = await loaded()
        reloaded = []

        async def reload_extension(name, **kw):
            reloaded.append(name)

        bot.reload_extension = reload_extension
        bot.git = types.SimpleNamespace(update=lambda: UpdateResult("a" * 40, "a" * 40, "origin/main"))
        type(bot).extensions = property(lambda self: {"x.cogs.a": 1})
        try:
            cog = bot.cogs["Debugging"]
            ctx = FakeCtx(bot)
            await cog.reload_cogs.callback(cog, ctx, "pull")
        finally:
            del type(bot).extensions
        assert reloaded == ["x.cogs.a"]
        assert ctx.sent == ["Already up to date.\nReloading a"]

    async def test_member_id_unknown_member(self, loaded):
        bot = await loaded()
        cog = bot.cogs["Debugging"]
        guild = FakeGuild(1)
        guild.get_member_named = lambda name: None
        ctx = FakeCtx(bot, guild=guild)
        await cog.member_id.callback(cog, ctx, "ghost")
        assert ctx.sent == ["ERROR: member not found."]

    async def test_exec_is_owner_only(self, loaded):
        bot = await loaded()
        cmd = bot.cogs["Debugging"].exec
        assert any(getattr(c, "__qualname__", "").endswith("_owner") or c.__name__ == "_owner" for c in cmd.checks)


class TestPrivacy:
    async def test_shows_the_policy_from_the_bot_root(self, loaded):
        bot = await loaded()
        (bot.paths.root).mkdir(parents=True, exist_ok=True)
        (bot.paths.root / "PRIVACY.md").write_text("# Policy\nWe keep nothing.\n")
        cog = bot.cogs["Privacy"]
        ctx = FakeCtx(bot)
        await cog.privacy_policy.callback(cog, ctx)
        assert ctx.sent == ["# Policy\nWe keep nothing.\n"]
        assert set(cog.privacy_policy.aliases) == {"privacy", "policy"}

    async def test_policy_file_can_be_overridden(self, loaded, tmp_path):
        bot = await loaded()
        other = tmp_path / "elsewhere.md"
        other.write_text("custom")
        bot.config["privacy_file"] = str(other)
        cog = bot.cogs["Privacy"]
        ctx = FakeCtx(bot)
        await cog.privacy_policy.callback(cog, ctx)
        assert ctx.sent == ["custom"]

    async def test_missing_policy_points_to_the_url(self, loaded):
        bot = await loaded()
        bot.config["privacy_url"] = "https://example.com/PRIVACY.md"
        cog = bot.cogs["Privacy"]
        ctx = FakeCtx(bot)
        await cog.privacy_policy.callback(cog, ctx)
        assert ctx.sent == ["No privacy policy file is available. See: https://example.com/PRIVACY.md"]
        bot.config["privacy_url"] = None
        ctx = FakeCtx(bot)
        await cog.privacy_policy.callback(cog, ctx)
        assert ctx.sent == ["No privacy policy file is available."]


def test_commands_and_discord_are_importable_names():
    assert discord and commands  # the shared cogs target discord.py 2.x
