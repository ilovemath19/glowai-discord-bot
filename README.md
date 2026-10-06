# Glow AI Discord Bot

A Discord.py bot for Glow AI with:

- 🎫 Private support tickets with staff permissions
- 🛡️ Moderation slash commands: `/warn`, `/timeout`, `/kick`, `/ban`
- 🤖 Lightweight AutoMod for common invite/scam spam
- 📨 Automatic invite attribution and persistent invite statistics
- 📈 `/invites` and `/invite-leaderboard`
- 📝 SQLite persistence (no external database required)
- ⚙️ Environment-based configuration

## Discord Developer Portal setup

Create a bot at the Discord Developer Portal and enable these **Privileged Gateway Intents**:

- Server Members Intent
- Message Content Intent

Invite the bot with the scopes `bot` + `applications.commands` and permissions appropriate for your server. For invite attribution, the bot needs permission to view/manage server invites (Manage Server is the simplest route).

## Install

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows
# cp .env.example .env  # macOS/Linux
```

Fill in `.env`, then:

```bash
python bot.py
```

## First-time server setup

1. Create a support category and staff role.
2. Put their IDs in `.env`.
3. Start the bot.
4. Run `/setup-ticket` in your support channel.
5. Set a mod-log and welcome channel if desired.

## Production notes

For reliable invite tracking, keep the bot online continuously and give it permission to read invites. Discord does not provide a universal historical "who invited whom" event, so the bot compares invite-use counters and stores the attribution at join time.
