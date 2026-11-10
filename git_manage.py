"""Self-update / git-log support.

The old per-bot ``git_manage.py`` derived "my repo" from its own ``__file__``.
Once this code lives in a shared ``botcore`` package that would point at the
core instead of the bot, so ``GitManager`` takes the repo root explicitly.

Update flow used by the runner::

    git = GitManager(bot_root)
    before = git.head()            # remember where we were
    result = git.update()          # fetch + rebase onto the upstream branch
    ...import the new code...
    git.rollback(before)           # only if that import failed

``update()`` fetches and then **rebases** the checkout onto its upstream branch
(equivalent to ``git pull --rebase``, but independent of the host's git config).
If the checkout carries local commits they are replayed on top of the new
upstream commits (their hashes change; ``UpdateResult.replayed`` lists them so
the bot can say so in its log channel).  A rebase that cannot be completed
(conflict, dirty working tree, no network) is aborted and reported in
``UpdateResult.error``; the checkout is left exactly as it was.

``rollback`` uses ``git reset --keep`` (never ``--hard``) so uncommitted local
edits are not destroyed, then re-syncs submodules so a bot's pinned ``botcore``
goes back to the old pin as well.
"""

import dataclasses
import datetime
import logging
import os

import git  # type: ignore
import humanize  # type: ignore
import pytz  # type: ignore

logger = logging.getLogger("discord." + __name__)

MAX_LISTED = 10  # most local commits listed in a notice


def _reason(error):
    """A one-line, length-limited description of a git error."""
    text = (getattr(error, "stderr", "") or str(error)).strip()
    return " ".join(text.split())[:300]


@dataclasses.dataclass
class UpdateResult:
    """What ``GitManager.update`` did."""

    before: str
    after: str
    upstream: str = None
    #: local commits that were replayed on top of upstream: ``[(old_sha7, subject), ...]``
    replayed: list = dataclasses.field(default_factory=list)
    error: str = None

    @property
    def moved(self):
        return self.before != self.after

    @property
    def ok(self):
        return self.error is None

    def summary(self):
        """One line for the person who ran the command."""
        if self.error:
            return f"Update failed: {self.error} (still on {self.after[:7]})."
        if not self.moved:
            return "Already up to date."
        out = f"Updated {self.before[:7]}..{self.after[:7]}"
        if self.replayed:
            out += f", replaying {len(self.replayed)} local commit(s) on top of {self.upstream}"
        return out + "."

    def notice(self):
        """Text for the log channel, or None if the update was routine.

        Only a rebase of local commits or a failure is worth announcing; a plain
        fast-forward is already covered by the reboot announcement.
        """
        if self.error:
            return f"Update failed: {self.error}\nStill on {self.after[:7]}; running the current code."
        if self.replayed:
            lines = [f"  {sha}  {subject}" for sha, subject in self.replayed[:MAX_LISTED]]
            if len(self.replayed) > MAX_LISTED:
                lines.append(f"  ... and {len(self.replayed) - MAX_LISTED} more")
            return (
                "Update rebased {} local commit(s) onto {} ({}..{}); their hashes have changed (old hashes below):\n{}"
            ).format(len(self.replayed), self.upstream, self.before[:7], self.after[:7], "\n".join(lines))
        return None


class GitManager:
    """Thin wrapper around one git checkout (the bot's own repo)."""

    def __init__(self, root):
        self.root = os.fspath(root)
        self._repo = None

    @property
    def repo(self):
        """The ``git.Repo`` for ``root`` (opened lazily so constructing is cheap/safe)."""
        if self._repo is None:
            self._repo = git.Repo(self.root)
        return self._repo

    def head(self):
        """Full sha of HEAD (the rollback point)."""
        return self.repo.head.commit.hexsha

    def has_submodules(self):
        return bool(self.repo.submodules)

    def update(self):
        """Fetch and rebase onto the upstream branch; returns an ``UpdateResult``.

        Git failures are reported in ``result.error`` (the checkout is left as it
        was), not raised.
        """
        repo = self.repo
        before = self.head()
        result = UpdateResult(before=before, after=before)
        try:
            upstream = repo.active_branch.tracking_branch()
        except TypeError:  # detached HEAD
            upstream = None
        if upstream is None:
            result.error = "no upstream branch to update from (detached HEAD or untracked branch)"
            return result
        result.upstream = upstream.name
        try:
            repo.remote(upstream.remote_name).fetch()
            if not list(repo.iter_commits("HEAD.." + upstream.name)):
                return result  # nothing new upstream
            local = list(repo.iter_commits(upstream.name + "..HEAD"))
            try:
                repo.git.rebase(upstream.name)
            except git.GitCommandError:
                self._abort_rebase()
                raise
        except git.GitCommandError as e:
            result.error = _reason(e)
            result.after = self.head()
            return result
        result.after = self.head()
        result.replayed = [
            (c.hexsha[:7], c.message.strip().splitlines()[0] if c.message.strip() else "") for c in reversed(local)
        ]
        try:
            self._sync_submodules()
        except git.GitCommandError as e:
            result.error = "submodule update failed: " + _reason(e)
        return result

    def _abort_rebase(self):
        git_dir = self.repo.git_dir
        if any(os.path.isdir(os.path.join(git_dir, d)) for d in ("rebase-merge", "rebase-apply")):
            try:
                self.repo.git.rebase("--abort")
            except git.GitCommandError:
                logger.exception("Could not abort the rebase in %s", self.root)

    def rollback(self, sha):
        """Return to ``sha`` (from ``head()``) without discarding local edits."""
        logger.warning("Rolling back %s to %s", self.root, sha[:7])
        self.repo.git.reset("--keep", sha)
        self._sync_submodules()

    def _sync_submodules(self):
        if self.has_submodules():
            self.repo.git.submodule("update", "--init", "--recursive")

    def log_items(self, look_back=None):
        """Formatted git log lines.  ``look_back`` is a datetime or a timedelta
        (default: 7 days)."""
        return log_items(self.repo, look_back)

    def last_updated(self):
        """Commit time of HEAD."""
        return last_updated(self.repo)


def log_items(repo, look_back=None):
    """Returns a list of formatted git log items for ``repo`` (a ``git.Repo``).

    ``look_back`` (datetime or timedelta) defaults to 7 days ago.
    """
    now = datetime.datetime.now(datetime.UTC)
    if look_back is None:
        look_back = datetime.timedelta(days=7)
    if isinstance(look_back, datetime.timedelta):
        look_back = now - abs(look_back)
    elif look_back.tzinfo is None:
        look_back = pytz.utc.localize(look_back)
    items = [i for i in repo.iter_commits() if i.committed_datetime > look_back]

    def dt(i):
        """Format timestamp to human readable string"""
        return humanize.naturaltime(now - i.committed_datetime)

    fmt = "{:}: {:} <{:}> [{:}]"
    return [fmt.format(dt(i), i.message.strip(), i.author.name, i.hexsha[:7]) for i in items]


def last_updated(repo):
    """Commit time of HEAD for ``repo`` (a ``git.Repo``)."""
    return repo.head.commit.committed_datetime
