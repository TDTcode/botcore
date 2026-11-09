import datetime
import logging
import types

import discord
import pytest
from discord.ext import commands

from .. import bot as bot_module
from .. import helpers, testing
from ..bot import BotCore
from ..storage import UserConfig
from .conftest import BotForTests, FakeChannel, FakeGuild


class TestConstruction:
    def test_defaults(self, bot, config):
        assert bot.case_insensitive is True
        assert bot.command_prefix == ['t$']
        assert bot.reissue is None
        assert bot.config is config and bot.paths is config.paths
        assert bot.startup.tzinfo is not None  # aware: safe to compare with git/now times
        assert bot.git.root == str(config.paths.root)

    def test_intents_are_least_privilege_by_default(self, bot):
        testing.assert_least_privilege(bot)

    def test_make_intents_can_be_overridden(self, make_bot):
        class Members(BotForTests):
            def make_intents(self):
                intents = super().make_intents()
                intents.members = True
                return intents
        testing.assert_least_privilege(make_bot(Members), members=True)

    def test_prefix_and_other_kwargs_can_be_overridden(self, make_bot):
        bot = make_bot(command_prefix='!')
        assert bot.command_prefix == '!'

    def test_activity_from_class_attributes(self, make_bot):
        class Playing(BotForTests):
            activity_name = 'Wit'
        assert make_bot().activity is None
        activity = make_bot(Playing).activity
        assert activity.name == 'Wit' and activity.type == discord.ActivityType.playing

    def test_user_config_directory_is_injected(self, bot, monkeypatch):
        assert UserConfig.directory == bot.paths.users

    def test_config_comes_from_default_config_hook(self, config):
        class Hooked(BotCore):
            @classmethod
            def default_config(cls):
                return config
        assert Hooked(package='x').config is config

    def test_missing_config_is_a_helpful_error(self):
        with pytest.raises(NotImplementedError, match='default_config'):
            BotCore(package='x')

    def test_package_name_defaults_to_parent_of_defining_module(self, config):
        class Sub(BotCore):
            pass
        Sub.__module__ = 'some_bot.bot'
        assert Sub.package_name() == 'some_bot'
        assert Sub(config=config).package == 'some_bot'

    def test_loop_kwarg_is_accepted_for_old_tests(self, make_bot):
        # TDTbot/tests/test_bot.py builds MainBot(loop=...)
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            make_bot(loop=loop)
        finally:
            loop.close()


class TestFindChannel:
    def test_by_id_uses_guild_lookup(self, bot):
        chan = FakeChannel(123, 'general')
        testing.add_guild(bot, FakeGuild(1, channels=[chan]))
        bot.get_channel = lambda cid: None
        assert bot.find_channel(123) is chan

    def test_by_name_uses_helpers_module_attribute(self, bot, monkeypatch):
        # the TDTbot test patches helpers.find_channel with a 2-argument fake
        chan = FakeChannel(321, 'random')
        testing.add_guild(bot, FakeGuild(2, channels=[chan]))
        monkeypatch.setattr(helpers, 'find_channel',
                            lambda guild, name: chan if name == 'random' else None)
        assert bot.find_channel('random') is chan

    def test_channel_aliases_from_config_are_passed_on(self, bot):
        chan = FakeChannel(77, 'real-name')
        testing.add_guild(bot, FakeGuild(3, channels=[chan]))
        bot.config['channel_aliases'] = {'nick': 77}
        assert bot.find_channel('nick') is chan

    def test_objects_pass_through_and_unknown_is_none(self, bot):
        chan = FakeChannel(5)
        assert bot.find_channel(chan) is chan
        assert bot.find_channel(object()) is None


class TestGetOrFetchUser:
    @pytest.fixture
    def b(self, bot):
        bot.get_user = lambda uid: None

        async def fetch_user(uid):
            return None
        bot.fetch_user = fetch_user
        return bot

    async def test_prefers_cached_member(self, b):
        member = types.SimpleNamespace(id=555)
        guild = FakeGuild(9, members=[member])
        assert await b.get_or_fetch_user(555, guild=guild) is member

    async def test_fetches_member_when_not_cached(self, b):
        member = types.SimpleNamespace(id=777)
        guild = FakeGuild(10)

        async def fetch_member(uid):
            return member
        guild.fetch_member = fetch_member
        assert await b.get_or_fetch_user(777, guild=guild) is member

    async def test_guild_may_be_an_id_or_a_context(self, b):
        member = types.SimpleNamespace(id=5)
        guild = FakeGuild(11, members=[member])
        testing.add_guild(b, guild)
        assert await b.get_or_fetch_user(5, guild=11) is member
        ctx = commands.Context.__new__(commands.Context)
        ctx.guild = guild
        assert await b.get_or_fetch_user(5, guild=ctx) is member

    async def test_returns_the_fetched_user(self, b):
        # regression: the old code fetched the user and then `return None`
        user = types.SimpleNamespace(id=42)

        async def fetch_user(uid):
            return user
        b.fetch_user = fetch_user
        assert await b.get_or_fetch_user(42) is user

    async def test_fallback_returns_raw_id_else_none(self, b):
        assert await b.get_or_fetch_user(999, fallback=True) == 999
        assert await b.get_or_fetch_user(999) is None

    async def test_not_found_is_handled(self, b):
        async def fetch_user(uid):
            raise discord.errors.NotFound(types.SimpleNamespace(status=404, reason='x'), 'nope')
        b.fetch_user = fetch_user
        assert await b.get_or_fetch_user(1) is None


class TestChecks:
    async def test_bot_check_is_dm_safe_and_honours_ignore_list(self, bot):
        bot.config['ignore_list'] = ['quiet']
        dm = types.SimpleNamespace(channel=types.SimpleNamespace())  # no .name, like a DMChannel
        assert await bot.bot_check(dm) is True
        assert await bot.bot_check(types.SimpleNamespace(channel=FakeChannel(name='quiet'))) is False
        assert await bot.bot_check(types.SimpleNamespace(channel=FakeChannel(name='loud'))) is True

    async def test_is_admin(self, bot, monkeypatch):
        bot.config['admin_role_ids'] = [100, 200]
        admin = types.SimpleNamespace(id=1, top_role=types.SimpleNamespace(id=200))
        plain = types.SimpleNamespace(id=2, top_role=types.SimpleNamespace(id=5))
        owner = types.SimpleNamespace(id=3, top_role=types.SimpleNamespace(id=5))

        async def is_owner(user):
            return user.id == 4
        monkeypatch.setattr(bot, 'is_owner', is_owner)
        assert await bot.is_admin(admin) is True
        assert await bot.is_admin(plain, FakeGuild(owner=owner)) is False
        assert await bot.is_admin(owner, FakeGuild(owner=owner)) is True  # guild owner
        assert await bot.is_admin(types.SimpleNamespace(id=4, top_role=None)) is True  # bot owner

    async def test_is_admin_without_configured_roles_is_owner_only(self, bot, monkeypatch):
        async def is_owner(user):
            return False
        monkeypatch.setattr(bot, 'is_owner', is_owner)
        top = types.SimpleNamespace(id=100)
        assert await bot.is_admin(types.SimpleNamespace(id=1, top_role=top)) is False


class TestHomeGuild:
    def test_configured_id(self, bot):
        guild = testing.add_guild(bot, FakeGuild(5))
        testing.add_guild(bot, FakeGuild(6))
        bot.config['home_guild_id'] = 5
        assert bot.home_guild() is guild

    def test_configured_id_not_joined(self, bot):
        bot.config['home_guild_id'] = 5
        with pytest.raises(RuntimeError, match='not found'):
            bot.home_guild()

    def test_single_guild_is_the_home_guild(self, bot):
        guild = testing.add_guild(bot, FakeGuild(5))
        assert bot.home_guild() is guild

    def test_ambiguous(self, bot):
        testing.add_guild(bot, FakeGuild(5))
        testing.add_guild(bot, FakeGuild(6))
        with pytest.raises(RuntimeError, match='home_guild_id'):
            bot.home_guild()


class TestCogDiscovery:
    @pytest.fixture
    def pkg(self, fake_package):
        name, root = fake_package
        cogs = root / 'cogs'
        cogs.mkdir()
        (cogs / '__init__.py').write_text('')
        (cogs / 'hello.py').write_text('async def setup(bot):\n    bot.hello_loaded = True\n')
        (cogs / '_private.py').write_text('')
        (cogs / 'privacy.py').write_text('async def setup(bot):\n    bot.own_privacy = True\n')
        return name

    def test_own_cogs_first_sorted_then_core_and_private_skipped(self, make_bot, pkg):
        bot = make_bot(package=pkg)
        exts = bot.cog_extensions()
        core = [e for e in exts if e.startswith('botcore.cogs.')]
        own = [e for e in exts if e.startswith(pkg)]
        assert exts == own + core
        assert own == [pkg + '.cogs.hello', pkg + '.cogs.privacy']

    def test_own_cog_of_the_same_name_overrides_the_core_one(self, make_bot, pkg):
        exts = make_bot(package=pkg).cog_extensions()
        assert 'botcore.cogs.privacy' not in exts
        assert pkg + '.cogs.privacy' in exts
        assert 'botcore.cogs.git_tools' in exts

    def test_exclude_cogs(self, make_bot, pkg):
        class Excluding(BotForTests):
            exclude_cogs = ('git_tools', 'hello')
        exts = make_bot(Excluding, package=pkg).cog_extensions()
        assert 'botcore.cogs.git_tools' not in exts
        assert pkg + '.cogs.hello' not in exts

    def test_core_cogs_can_be_trimmed(self, make_bot):
        class OnlyGit(BotForTests):
            core_cogs = ('git_tools',)
        assert make_bot(OnlyGit).cog_extensions() == ['botcore.cogs.git_tools']

    def test_package_without_cogs_package_has_only_core_cogs(self, make_bot, fake_package):
        name, _ = fake_package
        exts = make_bot(package=name).cog_extensions()
        assert exts and all(e.startswith('botcore.cogs.') for e in exts)

    async def test_setup_hook_loads_everything_once(self, make_bot, pkg):
        bot = make_bot(package=pkg)
        await bot.setup_hook()
        await bot.setup_hook()  # a second call must not raise ExtensionAlreadyLoaded
        assert bot.hello_loaded is True and bot.own_privacy is True
        assert pkg + '.cogs.hello' in bot.extensions
        assert bot.failed_cogs == {}
        assert {'GitTools', 'AdminTools', 'Debugging'} <= set(bot.cogs)
        for ext in list(bot.extensions):
            await bot.unload_extension(ext)

    async def test_a_broken_cog_is_logged_and_does_not_stop_the_rest(self, make_bot, fake_package,
                                                                   caplog):
        name, root = fake_package
        (root / 'cogs').mkdir()
        (root / 'cogs' / '__init__.py').write_text('')
        (root / 'cogs' / 'a_bad.py').write_text('raise RuntimeError("boom")\n')
        (root / 'cogs' / 'b_good.py').write_text('async def setup(bot):\n    bot.good = True\n')
        bot = make_bot(package=name)
        with caplog.at_level(logging.ERROR):
            await bot.load_cogs()
        assert bot.good is True
        assert list(bot.failed_cogs) == [name + '.cogs.a_bad']
        assert 'Failed to load cog' in caplog.text
        for ext in list(bot.extensions):
            await bot.unload_extension(ext)

    async def test_a_command_name_collision_costs_the_core_cog_not_the_bots_own(
            self, make_bot, fake_package):
        """TDTbot's own `main` cog already has a `privacy_policy` command, like the core Privacy cog."""
        name, root = fake_package
        (root / 'cogs').mkdir()
        (root / 'cogs' / '__init__.py').write_text('')
        (root / 'cogs' / 'main.py').write_text(
            'from discord.ext import commands\n'
            'class Main(commands.Cog):\n'
            '    @commands.command()\n'
            '    async def privacy_policy(self, ctx): pass\n'
            'async def setup(bot):\n'
            '    await bot.add_cog(Main())\n')
        bot = make_bot(package=name)
        await bot.load_cogs()
        assert 'Main' in bot.cogs  # the bot's own cog survived
        assert 'Privacy' not in bot.cogs  # the shared one gave way...
        assert list(bot.failed_cogs) == ['botcore.cogs.privacy']  # ...and says so
        assert 'GitTools' in bot.cogs  # unrelated core cogs still load
        for ext in list(bot.extensions):
            await bot.unload_extension(ext)

    async def test_sync_app_commands_flag(self, make_bot):
        class Syncing(BotForTests):
            sync_app_commands = True
            core_cogs = ()
        calls = []
        bot = make_bot(Syncing)

        async def sync():
            calls.append(1)
            return [1, 2]
        bot.tree.sync = sync
        await bot.setup_hook()
        assert calls == [1]
        quiet = make_bot()
        quiet.tree.sync = sync
        quiet.core_cogs = ()
        await quiet.setup_hook()
        assert calls == [1]  # flag off: no sync

    async def test_sync_errors_are_logged_not_raised(self, make_bot, caplog):
        class Syncing(BotForTests):
            sync_app_commands = True
            core_cogs = ()
        bot = make_bot(Syncing)

        async def sync():
            raise RuntimeError('discord said no')
        bot.tree.sync = sync
        with caplog.at_level(logging.ERROR):
            await bot.setup_hook()
        assert 'discord said no' in caplog.text


class TestReady:
    async def test_on_ready_runs_once_per_process(self, make_bot, monkeypatch):
        bot = make_bot()
        bot.reissue_delay = 0
        calls = []

        async def finish():
            calls.append(1)
        monkeypatch.setattr(bot, '_finish_reissue', finish)
        await bot.on_ready()
        first_startup = bot.startup
        await bot.on_ready()  # gateway reconnect
        assert calls == [1]
        assert bot.startup is first_startup

    async def test_legacy_async_init_is_opt_in(self, make_bot):
        class Legacy(commands.Cog):
            called = 0

            async def _async_init(self):
                Legacy.called += 1

        class Broken(commands.Cog):
            async def _async_init(self):
                raise RuntimeError('x')  # must not stop the other cogs

        for flag, expected in ((False, 0), (True, 1)):
            Legacy.called = 0
            bot = make_bot()
            bot.call_legacy_async_init = flag
            bot.reissue_delay = 0
            await bot.add_cog(Broken())
            await bot.add_cog(Legacy())
            await bot.on_ready()
            assert Legacy.called == expected

    async def test_reboot_announces_completion_and_new_commits(self, make_bot):
        bot = make_bot()
        bot.reissue_delay = 0
        channel = FakeChannel(9)
        bot.find_channel = lambda cid: channel if cid == 9 else None
        now = datetime.datetime.now(datetime.timezone.utc)
        bot.git = types.SimpleNamespace(
            last_updated=lambda: now - datetime.timedelta(minutes=1),
            log_items=lambda look_back=None: ['1 min ago: a change <me> [abc1234]'])
        bot.reissue = types.SimpleNamespace(channel=types.SimpleNamespace(id=9))
        await bot.on_ready()
        assert channel.sent[0] == 'Reboot complete. Git updates detected:'
        assert 'a change' in channel.sent[1]
        assert bot.reissue is None

    async def test_reboot_without_new_commits(self, make_bot):
        bot = make_bot()
        bot.reissue_delay = 0
        channel = FakeChannel(9)
        bot.find_channel = lambda cid: channel
        bot.git = types.SimpleNamespace(
            last_updated=lambda: datetime.datetime.now(datetime.timezone.utc),
            log_items=lambda look_back=None: [])
        bot.reissue = types.SimpleNamespace(channel=types.SimpleNamespace(id=9))
        await bot.on_ready()
        assert channel.sent == ['Reboot complete.']

    async def test_reboot_survives_git_and_channel_problems(self, make_bot, caplog):
        bot = make_bot()
        bot.reissue_delay = 0
        bot.find_channel = lambda cid: None

        def boom(*a, **k):
            raise RuntimeError('no git')
        bot.git = types.SimpleNamespace(last_updated=boom, log_items=boom)
        bot.reissue = types.SimpleNamespace(channel=types.SimpleNamespace(id=9))
        with caplog.at_level(logging.WARNING):
            await bot.on_ready()  # must not raise
        assert bot.reissue is None and 'Reissue channel not found' in caplog.text

    def test_restart_time_with_aware_message_timestamp(self, make_bot):
        # regression: pytz.utc.localize(aware) used to raise ValueError under discord.py 2
        bot = make_bot()
        later = bot.startup + datetime.timedelta(seconds=30)
        bot.reissue = types.SimpleNamespace(message=types.SimpleNamespace(created_at=later))
        assert bot.restart_time() == later
        bot.reissue = None
        assert bot.restart_time() == bot.startup

    async def test_on_command_error_replies_and_logs(self, bot, caplog):
        ctx = FakeChannel()
        ctx.command, ctx.author = 'cmd', 'someone'
        with caplog.at_level(logging.ERROR):
            await bot.on_command_error(ctx, commands.CommandError('nope'))
        assert ctx.sent == ['nope']
        assert 'Command "cmd" failed' in caplog.text

    async def test_on_command_error_survives_a_failed_reply(self, bot):
        class Mute(FakeChannel):
            async def send(self, *a, **k):
                raise discord.HTTPException(types.SimpleNamespace(status=403, reason='x'), 'no')
        ctx = Mute()
        ctx.command, ctx.author = 'cmd', 'someone'
        await bot.on_command_error(ctx, commands.CommandError('nope'))  # must not raise


class TestAnnouncements:
    """The log channel: where updates, rebases and rollbacks are reported."""

    @pytest.fixture
    def with_log(self, make_bot):
        bot = make_bot()
        bot.reissue_delay = 0
        log = FakeChannel(77, 'debugging')
        bot.find_channel = lambda ref: log if ref == 'debugging' else None
        return bot, log

    async def test_announce_posts_in_a_code_block_to_the_configured_log_channel(self, with_log):
        bot, log = with_log
        assert await bot.announce('hello') is True
        assert log.sent == ['```hello```']

    async def test_announce_uses_the_configured_channel_name(self, make_bot):
        bot = make_bot()
        other = FakeChannel(5, 'ops')
        bot.config['log_channel'] = 'ops'
        bot.find_channel = lambda ref: other if ref == 'ops' else None
        assert await bot.announce('x') is True and other.sent == ['```x```']

    async def test_announce_without_a_channel_logs_instead_of_raising(self, bot, caplog):
        bot.find_channel = lambda ref: None
        with caplog.at_level(logging.WARNING):
            assert await bot.announce('lost message') is False
        assert 'lost message' in caplog.text

    async def test_announce_survives_discord_errors(self, with_log):
        bot, log = with_log

        async def boom(*a, **k):
            raise discord.HTTPException(types.SimpleNamespace(status=403, reason='x'), 'no')
        log.send = boom
        assert await bot.announce('x') is False

    async def test_startup_notices_are_posted_once_on_first_ready(self, make_bot):
        log = FakeChannel(77)
        bot = make_bot(notices=['rebased 1 commit', 'rolled back'])
        bot.reissue_delay = 0
        bot.find_channel = lambda ref: log
        await bot.on_ready()
        await bot.on_ready()  # a gateway reconnect must not repeat them
        assert log.sent == ['```rebased 1 commit```', '```rolled back```']
        assert bot.notices == []

    def test_notices_argument_is_copied(self, make_bot):
        given = ['a']
        bot = make_bot(notices=given)
        bot.notices.append('b')
        assert given == ['a']
        assert make_bot().notices == []

    async def test_pull_code_stays_quiet_for_a_routine_update(self, with_log):
        bot, log = with_log
        from ..git_manage import UpdateResult
        bot.git = types.SimpleNamespace(update=lambda: UpdateResult('a' * 40, 'b' * 40, 'origin/main'))
        result = await bot.pull_code()
        assert result.moved and log.sent == []

    async def test_pull_code_announces_a_rebase_or_failure(self, with_log):
        bot, log = with_log
        from ..git_manage import UpdateResult
        bot.git = types.SimpleNamespace(update=lambda: UpdateResult(
            'a' * 40, 'b' * 40, 'origin/main', replayed=[('1234567', 'tweak')]))
        await bot.pull_code()
        assert 'rebased 1 local commit(s) onto origin/main' in log.sent[0]
        bot.git = types.SimpleNamespace(update=lambda: UpdateResult(
            'a' * 40, 'a' * 40, 'origin/main', error='conflict'))
        result = await bot.pull_code()
        assert not result.ok and 'Update failed: conflict' in log.sent[1]

    async def test_pull_code_does_not_repeat_itself_in_the_channel_it_replies_in(self, with_log):
        bot, log = with_log
        from ..git_manage import UpdateResult
        bot.git = types.SimpleNamespace(update=lambda: UpdateResult(
            'a' * 40, 'a' * 40, 'origin/main', error='conflict'))
        await bot.pull_code(channel=log)  # the caller replies in the log channel itself
        assert log.sent == []


def test_core_package_constant_is_the_package_the_core_was_imported_as():
    assert bot_module.CORE_PACKAGE == 'botcore'
