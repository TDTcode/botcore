import logging

import discord  # type: ignore
from discord.ext import commands  # type: ignore

from ..cog import AdminCog
from ..helpers import find_channel


logger = logging.getLogger('discord.' + __name__)


class AdminTools(AdminCog, command_attrs=dict(hidden=True)):
    """Admin-only commands: reboot the bot, speak as the bot"""

    @commands.command()
    async def reboot(self, ctx):
        """Reboots this bot"""
        await ctx.send("Ok. I will reboot now.")
        logger.info('\nRebooting\n\n\n\n')
        self.bot.reissue = ctx
        # This exits the bot loop, allowing the runner's loop to take over
        await self.bot.close()

    @commands.command()
    async def speak(self, ctx, message, channel: discord.TextChannel = None, guild: str = None):
        """Speak as the bot"""
        if guild is None:
            guild = ctx.guild
        else:
            try:
                guild = [i for i in self.bot.guilds if i.name == guild][0]
            except IndexError:
                await ctx.send('ERROR: server "{0}" not found.'.format(guild))
                return
        if channel:
            channel = find_channel(guild, channel)
        else:
            channel = ctx.channel
        await channel.send(message)


async def setup(bot):
    await bot.add_cog(AdminTools(bot))
