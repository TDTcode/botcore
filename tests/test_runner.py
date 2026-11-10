"""Runner tests use a throw-away bot package that is a real git checkout
(``<tmp>/work/fakebot``, cloned from ``<tmp>/origin``), like a deployed bot."""
import sys
import types
from pathlib import Path

import git
import pytest

from .. import runner as runner_module
from ..git_manage import GitManager
from ..runner import Runner, build_parser

PARAM = '''
from pathlib import Path
from {core}.config import Config
rc = Config({{'cmd_prefix': ['f$']}}, root=Path(__file__).parent, name='fake')
'''

BOT = '''
from {core}.bot import BotCore
from . import param

VERSION = {version}


class MainBot(BotCore):
    core_cogs = ()

    @classmethod
    def default_config(cls):
        return param.rc
'''


def configure_repo(repo):
    with repo.config_writer() as cw:
        cw.set_value('user', 'name', 'Tester')
        cw.set_value('user', 'email', 'tester@example.com')
        cw.set_value('commit', 'gpgsign', 'false')


def write_version(path, version, broken=False):
    text = BOT.format(core='botcore', version=version)
    if broken:
        text += '\ndef broken(:\n'
    (path / 'bot.py').write_text(text)


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    """Returns (origin_repo, package_name, work_path) with VERSION = 1 checked out."""
    name = 'fakebot_runner'
    origin_path = tmp_path / 'origin'
    origin = git.Repo.init(origin_path, initial_branch='main')
    configure_repo(origin)
    (origin_path / '__init__.py').write_text('')
    (origin_path / 'param.py').write_text(PARAM.format(core='botcore'))
    write_version(origin_path, 1)
    origin.index.add(['__init__.py', 'param.py', 'bot.py'])
    origin.index.commit('v1')
    work_parent = tmp_path / 'work'
    work_parent.mkdir()
    work = git.Repo.clone_from(str(origin_path), str(work_parent / name))
    configure_repo(work)
    monkeypatch.syspath_prepend(str(work_parent))
    yield origin, name, work_parent / name
    for mod in [m for m in sys.modules if m == name or m.startswith(name + '.')]:
        del sys.modules[mod]


def publish(origin, version, broken=False):
    write_version(Path(origin.working_tree_dir), version, broken)
    origin.index.add(['bot.py'])
    origin.index.commit('v{}{}'.format(version, ' (broken)' if broken else ''))


class TestParser:
    def test_flags(self):
        args = build_parser().parse_args(['-c', 'c.json', '-t', 'tok', '-v', '-l', 'x.log',
                                          '--no-tracemalloc'])
        assert (args.config, args.token, args.verbose, args.logfile, args.no_tracemalloc) == \
            ('c.json', 'tok', True, 'x.log', True)

    def test_defaults_and_old_roasts_flag_is_gone(self):
        args = build_parser().parse_args([])
        assert args.config is None and args.verbose is False and not hasattr(args, 'roasts')

    def test_bots_can_extend_the_parser(self):
        parser = build_parser(lambda p: p.add_argument('--extra', default='x'))
        assert parser.parse_args(['--extra', 'y']).extra == 'y'


class TestModules:
    def test_import_and_purge(self, deployed):
        _, name, _ = deployed
        r = Runner(name)
        param, bot_module = r.import_bot()
        assert bot_module.VERSION == 1 and param.rc('cmd_prefix') == ['f$']
        assert name + '.bot' in sys.modules
        r.purge_modules()
        assert not [m for m in sys.modules if m == name or m.startswith(name + '.')]

    def test_purge_only_touches_the_bot_package(self, deployed):
        _, name, _ = deployed
        r = Runner(name)
        r.import_bot()
        r.purge_modules()
        assert 'json' in sys.modules and 'botcore' in sys.modules


class TestUpdateAndReload:
    def test_picks_up_new_code(self, deployed):
        origin, name, work = deployed
        r = Runner(name)
        r.import_bot()
        publish(origin, 2)
        param, bot_module = r.update_and_reload(GitManager(work))
        assert bot_module.VERSION == 2

    def test_a_broken_update_is_rolled_back(self, deployed, caplog):
        origin, name, work = deployed
        r = Runner(name)
        r.import_bot()
        gm = GitManager(work)
        good = gm.head()
        publish(origin, 2, broken=True)
        param, bot_module = r.update_and_reload(gm)
        assert bot_module.VERSION == 1  # old code is running again
        assert gm.head() == good  # and the checkout is back where it was
        assert 'failed to import' in caplog.text and 'Rolled back' in caplog.text

    def test_a_failed_pull_still_reimports_current_code(self, deployed, tmp_path, caplog):
        origin, name, work = deployed
        r = Runner(name)
        r.import_bot()
        git.Repo(work).git.remote('set-url', 'origin', str(tmp_path / 'does-not-exist'))
        param, bot_module = r.update_and_reload(GitManager(work))
        assert bot_module.VERSION == 1 and 'Update failed' in caplog.text

    # --- what ends up in the log channel -------------------------------------------------
    def test_a_routine_update_leaves_no_notice(self, deployed):
        origin, name, work = deployed
        r = Runner(name)
        r.import_bot()
        publish(origin, 2)
        r.update_and_reload(GitManager(work))
        assert r.notices == []

    def test_a_failed_pull_leaves_a_notice(self, deployed, tmp_path):
        origin, name, work = deployed
        r = Runner(name)
        r.import_bot()
        git.Repo(work).git.remote('set-url', 'origin', str(tmp_path / 'does-not-exist'))
        r.update_and_reload(GitManager(work))
        assert len(r.notices) == 1 and r.notices[0].startswith('Update failed')

    def test_rebasing_local_commits_leaves_a_notice(self, deployed):
        origin, name, work = deployed
        r = Runner(name)
        r.import_bot()
        publish(origin, 2)
        wrepo = git.Repo(work)
        (work / 'local_note.txt').write_text('mine')
        wrepo.index.add(['local_note.txt'])
        wrepo.index.commit('my local tweak')
        param, bot_module = r.update_and_reload(GitManager(work))
        assert bot_module.VERSION == 2  # upstream change arrived...
        assert (work / 'local_note.txt').exists()  # ...and the local commit survived
        assert len(r.notices) == 1
        assert 'rebased 1 local commit(s) onto origin/main' in r.notices[0]
        assert 'my local tweak' in r.notices[0]

    def test_a_rollback_leaves_a_notice(self, deployed):
        origin, name, work = deployed
        r = Runner(name)
        r.import_bot()
        gm = GitManager(work)
        good = gm.head()
        publish(origin, 2, broken=True)
        bad = origin.head.commit.hexsha
        r.update_and_reload(gm)
        assert len(r.notices) == 1
        notice = r.notices[0]
        assert f'The update to {bad[:7]} failed to import' in notice
        assert 'SyntaxError' in notice and f'rolled back to {good[:7]}' in notice

    def test_the_next_bot_gets_the_notices_and_the_runner_forgets_them(self, deployed):
        origin, name, work = deployed
        r = Runner(name)
        param, bot_module = r.import_bot()
        r.notices.append('something to tell the log channel')
        bot = r.make_bot(bot_module, None, None)
        assert bot.notices == ['something to tell the log channel']
        assert r.notices == []

    def test_broken_code_with_no_update_to_blame_raises(self, deployed):
        origin, name, work = deployed
        r = Runner(name)
        write_version(work, 1, broken=True)  # local damage, nothing was pulled
        with pytest.raises(SyntaxError):
            r.update_and_reload(GitManager(work))


class TestRun:
    @pytest.fixture(autouse=True)
    def no_crash_loop_guard(self, monkeypatch):
        monkeypatch.setattr(runner_module, 'MIN_RUN_SECONDS', -1)  # every run "was long enough"

    def test_restart_hands_reissue_and_startup_to_the_next_bot(self, deployed):
        _, name, _ = deployed
        seen = []

        class Scripted(Runner):
            def serve(self, bot, token):
                seen.append((bot.reissue, bot.startup, token))
                if len(seen) == 1:
                    bot.reissue = 'the reboot ctx'  # what the reboot command does
                else:
                    raise KeyboardInterrupt

        r = Scripted(name, ['-t', 'sekrit', '--no-tracemalloc', '-l', str(Path(deployed[2]) / 'x.log')])
        r.update_and_reload = lambda git: r.import_bot()  # no real git update in this test
        with pytest.raises(KeyboardInterrupt):
            r.run()
        assert [s[2] for s in seen] == ['sekrit', 'sekrit']
        assert seen[0][0] is None
        assert seen[1][0] == 'the reboot ctx' and seen[1][1] is not None

    def test_missing_token_exits_2_without_starting_the_bot(self, deployed, monkeypatch):
        _, name, _ = deployed
        for var in ('FAKE_TOKEN', 'DISCORD_TOKEN'):
            monkeypatch.delenv(var, raising=False)

        class NeverServes(Runner):
            def serve(self, bot, token):
                raise AssertionError('must not start without a token')

        assert NeverServes(name, ['--no-tracemalloc']).run() == 2

    def test_missing_token_message_really_reaches_the_user(self, deployed, monkeypatch, capsys):
        """caplog hides this: discord.py puts a NullHandler on the 'discord' logger, so the
        message only reaches stderr if logging was initialised *before* the token check."""
        _, name, _ = deployed
        for var in ('FAKE_TOKEN', 'DISCORD_TOKEN'):
            monkeypatch.delenv(var, raising=False)
        assert Runner(name, ['--no-tracemalloc', '-l', str(deployed[2] / 'x.log')]).run() == 2
        err = capsys.readouterr().err
        assert 'No Discord token' in err and 'FAKE_TOKEN' in err and 'DISCORD_TOKEN' in err

    def test_connection_errors_are_survived(self, deployed, monkeypatch):
        _, name, _ = deployed
        from aiohttp.client_exceptions import ClientConnectorError
        slept = []
        monkeypatch.setattr(runner_module.time, 'sleep', slept.append)
        runs = []

        class Flaky(Runner):
            def serve(self, bot, token):
                runs.append(1)
                if len(runs) == 1:
                    raise ClientConnectorError(types.SimpleNamespace(), OSError('offline'))
                raise KeyboardInterrupt

        r = Flaky(name, ['-t', 'x', '--no-tracemalloc', '-l', str(deployed[2] / 'x.log')])
        r.update_and_reload = lambda git: r.import_bot()
        with pytest.raises(KeyboardInterrupt):
            r.run()
        assert len(runs) == 2 and slept == [60]

    def test_run_helper_exits_with_the_code(self, deployed, monkeypatch):
        _, name, _ = deployed
        for var in ('FAKE_TOKEN', 'DISCORD_TOKEN'):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(SystemExit) as e:
            runner_module.run(name, ['--no-tracemalloc'])
        assert e.value.code == 2
