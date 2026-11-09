"""Persistent storage shared by the bots.

On-disk formats are unchanged from the per-bot copies:

* ``DataContainer`` / ``UserConfig``: a json file (``indent=4``).
* ``PermaDict`` / ``IntPermaDict``: a ``shelve`` (dbm) file with ``str`` keys.

``DataContainer`` is wit_bot's version (the TDTbot copy that lived inside
``param.py`` is the same idea without ``__delitem__``/safe ``keys``).
Improvements that do not change formats: json is written atomically
(temp file + rename) and the user-config directory is injected instead of being
derived from ``__file__``.
"""
import json
import os
import shelve
from glob import glob


class DataContainer:
    """A dict-like object backed by a json file.

    Subclasses may implement ``_gen_data(key)`` (set ``self.data[key]``, or
    return for "nothing to generate") to create values on first access; the
    base class raises ``NotImplementedError``, which is treated as "cannot
    generate".  A missing file triggers ``_gen_data(None)`` (initial contents).
    """

    def __init__(self, fn):
        self.fn = os.fspath(fn)
        self.data = dict()
        self._file_data = self._load_own_data()
        if self._file_data is not None:
            for i in self._file_data:
                self.data[i] = self._file_data[i]

    def __getitem__(self, key):
        try:
            return self.data[key]
        except KeyError:
            pass
        try:
            out = self._file_data[key]
            self.data[key] = out
            return out
        except (KeyError, TypeError):
            pass
        # (The per-bot copies also tried "a method named like the key" here, but a
        # misplaced parenthesis meant it never worked, so it is not carried over.)
        try:
            self._gen_data(key)
            out = self.data[key]
            self._save()
            return out
        except (NotImplementedError, KeyError):
            pass
        raise KeyError('Could not find or generate "{0}".'.format(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self._save()

    def __delitem__(self, key):
        try:
            del self.data[key]
            self._save()
        except KeyError:
            pass

    def _load_own_data(self):
        try:
            with open(self.fn, 'r') as f:
                return json.load(f)
        except IOError:
            self._gen_data(None)
            self._save()

    def _save(self):
        tmp = self.fn + '.tmp'
        with open(tmp, 'w') as f:
            f.write(json.dumps(self.data, indent=4))
        os.replace(tmp, self.fn)
        self._file_data = self._load_own_data()

    def _gen_data(self, *args):
        raise NotImplementedError

    def keys(self):
        try:
            a = list(self.data.keys())
        except AttributeError:
            a = []
        try:
            b = list(self._file_data.keys())
        except AttributeError:
            b = []
        return set(a + b)

    def set_if_not_set(self, key, value):
        try:
            return self[key]
        except KeyError:
            self[key] = value
            return self[key]

    def __contains__(self, item):
        return item in self.keys()


class PermaDict:
    """A persistent dict (``shelve``) with ``str``-converted keys."""

    def __init__(self, fn):
        self.fn = os.fspath(fn)
        self.file = shelve.open(self.fn)

    def close(self):
        file = self.__dict__.pop('file', None)
        if file is not None:
            file.sync()
            file.close()

    def __del__(self):
        self.close()

    def __getitem__(self, key):
        return self.file[str(key)]

    def __setitem__(self, key, value):
        self.file[str(key)] = value

    def __delitem__(self, key):
        del self.file[str(key)]

    def get(self, key, default=None):
        return self.file.get(str(key), default)

    def __contains__(self, item):
        return str(item) in self.file

    def __len__(self):
        return len(self.file)

    def keys(self):
        return list(self.file.keys())

    def items(self):
        return self.file.items()

    def delete(self, key):
        del self.file[str(key)]

    def pop(self, key, *default):
        return self.file.pop(str(key), *default)


class IntPermaDict(PermaDict):
    """``PermaDict`` whose keys are integers (e.g. discord ids)."""

    def __setitem__(self, key, value):
        self.file[str(int(key))] = value

    def keys(self):
        return [int(k) for k in self.file.keys()]


class UserConfig(DataContainer):
    """Per-user json file: ``<users dir>/<discord id>.json``.

    ``discord_user`` is a discord user/member or a plain int id.  The directory
    comes from, in order: the ``directory`` argument, ``UserConfig.directory``
    (set once by ``BotCore`` at start-up from ``bot.paths.users``).
    """
    directory = None

    def __init__(self, discord_user, guild=None, directory=None):
        if isinstance(discord_user, int):
            user_id = discord_user
        else:
            user_id = discord_user.id
        directory = directory if directory is not None else type(self).directory
        if directory is None:
            raise RuntimeError('UserConfig has no directory: pass directory=... or let '
                               'BotCore set UserConfig.directory (bot.paths.users).')
        directory = os.fspath(directory)
        os.makedirs(directory, exist_ok=True)
        self.user = discord_user
        self.guild = guild
        super().__init__(os.path.join(directory, str(user_id) + '.json'))

    def _gen_data(self, *args):
        return


def get_all_user_config_files(directory=None):
    """Paths of every per-user json file in ``directory`` (default ``UserConfig.directory``)."""
    directory = directory if directory is not None else UserConfig.directory
    if directory is None:
        raise RuntimeError('No users directory configured.')
    return glob(os.path.join(os.fspath(directory), '*.json'))
