import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timezone, timedelta
from config import *
import db

INTENTS = discord.Intents.default()
INTENTS.members = True
INTENTS.message_content = True
INTENTS.guilds = True

class GlowAIBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.invite_cache = {}

    async def setup_hook(self):
        await db.init_db()
        self.add_view(TicketView())
        self.add_view(CloseTicketView())
        await self.add_cog(SupportCog(self))
        await self.add_cog(ModerationCog(self))
        await self.add_cog(InviteCog(self))
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        for guild in self.guilds:
            try:
                self.invite_cache[guild.id] = {i.code: i.uses for i in await guild.invites()}
            except discord.Forbidden:
                self.invite_cache[guild.id] = {}
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Glow AI support"))
        print(f"Glow AI bot online as {self.user} in {len(self.guilds)} server(s)")

    async def on_member_join(self, member):
        guild = member.guild
        try:
            invites = await guild.invites()
            old = self.invite_cache.get(guild.id, {})
            used = next((i for i in invites if i.uses > old.get(i.code, 0)), None)
            self.invite_cache[guild.id] = {i.code: i.uses for i in invites}
            if used:
                await db.record_invite(guild.id, used.inviter.id, used.code, used.uses)
                await db.record_member_invite(guild.id, member.id, used.inviter.id, used.code)
                inviter_text = f"Invited by **{used.inviter}**"
            else:
                inviter_text = "Invite source could not be determined"
        except discord.Forbidden:
            inviter_text = "Invite tracking needs the **Manage Server** permission"
        channel = guild.get_channel(WELCOME_CHANNEL_ID) if WELCOME_CHANNEL_ID else None
        if channel:
            await channel.send(f"Welcome {member.mention} to **Glow AI**! ✨\n{inviter_text}")

bot = GlowAIBot()

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Support Ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="glowai:ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        existing = discord.utils.get(guild.text_channels, name=f"ticket-{interaction.user.id}")
        if existing:
            return await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
        category = guild.get_channel(SUPPORT_CATEGORY_ID) if SUPPORT_CATEGORY_ID else None
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)}
        if STAFF_ROLE_ID:
            role = guild.get_role(STAFF_ROLE_ID)
            if role: overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
        channel = await guild.create_text_channel(f"ticket-{interaction.user.id}", category=category, overwrites=overwrites, reason="Glow AI support ticket")
        embed = discord.Embed(title="Glow AI Support", description=f"Hi {interaction.user.mention}! Tell us what you need help with.\n\nA staff member will respond here. Use **Close Ticket** when resolved.", color=discord.Color.blurple())
        await channel.send(embed=embed, view=CloseTicketView())
        await interaction.response.send_message(f"Your ticket is ready: {channel.mention}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="glowai:close_ticket")
    async def close(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels and STAFF_ROLE_ID and STAFF_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("Only support staff can close tickets.", ephemeral=True)
        await interaction.response.send_message("Closing this ticket in 5 seconds…")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

class SupportCog(commands.Cog):
    def __init__(self, bot): self.bot = bot
    @app_commands.command(name="setup-ticket", description="Post the Glow AI support ticket panel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_ticket(self, interaction):
        embed = discord.Embed(title="✨ Glow AI Support", description="Need help with Glow AI? Open a private support ticket and our team will assist you.", color=discord.Color.blurple())
        embed.add_field(name="Support", value="Billing, account help, technical issues, and general questions.", inline=False)
        await interaction.channel.send(embed=embed, view=TicketView())
        await interaction.response.send_message("Ticket panel created.", ephemeral=True)

class ModerationCog(commands.Cog):
    def __init__(self, bot): self.bot = bot
    async def modlog(self, guild, text):
        ch = guild.get_channel(MOD_LOG_CHANNEL_ID) if MOD_LOG_CHANNEL_ID else None
        if ch: await ch.send(text)

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction, member: discord.Member, reason: str):
        await db.add_warning(interaction.guild.id, member.id, interaction.user.id, reason)
        count = await db.warning_count(interaction.guild.id, member.id)
        try: await member.send(f"You received a warning in **{interaction.guild.name}**: {reason}")
        except discord.HTTPException: pass
        await self.modlog(interaction.guild, f"⚠️ **Warn** — {member} by {interaction.user}: {reason} (total: {count})")
        await interaction.response.send_message(f"Warned {member.mention}. Total warnings: **{count}**.", ephemeral=True)

    @app_commands.command(name="timeout", description="Timeout a member")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction, member: discord.Member, minutes: app_commands.Range[int,1,10080], reason: str = "No reason provided"):
        await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
        await self.modlog(interaction.guild, f"⏳ **Timeout** — {member} by {interaction.user} for {minutes}m: {reason}")
        await interaction.response.send_message(f"Timed out {member.mention} for {minutes} minutes.", ephemeral=True)

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction, member: discord.Member, reason: str = "No reason provided"):
        await member.kick(reason=reason)
        await self.modlog(interaction.guild, f"👢 **Kick** — {member} by {interaction.user}: {reason}")
        await interaction.response.send_message(f"Kicked **{member}**.", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction, member: discord.Member, reason: str = "No reason provided"):
        await member.ban(reason=reason, delete_message_days=1)
        await self.modlog(interaction.guild, f"🔨 **Ban** — {member} by {interaction.user}: {reason}")
        await interaction.response.send_message(f"Banned **{member}**.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not AUTO_MOD_ENABLED: return
        content = message.content.lower()
        blocked = ["discord.gg/", "free nitro scam", "@everyone get free"]
        if any(x in content for x in blocked) and not message.author.guild_permissions.manage_messages:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention}, that message was removed by Glow AI AutoMod.", delete_after=5)
                await self.modlog(message.guild, f"🛡️ **AutoMod** removed a message from {message.author} in {message.channel.mention}")
            except discord.HTTPException: pass

class InviteCog(commands.Cog):
    def __init__(self, bot): self.bot = bot
    @app_commands.command(name="invites", description="Show a member's tracked invites")
    async def invites(self, interaction, member: discord.Member | None = None):
        target = member or interaction.user
        rows = await db.leaderboard(interaction.guild.id, 100)
        total = next((int(v or 0) for uid,v in rows if uid == target.id), 0)
        await interaction.response.send_message(f"📨 **{target.display_name}** has **{total}** tracked invite uses.", ephemeral=True)

    @app_commands.command(name="invite-leaderboard", description="Show the invite leaderboard")
    async def invite_leaderboard(self, interaction):
        rows = await db.leaderboard(interaction.guild.id, 10)
        if not rows: return await interaction.response.send_message("No invite data has been tracked yet.", ephemeral=True)
        lines = [f"**{i}.** <@{uid}> — **{int(v or 0)}** invites" for i,(uid,v) in enumerate(rows,1)]
        await interaction.response.send_message("📈 **Glow AI Invite Leaderboard**\n" + "\n".join(lines))

@bot.tree.error
async def on_app_command_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use that command."
    else:
        print("Command error:", repr(error))
        msg = "Something went wrong while running that command."
    if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
    else: await interaction.response.send_message(msg, ephemeral=True)

if __name__ == "__main__":
    if not TOKEN: raise SystemExit("Set DISCORD_TOKEN in .env")
    bot.run(TOKEN)
