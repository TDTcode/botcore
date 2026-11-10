from pathlib import Path

from discord.ext import commands  # type: ignore

from ..async_helpers import split_send
from ..cog import CoreCog


class Privacy(CoreCog):
    """Command that explains how the bot handles Discord data.

    Shows ``<bot root>/PRIVACY.md`` (override the location with the
    ``privacy_file`` config key).  The public URL of the policy, if any, is
    the ``privacy_url`` config key.
    """

    def policy_path(self):
        override = self.bot.config("privacy_file")
        return Path(override) if override else self.bot.paths.root / "PRIVACY.md"

    @commands.command(aliases=["privacy", "policy"])
    async def privacy_policy(self, ctx):
        """Display the privacy policy in chat."""
        path = self.policy_path()
        try:
            policy = path.read_text(encoding="utf-8")
        except OSError:
            url = self.bot.config("privacy_url")
            await ctx.send("No privacy policy file is available." + (" See: " + url if url else ""))
            return
        await split_send(ctx, policy)


async def setup(bot):
    await bot.add_cog(Privacy(bot))
