import json

import pytest

from ..config import Config, Paths, default_prefixes


def test_default_prefixes():
    assert default_prefixes('wit') == ['WIT$', 'Wit$', 'wit$']
    assert default_prefixes('TDT') == ['TDT$', 'Tdt$', 'tdt$']


class TestLayers:
    def test_core_defaults_derive_from_name_and_root(self, tmp_path):
        c = Config(root=tmp_path, name='wit')
        assert c('cmd_prefix') == ['WIT$', 'Wit$', 'wit$']
        assert c('config_file') == str(tmp_path / 'config' / 'wit.json')
        assert c('logfile') == str(tmp_path / 'logs' / 'wit.log')
        assert c('token_file') == str(tmp_path / 'config' / 'token.txt')
        assert c('bot_key') == 'wit'
        assert c('admin_role_ids') == []

    def test_bot_defaults_override_core_defaults(self, tmp_path):
        c = Config({'cmd_prefix': ['x$'], 'bot_key': 'wit_bot', 'extra': 1},
                   root=tmp_path, name='wit')
        assert c('cmd_prefix') == ['x$']
        assert c('bot_key') == 'wit_bot'
        assert c('extra') == 1

    def test_json_file_overrides_defaults(self, tmp_path):
        (tmp_path / 'config').mkdir()
        (tmp_path / 'config' / 'wit.json').write_text(json.dumps({'timezone': 'UTC', 'new': 5}))
        c = Config({'timezone': 'US/Pacific'}, root=tmp_path, name='wit')
        assert c('timezone') == 'UTC' and c['new'] == 5

    def test_missing_json_is_fine_invalid_json_raises(self, tmp_path):
        Config(root=tmp_path, name='wit')  # no file: no error
        (tmp_path / 'config').mkdir()
        (tmp_path / 'config' / 'wit.json').write_text('{not json')
        with pytest.raises(ValueError):
            Config(root=tmp_path, name='wit')

    def test_call_and_dget_defaults(self, tmp_path):
        c = Config({'a': 1}, root=tmp_path, name='x')
        assert c('a') == 1
        assert c('missing') is None
        assert c('missing', 'fallback') == 'fallback'
        c['a'] = 2
        assert c.dget('a') == 2
        del c['a']
        assert c.dget('a') == 1  # falls back to the defaults

    def test_requires_root(self):
        with pytest.raises(ValueError):
            Config(name='x')

    def test_name_defaults_to_root_dirname(self, tmp_path):
        assert Config(root=tmp_path / 'MyBot').name == 'mybot'


class TestToken:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for var in ('TEST_TOKEN', 'DISCORD_TOKEN'):
            monkeypatch.delenv(var, raising=False)

    def test_cli_file(self, config, tmp_path):
        f = tmp_path / 'tok.txt'
        f.write_text('# comment\nabc123  # trailing\n\n')
        assert config.read_token(str(f)) == 'abc123' and config['token'] == 'abc123'

    def test_cli_literal_when_not_a_file(self, config):
        assert config.read_token('literal-token') == 'literal-token'

    def test_malformed_token_file(self, config, tmp_path):
        f = tmp_path / 'tok.txt'
        f.write_text('one\ntwo\n')
        with pytest.raises(ValueError, match='not properly formatted'):
            config.read_token(str(f))

    def test_env_beats_token_file_and_specific_beats_generic(self, config, monkeypatch):
        (config.paths.config).mkdir()
        (config.paths.config / 'token.txt').write_text('from-file')
        assert config.read_token() == 'from-file'
        monkeypatch.setenv('DISCORD_TOKEN', 'generic')
        assert config.read_token() == 'generic'
        monkeypatch.setenv('TEST_TOKEN', 'specific')
        assert config.read_token() == 'specific'

    def test_cli_beats_env(self, config, monkeypatch):
        monkeypatch.setenv('TEST_TOKEN', 'env')
        assert config.read_token('cli') == 'cli'

    def test_nothing_configured_is_none_not_a_path(self, config):
        assert config.read_token() is None
        assert config['token'] is None

    def test_env_names(self, config):
        assert config.token_env_names == ['TEST_TOKEN', 'DISCORD_TOKEN']


class TestPaths:
    def test_default_layout_is_inside_the_checkout(self, tmp_path, monkeypatch):
        monkeypatch.delenv('BOT_DATA_DIR', raising=False)
        p = Paths(tmp_path)
        assert p.root == tmp_path.resolve()
        assert p.config == p.root / 'config'
        assert p.data == p.root / 'config'
        assert p.users == p.root / 'config' / 'users'
        assert p.logs == p.root / 'logs'
        assert p.overridden is False

    def test_data_dir_argument_moves_mutable_state(self, tmp_path):
        p = Paths(tmp_path / 'bot', data_dir=tmp_path / 'state')
        assert p.config == (tmp_path / 'bot').resolve() / 'config'  # static config stays
        assert p.data == (tmp_path / 'state').resolve()
        assert p.users == p.data / 'users' and p.logs == p.data / 'logs'
        assert p.overridden is True

    def test_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv('BOT_DATA_DIR', str(tmp_path / 'envstate'))
        assert Paths(tmp_path).data == (tmp_path / 'envstate').resolve()

    def test_ensure_creates_directories(self, tmp_path):
        p = Paths(tmp_path / 'bot', data_dir=tmp_path / 'state').ensure()
        assert p.users.is_dir() and p.logs.is_dir()


def test_public_items_hide_secrets(config):
    config['token'] = 'sekrit'
    config['api_secret'] = 'x'
    config['roasts'] = ['r']
    config['visible'] = 1
    items = config.public_items()
    assert 'visible' in items and 'cmd_prefix' in items
    assert not {'token', 'api_secret', 'roasts'} & set(items)
