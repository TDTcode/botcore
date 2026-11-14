import asyncio
import logging

import discord  # type: ignore
from discord.ext import commands  # type: ignore

from ..cog import AdminCog, slash_admin_only
from ..helpers import find_channel

logger = logging.getLogger("discord." + __name__)


class AdminTools(AdminCog, command_attrs={"hidden": True}):
    """Admin-only commands: reboot the bot, speak as the bot"""

    @commands.hybrid_command()
    @slash_admin_only
    async def reboot(self, ctx):
        """Reboots this bot"""
        await ctx.send("Ok. I will reboot now.")
        logger.info("\nRebooting\n\n\n\n")
        self.bot.reissue = ctx
        # Closing the bot exits the bot loop, allowing the runner's loop to take over.  Do it from a task, after
        # this command has returned: discord.py 2's close() clears bot.loop, but commands.Bot.invoke still has to
        # dispatch "command_completion" when we return, which raised an AttributeError on every reboot.
        self._closing = asyncio.create_task(self.bot.close())  # keep a reference so it cannot be collected

    @commands.hybrid_command()
    @slash_admin_only
    async def speak(self, ctx, message, channel: discord.TextChannel = None, guild: str | None = None):
        """Speak as the bot"""
        if guild is None:
            guild = ctx.guild
        else:
            try:
                guild = next(i for i in self.bot.guilds if i.name == guild)
            except StopIteration:
                await ctx.send(f'ERROR: server "{guild}" not found.')
                return
        if channel:
            channel = find_channel(guild, channel)
        else:
            channel = ctx.channel
        await channel.send(message)
        if ctx.interaction is not None:  # a slash command must be answered; nothing else is shown to the caller
            await ctx.send("Sent.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminTools(bot))
