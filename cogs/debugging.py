import logging

import discord  # type: ignore
from discord.ext import commands  # type: ignore

from ..async_helpers import split_send
from ..cog import AdminCog, slash_admin_only
from ..helpers import find_channel

logger = logging.getLogger("discord." + __name__)


async def _owner(ctx):
    return await ctx.bot.is_owner(ctx.author)


class Debugging(AdminCog, command_attrs={"hidden": True}):
    """Cog designed for debugging the bot"""

    async def _find_guild(self, ctx, guild):
        """``ctx.guild`` if ``guild`` is None, else the joined guild with that name."""
        if guild is None:
            return ctx.guild
        try:
            return next(i for i in self.bot.guilds if i.name == guild)
        except StopIteration:
            await ctx.send(f'ERROR: server "{guild}" not found.')

    @commands.hybrid_command()
    @slash_admin_only
    async def channel_id(self, ctx, channel: discord.TextChannel = None, guild: str | None = None):
        """Show the id of a channel (default: this one)"""
        guild = await self._find_guild(ctx, guild)
        if guild is None:
            return
        if channel:
            channel = find_channel(guild, channel)
        else:
            channel = ctx.channel
        await ctx.send(f'Channel "{channel.name}" has id {channel.id}.')

    @commands.hybrid_command()
    @slash_admin_only
    async def member_id(self, ctx, member: str | None = None, guild: str | None = None):
        """Show the id of a member (default: you)"""
        guild = await self._find_guild(ctx, guild)
        if guild is None:
            return
        if member:
            member = guild.get_member_named(member)
            if member is None:
                await ctx.send("ERROR: member not found.")
                return
        else:
            member = ctx.author
        await ctx.send(f"{member.name} has id {member.id}.")

    @commands.hybrid_command()
    @slash_admin_only
    async def reload_cogs(self, ctx, option: str | None = None):
        """Reloads all cogs that were added as extensions ('pull' to git pull first)"""
        await ctx.defer()  # a slash command has 3 seconds to answer; pulling and reloading can take longer
        pulled = None
        if option == "pull":
            pulled = await self.bot.pull_code(ctx.channel)
            if not pulled.ok:  # don't reload half-updated code
                await ctx.send(pulled.summary())
                return
        extensions = list(self.bot.extensions)
        msg = "Reloading " + ", ".join([i.split(".")[-1] for i in extensions])
        for i in extensions:
            await self.bot.reload_extension(i)
        await ctx.send((pulled.summary() + "\n" if pulled is not None else "") + msg)

    @commands.hybrid_command()
    @slash_admin_only
    async def print(self, ctx, *, text: str):
        """Print the text to the terminal. This is useful for emojis."""
        logger.info(text)
        if ctx.interaction is not None:  # a slash command must be answered
            await ctx.send("Printed.", ephemeral=True)

    @commands.hybrid_command(name="param", aliases=["config"])
    @slash_admin_only
    async def param(self, ctx):
        """Print the bot's config to discord chat (secrets are left out)."""
        items = self.bot.config.public_items()
        if items:
            msg = "\n".join([f"{i}: {j}" for i, j in items.items()])
            await split_send(ctx, msg, style="```")
        else:
            await ctx.send("Param is empty.")

    @commands.hybrid_command()
    @slash_admin_only
    async def reload_extension(self, ctx, name: str):
        """Reload an extension"""
        await self.bot.reload_extension(name)
        await ctx.send("Reloaded " + name)

    @commands.hybrid_command()
    @slash_admin_only
    async def id_this(self, ctx):
        """Print the ids of the message (or the one it replies to), server and channel"""
        ref = ctx.message.reference
        msg = ctx.message
        if ref:
            if hasattr(ref, "resolved"):
                msg = ref.resolved
            else:
                channel = self.bot.find_channel(ref.channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(ref.channel_id)
                if channel is None:
                    msg = ref
                else:
                    msg = await channel.fetch_message(ref.message_id)
        if hasattr(msg, "id"):
            txt = ["message id: " + str(msg.id), "channel id: " + str(msg.channel.id), "guild id: " + str(msg.guild.id)]
        else:
            txt = [
                "{}: {}".format(i.replace("_", " "), getattr(msg, i, "unknown"))
                for i in ["message_id", "channel_id", "guild_id"]
            ]
        if hasattr(msg, "author"):
            a = msg.author
            txt.append(f"author: {a.display_name}")
            txt.append(f"author id: {a.id}")
        await split_send(ctx, txt)

    @commands.hybrid_command()
    @slash_admin_only
    async def channel_hist(self, ctx, channel: discord.TextChannel = None, n: int = 10):
        """Show the channel's history (default: this channel, past 10 entries)"""
        await ctx.defer()
        if channel:
            channel = find_channel(ctx.guild, channel)
        else:
            channel = ctx.channel
        hist = [h async for h in channel.history(limit=n)]
        msg = [f"Item {i + 1:d} {m.id}\n{m.content}" for i, m in enumerate(hist)]
        if not msg:
            msg = ["No history available."]
        await split_send(ctx, msg)

    @commands.hybrid_command()
    @slash_admin_only
    async def channel_pins(self, ctx, channel: discord.TextChannel = None):
        """Show the channel's pins (default: this channel)"""
        await ctx.defer()
        if channel:
            if hasattr(channel, "lower"):
                channel = find_channel(ctx.guild, channel)
        else:
            channel = ctx.channel
        pins = await channel.pins()
        msg = [f"Item {i + 1:d} {m.id}\n{m.content}" for i, m in enumerate(pins)]
        if not msg:
            msg = ["No pins available."]
        await split_send(ctx, msg)

    @commands.hybrid_command()
    @slash_admin_only
    @commands.check(_owner)
    async def exec(self, ctx, *, code: str):
        """Execute code. Items in `out` get printed to discord."""
        out = []
        var_data = {"out": out, "self": self}  # noqa: F841  (read implicitly by exec below)
        cmd = code.strip().strip("`\"'")
        await ctx.send("Running the following code:\n```\n" + cmd + "\n```")
        exec(cmd)
        if out:
            n = len(str(len(out)))
            fmt = "{:0" + str(n) + "d}) `{:}`"
            out = ["Outputs:"] + [fmt.format(i, o) for i, o in enumerate(out)]
            await split_send(ctx, out)


async def setup(bot):
    await bot.add_cog(Debugging(bot))
