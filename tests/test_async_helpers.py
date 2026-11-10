import datetime
import time
import types

import pytest

from .. import async_helpers, helpers
from .conftest import FakeChannel, FakeGuild


class TestSplitSend:
    async def test_short_message_single_send(self):
        ch = FakeChannel()
        out = await async_helpers.split_send(ch, 'a\nb\nc')
        assert ch.sent == ['a\nb\nc']
        assert len(out) == 1

    async def test_splits_below_limit_and_wraps_style(self):
        ch = FakeChannel()
        await async_helpers.split_send(ch, 'aaaa\nbbbb\ncccc', n=14, style='`')
        assert ch.sent == ['`aaaa\nbbbb`', '`cccc`']
        assert all(len(s) < 14 for s in ch.sent)

    async def test_list_input_is_not_consumed(self):
        ch = FakeChannel()
        items = ['x', 'y']
        await async_helpers.split_send(ch, items)
        assert items == ['x', 'y']
        assert ch.sent == ['x\ny']

    async def test_empty_message_sends_nothing(self):
        ch = FakeChannel()
        assert await async_helpers.split_send(ch, '') is None
        assert ch.sent == []

    async def test_sopt_goes_to_final_send(self):
        ch = FakeChannel()
        out = await async_helpers.split_send(ch, 'a', sopt={'silent': True})
        assert out[0].kwargs == {'silent': True}


class FakeBotWithAdmin:
    def __init__(self, result):
        self.result = result
        self.seen = None

    async def is_admin(self, member, guild=None):
        self.seen = (member, guild)
        return self.result


class FakeBotPlain:
    def __init__(self, owner=False):
        self.owner = owner

    async def is_owner(self, member):
        return self.owner


class TestAdminCheck:
    async def test_delegates_to_bot_is_admin(self):
        bot = FakeBotWithAdmin(True)
        guild = FakeGuild()
        ctx = types.SimpleNamespace(author='me', guild=guild, bot=bot)
        assert await async_helpers.admin_check(ctx) is True
        assert bot.seen == ('me', guild)

    async def test_author_only_without_ctx(self):
        # used to raise AttributeError on ctx.guild
        bot = FakeBotWithAdmin(False)
        assert await async_helpers.admin_check(bot=bot, author='me') is False

    async def test_requires_ctx_or_author(self):
        with pytest.raises(ValueError):
            await async_helpers.admin_check()

    async def test_fallback_guild_owner(self):
        me = types.SimpleNamespace(id=1)
        ctx = types.SimpleNamespace(author=me, guild=FakeGuild(owner=me), bot=FakeBotPlain())
        assert await async_helpers.admin_check(ctx) is True

    async def test_fallback_bot_owner_and_denied(self):
        me = types.SimpleNamespace(id=1)
        other = types.SimpleNamespace(id=2)
        guild = FakeGuild(owner=other)
        assert await async_helpers.admin_check(
            types.SimpleNamespace(author=me, guild=guild, bot=FakeBotPlain(owner=True)))
        assert not await async_helpers.admin_check(
            types.SimpleNamespace(author=me, guild=guild, bot=FakeBotPlain(owner=False)))


class FakePayloadBot:
    def __init__(self):
        self.guild = FakeGuild(id=7)
        self.guilds = [self.guild]
        self.fetched = []

    def find_channel(self, cid):
        return None  # force the fetch_channel path

    async def fetch_channel(self, cid):
        self.fetched.append(cid)
        return FakeFetchableChannel()

    async def get_or_fetch_user(self, uid, guild):
        return types.SimpleNamespace(id=uid, guild=guild)


class FakeFetchableChannel:
    async def fetch_message(self, mid):
        return types.SimpleNamespace(id=mid)


class TestParsePayload:
    payload = types.SimpleNamespace(guild_id=7, user_id=3, member=None, channel_id=5, message_id=9)

    async def test_default_guild_and_member(self):
        out = await async_helpers.parse_payload(self.payload, FakePayloadBot())
        assert set(out) == {'guild', 'member'}
        assert out['member'].id == 3 and out['member'].guild.id == 7

    async def test_message_pulls_in_channel(self):
        # regression: a typo ('messsage') meant this raised KeyError('channel')
        bot = FakePayloadBot()
        out = await async_helpers.parse_payload(self.payload, bot, 'message')
        assert out['message'].id == 9
        assert 'channel' in out
        assert bot.fetched == [5]


async def test_wait_until_returns_for_a_near_future_naive_utc_time():
    start = time.monotonic()
    await async_helpers.wait_until(helpers.utcnow() + datetime.timedelta(seconds=0.05))
    assert 0.0 <= time.monotonic() - start < 2


async def test_git_log_sends_items_in_code_block():
    ch = FakeChannel()
    git = types.SimpleNamespace(log_items=lambda: ['one', 'two'])
    await async_helpers.git_log(ch, git)
    assert ch.sent == ['```one\ntwo```']
