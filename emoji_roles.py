"""Reaction -> role assignment ("react to get a role").

Extracted from the per-bot ``MainBot.enroll_emoji_role`` / ``emoji2role``.
``BotCore`` owns one ``EmojiRoles`` (``bot.emoji_roles``) and exposes the old
``bot.enroll_emoji_role(...)`` / ``bot.emoji2role(...)`` methods as thin
delegates.  The raw-reaction listeners only run when the bot class sets
``emoji_roles_enabled = True`` (TDTbot: yes; wit_bot: no, matching its
currently disabled handlers).

Fixes relative to the per-bot copies:

* ``guild`` given as an int id is looked up *by id* (the old code indexed the
  guild list with it);
* a member that cannot be resolved is logged and skipped (the old code carried
  on and crashed on ``None``);
* on a reaction *remove*, kwargs from one enrolment no longer leak into the
  next enrolment's call.
"""
import logging

import discord  # type: ignore

from . import helpers


logger = logging.getLogger('discord.' + __name__)


class EmojiRoles:
    """Holds the enrolled emoji->role rules for one bot and applies them."""

    def __init__(self, bot):
        self.bot = bot
        self._rules = []        # [(args, kwargs), ...] as given to enroll()
        self.dm_targets = {}    # member id -> (accept_string, target) for the DM-accept flow

    def enroll(self, *args, **kwargs):
        """Register a rule.  Arguments are those of ``assign`` after ``payload``:
        the first must be the ``{emoji: role}`` dict."""
        if not args:
            raise ValueError("Must provide at least one argument")
        if not isinstance(args[0], dict):
            raise ValueError("First argument must be a dict")
        self._rules.append((args, kwargs))

    async def handle(self, payload, delete=False):
        """Apply every enrolled rule to a raw reaction payload."""
        for args_, kwargs_ in self._rules:
            kwargs = dict(kwargs_)
            if delete:
                kwargs['delete'] = True
            await self.assign(payload, *args_, **kwargs)

    async def assign(self, payload, emoji_dict, emoji=None, message_id=None,
                     member=None, guild=None, min_role=None, delete=False,
                     remove=None, send_message=None, accept_string=None,
                     target=None):
        """Handle one reaction.  Returns the role that was added/removed, or None.

        ``emoji_dict``   {emoji (str/id/name): role (id or name)}
        ``message_id``   only react to this message
        ``min_role``     minimum top role (name/id/Role) needed to *get* the role
        ``remove``       role(s) to remove from the member before adding
        ``send_message`` / ``accept_string`` / ``target``  optional DM-accept flow
                         (TDTbot): DM the member and remember who must answer.
        """
        bot = self.bot
        # ensure that the payload is valid
        if message_id is not None:
            if getattr(payload, "message_id") != message_id:
                return
        # get the author of the reaction
        author = getattr(getattr(payload, "member", None), "id", None)
        uid = getattr(payload, "user_id", None)
        debug = uid is not None and uid == bot.config('emoji_debug_user')
        # if the reaction is from the bot itself, ignore it
        if bot.user.id in [author, uid]:
            if debug:
                logger.info('emoji2role: self return')
            return
        # make sure we have a guild and member
        if guild is None:
            guild = [g for g in bot.guilds if g.id == payload.guild_id][0]
        if isinstance(guild, int):
            guild = [g for g in bot.guilds if g.id == guild][0]
        if member is None:
            member = getattr(payload, "member", None)
            if member is None:
                member = await bot.get_or_fetch_user(payload.user_id, guild)
        # if member is None, we can't do anything, so log it
        if member is None:
            data = dict(payload=payload, emoji_dict=emoji_dict, emoji=emoji, message_id=message_id,
                        guild=guild, min_role=min_role, delete=delete, remove=remove)
            data = {i: j for i, j in data.items() if j is not None}
            msg = "Member is None object"
            msg += '\n' + '\n'.join(['{}: {}'.format(i, j) for i, j in data.items()])
            logger.info(msg)
            return
        # check role requirements
        if min_role is not None and not delete:
            if not isinstance(min_role, discord.Role):
                min_role = helpers.find_role(guild, min_role)
            if member.top_role < min_role:
                if debug:
                    logger.info('emoji2role: min_role return')
                return
        # get the emoji from the payload
        if emoji is None:
            emoji = payload.emoji
        if delete and debug:
            logger.info('Delete (rxn): {}\n{}'.format(emoji, payload))
        if debug:
            logger.info('emoji2role: {}\n\t\t{}'.format(emoji, emoji_dict))
        # compare the emoji to the keys in the emoji_dict
        keys = [i for i in emoji_dict if helpers.emotes_equal(i, emoji)]
        if len(keys) == 1:
            key = keys[0]
            role0 = emoji_dict[key]
            role = helpers.find_role(guild, role0)
            if remove is not None:
                if not isinstance(remove, list) and not isinstance(remove, tuple):
                    remove = [remove]

                for i in remove:
                    if not isinstance(i, discord.Role):
                        i = helpers.find_role(guild, i)
                    if i == role:
                        continue
                    await member.remove_roles(i)
            try:
                if delete:
                    logger.info('Delete (role): {}->{}->{}'.format(key, role0, role))
                    await member.remove_roles(role)
                else:
                    # if we get here, we've uniquely matched the emoji to a role
                    if send_message is not None:
                        await member.send(send_message)
                        if accept_string is not None:
                            accept_string = helpers.clean_string(accept_string)
                        self.dm_targets[member.id] = accept_string, target
                    await member.add_roles(role)
                return role
            except AttributeError:
                logger.info('Role attr err: {}->{}->{}'.format(key, role0, role))
        elif len(keys) > 1:
            logger.info('Multiple matches: {}'.format({i: emoji_dict[i] for i in keys}))

        return None
