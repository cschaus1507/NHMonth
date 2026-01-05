# Noble Hero of the Month (Render + Discord Login + CoC Roster)

This is a lightweight Flask app you can deploy to Render and share as an external link in your Discord server.

## What it does

- **Discord OAuth login** ("Login with Discord")
- **One vote per Discord account** (enforced by Discord user ID)
- **Candidate list auto-populates from the Clash of Clans clan roster** (Supercell CoC API)
- Live standings endpoint (`/results`)

## Environment variables (Render)

### Required for Discord login

- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_CALLBACK_URL` (example: `https://YOUR-SERVICE.onrender.com/auth/discord/callback`)
- `SECRET_KEY` (any long random string)

### Required for Clash of Clans roster

- `COC_BEARER_TOKEN` (from https://developer.clashofclans.com)
- `CLAN_TAG` (example: `#8G28UGQ2`)

### Optional

- `ROSTER_CACHE_TTL` (seconds; default 1800)
- `DB_PATH` (default `votes.db`) — use a Render **Persistent Disk** so votes survive restarts
- `ADMIN_RESET_TOKEN` — if set, `/reset` requires this token (JSON body `{ "token": "..." }` or header `X-Admin-Token`)

## Render IP allowlisting (Supercell API)

Supercell API keys require **allowlisted IPs**.

1. Deploy this app to Render.
2. Open: `/debug/egress-ip` on your deployed site.
3. Copy the returned IP address.
4. In the Clash of Clans developer portal, add that IP to your API key allowlist.

If the outbound IP changes on Render, you may need a static outbound IP solution.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export SECRET_KEY="dev"
export DISCORD_CLIENT_ID="..."
export DISCORD_CLIENT_SECRET="..."
export DISCORD_CALLBACK_URL="http://localhost:5000/auth/discord/callback"

export COC_BEARER_TOKEN="..."
export CLAN_TAG="#8G28UGQ2"

python app.py
```

## Notes

- No Discord bot is required.
- Votes are stored in `votes.db` (SQLite) by default.
