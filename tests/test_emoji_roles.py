import types

import pytest

from .. import testing
from .conftest import BotForTests, FakeGuild


class FakeMember:
    def __init__(self, id=100, roles=(), top_role=None):
        self.id = id
        self.roles = list(roles)
        self.top_role = top_role
        self.added, self.removed, self.dms = [], [], []

    async def add_roles(self, r):
        self.added.append(r)
        self.roles.append(r)

    async def remove_roles(self, r):
        self.removed.append(r)
        if r in self.roles:
            self.roles.remove(r)

    async def send(self, text):
        self.dms.append(text)


def role(id, name='r'):
    return types.SimpleNamespace(id=id, name=name)


def payload(member, guild, emoji='🙂', message_id=None):
    return types.SimpleNamespace(message_id=message_id, guild_id=guild.id, emoji=emoji,
                                 member=member, user_id=member.id)


@pytest.fixture
def setup(bot):
    r1, r2 = role(1, 'one'), role(2, 'two')
    member = FakeMember(roles=[r2], top_role=r2)
    guild = FakeGuild(12, roles=[r1, r2], members=[member])
    testing.add_guild(bot, guild)
    return bot, member, guild, r1, r2


# --- ported from TDTbot/tests/test_bot.py ---------------------------------------------

async def test_adds_role_on_match(setup):
    bot, member, guild, r1, _ = setup
    result = await bot.emoji2role(payload(member, guild), {'🙂': r1.id})
    assert result is r1 and r1 in member.added


async def test_delete_removes_role_and_the_remove_list(setup):
    bot, member, guild, r1, r2 = setup
    member.roles.append(r1)
    result = await bot.emoji2role(payload(member, guild), {'🙂': r1.id},
                                  delete=True, remove=[r2.id])
    assert result is r1
    assert r1 in member.removed and r2 in member.removed and r1 not in member.roles


# --- behaviour kept / fixed -------------------------------------------------------------

async def test_ignores_the_bots_own_reactions(setup):
    bot, member, guild, r1, _ = setup
    bot._connection.user = types.SimpleNamespace(id=member.id)
    assert await bot.emoji2role(payload(member, guild), {'🙂': r1.id}) is None
    assert member.added == []


async def test_message_id_filter(setup):
    bot, member, guild, r1, _ = setup
    p = payload(member, guild, message_id=5)
    assert await bot.emoji2role(p, {'🙂': r1.id}, message_id=6) is None
    assert await bot.emoji2role(p, {'🙂': r1.id}, message_id=5) is r1


async def test_unmatched_emoji_does_nothing(setup):
    bot, member, guild, r1, _ = setup
    assert await bot.emoji2role(payload(member, guild, emoji='🙃'), {'🙂': r1.id}) is None
    assert member.added == []


async def test_remove_list_skips_the_role_being_added(setup):
    bot, member, guild, r1, r2 = setup
    await bot.emoji2role(payload(member, guild), {'🙂': r1.id}, remove=[r1.id, r2.id])
    assert member.removed == [r2]


async def test_guild_given_as_an_id_is_looked_up_by_id(setup):
    # regression: an int guild used to index the guild *list* with the id
    bot, member, guild, r1, _ = setup
    result = await bot.emoji2role(payload(member, guild), {'🙂': r1.id}, guild=guild.id)
    assert result is r1


@pytest.mark.parametrize(('guild_arg', 'payload_guild_id'), [
    (None, None),    # no guild given and the payload has none (e.g. a DM reaction)
    (None, 999),     # no guild given and the payload's guild is not one of the bot's
    (999, 12),       # an unknown guild id given explicitly
])
async def test_unknown_guild_raises_lookuperror(setup, guild_arg, payload_guild_id):
    bot, member, guild, r1, _ = setup
    p = payload(member, guild)
    p.guild_id = payload_guild_id
    with pytest.raises(LookupError):
        await bot.emoji2role(p, {'🙂': r1.id}, guild=guild_arg)


async def test_unresolvable_member_is_logged_and_skipped(setup, caplog):
    bot, member, guild, r1, _ = setup
    p = types.SimpleNamespace(message_id=None, guild_id=guild.id, emoji='🙂', member=None,
                              user_id=4242)

    async def nobody(uid, guild=None, fallback=False):
        return None
    bot.get_or_fetch_user = nobody
    with caplog.at_level('INFO'):
        assert await bot.emoji2role(p, {'🙂': r1.id}) is None  # used to crash on None
    assert 'Member is None object' in caplog.text


async def test_dm_accept_flow(setup):
    bot, member, guild, r1, _ = setup
    await bot.emoji2role(payload(member, guild), {'🙂': r1.id}, send_message='Do you accept?',
                         accept_string='I Accept!', target='somewhere')
    assert member.dms == ['Do you accept?']
    assert bot.emoji_roles.dm_targets[member.id] == ('i accept', 'somewhere')


async def test_min_role_blocks_adding_but_not_removing(setup):
    bot, member, guild, r1, r2 = setup

    class Rank:
        def __init__(self, n):
            self.n = n

        def __lt__(self, other):
            return self.n < other.n
    member.top_role = Rank(1)
    guild.roles.append(types.SimpleNamespace(id=9, name='boss'))
    import discord
    boss = types.SimpleNamespace(id=9, name='boss')
    # min_role must be a discord.Role or resolvable by name/id; patch the resolver for a Rank
    from .. import helpers
    orig = helpers.find_role
    helpers.find_role = lambda g, n: Rank(5) if n == 9 else orig(g, n)
    try:
        assert await bot.emoji2role(payload(member, guild), {'🙂': r1.id}, min_role=9) is None
        assert member.added == []
        member.roles.append(r1)
        assert await bot.emoji2role(payload(member, guild), {'🙂': r1.id}, min_role=9,
                                    delete=True) is r1
    finally:
        helpers.find_role = orig
    assert discord and boss  # (imports kept for readability of the scenario)


# --- enrolment and the raw reaction listeners ----------------------------------------------

def test_enroll_validates_arguments(bot):
    with pytest.raises(ValueError):
        bot.enroll_emoji_role()
    with pytest.raises(ValueError):
        bot.enroll_emoji_role('not a dict')
    bot.enroll_emoji_role({'🙂': 1}, message_id=5)
    assert bot.emoji_roles._rules == [(({'🙂': 1},), {'message_id': 5})]


async def test_listeners_do_nothing_unless_enabled(setup):
    bot, member, guild, r1, _ = setup
    bot.enroll_emoji_role({'🙂': r1.id})
    await bot.on_raw_reaction_add(payload(member, guild))
    assert member.added == []


async def test_listeners_apply_rules_when_enabled(make_bot, setup):
    class Enabled(BotForTests):
        emoji_roles_enabled = True
    _, member, guild, r1, r2 = setup
    bot = make_bot(Enabled)
    testing.add_guild(bot, guild)
    bot.enroll_emoji_role({'🙂': r1.id})
    await bot.on_raw_reaction_add(payload(member, guild))
    assert member.added == [r1]
    await bot.on_raw_reaction_remove(payload(member, guild))
    assert member.removed == [r1]


async def test_remove_kwargs_do_not_leak_between_rules(make_bot, setup):
    # regression: the old on_raw_reaction_remove accumulated kwargs across enrolments
    class Enabled(BotForTests):
        emoji_roles_enabled = True
    _, member, guild, r1, r2 = setup
    bot = make_bot(Enabled)
    testing.add_guild(bot, guild)
    bot.enroll_emoji_role({'🙂': r1.id}, message_id=111)  # other message
    bot.enroll_emoji_role({'🙂': r2.id})
    member.roles = [r1, r2]
    await bot.on_raw_reaction_remove(payload(member, guild, message_id=222))
    assert member.removed == [r2]  # the first rule's message_id=111 did not leak into the second
