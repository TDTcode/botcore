import datetime
from pathlib import Path

import git
import pytest

from ..git_manage import GitManager, UpdateResult, last_updated, log_items


def _configure(repo):
    with repo.config_writer() as cw:
        cw.set_value('user', 'name', 'Tester')
        cw.set_value('user', 'email', 'tester@example.com')
        cw.set_value('commit', 'gpgsign', 'false')


def commit(repo, name, text, message):
    path = Path(repo.working_tree_dir) / name
    path.write_text(text)
    repo.index.add([name])
    return repo.index.commit(message)


@pytest.fixture
def repos(tmp_path):
    """(origin, work): ``work`` is a clone of ``origin``, one commit each."""
    origin = git.Repo.init(tmp_path / 'origin', initial_branch='main')
    _configure(origin)
    commit(origin, 'a.txt', '1', 'first commit')
    work = git.Repo.clone_from(str(tmp_path / 'origin'), str(tmp_path / 'work'))
    _configure(work)
    return origin, work


def test_head_and_last_updated(repos):
    _, work = repos
    gm = GitManager(work.working_tree_dir)
    assert gm.head() == work.head.commit.hexsha and len(gm.head()) == 40
    assert gm.last_updated() == last_updated(work)
    assert gm.has_submodules() is False


def test_log_items_format_and_look_back(repos):
    _, work = repos
    gm = GitManager(work.working_tree_dir)
    items = gm.log_items()
    assert len(items) == 1
    assert 'first commit <Tester> [' + gm.head()[:7] + ']' in items[0]
    # timedelta and (naive and aware) datetime look-backs
    assert log_items(work, datetime.timedelta(days=1))
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
    assert log_items(work, future) == []
    assert log_items(work, future.replace(tzinfo=None)) == []


class TestUpdate:
    def test_up_to_date(self, repos):
        _, work = repos
        result = GitManager(work.working_tree_dir).update()
        assert result.ok and not result.moved and result.replayed == []
        assert result.summary() == 'Already up to date.'
        assert result.notice() is None

    def test_fast_forward_is_routine(self, repos):
        origin, work = repos
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        commit(origin, 'a.txt', '2', 'second commit')
        result = gm.update()
        assert result.ok and result.moved and result.replayed == []
        assert (result.before, result.after) == (before, origin.head.commit.hexsha)
        assert gm.head() == origin.head.commit.hexsha
        assert result.summary() == f'Updated {before[:7]}..{gm.head()[:7]}.'
        assert result.notice() is None  # nothing worth telling the log channel

    def test_local_commits_are_rebased_and_reported(self, repos):
        origin, work = repos
        commit(origin, 'a.txt', 'origin change', 'upstream change')
        local = commit(work, 'b.txt', 'local only', 'my local tweak')
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        result = gm.update()
        assert result.ok and result.moved
        assert result.replayed == [(local.hexsha[:7], 'my local tweak')]
        # linear history: upstream commit, then the replayed local commit on top
        assert [c.message.strip() for c in work.iter_commits()][:3] == \
            ['my local tweak', 'upstream change', 'first commit']
        assert gm.head() != before and not work.head.commit.parents[0].hexsha == before
        assert (Path(work.working_tree_dir) / 'b.txt').read_text() == 'local only'
        assert (Path(work.working_tree_dir) / 'a.txt').read_text() == 'origin change'
        # ...and it says so
        notice = result.notice()
        assert 'rebased 1 local commit(s) onto origin/main' in notice
        assert local.hexsha[:7] + '  my local tweak' in notice
        assert 'replaying 1 local commit(s)' in result.summary()

    def test_many_local_commits_are_listed_in_order_and_truncated(self, repos):
        origin, work = repos
        commit(origin, 'a.txt', 'origin change', 'upstream change')
        for i in range(12):
            commit(work, f'f{i}.txt', str(i), f'local {i}')
        result = GitManager(work.working_tree_dir).update()
        assert [s for _, s in result.replayed] == [f'local {i}' for i in range(12)]
        notice = result.notice()
        assert 'rebased 12 local commit(s)' in notice and '... and 2 more' in notice
        assert 'local 0' in notice and 'local 9' in notice and 'local 10' not in notice

    def test_a_conflicting_rebase_is_aborted_and_reported(self, repos):
        origin, work = repos
        commit(origin, 'a.txt', 'upstream version', 'upstream edits a.txt')
        commit(work, 'a.txt', 'local version', 'local edits a.txt')
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        result = gm.update()
        assert not result.ok and not result.moved
        assert gm.head() == before  # exactly where it was
        assert not (Path(work.git_dir) / 'rebase-merge').exists()  # no half-finished rebase
        assert not (Path(work.git_dir) / 'rebase-apply').exists()
        assert (Path(work.working_tree_dir) / 'a.txt').read_text() == 'local version'
        assert 'conflict' in result.error.lower()
        assert 'Update failed' in result.notice() and before[:7] in result.notice()
        assert result.summary().startswith('Update failed')

    def test_a_dirty_working_tree_is_reported_not_clobbered(self, repos):
        origin, work = repos
        commit(origin, 'a.txt', '2', 'second commit')
        path = Path(work.working_tree_dir) / 'a.txt'
        path.write_text('uncommitted edit')
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        result = gm.update()
        assert not result.ok and gm.head() == before
        assert 'unstaged changes' in result.error or 'uncommitted' in result.error
        assert path.read_text() == 'uncommitted edit'

    @pytest.mark.parametrize('setting', ['true', 'false'])
    def test_host_git_config_does_not_change_the_result(self, repos, setting):
        """Explicit fetch + rebase: pull.rebase / pull.ff on the host are irrelevant."""
        origin, work = repos
        with work.config_writer() as cw:
            cw.set_value('pull', 'rebase', setting)
            cw.set_value('pull', 'ff', 'only')
        commit(origin, 'a.txt', 'origin change', 'upstream change')
        commit(work, 'b.txt', 'local only', 'my local tweak')
        result = GitManager(work.working_tree_dir).update()
        assert result.ok and len(result.replayed) == 1
        assert not [c for c in work.iter_commits() if len(c.parents) > 1]  # no merge commits

    def test_unreachable_remote_is_reported(self, repos, tmp_path):
        _, work = repos
        work.git.remote('set-url', 'origin', str(tmp_path / 'does-not-exist'))
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        result = gm.update()
        assert not result.ok and result.after == before and result.error
        assert 'Update failed' in result.notice()

    def test_no_upstream_branch(self, repos):
        _, work = repos
        work.git.checkout('--detach')
        result = GitManager(work.working_tree_dir).update()
        assert not result.ok and 'no upstream' in result.error
        work.git.checkout('main')
        work.git.branch('--unset-upstream')
        result = GitManager(work.working_tree_dir).update()
        assert not result.ok and 'no upstream' in result.error


class TestRollback:
    def test_restores_head_and_keeps_untracked_files(self, repos):
        origin, work = repos
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        commit(origin, 'a.txt', '2', 'second commit')
        gm.update()
        (Path(work.working_tree_dir) / 'scratch.txt').write_text('mine')
        gm.rollback(before)
        assert gm.head() == before
        assert (Path(work.working_tree_dir) / 'a.txt').read_text() == '1'
        assert (Path(work.working_tree_dir) / 'scratch.txt').read_text() == 'mine'

    def test_rollback_after_a_rebase_restores_the_original_local_commits(self, repos):
        origin, work = repos
        commit(origin, 'a.txt', 'origin change', 'upstream change')
        local = commit(work, 'b.txt', 'local only', 'my local tweak')
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        assert before == local.hexsha
        gm.update()
        assert gm.head() != before  # rebased: new hash
        gm.rollback(before)
        assert gm.head() == before  # the pre-update local commit, byte for byte

    def test_does_not_clobber_uncommitted_edits_to_tracked_files(self, repos):
        origin, work = repos
        gm = GitManager(work.working_tree_dir)
        before = gm.head()
        commit(origin, 'b.txt', 'new', 'adds b')
        gm.update()
        path = Path(work.working_tree_dir) / 'a.txt'
        path.write_text('uncommitted work')
        gm.rollback(before)  # a.txt is the same in both commits, so --keep preserves the edit
        assert path.read_text() == 'uncommitted work'
        assert gm.head() == before


def test_repo_is_opened_lazily(tmp_path):
    gm = GitManager(tmp_path)  # not a repo: constructing must not fail
    with pytest.raises(git.InvalidGitRepositoryError):
        gm.head()


class TestUpdateResult:
    def test_messages_for_each_shape(self):
        a, b = 'a' * 40, 'b' * 40
        assert UpdateResult(a, a).summary() == 'Already up to date.'
        assert UpdateResult(a, a).notice() is None
        assert UpdateResult(a, b, 'origin/x').summary() == 'Updated aaaaaaa..bbbbbbb.'
        failed = UpdateResult(a, a, error='boom')
        assert not failed.ok and failed.notice().startswith('Update failed: boom')
        assert failed.summary() == 'Update failed: boom (still on aaaaaaa).'
