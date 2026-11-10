"""Async helpers shared by the bots.

Superset of the TDTbot/wit_bot copies (wit's ``sopt`` kwarg on ``split_send``).
Differences from those copies:

* ``admin_check`` no longer hard-codes a guild's role ids.  It delegates to
  ``bot.is_admin`` (see ``BotCore.is_admin``, driven by the ``admin_role_ids``
  config key) when a ``BotCore`` is available.
* ``parse_payload`` fixes the ``'messsage'`` typo, so asking for ``message``
  now pulls in ``channel`` automatically.
* ``git_log`` takes the ``GitManager`` explicitly instead of a module global.
"""
import asyncio
import datetime
import logging
import time

import discord  # type: ignore

from .helpers import localize, utcnow

logger = logging.getLogger('discord.' + __name__)


async def split_send(channel, message, deliminator='\n', n=2000, style='', sopt=None):
    """Split a message into chunks (each < ``n`` chars) and send them.

    ``message`` is a string (split on ``deliminator``) or a list of strings.
    ``style`` wraps every chunk (e.g. ``'```'``).  ``sopt`` is a dict of extra
    kwargs for the *final* ``channel.send`` call.  Returns the sent messages.
    """
    out = []
    if not message:
        return
    if type(message) not in [tuple, list]:
        message = message.split(deliminator)
    else:
        message = list(message)  # don't consume the caller's list
    msg = message.pop(0)
    while message:
        tmp = message.pop(0)
        if len(msg + deliminator + tmp + style * 2) >= n:
            out.append(await channel.send(style + msg + style))
            msg = tmp
        else:
            msg += deliminator + tmp
    if sopt is None:
        sopt = dict()
    out.append(await channel.send(style + msg + style, **sopt))
    return out


async def sleep(dt):
    """asyncio.sleep that falls back to a blocking sleep if discord raises mid-sleep"""
    now = datetime.datetime.now()
    try:
        await asyncio.sleep(dt)
    except discord.HTTPException:
        dt -= (datetime.datetime.now() - now).total_seconds()
        time.sleep(dt)


async def admin_check(ctx=None, bot=None, author=None, guild=None):
    """Is ``author`` (default ``ctx.author``) allowed to run admin commands?

    Uses ``bot.is_admin`` if the bot has one (``BotCore``); otherwise falls back
    to guild-owner / bot-owner only.
    """
    if author is None:
        if ctx is None:
            raise ValueError("Either ctx or author must be specified")
        author = ctx.author
    if guild is None:
        guild = getattr(ctx, 'guild', None)
    if bot is None:
        bot = getattr(ctx, 'bot', None)
    logger.debug('admin_check: %s (top role: %s)', author, getattr(author, "top_role", "NO_ROLE"))
    is_admin = getattr(bot, 'is_admin', None)
    if is_admin is not None:
        return await is_admin(author, guild)
    if guild is not None and guild.owner == author:
        return True
    if bot is not None and await bot.is_owner(author):
        return True
    return False


async def wait_until(dt):
    """sleep until the specified datetime (assumes UTC)"""
    while True:
        now = localize(utcnow())
        remaining = (localize(dt) - now).total_seconds()
        if remaining < 86400:
            break
        # asyncio.sleep doesn't like long sleeps, so don't sleep more than a day at a time
        await asyncio.sleep(86400)
    await asyncio.sleep(remaining)


async def git_log(channel, git, *args):
    """Print the git log of ``git`` (a ``GitManager``) to discord chat."""
    await split_send(channel, git.log_items(), style='```')


async def parse_payload(payload, bot, *fields):
    """Resolve the objects behind a raw reaction/event payload.

    ``fields`` is any of ``guild``, ``member``, ``channel``, ``message``
    (default: ``guild`` and ``member``).  Dependencies are added automatically:
    ``member`` needs ``guild``; ``message`` needs ``channel``.
    """
    fields = list(fields)
    if not fields:
        fields = ['guild', 'member']
    requires = {'member': ['guild'],
                'message': ['channel']}

    for i in fields:
        add = requires.get(i, [])
        for j in add:
            if j not in fields:
                fields.append(j)

    out = dict()
    if "guild" in fields:
        try:
            out["guild"] = [g for g in bot.guilds if g.id == payload.guild_id][0]
        except IndexError:
            out['guild'] = await bot.fetch_guild(payload.guild_id)
    if "member" in fields:
        member = payload.member
        if not member:
            member = await bot.get_or_fetch_user(payload.user_id, out["guild"])
        out["member"] = member
    if "channel" in fields:
        channel = bot.find_channel(payload.channel_id)
        if channel is None:
            channel = await bot.fetch_channel(payload.channel_id)
        out["channel"] = channel
    if "message" in fields:
        out["message"] = await out["channel"].fetch_message(payload.message_id)
    return out
