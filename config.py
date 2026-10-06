import os
from dotenv import load_dotenv

load_dotenv()

def env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = env_int("GUILD_ID")
SUPPORT_CATEGORY_ID = env_int("SUPPORT_CATEGORY_ID")
TICKET_LOG_CHANNEL_ID = env_int("TICKET_LOG_CHANNEL_ID")
MOD_LOG_CHANNEL_ID = env_int("MOD_LOG_CHANNEL_ID")
WELCOME_CHANNEL_ID = env_int("WELCOME_CHANNEL_ID")
STAFF_ROLE_ID = env_int("STAFF_ROLE_ID")
TICKET_TRANSCRIPT_CHANNEL_ID = env_int("TICKET_TRANSCRIPT_CHANNEL_ID")
AUTO_MOD_ENABLED = os.getenv("AUTO_MOD_ENABLED", "true").lower() == "true"
