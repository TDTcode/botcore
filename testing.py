"""Helpers for testing a ``BotCore`` bot without Discord or a network.

Meant to be reused by the bots' own tests (``from .botcore import testing``)::

    bot = testing.make_bot(MainBot)
    testing.assert_least_privilege(bot, members=False)
"""

import types


def attach_fake_connection(bot, user_id=0):
    """Give an un-logged-in bot an empty guild cache and a fake ``bot.user``."""
    if not hasattr(bot, "_connection"):
        bot._connection = types.SimpleNamespace()
    bot._connection._guilds = {}
    bot._connection.user = types.SimpleNamespace(id=user_id)
    return bot


def make_bot(bot_cls, *args, user_id=0, **kwargs):
    """Build ``bot_cls`` (a ``BotCore`` subclass) with a fake connection."""
    return attach_fake_connection(bot_cls(*args, **kwargs), user_id=user_id)


def add_guild(bot, guild):
    """Put a fake guild (needs ``.id``) into the bot's guild cache."""
    bot._connection._guilds[guild.id] = guild
    return guild


def assert_least_privilege(bot, members=False, presences=False, message_content=True):
    """Assert the privileged-intent posture (the check wit_bot's privacy review relies on)."""
    intents = bot.intents
    assert intents.members is members, f"members intent should be {members}"
    assert intents.presences is presences, f"presences intent should be {presences}"
    assert intents.message_content is message_content, f"message_content intent should be {message_content}"
