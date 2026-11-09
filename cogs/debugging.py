import logging

import discord  # type: ignore
from discord.ext import commands  # type: ignore

from ..async_helpers import split_send
from ..cog import AdminCog
from ..helpers import find_channel


logger = logging.getLogger('discord.' + __name__)


async def _owner(ctx):
    return await ctx.bot.is_owner(ctx.author)


class Debugging(AdminCog, command_attrs=dict(hidden=True)):
    """Cog designed for debugging the bot"""

    async def _find_guild(self, ctx, guild):
        """``ctx.guild`` if ``guild`` is None, else the joined guild with that name."""
        if guild is None:
            return ctx.guild
        try:
            return [i for i in self.bot.guilds if i.name == guild][0]
        except IndexError:
            await ctx.send('ERROR: server "{0}" not found.'.format(guild))

    @commands.command()
    async def channel_id(self, ctx, channel: discord.TextChannel = None, guild: str = None):
        """<channel (optional)> <server (optional)> shows the id of a channel"""
        guild = await self._find_guild(ctx, guild)
        if guild is None:
            return
        if channel:
            channel = find_channel(guild, channel)
        else:
            channel = ctx.channel
        await ctx.send('Channel "{0.name}" has id {0.id}.'.format(channel))

    @commands.command()
    async def member_id(self, ctx, member: str = None, guild: str = None):
        """<member (optional)> <server (optional)> shows the id of a member"""
        guild = await self._find_guild(ctx, guild)
        if guild is None:
            return
        if member:
            member = guild.get_member_named(member)
            if member is None:
                await ctx.send('ERROR: member not found.')
                return
        else:
            member = ctx.author
        await ctx.send('{0.name} has id {0.id}.'.format(member))

    @commands.command()
    async def reload_cogs(self, ctx, option=None):
        """Reloads all cogs that were added as extensions ('pull' to git pull first)"""
        pulled = None
        if option == 'pull':
            pulled = await self.bot.pull_code(ctx.channel)
            if not pulled.ok:  # don't reload half-updated code
                await ctx.send(pulled.summary())
                return
        extensions = list(self.bot.extensions)
        msg = 'Reloading ' + ', '.join([i.split('.')[-1] for i in extensions])
        for i in extensions:
            await self.bot.reload_extension(i)
        await ctx.send((pulled.summary() + '\n' if pulled is not None else '') + msg)

    @commands.command()
    async def print(self, ctx, *args):
        """Print text following command to terminal. This is useful for emojis."""
        logger.info(' '.join(args))

    @commands.command(name='param', aliases=['config'])
    async def param(self, ctx, *args):
        """Print the bot's config to discord chat (secrets are left out)."""
        items = self.bot.config.public_items()
        if items:
            msg = '\n'.join(["{:}: {:}".format(i, j) for i, j in items.items()])
            await split_send(ctx, msg, style='```')
        else:
            await ctx.send("Param is empty.")

    @commands.command()
    async def reload_extension(self, ctx, name):
        """<name> - Reloads an extension"""
        await self.bot.reload_extension(name)
        await ctx.send('Reloaded ' + name)

    @commands.command()
    async def id_this(self, ctx):
        """Prints the id of message, ser, and channel"""
        ref = ctx.message.reference
        msg = ctx.message
        if ref:
            if hasattr(ref, 'resolved'):
                msg = ref.resolved
            else:
                channel = self.bot.find_channel(ref.channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(ref.channel_id)
                if channel is None:
                    msg = ref
                else:
                    msg = await channel.fetch_message(ref.message_id)
        if hasattr(msg, 'id'):
            txt = ['message id: ' + str(msg.id),
                   'channel id: ' + str(msg.channel.id),
                   'guild id: ' + str(msg.guild.id)]
        else:
            txt = ['{}: {}'.format(i.replace('_', ' '), getattr(msg, i, 'unknown'))
                   for i in ['message_id', 'channel_id', 'guild_id']]
        if hasattr(msg, 'author'):
            a = msg.author
            txt.append('author: {}'.format(a.display_name))
            txt.append('author id: {0}'.format(a.id))
        await split_send(ctx, txt)

    @commands.command()
    async def channel_hist(self, ctx, channel: discord.TextChannel = None, n: int = 10):
        """<channel (optional)> shows channel history (past 10 entries)"""
        if channel:
            channel = find_channel(ctx.guild, channel)
        else:
            channel = ctx.channel
        hist = [h async for h in channel.history(limit=n)]
        msg = ["Item {0:d} {1.id}\n{1.content}".format(i + 1, m)
               for i, m in enumerate(hist)]
        if not msg:
            msg = ["No history available."]
        await split_send(ctx, msg)

    @commands.command()
    async def channel_pins(self, ctx, channel: discord.TextChannel = None):
        """<channel (optional)> shows channel pins"""
        if channel:
            if hasattr(channel, "lower"):
                channel = find_channel(ctx.guild, channel)
        else:
            channel = ctx.channel
        pins = await channel.pins()
        msg = ["Item {0:d} {1.id}\n{1.content}".format(i + 1, m)
               for i, m in enumerate(pins)]
        if not msg:
            msg = ["No pins available."]
        await split_send(ctx, msg)

    @commands.command()
    @commands.check(_owner)
    async def exec(self, ctx, *args):
        """<code> - Execute code. Items in `out` get printed to discord."""
        out = []
        var_data = dict(out=out, self=self)  # noqa: F841
        cmd = ' '.join(args).strip().strip('`"\'')
        await ctx.send("Running the following code:\n```\n" + cmd + "\n```")
        exec(cmd)
        if out:
            n = len(str(len(out)))
            fmt = '{:0' + str(n) + 'd}) `{:}`'
            out = ['Outputs:'] + [fmt.format(i, o) for i, o in enumerate(out)]
            await split_send(ctx, out)


async def setup(bot):
    await bot.add_cog(Debugging(bot))
