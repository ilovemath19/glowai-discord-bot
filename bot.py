import os
import re
import random
import asyncio
import sqlite3
import threading
from datetime import timedelta, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

SUPPORT_CATEGORY_ID = int(os.getenv("SUPPORT_CATEGORY_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))

AUTO_MOD_ENABLED = os.getenv("AUTO_MOD_ENABLED", "true").lower() == "true"

# Render provides PORT automatically.
PORT = int(os.getenv("PORT", "10000"))


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")

if not GUILD_ID:
    raise RuntimeError("GUILD_ID is missing.")


# ============================================================
# RENDER WEB SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b"Glow AI Bot is online."

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        # Keep Render logs clean.
        return


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)

    print(f"Health server listening on port {PORT}")
    print("Render health server is READY")

    server.serve_forever()


# Start the HTTP server BEFORE the Discord bot starts.
health_thread = threading.Thread(
    target=start_health_server,
    daemon=True
)

health_thread.start()


# ============================================================
# DISCORD INTENTS
# ============================================================

intents = discord.Intents.default()

intents.members = True
intents.message_content = True


# ============================================================
# BOT
# ============================================================

class GlowBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):

        # Persistent ticket buttons
        self.add_view(TicketPanel())
        self.add_view(CloseTicketView())

        # Sync commands to the Glow AI server.
        guild = discord.Object(id=GUILD_ID)

        try:
            synced = await self.tree.sync(guild=guild)

            print(
                f"Synced {len(synced)} slash commands "
                f"to guild {GUILD_ID}"
            )

        except Exception as e:
            print(f"Command sync error: {e}")


bot = GlowBot()


# ============================================================
# DATABASE
# ============================================================

os.makedirs("data", exist_ok=True)

DB_PATH = os.path.join("data", "glowai.db")

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    inviter_id INTEGER,
    invite_code TEXT,
    created_at TEXT NOT NULL
)
""")

db.commit()

db_lock = threading.Lock()


# ============================================================
# MEMORY
# ============================================================

# guild_id -> {invite_code: uses}
invite_cache = {}

# giveaway_id -> giveaway information
giveaways = {}

# Auto incrementing giveaway ID
next_giveaway_id = 1


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def is_staff(member: discord.Member):
    if member.guild_permissions.administrator:
        return True

    if STAFF_ROLE_ID:
        role = member.guild.get_role(STAFF_ROLE_ID)

        if role and role in member.roles:
            return True

    return False


def parse_duration(value: str):

    if not value:
        return None

    value = value.strip().lower()

    match = re.fullmatch(
        r"(\d+)\s*(s|m|h|d|w)",
        value
    )

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    seconds = {
        "s": amount,
        "m": amount * 60,
        "h": amount * 60 * 60,
        "d": amount * 60 * 60 * 24,
        "w": amount * 60 * 60 * 24 * 7
    }[unit]

    if seconds < 10:
        return None

    if seconds > 7 * 24 * 60 * 60:
        return None

    return seconds


def hex_to_int(value: str):

    if not value:
        return 0x5865F2

    value = value.strip().replace("#", "")

    try:
        return int(value, 16)

    except ValueError:
        return 0x5865F2


async def send_mod_log(guild: discord.Guild, message: str):

    if not MOD_LOG_CHANNEL_ID:
        return

    channel = guild.get_channel(MOD_LOG_CHANNEL_ID)

    if channel is None:
        return

    try:
        await channel.send(message)

    except discord.HTTPException:
        pass


async def staff_check(interaction: discord.Interaction):

    if not interaction.guild:
        return False

    if not isinstance(interaction.user, discord.Member):
        return False

    return is_staff(interaction.user)


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print("")
    print("========================================")
    print("Glow AI Bot Online")
    print(f"Logged in as: {bot.user}")
    print(f"Servers: {len(bot.guilds)}")
    print(f"Render PORT: {PORT}")
    print("========================================")
    print("")

    # Cache server invites for invite tracking.
    for guild in bot.guilds:

        try:
            invites = await guild.invites()

            invite_cache[guild.id] = {
                invite.code: invite.uses
                for invite in invites
            }

        except Exception as e:
            print(
                f"Could not cache invites for "
                f"{guild.name}: {e}"
            )


# ============================================================
# MEMBER JOIN / INVITE TRACKING
# ============================================================

@bot.event
async def on_member_join(member: discord.Member):

    guild = member.guild

    inviter = None
    used_code = None

    try:

        current_invites = await guild.invites()

        old_invites = invite_cache.get(
            guild.id,
            {}
        )

        for invite in current_invites:

            old_uses = old_invites.get(
                invite.code,
                0
            )

            if invite.uses > old_uses:
                inviter = invite.inviter
                used_code = invite.code
                break

        invite_cache[guild.id] = {
            invite.code: invite.uses
            for invite in current_invites
        }

    except Exception as e:

        print(
            f"Invite tracking error: {e}"
        )

    inviter_id = inviter.id if inviter else None

    with db_lock:

        db.execute(
            """
            INSERT INTO invites
            (guild_id, member_id, inviter_id, invite_code, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guild.id,
                member.id,
                inviter_id,
                used_code,
                utc_now().isoformat()
            )
        )

        db.commit()

    if WELCOME_CHANNEL_ID:

        channel = guild.get_channel(
            WELCOME_CHANNEL_ID
        )

        if channel:

            try:

                if inviter:

                    await channel.send(
                        f"✨ Welcome {member.mention}! "
                        f"You were invited by {inviter.mention}."
                    )

                else:

                    await channel.send(
                        f"✨ Welcome {member.mention}!"
                    )

            except discord.HTTPException:
                pass


# ============================================================
# AUTOMOD
# ============================================================

BLOCKED_PATTERNS = [
    "discord.gg/",
    "free nitro scam",
    "@everyone get free",
    "claim free nitro",
    "nitro generator",
    "free nitro generator"
]


@bot.event
async def on_message(message: discord.Message):

    if message.author.bot:
        return

    if AUTO_MOD_ENABLED and message.guild:

        content = message.content.lower()

        matched = None

        for pattern in BLOCKED_PATTERNS:

            if pattern in content:
                matched = pattern
                break

        if matched:

            try:
                await message.delete()

            except discord.HTTPException:
                pass

            try:

                warning = await message.channel.send(
                    f"{message.author.mention}, "
                    "that message was removed by AutoMod."
                )

                await asyncio.sleep(5)

                await warning.delete()

            except discord.HTTPException:
                pass

            await send_mod_log(
                message.guild,
                (
                    f"🛡️ **AutoMod Action**\n"
                    f"User: {message.author.mention}\n"
                    f"Channel: {message.channel.mention}\n"
                    f"Matched: `{matched}`"
                )
            )

            return

    await bot.process_commands(message)


# ============================================================
# PING
# ============================================================

@bot.tree.command(
    name="ping",
    description="Check Glow Bot latency.",
    guild=discord.Object(id=GUILD_ID)
)
async def ping(interaction: discord.Interaction):

    # Respond immediately.
    latency = round(bot.latency * 1000)

    await interaction.response.send_message(
        f"🏓 Pong! **{latency}ms**"
    )


# ============================================================
# INVITES
# ============================================================

@bot.tree.command(
    name="invites",
    description="Check how many members you invited.",
    guild=discord.Object(id=GUILD_ID)
)
async def invites(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    target = member or interaction.user

    with db_lock:

        row = db.execute(
            """
            SELECT COUNT(*) AS total
            FROM invites
            WHERE guild_id = ?
            AND inviter_id = ?
            """,
            (
                interaction.guild.id,
                target.id
            )
        ).fetchone()

    total = row["total"]

    await interaction.response.send_message(
        f"📨 **{target.display_name}** has "
        f"**{total}** tracked invite(s)."
    )


@bot.tree.command(
    name="invite-leaderboard",
    description="Show the server invite leaderboard.",
    guild=discord.Object(id=GUILD_ID)
)
async def invite_leaderboard(
    interaction: discord.Interaction
):

    with db_lock:

        rows = db.execute(
            """
            SELECT inviter_id, COUNT(*) AS total
            FROM invites
            WHERE guild_id = ?
            AND inviter_id IS NOT NULL
            GROUP BY inviter_id
            ORDER BY total DESC
            LIMIT 10
            """,
            (interaction.guild.id,)
        ).fetchall()

    if not rows:

        await interaction.response.send_message(
            "No tracked invites yet."
        )

        return

    lines = []

    for index, row in enumerate(rows, start=1):

        member = interaction.guild.get_member(
            row["inviter_id"]
        )

        name = (
            member.mention
            if member
            else f"<@{row['inviter_id']}>"
        )

        lines.append(
            f"**{index}.** {name} — "
            f"**{row['total']}** invites"
        )

    embed = discord.Embed(
        title="🏆 Invite Leaderboard",
        description="\n".join(lines),
        color=0x5865F2
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# GIVE COMMUNITY ROLE
# ============================================================

@bot.tree.command(
    name="give-community",
    description="Give a community role to all non-bot members.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    role="The community role to give."
)
async def give_community(
    interaction: discord.Interaction,
    role: discord.Role
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions to use this.",
            ephemeral=True
        )

        return

    # Acknowledge immediately because this operation
    # can take some time.
    await interaction.response.defer(
        ephemeral=True
    )

    bot_member = interaction.guild.me

    if bot_member is None:

        await interaction.followup.send(
            "❌ I could not determine my server member.",
            ephemeral=True
        )

        return

    if role >= bot_member.top_role:

        await interaction.followup.send(
            "❌ I cannot manage that role because it is "
            "higher than or equal to my highest role.",
            ephemeral=True
        )

        return

    added = 0
    skipped = 0

    for member in interaction.guild.members:

        if member.bot:
            continue

        if role in member.roles:
            skipped += 1
            continue

        try:

            await member.add_roles(
                role,
                reason="Glow AI community role"
            )

            added += 1

        except discord.HTTPException:

            skipped += 1

    await interaction.followup.send(
        f"✅ Community role completed.\n"
        f"Added: **{added}**\n"
        f"Skipped: **{skipped}**",
        ephemeral=True
    )


# ============================================================
# EMBED COMMAND
# ============================================================

@bot.tree.command(
    name="embed",
    description="Create a custom embed.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    title="Embed title.",
    description="Embed description.",
    color="Hex color such as #5865F2.",
    footer="Optional footer.",
    image_url="Optional image URL."
)
async def embed_command(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: str = "#5865F2",
    footer: str = "",
    image_url: str = ""
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions to use this.",
            ephemeral=True
        )

        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=hex_to_int(color)
    )

    if footer:
        embed.set_footer(text=footer)

    if image_url:
        embed.set_image(url=image_url)

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# WARN
# ============================================================

@bot.tree.command(
    name="warn",
    description="Warn a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to warn.",
    reason="Reason for the warning."
)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions.",
            ephemeral=True
        )

        return

    with db_lock:

        db.execute(
            """
            INSERT INTO warnings
            (guild_id, user_id, moderator_id, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                interaction.guild.id,
                member.id,
                interaction.user.id,
                reason,
                utc_now().isoformat()
            )
        )

        db.commit()

        count = db.execute(
            """
            SELECT COUNT(*) AS total
            FROM warnings
            WHERE guild_id = ?
            AND user_id = ?
            """,
            (
                interaction.guild.id,
                member.id
            )
        ).fetchone()["total"]

    await interaction.response.send_message(
        f"⚠️ Warned {member.mention}.\n"
        f"Reason: **{reason}**\n"
        f"Total warnings: **{count}**"
    )

    await send_mod_log(
        interaction.guild,
        (
            f"⚠️ **Warning**\n"
            f"Member: {member.mention}\n"
            f"Moderator: {interaction.user.mention}\n"
            f"Reason: {reason}\n"
            f"Total warnings: {count}"
        )
    )


# ============================================================
# TIMEOUT
# ============================================================

@bot.tree.command(
    name="timeout",
    description="Timeout a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to timeout.",
    minutes="Timeout length in minutes.",
    reason="Reason."
)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: int,
    reason: str = "No reason provided"
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions.",
            ephemeral=True
        )

        return

    if minutes < 1 or minutes > 40320:

        await interaction.response.send_message(
            "❌ Timeout must be between 1 minute and 28 days.",
            ephemeral=True
        )

        return

    try:

        await member.timeout(
            timedelta(minutes=minutes),
            reason=reason
        )

        await interaction.response.send_message(
            f"⏱️ {member.mention} was timed out "
            f"for **{minutes} minutes**.\n"
            f"Reason: **{reason}**"
        )

        await send_mod_log(
            interaction.guild,
            (
                f"⏱️ **Timeout**\n"
                f"Member: {member.mention}\n"
                f"Moderator: {interaction.user.mention}\n"
                f"Duration: {minutes} minutes\n"
                f"Reason: {reason}"
            )
        )

    except discord.HTTPException as e:

        await interaction.response.send_message(
            f"❌ Could not timeout member: `{e}`",
            ephemeral=True
        )


# ============================================================
# KICK
# ============================================================

@bot.tree.command(
    name="kick",
    description="Kick a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to kick.",
    reason="Reason."
)
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions.",
            ephemeral=True
        )

        return

    try:

        await member.kick(
            reason=reason
        )

        await interaction.response.send_message(
            f"👢 Kicked **{member}**.\n"
            f"Reason: **{reason}**"
        )

        await send_mod_log(
            interaction.guild,
            (
                f"👢 **Kick**\n"
                f"Member: {member} (`{member.id}`)\n"
                f"Moderator: {interaction.user.mention}\n"
                f"Reason: {reason}"
            )
        )

    except discord.HTTPException as e:

        await interaction.response.send_message(
            f"❌ Could not kick member: `{e}`",
            ephemeral=True
        )


# ============================================================
# BAN
# ============================================================

@bot.tree.command(
    name="ban",
    description="Ban a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to ban.",
    reason="Reason."
)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions.",
            ephemeral=True
        )

        return

    try:

        await member.ban(
            reason=reason
        )

        await interaction.response.send_message(
            f"🔨 Banned **{member}**.\n"
            f"Reason: **{reason}**"
        )

        await send_mod_log(
            interaction.guild,
            (
                f"🔨 **Ban**\n"
                f"Member: {member} (`{member.id}`)\n"
                f"Moderator: {interaction.user.mention}\n"
                f"Reason: {reason}"
            )
        )

    except discord.HTTPException as e:

        await interaction.response.send_message(
            f"❌ Could not ban member: `{e}`",
            ephemeral=True
        )


# ============================================================
# TICKET SYSTEM
# ============================================================

class TicketPanel(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Support Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="glowai_open_ticket"
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        guild = interaction.guild
        member = interaction.user

        if guild is None:
            return

        # Check if user already has a ticket.
        existing = discord.utils.get(
            guild.text_channels,
            name=f"ticket-{member.id}"
        )

        if existing:

            await interaction.response.send_message(
                f"❌ You already have a ticket: {existing.mention}",
                ephemeral=True
            )

            return

        category = None

        if SUPPORT_CATEGORY_ID:

            channel = guild.get_channel(
                SUPPORT_CATEGORY_ID
            )

            if isinstance(
                channel,
                discord.CategoryChannel
            ):
                category = channel

        overwrites = {

            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=False
                ),

            member:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                ),

            guild.me:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True
                )
        }

        staff_role = guild.get_role(
            STAFF_ROLE_ID
        )

        if staff_role:

            overwrites[staff_role] = (
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
            )

        try:

            channel = await guild.create_text_channel(
                name=f"ticket-{member.id}",
                category=category,
                overwrites=overwrites,
                reason="Glow AI support ticket"
            )

        except discord.HTTPException as e:

            await interaction.response.send_message(
                f"❌ Could not create ticket: `{e}`",
                ephemeral=True
            )

            return

        embed = discord.Embed(
            title="🎫 Glow AI Support",
            description=(
                f"Welcome {member.mention}!\n\n"
                "Please explain what you need help with. "
                "A member of the Glow AI team will assist you."
            ),
            color=0x5865F2
        )

        await channel.send(
            content=(
                member.mention
                + (
                    f" {staff_role.mention}"
                    if staff_role
                    else ""
                )
            ),
            embed=embed,
            view=CloseTicketView()
        )

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {channel.mention}",
            ephemeral=True
        )


class CloseTicketView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="glowai_close_ticket"
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await staff_check(interaction):

            await interaction.response.send_message(
                "❌ Only staff can close tickets.",
                ephemeral=True
            )

            return

        await interaction.response.send_message(
            "🔒 Closing this ticket in 3 seconds..."
        )

        await asyncio.sleep(3)

        try:

            await interaction.channel.delete(
                reason="Glow AI ticket closed"
            )

        except discord.HTTPException:
            pass


@bot.tree.command(
    name="setup-ticket",
    description="Post the Glow AI support ticket panel.",
    guild=discord.Object(id=GUILD_ID)
)
async def setup_ticket(
    interaction: discord.Interaction
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions.",
            ephemeral=True
        )

        return

    embed = discord.Embed(
        title="🎫 Glow AI Support",
        description=(
            "Need help with Glow AI?\n\n"
            "Click the button below to open a private "
            "support ticket with the Glow AI team."
        ),
        color=0x5865F2
    )

    await interaction.response.send_message(
        embed=embed,
        view=TicketPanel()
    )


# ============================================================
# GIVEAWAY
# ============================================================

class GiveawayView(discord.ui.View):

    def __init__(self, giveaway_id: int):

        super().__init__(timeout=None)

        self.giveaway_id = giveaway_id

    @discord.ui.button(
        label="Enter Giveaway",
        style=discord.ButtonStyle.success,
        emoji="🎉",
        custom_id="glowai_giveaway_enter"
    )
    async def enter(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        giveaway = giveaways.get(
            self.giveaway_id
        )

        if giveaway is None:

            await interaction.response.send_message(
                "❌ This giveaway is no longer active.",
                ephemeral=True
            )

            return

        if interaction.user.bot:

            await interaction.response.send_message(
                "❌ Bots cannot enter giveaways.",
                ephemeral=True
            )

            return

        giveaway["entries"].add(
            interaction.user.id
        )

        await interaction.response.send_message(
            "🎉 You are entered into the giveaway!",
            ephemeral=True
        )


async def finish_giveaway(giveaway_id: int):

    giveaway = giveaways.get(
        giveaway_id
    )

    if giveaway is None:
        return

    channel = bot.get_channel(
        giveaway["channel_id"]
    )

    if channel is None:
        giveaways.pop(giveaway_id, None)
        return

    entries = list(
        giveaway["entries"]
    )

    winners_count = min(
        giveaway["winners"],
        len(entries)
    )

    if winners_count == 0:

        winner_text = "No valid entries."

        winners = []

    else:

        winner_ids = random.sample(
            entries,
            winners_count
        )

        winners = []

        for user_id in winner_ids:

            try:

                user = await bot.fetch_user(
                    user_id
                )

                winners.append(user)

            except discord.HTTPException:
                pass

        if winners:

            winner_text = ", ".join(
                user.mention
                for user in winners
            )

        else:

            winner_text = "No valid winners."

    embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=(
            f"**Prize:** {giveaway['prize']}\n\n"
            f"🏆 **Winner(s):** {winner_text}"
        ),
        color=0xED4245
    )

    try:

        await channel.send(
            content=(
                f"🎉 Giveaway ended! "
                f"{winner_text}"
            ),
            embed=embed
        )

    except discord.HTTPException:
        pass

    giveaways.pop(
        giveaway_id,
        None
    )


@bot.tree.command(
    name="giveaway",
    description="Start a giveaway.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    duration="Examples: 10m, 2h, 1d, 1w.",
    prize="The giveaway prize.",
    winners="Number of winners, from 1 to 20."
)
async def giveaway(
    interaction: discord.Interaction,
    duration: str,
    prize: str,
    winners: int = 1
):

    if not await staff_check(interaction):

        await interaction.response.send_message(
            "❌ You need staff permissions.",
            ephemeral=True
        )

        return

    seconds = parse_duration(
        duration
    )

    if seconds is None:

        await interaction.response.send_message(
            "❌ Duration must be between "
            "10 seconds and 7 days, like "
            "`10m`, `2h`, or `1d`.",
            ephemeral=True
        )

        return

    if winners < 1 or winners > 20:

        await interaction.response.send_message(
            "❌ Winners must be between 1 and 20.",
            ephemeral=True
        )

        return

    # Respond immediately so Discord does not
    # expire the interaction.
    await interaction.response.defer(
        ephemeral=True
    )

    global next_giveaway_id

    giveaway_id = next_giveaway_id
    next_giveaway_id += 1

    ends_at = utc_now() + timedelta(
        seconds=seconds
    )

    giveaway_data = {
        "id": giveaway_id,
        "channel_id": interaction.channel.id,
        "prize": prize,
        "winners": winners,
        "entries": set(),
        "ends_at": ends_at
    }

    giveaways[giveaway_id] = giveaway_data

    timestamp = int(
        ends_at.timestamp()
    )

    embed = discord.Embed(
        title="🎉 GIVEAWAY",
        description=(
            f"## {prize}\n\n"
            f"🎁 **Winners:** {winners}\n"
            f"⏰ **Ends:** <t:{timestamp}:R>\n\n"
            "Click **Enter Giveaway** below to enter!"
        ),
        color=0x5865F2
    )

    embed.set_footer(
        text=f"Glow AI Giveaway #{giveaway_id}"
    )

    try:

        await interaction.channel.send(
            embed=embed,
            view=GiveawayView(giveaway_id)
        )

        await interaction.followup.send(
            "✅ Giveaway created!",
            ephemeral=True
        )

    except discord.HTTPException as e:

        giveaways.pop(
            giveaway_id,
            None
        )

        await interaction.followup.send(
            f"❌ Could not create giveaway: `{e}`",
            ephemeral=True
        )

        return

    await asyncio.sleep(seconds)

    await finish_giveaway(
        giveaway_id
    )


# ============================================================
# COMMAND ERROR HANDLING
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    print(
        f"Slash command error: {error}"
    )

    # Don't attempt a second response if
    # Discord has already expired the interaction.
    try:

        if interaction.response.is_done():

            await interaction.followup.send(
                "❌ Something went wrong while running that command.",
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                "❌ Something went wrong while running that command.",
                ephemeral=True
            )

    except discord.NotFound:
        pass

    except discord.HTTPException:
        pass


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":

    print("Starting Glow AI Bot...")
    print(f"Web server port: {PORT}")
    print("Connecting to Discord...")

    bot.run(TOKEN)
