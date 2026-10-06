import os
import re
import random
import sqlite3
import asyncio
from datetime import timedelta, datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
SUPPORT_CATEGORY_ID = int(os.getenv("SUPPORT_CATEGORY_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
AUTO_MOD_ENABLED = os.getenv("AUTO_MOD_ENABLED", "true").lower() == "true"

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = sqlite3.connect("data/glowai.db")
db.execute("""CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)""")
db.execute("""CREATE TABLE IF NOT EXISTS invite_uses (
    guild_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    inviter_id INTEGER,
    invite_code TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, member_id)
)""")
db.commit()

invite_cache = {}
giveaways = {}

def staff_check(interaction: discord.Interaction) -> bool:
    return (
        interaction.user.guild_permissions.manage_guild
        or interaction.user.guild_permissions.manage_messages
        or any(r.id == STAFF_ROLE_ID for r in getattr(interaction.user, "roles", []))
    )

async def mod_log(guild: discord.Guild, message: str):
    channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except discord.HTTPException:
            pass

class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Support Ticket", style=discord.ButtonStyle.blurple, emoji="🎫", custom_id="glow_open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            return
        existing = discord.utils.get(guild.text_channels, name=f"ticket-{interaction.user.id}")
        if existing:
            await interaction.response.send_message(f"You already have a ticket: {existing.mention}", ephemeral=True)
            return

        category = guild.get_channel(SUPPORT_CATEGORY_ID)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel = await guild.create_text_channel(
            f"ticket-{interaction.user.id}",
            category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=overwrites,
            reason="Glow Bot support ticket",
        )
        await channel.send(
            f"{interaction.user.mention} welcome! A staff member will be with you shortly.",
            view=CloseTicketView()
        )
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒", custom_id="glow_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not staff_check(interaction):
            await interaction.response.send_message("You don't have permission to close tickets.", ephemeral=True)
            return
        await interaction.response.send_message("Closing ticket...", ephemeral=True)
        await asyncio.sleep(1)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            pass

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.green, emoji="🎉", custom_id="glow_giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = giveaways.get(self.giveaway_id)
        if not data or data["ended"]:
            await interaction.response.send_message("This giveaway has already ended.", ephemeral=True)
            return
        if interaction.user.id in data["entries"]:
            await interaction.response.send_message("You're already entered! 🎉", ephemeral=True)
            return
        data["entries"].add(interaction.user.id)
        await interaction.response.send_message("You're entered! Good luck! 🍀", ephemeral=True)

async def end_giveaway(giveaway_id: int):
    data = giveaways.get(giveaway_id)
    if not data:
        return
    await asyncio.sleep(data["duration"])
    if data["ended"]:
        return
    data["ended"] = True
    channel = bot.get_channel(data["channel_id"])
    if not channel:
        return

    entries = list(data["entries"])
    winners_count = min(data["winners"], len(entries))
    if winners_count == 0:
        await channel.send(f"🎉 Giveaway ended for **{data['prize']}**, but nobody entered.")
        return

    winners = random.sample(entries, winners_count)
    mentions = ", ".join(f"<@{uid}>" for uid in winners)
    await channel.send(f"🎉 **Giveaway ended!**\nPrize: **{data['prize']}**\nWinner(s): {mentions}")

def parse_duration(value: str):
    match = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return amount * seconds

@bot.event
async def setup_hook():
    bot.add_view(TicketPanel())
    bot.add_view(CloseTicketView())
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

@bot.event
async def on_ready():
    print("Glow AI Bot Online")
    print(f"Logged in as: {bot.user}")
    print(f"Servers: {len(bot.guilds)}")
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invite_cache[guild.id] = {i.code: i.uses or 0 for i in invites}
        except discord.Forbidden:
            invite_cache[guild.id] = {}

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    try:
        invites = await guild.invites()
        before = invite_cache.get(guild.id, {})
        used = None
        for inv in invites:
            old = before.get(inv.code, 0)
            if (inv.uses or 0) > old:
                used = inv
                break
        invite_cache[guild.id] = {i.code: i.uses or 0 for i in invites}
        inviter_id = used.inviter.id if used and used.inviter else None
        db.execute(
            "INSERT OR REPLACE INTO invite_uses VALUES (?, ?, ?, ?, ?)",
            (guild.id, member.id, inviter_id, used.code if used else None, datetime.now(timezone.utc).isoformat())
        )
        db.commit()
    except discord.Forbidden:
        pass

    if WELCOME_CHANNEL_ID:
        channel = guild.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            await channel.send(f"Welcome {member.mention}! 👋")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if AUTO_MOD_ENABLED:
        blocked = ["discord.gg/", "free nitro scam", "@everyone get free"]
        content = message.content.lower()
        if any(x in content for x in blocked):
            try:
                await message.delete()
                await message.channel.send(
                    f"{message.author.mention}, that message was removed by AutoMod.",
                    delete_after=5
                )
                await mod_log(message.guild, f"🤖 AutoMod removed a message from {message.author.mention} in {message.channel.mention}.")
            except discord.HTTPException:
                pass
    await bot.process_commands(message)

@bot.tree.command(name="ping", description="Check Glow Bot's latency.")
async def ping(interaction: discord.Interaction):
    ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! **{ms}ms**")

@bot.tree.command(name="setup-ticket", description="Create the Glow Bot support ticket panel.")
async def setup_ticket(interaction: discord.Interaction):
    if not staff_check(interaction):
        await interaction.response.send_message("You need staff permissions to do this.", ephemeral=True)
        return
    await interaction.channel.send("🎫 **Glow AI Support**\nNeed help? Click the button below to open a private support ticket.", view=TicketPanel())
    await interaction.response.send_message("Ticket panel created!", ephemeral=True)

@bot.tree.command(name="warn", description="Warn a member.")
@app_commands.describe(member="Member to warn", reason="Reason for the warning")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("You need Manage Messages.", ephemeral=True)
        return
    db.execute("INSERT INTO warnings (guild_id,user_id,moderator_id,reason,created_at) VALUES (?,?,?,?,?)",
               (interaction.guild.id, member.id, interaction.user.id, reason, datetime.now(timezone.utc).isoformat()))
    db.commit()
    await interaction.response.send_message(f"⚠️ {member.mention} was warned. Reason: {reason}")
    await mod_log(interaction.guild, f"⚠️ {interaction.user.mention} warned {member.mention}: {reason}")

@bot.tree.command(name="timeout", description="Timeout a member.")
@app_commands.describe(member="Member to timeout", minutes="Timeout duration in minutes", reason="Reason")
async def timeout_member(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("You need Moderate Members.", ephemeral=True)
        return
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"⏳ {member.mention} timed out for {minutes} minute(s).")
    await mod_log(interaction.guild, f"⏳ {interaction.user.mention} timed out {member.mention} for {minutes}m: {reason}")

@bot.tree.command(name="kick", description="Kick a member.")
@app_commands.describe(member="Member to kick", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("You need Kick Members.", ephemeral=True)
        return
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 Kicked {member.mention}.")
    await mod_log(interaction.guild, f"👢 {interaction.user.mention} kicked {member.mention}: {reason}")

@bot.tree.command(name="ban", description="Ban a member.")
@app_commands.describe(member="Member to ban", reason="Reason")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("You need Ban Members.", ephemeral=True)
        return
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 Banned {member.mention}.")
    await mod_log(interaction.guild, f"🔨 {interaction.user.mention} banned {member.mention}: {reason}")

@bot.tree.command(name="invites", description="Show a member's tracked invites.")
@app_commands.describe(member="Member to check")
async def invites(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    count = db.execute(
        "SELECT COUNT(*) FROM invite_uses WHERE guild_id=? AND inviter_id=?",
        (interaction.guild.id, target.id)
    ).fetchone()[0]
    await interaction.response.send_message(f"📨 {target.mention} has **{count}** tracked invite(s).")

@bot.tree.command(name="invite-leaderboard", description="Show the invite leaderboard.")
async def invite_leaderboard(interaction: discord.Interaction):
    rows = db.execute(
        "SELECT inviter_id, COUNT(*) c FROM invite_uses WHERE guild_id=? AND inviter_id IS NOT NULL GROUP BY inviter_id ORDER BY c DESC LIMIT 10",
        (interaction.guild.id,)
    ).fetchall()
    if not rows:
        await interaction.response.send_message("No tracked invites yet.")
        return
    lines = [f"**{i}.** <@{uid}> — **{count}** invite(s)" for i, (uid, count) in enumerate(rows, 1)]
    await interaction.response.send_message("🏆 **Invite Leaderboard**\n" + "\n".join(lines))

@bot.tree.command(name="give-community", description="Give the Community role to every member.")
@app_commands.describe(role="The Community role to give to all members")
async def give_community(interaction: discord.Interaction, role: discord.Role):
    if not staff_check(interaction):
        await interaction.response.send_message("You need staff permissions to use this.", ephemeral=True)
        return
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message("I can't assign that role because it is at or above my highest role. Move my bot role above it.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    success = 0
    skipped = 0
    for member in interaction.guild.members:
        if member.bot:
            continue
        if role in member.roles:
            skipped += 1
            continue
        try:
            await member.add_roles(role, reason=f"Community role assigned by {interaction.user}")
            success += 1
        except discord.HTTPException:
            skipped += 1
    await interaction.followup.send(f"✅ Added {role.mention} to **{success}** member(s). Skipped **{skipped}**.", ephemeral=True)

@bot.tree.command(name="embed", description="Send a custom embed message.")
@app_commands.describe(
    title="Embed title",
    description="Embed description",
    color="Hex color like #5865F2 (optional)",
    footer="Footer text (optional)",
    image_url="Image URL (optional)"
)
async def embed_message(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: str = "#5865F2",
    footer: str = "",
    image_url: str = ""
):
    if not staff_check(interaction):
        await interaction.response.send_message("You need staff permissions to use this.", ephemeral=True)
        return
    try:
        clean = color.strip().lstrip("#")
        if len(clean) != 6:
            raise ValueError
        embed_color = discord.Color(int(clean, 16))
    except ValueError:
        await interaction.response.send_message("Invalid color. Use a 6-digit hex color like `#5865F2`.", ephemeral=True)
        return
    embed = discord.Embed(title=title, description=description, color=embed_color)
    if footer:
        embed.set_footer(text=footer)
    if image_url:
        embed.set_image(url=image_url)
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Embed sent!", ephemeral=True)

@bot.tree.command(name="giveaway", description="Start a giveaway.")
@app_commands.describe(
    duration="Duration like 10m, 2h, 1d, or 1w",
    prize="Giveaway prize",
    winners="Number of winners (1-20)"
)
async def giveaway(interaction: discord.Interaction, duration: str, prize: str, winners: int = 1):
    if not staff_check(interaction):
        await interaction.response.send_message("You need staff permissions to start a giveaway.", ephemeral=True)
        return
    seconds = parse_duration(duration)
    if seconds is None or seconds < 10 or seconds > 604800:
        await interaction.response.send_message("Duration must be between 10 seconds and 7 days, like `10m`, `2h`, or `1d`.", ephemeral=True)
        return
    if winners < 1 or winners > 20:
        await interaction.response.send_message("Winners must be between 1 and 20.", ephemeral=True)
        return

    giveaway_id = random.randint(100000, 999999999)
    giveaways[giveaway_id] = {
        "channel_id": interaction.channel.id,
        "prize": prize,
        "winners": winners,
        "duration": seconds,
        "entries": set(),
        "ended": False,
    }
    end_time = int(datetime.now(timezone.utc).timestamp() + seconds)
    embed = discord.Embed(
        title="🎉 GIVEAWAY 🎉",
        description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{end_time}:R>\n\nClick **Enter Giveaway** below to enter!",
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Giveaway ID: {giveaway_id}")
    await interaction.response.send_message("Giveaway created!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=GiveawayView(giveaway_id))
    asyncio.create_task(end_giveaway(giveaway_id))

bot.run(TOKEN)
