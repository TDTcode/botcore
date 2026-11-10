import datetime
import types

import pytest
import pytz

from .. import helpers


def chan(id, name):
    return types.SimpleNamespace(id=id, name=name)


GUILD = types.SimpleNamespace(
    channels=[chan(10, 'General'), chan(20, 'debugging')],
    roles=[types.SimpleNamespace(id=1, name='Admin'), types.SimpleNamespace(id=2, name='Mod')])


class Struct:
    """Like the bots' ``param._Struct`` (supports ``in`` and ``[]``)."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __contains__(self, item):
        return item in self.__dict__

    def __getitem__(self, item):
        return self.__dict__[item]


class TestFindChannel:
    def test_by_id_and_digit_string(self):
        assert helpers.find_channel(GUILD, 10).name == 'General'
        assert helpers.find_channel(GUILD, '20').name == 'debugging'
        assert helpers.find_channel(GUILD, 99) is None

    def test_by_name_is_case_and_space_insensitive(self):
        assert helpers.find_channel(GUILD, ' general ').id == 10

    def test_hash_and_mention_forms(self):
        assert helpers.find_channel(GUILD, '#debugging').id == 20
        assert helpers.find_channel(GUILD, '<#10>').id == 10

    def test_object_passes_through(self):
        c = chan(5, 'x')
        assert helpers.find_channel(GUILD, c) is c

    def test_default_used_when_channel_none(self):
        assert helpers.find_channel(GUILD, default='general').id == 10
        assert helpers.find_channel(GUILD) is None

    def test_aliases(self):
        aliases = Struct(logs=20, nice='general')
        assert helpers.find_channel(GUILD, 'logs', aliases=aliases).id == 20
        assert helpers.find_channel(GUILD, 'nice', aliases=aliases).id == 10
        assert helpers.find_channel(GUILD, 'nope', aliases=aliases) is None
        assert helpers.find_channel(GUILD, 'logs') is None  # no aliases given


def test_find_role():
    assert helpers.find_role(GUILD, 1).name == 'Admin'
    assert helpers.find_role(GUILD, 'mod').id == 2
    assert helpers.find_role(GUILD, 'nobody') is None


def test_valid_url():
    assert helpers.valid_url('https://example.com/a?b=1')
    assert helpers.valid_url('http://localhost:8080')
    assert not helpers.valid_url('example.com')
    assert not helpers.valid_url('not a url')


def test_parse_filetype():
    assert helpers.parse_filetype('cat.PNG') == []  # extension match is case-sensitive
    assert helpers.parse_filetype('cat.png') == ['png', 'image/png']
    assert helpers.parse_filetype('clip.mp4') == ['mp4', 'video/mp4']
    assert helpers.parse_filetype('weird.xyz') == []
    assert helpers.parse_filetype('weird.xyz', force_list=False) is None


def test_parse_message():
    att = types.SimpleNamespace(filename='pic.png')
    msg = types.SimpleNamespace(content='look https://example.com now', attachments=[att])
    out = helpers.parse_message(msg)
    assert out['urls'] == ['https://example.com']
    assert out['attachments'] == [[att, 'png', 'image/png']]
    assert out['type'] == ['image/png', 'url']
    plain = helpers.parse_message(types.SimpleNamespace(content='hi', attachments=[]))
    assert plain['type'] == ['normal']


def test_emotes_equal():
    emoji = types.SimpleNamespace(id=123, name='party', __str__=lambda self: '<:party:123>')
    assert helpers.emotes_equal('🙂', '🙂')
    assert not helpers.emotes_equal('🙂', '🙃')
    assert helpers.emotes_equal(emoji, 123)
    assert helpers.emotes_equal('party', emoji)


def test_int_time_round_trip():
    t0 = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    t = datetime.datetime(2000, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    secs = helpers.int_time(t, t0)
    assert secs == 86400 + 3 * 3600 + 4 * 60 + 5
    back = helpers.seconds_to_datetime(secs, t0=datetime.datetime(2000, 1, 1))
    assert back == t
    unix0 = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    assert helpers.int_time(t, unix0) == int(t.timestamp())


def test_naive_datetimes_are_treated_as_system_local_time():
    """Documents an inherited quirk (kept for compatibility, see BOTCORE_MIGRATION_NOTES.md):
    ``localize`` uses ``astimezone`` semantics for naive input, so ``int_time(x, 'unix')``
    and ``int_time(x, 0)`` (which build a *naive-UTC* t0) are only right on a UTC host."""
    naive = datetime.datetime(2020, 5, 1, 12)
    assert helpers.localize(naive) == naive.astimezone(pytz.utc)


def test_int_time_default_is_roughly_now():
    # naive-UTC "now" vs the epoch, both interpreted the same way
    assert abs(helpers.int_time() - helpers.int_time(helpers.utcnow())) <= 1


def test_utcnow_is_naive_and_matches_utc():
    now = helpers.utcnow()
    assert now.tzinfo is None
    real = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    assert abs((real - now).total_seconds()) < 5


def test_parse_timezone():
    assert helpers.parse_timezone('US/Pacific').zone == 'US/Pacific'
    assert helpers.parse_timezone('PST').zone == 'US/Pacific'
    assert helpers.parse_timezone('cest').zone == 'Europe/Berlin'
    assert helpers.parse_timezone('America/Los Angeles').zone == 'America/Los_Angeles'
    with pytest.raises(pytz.exceptions.UnknownTimeZoneError):
        helpers.parse_timezone('Not/AZone')


def test_localize_and_delocalize():
    aware = datetime.datetime(2020, 5, 1, 12, tzinfo=datetime.UTC)
    assert helpers.localize(aware) is aware
    assert helpers.delocalize(aware) == datetime.datetime(2020, 5, 1, 12)
    naive = datetime.datetime(2020, 5, 1, 12)
    assert helpers.delocalize(naive) is naive
    assert helpers.localize(naive).tzinfo is not None


def test_clean_string():
    assert helpers.clean_string('  Hello, World!! ') == 'hello world'
    assert helpers.clean_string('snake_case-ok') == 'snake_caseok'
