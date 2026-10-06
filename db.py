import aiosqlite
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "glowai.db"

async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS invite_stats (
            guild_id INTEGER NOT NULL,
            inviter_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            uses INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, inviter_id, code)
        );
        CREATE TABLE IF NOT EXISTS member_invites (
            guild_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            inviter_id INTEGER,
            code TEXT,
            joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, member_id)
        );
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        await db.commit()

async def record_invite(guild_id, inviter_id, code, uses):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO invite_stats(guild_id,inviter_id,code,uses) VALUES(?,?,?,?) ON CONFLICT(guild_id,inviter_id,code) DO UPDATE SET uses=excluded.uses", (guild_id, inviter_id, code, uses))
        await db.commit()

async def record_member_invite(guild_id, member_id, inviter_id, code):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO member_invites(guild_id,member_id,inviter_id,code) VALUES(?,?,?,?)", (guild_id, member_id, inviter_id, code))
        await db.commit()

async def leaderboard(guild_id, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT inviter_id, SUM(uses) FROM invite_stats WHERE guild_id=? GROUP BY inviter_id ORDER BY SUM(uses) DESC LIMIT ?", (guild_id, limit))
        return await cur.fetchall()

async def add_warning(guild_id, user_id, moderator_id, reason):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason) VALUES(?,?,?,?)", (guild_id,user_id,moderator_id,reason))
        await db.commit()

async def warning_count(guild_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?", (guild_id,user_id))
        return (await cur.fetchone())[0]
