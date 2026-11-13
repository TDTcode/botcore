from discord.ext import commands  # type: ignore

from ..async_helpers import git_log as send_git_log
from ..cog import AdminCog, slash_admin_only


class GitTools(AdminCog, command_attrs={"hidden": True}):
    """Git commands for the bot's own checkout"""

    @commands.hybrid_command()
    @slash_admin_only
    async def git_pull(self, ctx):
        """Update own code (fetch + rebase); reboot to run the new code"""
        await ctx.defer()  # a slash command has 3 seconds to answer; the pull can take longer
        result = await self.bot.pull_code(ctx.channel)
        await ctx.send(result.summary())

    @commands.hybrid_command()
    @slash_admin_only
    async def git_log(self, ctx):
        """Print git log to discord chat."""
        return await send_git_log(ctx, self.bot.git)

    @commands.hybrid_group()
    @slash_admin_only
    async def git(self, ctx):
        """Base function for git sub commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send("Invalid git command passed...")

    @git.command()
    async def pull(self, ctx):
        """Alias for git_pull"""
        await self.git_pull(ctx)

    @git.command()
    async def log(self, ctx):
        """Alias for git_log"""
        await self.git_log(ctx)


async def setup(bot):
    await bot.add_cog(GitTools(bot))
