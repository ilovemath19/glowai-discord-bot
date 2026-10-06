# ============================================================
# GLOW AI - ALL-IN-ONE DISCORD BOT
# ============================================================
# IMPORTANT:
# - Keep your Discord bot token in Render Environment Variables.
# - Do NOT put the token inside this file.
# - Enable Server Members Intent + Message Content Intent.
# ============================================================

import os
import re
import random
import asyncio
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


# ============================================================
# ========================= CONFIG ============================
# ============================================================

# Your Discord server
GUILD_ID = 1548056750140432496

# Community role automatically given to new members
COMMUNITY_ROLE_ID = 1556523668815741000

# Staff role
STAFF_ROLE_ID = 1556521675523366992

# Support ticket category
SUPPORT_CATEGORY_ID = 1556856034461487134

# Moderation log channel
MOD_LOG_CHANNEL_ID = 1556523457397788682

# Set to 0 if you DON'T want welcome messages
WELCOME_CHANNEL_ID = 0

# AutoMod
AUTO_MOD_ENABLED = True

# Words/phrases AutoMod blocks
AUTO_MOD_BLOCKED_PHRASES = [
    "discord.gg/",
    "discord.com/invite/",
    "free nitro scam",
    "@everyone get free",
    "claim free nitro",
    "nitro giveaway link",
]

# Automatically timeout users caught by AutoMod?
AUTO_MOD_TIMEOUT = False

# AutoMod timeout length
AUTO_MOD_TIMEOUT_MINUTES = 10

# Ticket settings
TICKET_PREFIX = "ticket"
TICKET_MAX_PER_USER = 1

# Giveaway button
GIVEAWAY_DEFAULT_EMOJI = "🎉"

# Database
DATABASE_PATH = "data/glowai.db"

# Render gives us this automatically
PORT = int(os.getenv("PORT", "10000"))


# ============================================================
# ======================= ENVIRONMENT =========================
# ============================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing. Add DISCORD_TOKEN to Render Environment Variables."
    )


# ============================================================
# ========================= DATABASE ==========================
# ============================================================

os.makedirs("data", exist_ok=True)

db_lock = threading.Lock()

db = sqlite3.connect(
    DATABASE_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row


def db_execute(
    query,
    params=(),
    fetch=False,
    fetchone=False,
    commit=False
):
    with db_lock:

        cursor = db.cursor()

        cursor.execute(query, params)

        if commit:
            db.commit()

        if fetchone:
            return cursor.fetchone()

        if fetch:
            return cursor.fetchall()

        return None


def init_database():

    db_execute(
        """
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        commit=True
    )

    db_execute(
        """
        CREATE TABLE IF NOT EXISTS invite_users (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            inviter_id INTEGER,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
        """,
        commit=True
    )

    db_execute(
        """
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            host_id INTEGER NOT NULL,
            prize TEXT NOT NULL,
            winners INTEGER NOT NULL,
            end_time TEXT NOT NULL,
            ended INTEGER NOT NULL DEFAULT 0
        )
        """,
        commit=True
    )

    db_execute(
        """
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (giveaway_id, user_id)
        )
        """,
        commit=True
    )


init_database()


# ============================================================
# ========================= HELPERS ===========================
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def is_staff_member(member):

    if not isinstance(member, discord.Member):
        return False

    if member.guild_permissions.administrator:
        return True

    return any(
        role.id == STAFF_ROLE_ID
        for role in member.roles
    )


def parse_duration(value):

    match = re.fullmatch(
        r"(\d+)\s*([smhdw])",
        value.lower().strip()
    )

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800
    }

    return amount * multipliers[unit]


def format_duration(seconds):

    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds}s"

    if seconds < 3600:
        return f"{seconds // 60}m"

    if seconds < 86400:
        return f"{seconds // 3600}h"

    return f"{seconds // 86400}d"


async def safe_ephemeral(interaction, message):

    try:

        if interaction.response.is_done():

            await interaction.followup.send(
                message,
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                message,
                ephemeral=True
            )

    except Exception:
        pass


async def send_mod_log(
    guild,
    title,
    description,
    color=discord.Color.orange()
):

    channel = guild.get_channel(
        MOD_LOG_CHANNEL_ID
    )

    if not channel:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=utc_now()
    )

    try:
        await channel.send(
            embed=embed
        )
    except Exception:
        pass


def get_community_role(guild):

    return guild.get_role(
        COMMUNITY_ROLE_ID
    )


def get_staff_role(guild):

    return guild.get_role(
        STAFF_ROLE_ID
    )


def get_ticket_category(guild):

    category = guild.get_channel(
        SUPPORT_CATEGORY_ID
    )

    if isinstance(
        category,
        discord.CategoryChannel
    ):
        return category

    return None


def make_ticket_name(member):

    username = re.sub(
        r"[^a-zA-Z0-9-]",
        "",
        member.name.lower()
    )

    username = username[:20]

    if not username:
        username = "user"

    return f"{TICKET_PREFIX}-{username}"


# ============================================================
# ===================== RENDER HEALTH SERVER =================
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Glow AI Discord Bot is online."
        )

    def do_HEAD(self):

        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():

    try:

        server = ThreadingHTTPServer(
            ("0.0.0.0", PORT),
            HealthHandler
        )

        print(
            f"Health server listening on port {PORT}"
        )

        print(
            "Render health server is READY"
        )

        server.serve_forever()

    except Exception as error:

        print(
            f"Health server error: {error}"
        )


health_thread = threading.Thread(
    target=start_health_server,
    daemon=True
)

health_thread.start()


# ============================================================
# ========================== BOT ==============================
# ============================================================

intents = discord.Intents.default()

intents.members = True
intents.message_content = True


class GlowBot(commands.Bot):

    def __init__(self):

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )

        self.invite_cache = {}
        self.giveaway_tasks = {}

    async def setup_hook(self):

        # Persistent ticket buttons
        self.add_view(
            TicketPanel()
        )

        self.add_view(
            CloseTicketView()
        )

        # Restore giveaways
        await restore_giveaways()

        # Sync slash commands
        guild = discord.Object(
            id=GUILD_ID
        )

        try:

            synced = await self.tree.sync(
                guild=guild
            )

            print(
                f"Synced {len(synced)} slash commands."
            )

        except Exception as error:

            print(
                f"Slash command sync error: {error}"
            )


bot = GlowBot()


# ============================================================
# ========================= BOT READY =========================
# ============================================================

@bot.event
async def on_ready():

    print("=" * 60)

    print(
        f"Logged in as {bot.user}"
    )

    print(
        f"Bot ID: {bot.user.id}"
    )

    print(
        f"Connected to {len(bot.guilds)} server(s)"
    )

    print(
        "Glow AI Discord Bot is ONLINE."
    )

    print("=" * 60)

    guild = bot.get_guild(
        GUILD_ID
    )

    if guild:

        try:

            invites = await guild.invites()

            bot.invite_cache[guild.id] = {
                invite.code: invite.uses or 0
                for invite in invites
            }

        except Exception as error:

            print(
                f"Invite cache error: {error}"
            )


# ============================================================
# ======================= MEMBER JOIN ========================
# ============================================================

@bot.event
async def on_member_join(
    member: discord.Member
):

    guild = member.guild

    # --------------------------------------------------------
    # AUTOMATIC COMMUNITY ROLE
    # --------------------------------------------------------

    community_role = get_community_role(
        guild
    )

    if community_role:

        try:

            if community_role < guild.me.top_role:

                await member.add_roles(
                    community_role,
                    reason=(
                        "Automatic Community role "
                        "for new member"
                    )
                )

                print(
                    f"Community role given to "
                    f"{member} ({member.id})"
                )

            else:

                print(
                    "ERROR: Community role is above "
                    "the bot's highest role."
                )

        except discord.Forbidden:

            print(
                "ERROR: Discord denied role assignment."
            )

        except Exception as error:

            print(
                f"Community role error: {error}"
            )

    # --------------------------------------------------------
    # INVITE TRACKING
    # --------------------------------------------------------

    inviter_id = None

    try:

        old_invites = bot.invite_cache.get(
            guild.id,
            {}
        )

        new_invites = await guild.invites()

        for invite in new_invites:

            old_uses = old_invites.get(
                invite.code,
                0
            )

            new_uses = invite.uses or 0

            if new_uses > old_uses:

                if invite.inviter:
                    inviter_id = invite.inviter.id

                break

        bot.invite_cache[guild.id] = {
            invite.code: invite.uses or 0
            for invite in new_invites
        }

    except Exception as error:

        print(
            f"Invite tracking error: {error}"
        )

    db_execute(
        """
        INSERT OR REPLACE INTO invite_users
        (guild_id, user_id, inviter_id, joined_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            guild.id,
            member.id,
            inviter_id,
            iso_now()
        ),
        commit=True
    )

    # --------------------------------------------------------
    # OPTIONAL WELCOME MESSAGE
    # --------------------------------------------------------

    if WELCOME_CHANNEL_ID:

        channel = guild.get_channel(
            WELCOME_CHANNEL_ID
        )

        if channel:

            embed = discord.Embed(
                title="👋 Welcome!",
                description=(
                    f"Welcome {member.mention} "
                    f"to **{guild.name}**!\n\n"
                    "We're glad you're here."
                ),
                color=discord.Color.blurple()
            )

            try:
                await channel.send(
                    embed=embed
                )
            except Exception:
                pass


# ============================================================
# ======================= MEMBER LEAVE ========================
# ============================================================

@bot.event
async def on_member_remove(member):

    await send_mod_log(
        member.guild,
        "Member Left",
        (
            f"**User:** {member}\n"
            f"**ID:** `{member.id}`"
        ),
        discord.Color.red()
    )


# ============================================================
# ========================== AUTOMOD ==========================
# ============================================================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if (
        message.guild
        and AUTO_MOD_ENABLED
    ):

        content = message.content.lower()

        matched = None

        for phrase in AUTO_MOD_BLOCKED_PHRASES:

            if phrase.lower() in content:

                matched = phrase
                break

        if matched:

            try:
                await message.delete()
            except Exception:
                pass

            await send_mod_log(
                message.guild,
                "🚨 AutoMod Action",
                (
                    f"**User:** "
                    f"{message.author.mention}\n"
                    f"**Channel:** "
                    f"{message.channel.mention}\n"
                    f"**Matched:** `{matched}`"
                ),
                discord.Color.red()
            )

            if AUTO_MOD_TIMEOUT:

                try:

                    await message.author.timeout(
                        timedelta(
                            minutes=AUTO_MOD_TIMEOUT_MINUTES
                        ),
                        reason="AutoMod blocked message"
                    )

                except Exception:
                    pass

            return

    await bot.process_commands(
        message
    )


# ============================================================
# ========================== /PING ============================
# ============================================================

@bot.tree.command(
    name="ping",
    description="Check if the bot is online.",
    guild=discord.Object(id=GUILD_ID)
)
async def ping(
    interaction: discord.Interaction
):

    latency = round(
        bot.latency * 1000
    )

    await interaction.response.send_message(
        f"🏓 Pong! `{latency}ms`",
        ephemeral=True
    )


# ============================================================
# ========================== /HELP ============================
# ============================================================

@bot.tree.command(
    name="help",
    description="Show all bot commands.",
    guild=discord.Object(id=GUILD_ID)
)
async def help_command(
    interaction: discord.Interaction
):

    embed = discord.Embed(
        title="🤖 Glow AI Bot",
        description="All available commands:",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🎫 Support",
        value=(
            "`/setup-ticket`\n"
            "`/close-ticket`"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Moderation",
        value=(
            "`/warn`\n"
            "`/warnings`\n"
            "`/clearwarnings`\n"
            "`/timeout`\n"
            "`/kick`\n"
            "`/ban`\n"
            "`/clear`\n"
            "`/lock`\n"
            "`/unlock`\n"
            "`/slowmode`"
        ),
        inline=False
    )

    embed.add_field(
        name="👤 Information",
        value=(
            "`/userinfo`\n"
            "`/serverinfo`\n"
            "`/invites`\n"
            "`/invite-leaderboard`"
        ),
        inline=False
    )

    embed.add_field(
        name="🎉 Giveaways",
        value="`/giveaway`",
        inline=False
    )

    embed.add_field(
        name="📢 Management",
        value=(
            "`/embed`\n"
            "`/say`\n"
            "`/give-community`"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# ======================== /USERINFO ==========================
# ============================================================

@bot.tree.command(
    name="userinfo",
    description="Show information about a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to inspect."
)
async def userinfo(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    member = member or interaction.user

    roles = [
        role.mention
        for role in reversed(member.roles)
        if role != interaction.guild.default_role
    ]

    role_text = (
        ", ".join(roles[:20])
        if roles
        else "None"
    )

    embed = discord.Embed(
        title=f"👤 {member}",
        color=(
            member.color
            if member.color.value
            else discord.Color.blurple()
        )
    )

    embed.set_thumbnail(
        url=member.display_avatar.url
    )

    embed.add_field(
        name="User ID",
        value=f"`{member.id}`",
        inline=False
    )

    embed.add_field(
        name="Account Created",
        value=discord.utils.format_dt(
            member.created_at,
            "F"
        ),
        inline=False
    )

    if member.joined_at:

        embed.add_field(
            name="Joined Server",
            value=discord.utils.format_dt(
                member.joined_at,
                "F"
            ),
            inline=False
        )

    embed.add_field(
        name="Roles",
        value=role_text,
        inline=False
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# ======================= /SERVERINFO =========================
# ============================================================

@bot.tree.command(
    name="serverinfo",
    description="Show server information.",
    guild=discord.Object(id=GUILD_ID)
)
async def serverinfo(
    interaction: discord.Interaction
):

    guild = interaction.guild

    embed = discord.Embed(
        title=f"📊 {guild.name}",
        color=discord.Color.blurple()
    )

    if guild.icon:

        embed.set_thumbnail(
            url=guild.icon.url
        )

    embed.add_field(
        name="Members",
        value=str(guild.member_count),
        inline=True
    )

    embed.add_field(
        name="Channels",
        value=str(len(guild.channels)),
        inline=True
    )

    embed.add_field(
        name="Roles",
        value=str(len(guild.roles)),
        inline=True
    )

    embed.add_field(
        name="Owner",
        value=f"<@{guild.owner_id}>",
        inline=True
    )

    embed.add_field(
        name="Server ID",
        value=f"`{guild.id}`",
        inline=True
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# ========================= /INVITES ==========================
# ============================================================

@bot.tree.command(
    name="invites",
    description="Check tracked invites.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to check."
)
async def invites(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    member = member or interaction.user

    row = db_execute(
        """
        SELECT COUNT(*) AS total
        FROM invite_users
        WHERE guild_id = ?
        AND inviter_id = ?
        """,
        (
            interaction.guild.id,
            member.id
        ),
        fetchone=True
    )

    total = row["total"] if row else 0

    await interaction.response.send_message(
        f"📨 **{member.display_name}** has "
        f"**{total}** tracked invite(s).",
        ephemeral=True
    )


# ============================================================
# =================== /INVITE-LEADERBOARD =====================
# ============================================================

@bot.tree.command(
    name="invite-leaderboard",
    description="Show invite leaderboard.",
    guild=discord.Object(id=GUILD_ID)
)
async def invite_leaderboard(
    interaction: discord.Interaction
):

    rows = db_execute(
        """
        SELECT inviter_id, COUNT(*) AS total
        FROM invite_users
        WHERE guild_id = ?
        AND inviter_id IS NOT NULL
        GROUP BY inviter_id
        ORDER BY total DESC
        LIMIT 10
        """,
        (interaction.guild.id,),
        fetch=True
    )

    if not rows:

        await interaction.response.send_message(
            "No tracked invites yet."
        )

        return

    lines = []

    for index, row in enumerate(
        rows,
        start=1
    ):

        member = interaction.guild.get_member(
            row["inviter_id"]
        )

        if member:

            name = member.mention

        else:

            name = f"<@{row['inviter_id']}>"

        lines.append(
            f"**{index}.** {name} — `{row['total']}`"
        )

    embed = discord.Embed(
        title="🏆 Invite Leaderboard",
        description="\n".join(lines),
        color=discord.Color.gold()
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# ===================== /GIVE-COMMUNITY =======================
# ============================================================

@bot.tree.command(
    name="give-community",
    description="Give Community role to a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member who should receive the role."
)
async def give_community(
    interaction: discord.Interaction,
    member: discord.Member
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    role = get_community_role(
        interaction.guild
    )

    if not role:

        await safe_ephemeral(
            interaction,
            "❌ Community role was not found."
        )

        return

    try:

        await member.add_roles(
            role,
            reason=(
                f"Community role manually "
                f"added by {interaction.user}"
            )
        )

        await interaction.response.send_message(
            f"✅ Gave {role.mention} "
            f"to {member.mention}."
        )

    except discord.Forbidden:

        await safe_ephemeral(
            interaction,
            "❌ I cannot give that role. "
            "Move the bot role above the Community role."
        )


# ============================================================
# ========================== /WARN ============================
# ============================================================

@bot.tree.command(
    name="warn",
    description="Warn a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to warn.",
    reason="Reason for warning."
)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    db_execute(
        """
        INSERT INTO warnings
        (
            guild_id,
            user_id,
            moderator_id,
            reason,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            interaction.guild.id,
            member.id,
            interaction.user.id,
            reason,
            iso_now()
        ),
        commit=True
    )

    row = db_execute(
        """
        SELECT COUNT(*) AS total
        FROM warnings
        WHERE guild_id = ?
        AND user_id = ?
        """,
        (
            interaction.guild.id,
            member.id
        ),
        fetchone=True
    )

    total = (
        row["total"]
        if row
        else 1
    )

    await interaction.response.send_message(
        f"⚠️ Warned {member.mention}.\n"
        f"**Reason:** {reason}\n"
        f"**Total warnings:** `{total}`"
    )

    await send_mod_log(
        interaction.guild,
        "⚠️ Member Warned",
        (
            f"**Member:** {member.mention}\n"
            f"**Moderator:** {interaction.user.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Total warnings:** `{total}`"
        )
    )


# ============================================================
# ======================== /WARNINGS ==========================
# ============================================================

@bot.tree.command(
    name="warnings",
    description="View a member's warnings.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to check."
)
async def warnings(
    interaction: discord.Interaction,
    member: discord.Member
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    rows = db_execute(
        """
        SELECT *
        FROM warnings
        WHERE guild_id = ?
        AND user_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (
            interaction.guild.id,
            member.id
        ),
        fetch=True
    )

    if not rows:

        await interaction.response.send_message(
            f"✅ {member.mention} has no warnings.",
            ephemeral=True
        )

        return

    lines = []

    for row in rows:

        lines.append(
            f"**#{row['id']}** — {row['reason']}\n"
            f"Moderator: <@{row['moderator_id']}>"
        )

    embed = discord.Embed(
        title=f"⚠️ Warnings — {member}",
        description="\n\n".join(lines),
        color=discord.Color.orange()
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# ===================== /CLEARWARNINGS ========================
# ============================================================

@bot.tree.command(
    name="clearwarnings",
    description="Clear all warnings for a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member whose warnings should be cleared."
)
async def clearwarnings(
    interaction: discord.Interaction,
    member: discord.Member
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    db_execute(
        """
        DELETE FROM warnings
        WHERE guild_id = ?
        AND user_id = ?
        """,
        (
            interaction.guild.id,
            member.id
        ),
        commit=True
    )

    await interaction.response.send_message(
        f"✅ Cleared all warnings for "
        f"{member.mention}."
    )


# ============================================================
# ======================== /TIMEOUT ===========================
# ============================================================

@bot.tree.command(
    name="timeout",
    description="Timeout a member.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    member="Member to timeout.",
    duration="Examples: 10m, 2h, 1d.",
    reason="Reason."
)
async def timeout_member(
    interaction: discord.Interaction,
    member: discord.Member,
    duration: str,
    reason: str
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    seconds = parse_duration(
        duration
    )

    if not seconds:

        await safe_ephemeral(
            interaction,
            "❌ Use durations like `30s`, `10m`, `2h`, or `1d`."
        )

        return

    if seconds > 28 * 86400:

        await safe_ephemeral(
            interaction,
            "❌ Maximum timeout is 28 days."
        )

        return

    try:

        await member.timeout(
            timedelta(
                seconds=seconds
            ),
            reason=reason
        )

        await interaction.response.send_message(
            f"⏳ Timed out {member.mention} "
            f"for `{format_duration(seconds)}`.\n"
            f"**Reason:** {reason}"
        )

        await send_mod_log(
            interaction.guild,
            "⏳ Member Timed Out",
            (
                f"**Member:** {member.mention}\n"
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Duration:** {format_duration(seconds)}\n"
                f"**Reason:** {reason}"
            )
        )

    except discord.Forbidden:

        await safe_ephemeral(
            interaction,
            "❌ I cannot timeout that member. "
            "Check role hierarchy."
        )


# ============================================================
# =========================== /KICK ===========================
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
async def kick_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    try:

        await member.kick(
            reason=reason
        )

        await interaction.response.send_message(
            f"👢 Kicked {member.mention}.\n"
            f"**Reason:** {reason}"
        )

        await send_mod_log(
            interaction.guild,
            "👢 Member Kicked",
            (
                f"**Member:** {member}\n"
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Reason:** {reason}"
            )
        )

    except discord.Forbidden:

        await safe_ephemeral(
            interaction,
            "❌ I cannot kick that member."
        )


# ============================================================
# ============================ /BAN ===========================
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
async def ban_member(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    try:

        await member.ban(
            reason=reason,
            delete_message_days=1
        )

        await interaction.response.send_message(
            f"🔨 Banned {member.mention}.\n"
            f"**Reason:** {reason}"
        )

        await send_mod_log(
            interaction.guild,
            "🔨 Member Banned",
            (
                f"**Member:** {member}\n"
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Reason:** {reason}"
            ),
            discord.Color.red()
        )

    except discord.Forbidden:

        await safe_ephemeral(
            interaction,
            "❌ I cannot ban that member."
        )


# ============================================================
# ============================ /CLEAR =========================
# ============================================================

@bot.tree.command(
    name="clear",
    description="Delete messages.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    amount="Number of messages, 1-100."
)
async def clear_messages(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 100]
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    if not isinstance(
        interaction.channel,
        discord.TextChannel
    ):

        await safe_ephemeral(
            interaction,
            "❌ Text channels only."
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    try:

        deleted = await interaction.channel.purge(
            limit=amount
        )

        await interaction.followup.send(
            f"🧹 Deleted `{len(deleted)}` messages.",
            ephemeral=True
        )

        await send_mod_log(
            interaction.guild,
            "🧹 Messages Cleared",
            (
                f"**Channel:** "
                f"{interaction.channel.mention}\n"
                f"**Moderator:** "
                f"{interaction.user.mention}\n"
                f"**Amount:** `{len(deleted)}`"
            )
        )

    except discord.Forbidden:

        await interaction.followup.send(
            "❌ I cannot delete messages.",
            ephemeral=True
        )


# ============================================================
# ============================ /LOCK ==========================
# ============================================================

@bot.tree.command(
    name="lock",
    description="Lock the current channel.",
    guild=discord.Object(id=GUILD_ID)
)
async def lock_channel(
    interaction: discord.Interaction
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    channel = interaction.channel

    if not isinstance(
        channel,
        discord.TextChannel
    ):

        await safe_ephemeral(
            interaction,
            "❌ Text channels only."
        )

        return

    await channel.set_permissions(
        interaction.guild.default_role,
        send_messages=False,
        reason=f"Locked by {interaction.user}"
    )

    await interaction.response.send_message(
        "🔒 This channel has been locked."
    )


# ============================================================
# =========================== /UNLOCK =========================
# ============================================================

@bot.tree.command(
    name="unlock",
    description="Unlock the current channel.",
    guild=discord.Object(id=GUILD_ID)
)
async def unlock_channel(
    interaction: discord.Interaction
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    channel = interaction.channel

    if not isinstance(
        channel,
        discord.TextChannel
    ):

        await safe_ephemeral(
            interaction,
            "❌ Text channels only."
        )

        return

    await channel.set_permissions(
        interaction.guild.default_role,
        send_messages=None,
        reason=f"Unlocked by {interaction.user}"
    )

    await interaction.response.send_message(
        "🔓 This channel has been unlocked."
    )


# ============================================================
# ========================== /SLOWMODE ========================
# ============================================================

@bot.tree.command(
    name="slowmode",
    description="Set channel slowmode.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    seconds="0 disables slowmode. Maximum 21600."
)
async def slowmode(
    interaction: discord.Interaction,
    seconds: app_commands.Range[int, 0, 21600]
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    if not isinstance(
        interaction.channel,
        discord.TextChannel
    ):

        await safe_ephemeral(
            interaction,
            "❌ Text channels only."
        )

        return

    await interaction.channel.edit(
        slowmode_delay=seconds,
        reason=f"Slowmode changed by {interaction.user}"
    )

    await interaction.response.send_message(
        f"🐌 Slowmode set to `{seconds}` seconds."
    )


# ============================================================
# ============================ /SAY ===========================
# ============================================================

@bot.tree.command(
    name="say",
    description="Send a message as the bot.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    message="Message to send."
)
async def say(
    interaction: discord.Interaction,
    message: str
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    await interaction.channel.send(
        message
    )

    await interaction.followup.send(
        "✅ Message sent.",
        ephemeral=True
    )


# ============================================================
# =========================== /EMBED ==========================
# ============================================================

@bot.tree.command(
    name="embed",
    description="Send an embedded announcement.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    title="Embed title.",
    message="Embed description."
)
async def embed_command(
    interaction: discord.Interaction,
    title: str,
    message: str
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    embed = discord.Embed(
        title=title,
        description=message,
        color=discord.Color.blurple(),
        timestamp=utc_now()
    )

    embed.set_footer(
        text=(
            f"Posted by "
            f"{interaction.user.display_name}"
        )
    )

    await interaction.response.send_message(
        "✅ Embed sent.",
        ephemeral=True
    )

    await interaction.channel.send(
        embed=embed
    )


# ============================================================
# ======================== TICKET PANEL =======================
# ============================================================

class TicketPanel(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

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

        category = get_ticket_category(
            guild
        )

        if not category:

            await safe_ephemeral(
                interaction,
                "❌ Support category not found."
            )

            return

        existing = [

            channel

            for channel in category.channels

            if (
                isinstance(
                    channel,
                    discord.TextChannel
                )
                and channel.topic
                and f"ticket-owner:{member.id}"
                in channel.topic
            )

        ]

        if len(existing) >= TICKET_MAX_PER_USER:

            await safe_ephemeral(
                interaction,
                f"❌ You already have a ticket: "
                f"{existing[0].mention}"
            )

            return

        staff_role = get_staff_role(
            guild
        )

        overwrites = {

            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=False
                ),

            member:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True
                )
        }

        if staff_role:

            overwrites[staff_role] = (
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True
                )
            )

        channel = await guild.create_text_channel(

            make_ticket_name(member),

            category=category,

            overwrites=overwrites,

            topic=f"ticket-owner:{member.id}",

            reason=(
                f"Support ticket opened by "
                f"{member}"
            )
        )

        embed = discord.Embed(
            title="🎫 Support Ticket",
            description=(
                f"Hello {member.mention}!\n\n"
                "Please explain your issue and "
                "a staff member will help you.\n\n"
                "Use the button below when you "
                "are finished."
            ),
            color=discord.Color.blurple()
        )

        await channel.send(
            content=member.mention,
            embed=embed,
            view=CloseTicketView()
        )

        await interaction.response.send_message(
            f"✅ Ticket created: "
            f"{channel.mention}",
            ephemeral=True
        )

        await send_mod_log(
            guild,
            "🎫 Ticket Opened",
            (
                f"**User:** {member.mention}\n"
                f"**Channel:** {channel.mention}"
            ),
            discord.Color.green()
        )


# ============================================================
# ====================== CLOSE TICKET VIEW ====================
# ============================================================

class CloseTicketView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

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

        channel = interaction.channel

        if not isinstance(
            channel,
            discord.TextChannel
        ):

            await safe_ephemeral(
                interaction,
                "❌ Not a ticket channel."
            )

            return

        if not channel.topic:

            await safe_ephemeral(
                interaction,
                "❌ Not a ticket channel."
            )

            return

        owner_text = (
            f"ticket-owner:"
            f"{interaction.user.id}"
        )

        if not (
            is_staff_member(
                interaction.user
            )
            or owner_text in channel.topic
        ):

            await safe_ephemeral(
                interaction,
                "❌ Only the ticket owner "
                "or staff can close it."
            )

            return

        await interaction.response.send_message(
            "🔒 Closing this ticket in 5 seconds..."
        )

        await send_mod_log(
            interaction.guild,
            "🔒 Ticket Closed",
            (
                f"**Channel:** {channel.name}\n"
                f"**Closed by:** "
                f"{interaction.user.mention}"
            ),
            discord.Color.red()
        )

        await asyncio.sleep(5)

        try:

            await channel.delete(
                reason=(
                    f"Ticket closed by "
                    f"{interaction.user}"
                )
            )

        except Exception:
            pass


# ============================================================
# ======================= /SETUP-TICKET =======================
# ============================================================

@bot.tree.command(
    name="setup-ticket",
    description="Create the support ticket panel.",
    guild=discord.Object(id=GUILD_ID)
)
async def setup_ticket(
    interaction: discord.Interaction
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    embed = discord.Embed(
        title="🎫 Glow AI Support",
        description=(
            "Need help?\n\n"
            "Click the button below to "
            "open a private support ticket."
        ),
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(
        embed=embed,
        view=TicketPanel()
    )


# ============================================================
# ======================= /CLOSE-TICKET =======================
# ============================================================

@bot.tree.command(
    name="close-ticket",
    description="Close the current ticket.",
    guild=discord.Object(id=GUILD_ID)
)
async def close_ticket_command(
    interaction: discord.Interaction
):

    channel = interaction.channel

    if not isinstance(
        channel,
        discord.TextChannel
    ):

        await safe_ephemeral(
            interaction,
            "❌ Not a ticket channel."
        )

        return

    if (
        not channel.topic
        or "ticket-owner:" not in channel.topic
    ):

        await safe_ephemeral(
            interaction,
            "❌ Not a ticket channel."
        )

        return

    if not (
        is_staff_member(
            interaction.user
        )
        or f"ticket-owner:{interaction.user.id}"
        in channel.topic
    ):

        await safe_ephemeral(
            interaction,
            "❌ Only the ticket owner "
            "or staff can close it."
        )

        return

    await interaction.response.send_message(
        "🔒 Closing ticket in 5 seconds..."
    )

    await asyncio.sleep(5)

    try:

        await channel.delete(
            reason=(
                f"Ticket closed by "
                f"{interaction.user}"
            )
        )

    except Exception:
        pass


# ============================================================
# ======================== GIVEAWAYS ==========================
# ============================================================

def giveaway_end_datetime(row):

    return datetime.fromisoformat(
        row["end_time"]
    )


class GiveawayView(
    discord.ui.View
):

    def __init__(
        self,
        giveaway_id
    ):

        super().__init__(
            timeout=None
        )

        button = discord.ui.Button(
            label="Enter Giveaway",
            emoji=GIVEAWAY_DEFAULT_EMOJI,
            style=discord.ButtonStyle.success,
            custom_id=(
                f"glowai_giveaway:"
                f"{giveaway_id}"
            )
        )

        async def callback(
            interaction
        ):

            await giveaway_enter(
                interaction,
                giveaway_id
            )

        button.callback = callback

        self.add_item(
            button
        )


async def giveaway_enter(
    interaction,
    giveaway_id
):

    row = db_execute(
        """
        SELECT *
        FROM giveaways
        WHERE id = ?
        """,
        (giveaway_id,),
        fetchone=True
    )

    if not row or row["ended"]:

        await safe_ephemeral(
            interaction,
            "❌ This giveaway has ended."
        )

        return

    if (
        utc_now()
        >= giveaway_end_datetime(row)
    ):

        await safe_ephemeral(
            interaction,
            "❌ This giveaway has ended."
        )

        return

    existing = db_execute(
        """
        SELECT 1
        FROM giveaway_entries
        WHERE giveaway_id = ?
        AND user_id = ?
        """,
        (
            giveaway_id,
            interaction.user.id
        ),
        fetchone=True
    )

    if existing:

        await safe_ephemeral(
            interaction,
            "❌ You are already entered."
        )

        return

    db_execute(
        """
        INSERT INTO giveaway_entries
        (giveaway_id, user_id)
        VALUES (?, ?)
        """,
        (
            giveaway_id,
            interaction.user.id
        ),
        commit=True
    )

    await safe_ephemeral(
        interaction,
        "🎉 You entered the giveaway!"
    )


async def finish_giveaway(
    giveaway_id
):

    row = db_execute(
        """
        SELECT *
        FROM giveaways
        WHERE id = ?
        """,
        (giveaway_id,),
        fetchone=True
    )

    if not row or row["ended"]:
        return

    wait_seconds = (
        giveaway_end_datetime(row)
        - utc_now()
    ).total_seconds()

    if wait_seconds > 0:

        await asyncio.sleep(
            wait_seconds
        )

    row = db_execute(
        """
        SELECT *
        FROM giveaways
        WHERE id = ?
        """,
        (giveaway_id,),
        fetchone=True
    )

    if not row or row["ended"]:
        return

    entries = db_execute(
        """
        SELECT user_id
        FROM giveaway_entries
        WHERE giveaway_id = ?
        """,
        (giveaway_id,),
        fetch=True
    )

    channel = bot.get_channel(
        row["channel_id"]
    )

    winner_ids = []

    if entries:

        winner_count = min(
            row["winners"],
            len(entries)
        )

        winner_ids = random.sample(
            [
                entry["user_id"]
                for entry in entries
            ],
            winner_count
        )

    db_execute(
        """
        UPDATE giveaways
        SET ended = 1
        WHERE id = ?
        """,
        (giveaway_id,),
        commit=True
    )

    if not channel:
        return

    if winner_ids:

        winners = ", ".join(
            f"<@{user_id}>"
            for user_id in winner_ids
        )

        message = (
            "🎉 **GIVEAWAY ENDED!**\n\n"
            f"**Prize:** {row['prize']}\n"
            f"**Winner(s):** {winners}\n\n"
            "Congratulations! 🎊"
        )

    else:

        message = (
            "🎉 **GIVEAWAY ENDED!**\n\n"
            f"**Prize:** {row['prize']}\n\n"
            "There were no entries."
        )

    try:

        await channel.send(
            message
        )

    except Exception:
        pass


async def restore_giveaways():

    rows = db_execute(
        """
        SELECT *
        FROM giveaways
        WHERE ended = 0
        """,
        fetch=True
    )

    for row in rows:

        try:

            bot.add_view(
                GiveawayView(
                    row["id"]
                ),
                message_id=row["message_id"]
            )

        except Exception:
            pass

        task = asyncio.create_task(
            finish_giveaway(
                row["id"]
            )
        )

        bot.giveaway_tasks[
            row["id"]
        ] = task


# ============================================================
# ========================== /GIVEAWAY =========================
# ============================================================

@bot.tree.command(
    name="giveaway",
    description="Start a giveaway.",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    duration="Examples: 10m, 1h, 1d.",
    winners="Number of winners.",
    prize="What are you giving away?"
)
async def giveaway(
    interaction: discord.Interaction,
    duration: str,
    winners: app_commands.Range[int, 1, 20],
    prize: str
):

    if not is_staff_member(
        interaction.user
    ):

        await safe_ephemeral(
            interaction,
            "❌ Staff only."
        )

        return

    seconds = parse_duration(
        duration
    )

    if not seconds or seconds < 10:

        await safe_ephemeral(
            interaction,
            "❌ Use `10m`, `1h`, `1d`, etc. "
            "Minimum is 10 seconds."
        )

        return

    end_time = (
        utc_now()
        + timedelta(seconds=seconds)
    )

    db_execute(
        """
        INSERT INTO giveaways
        (
            guild_id,
            channel_id,
            message_id,
            host_id,
            prize,
            winners,
            end_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interaction.guild.id,
            interaction.channel.id,
            0,
            interaction.user.id,
            prize,
            winners,
            end_time.isoformat()
        ),
        commit=True
    )

    row = db_execute(
        """
        SELECT id
        FROM giveaways
        WHERE guild_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (interaction.guild.id,),
        fetchone=True
    )

    giveaway_id = row["id"]

    embed = discord.Embed(
        title=f"{GIVEAWAY_DEFAULT_EMOJI} GIVEAWAY",
        description=(
            f"**Prize:** {prize}\n"
            f"**Winners:** `{winners}`\n"
            f"**Ends:** "
            f"{discord.utils.format_dt(end_time, 'R')}\n\n"
            "Click the button below to enter!"
        ),
        color=discord.Color.gold()
    )

    embed.set_footer(
        text=(
            f"Hosted by "
            f"{interaction.user.display_name}"
        )
    )

    view = GiveawayView(
        giveaway_id
    )

    await interaction.response.send_message(
        embed=embed,
        view=view
    )

    message = await interaction.original_response()

    db_execute(
        """
        UPDATE giveaways
        SET message_id = ?
        WHERE id = ?
        """,
        (
            message.id,
            giveaway_id
        ),
        commit=True
    )

    task = asyncio.create_task(
        finish_giveaway(
            giveaway_id
        )
    )

    bot.giveaway_tasks[
        giveaway_id
    ] = task


# ============================================================
# ======================= ERROR HANDLER =======================
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction,
    error
):

    print(
        f"Slash command error: {repr(error)}"
    )

    await safe_ephemeral(
        interaction,
        "❌ Something went wrong while running that command."
    )


# ============================================================
# ============================ START ==========================
# ============================================================

if __name__ == "__main__":

    try:

        bot.run(
            DISCORD_TOKEN
        )

    except KeyboardInterrupt:

        pass

    except Exception as error:

        print(
            f"Bot stopped with error: {error}"
        )

        raise
