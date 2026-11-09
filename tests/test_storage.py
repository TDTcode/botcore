import json
import types

import pytest

from ..storage import (DataContainer, IntPermaDict, PermaDict, UserConfig,
                       get_all_user_config_files)


class Container(DataContainer):
    def _gen_data(self, *args):
        return


class Generating(DataContainer):
    def _gen_data(self, key):
        if key is None:
            self.data['seeded'] = 1
        elif key == 'made':
            self.data['made'] = 'generated'
        else:
            raise NotImplementedError


class TestDataContainer:
    def test_set_get_persist_and_reload(self, tmp_path):
        fn = tmp_path / 'c.json'
        c = Container(fn)
        c['a'] = 1
        c['b'] = [1, 2]
        assert json.loads(fn.read_text()) == {'a': 1, 'b': [1, 2]}
        again = Container(fn)
        assert again['a'] == 1 and again['b'] == [1, 2]

    def test_file_format_is_indent_4_json(self, tmp_path):
        fn = tmp_path / 'c.json'
        Container(fn)['k'] = 'v'
        assert fn.read_text() == '{\n    "k": "v"\n}'

    def test_write_is_atomic_and_leaves_no_temp_file(self, tmp_path):
        fn = tmp_path / 'c.json'
        c = Container(fn)
        c['a'] = 1
        assert sorted(p.name for p in tmp_path.iterdir()) == ['c.json']

    def test_missing_key_raises_keyerror(self, tmp_path):
        with pytest.raises(KeyError):
            Container(tmp_path / 'c.json')['nope']

    def test_delete_and_contains_and_keys(self, tmp_path):
        c = Container(tmp_path / 'c.json')
        c['a'] = 1
        c['b'] = 2
        assert 'a' in c and c.keys() == {'a', 'b'}
        del c['a']
        del c['missing']  # silently ignored
        assert 'a' not in c
        assert json.loads((tmp_path / 'c.json').read_text()) == {'b': 2}

    def test_set_if_not_set(self, tmp_path):
        c = Container(tmp_path / 'c.json')
        assert c.set_if_not_set('x', 5) == 5
        assert c.set_if_not_set('x', 9) == 5

    def test_gen_data_seeds_new_file_and_generates_on_demand(self, tmp_path):
        c = Generating(tmp_path / 'g.json')
        assert c['seeded'] == 1
        assert c['made'] == 'generated'
        assert json.loads((tmp_path / 'g.json').read_text())['made'] == 'generated'
        with pytest.raises(KeyError):
            c['other']

    def test_base_class_cannot_create_a_new_file(self, tmp_path):
        with pytest.raises(NotImplementedError):
            DataContainer(tmp_path / 'new.json')


class TestPermaDict:
    def test_round_trip_and_str_keys(self, tmp_path):
        d = PermaDict(tmp_path / 'p')
        d[1] = 'one'
        assert d['1'] == 'one' and 1 in d and '1' in d
        assert d.get(2) is None and d.get(2, 'x') == 'x'
        assert d.keys() == ['1'] and len(d) == 1
        assert d.pop(1) == 'one' and 1 not in d
        d['k'] = 1
        del d['k']
        assert 'k' not in d
        d.close()
        d.close()  # idempotent

    def test_persists_between_instances(self, tmp_path):
        d = PermaDict(tmp_path / 'p')
        d['a'] = [1, 2, 3]
        d.close()
        assert PermaDict(tmp_path / 'p')['a'] == [1, 2, 3]

    def test_int_keys(self, tmp_path):
        d = IntPermaDict(tmp_path / 'i')
        d[10] = 'a'
        d['20'] = 'b'
        assert sorted(d.keys()) == [10, 20]
        d.close()


class TestUserConfig:
    def test_id_or_user_object_and_directory_argument(self, tmp_path):
        a = UserConfig(5, directory=tmp_path)
        a['x'] = 1
        b = UserConfig(types.SimpleNamespace(id=5), directory=tmp_path)
        assert b['x'] == 1
        assert (tmp_path / '5.json').exists()

    def test_class_default_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(UserConfig, 'directory', tmp_path / 'users')
        UserConfig(7)['k'] = 'v'
        assert (tmp_path / 'users' / '7.json').exists()  # directory is created on demand

    def test_no_directory_is_a_clear_error(self, monkeypatch):
        monkeypatch.setattr(UserConfig, 'directory', None)
        with pytest.raises(RuntimeError, match='directory'):
            UserConfig(1)

    def test_keeps_user_and_guild(self, tmp_path):
        user, guild = types.SimpleNamespace(id=1), object()
        uc = UserConfig(user, guild=guild, directory=tmp_path)
        assert uc.user is user and uc.guild is guild

    def test_get_all_user_config_files(self, tmp_path):
        UserConfig(1, directory=tmp_path)['a'] = 1
        UserConfig(2, directory=tmp_path)['a'] = 1
        (tmp_path / 'notes.txt').write_text('x')
        names = sorted(p.rsplit('/', 1)[-1] for p in get_all_user_config_files(tmp_path))
        assert names == ['1.json', '2.json']
