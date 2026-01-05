import os
import sqlite3
import time
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, redirect, render_template, request, session


app = Flask(__name__)

# ---- Configuration (env vars) ----
# Flask session cookie signing
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")

# Discord OAuth
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
DISCORD_CALLBACK_URL = os.environ.get(
    "DISCORD_CALLBACK_URL", "http://localhost:5000/auth/discord/callback"
)

# Clash of Clans API
COC_BEARER_TOKEN = os.environ.get("COC_BEARER_TOKEN")
CLAN_TAG = os.environ.get("CLAN_TAG", "#8G28UGQ2")

# Optional: lock down /reset with a shared secret
ADMIN_RESET_TOKEN = os.environ.get("ADMIN_RESET_TOKEN")

# SQLite (recommended: use a Render Persistent Disk so votes survive restarts)
DB_PATH = os.environ.get("DB_PATH", "votes.db")

# Simple in-memory cache for roster lookups (seconds)
ROSTER_CACHE_TTL = int(os.environ.get("ROSTER_CACHE_TTL", "1800"))  # 30 min
_roster_cache = {"ts": 0, "data": None}


# ---- DB helpers ----
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS votes (
            discord_id TEXT PRIMARY KEY,
            discord_username TEXT,
            self_tag TEXT,
            self_name TEXT,
            candidate_tag TEXT NOT NULL,
            candidate_name TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )

    # Lightweight migration for existing DBs
    cur.execute("PRAGMA table_info(votes)")
    existing_cols = {row[1] for row in cur.fetchall()}  # (cid, name, type, ...)
    if "self_tag" not in existing_cols:
        cur.execute("ALTER TABLE votes ADD COLUMN self_tag TEXT")
    if "self_name" not in existing_cols:
        cur.execute("ALTER TABLE votes ADD COLUMN self_name TEXT")

    conn.commit()
    conn.close()


init_db()


# ---- Discord OAuth helpers ----
def discord_configured() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_CALLBACK_URL)


def require_login():
    user = session.get("discord_user")
    if not user:
        return False
    # minimal shape check
    return "id" in user


@app.route("/auth/discord")
def discord_login():
    if not discord_configured():
        return (
            jsonify(
                {
                    "error": "Discord OAuth is not configured. Set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, and DISCORD_CALLBACK_URL."
                }
            ),
            500,
        )

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_CALLBACK_URL,
        "response_type": "code",
        "scope": "identify",
        "prompt": "consent",
    }
    return redirect("https://discord.com/api/oauth2/authorize?" + urlencode(params))


@app.route("/auth/discord/callback")
def discord_callback():
    if not discord_configured():
        return "Discord OAuth not configured.", 500

    code = request.args.get("code")
    if not code:
        return "Missing OAuth code.", 400

    # Exchange code for token
    token_res = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_CALLBACK_URL,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if token_res.status_code != 200:
        return (
            f"Discord token exchange failed ({token_res.status_code}).", 400
        )
    token_json = token_res.json()
    access_token = token_json.get("access_token")
    if not access_token:
        return "Discord did not return an access token.", 400

    # Fetch user profile
    me_res = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if me_res.status_code != 200:
        return f"Discord profile fetch failed ({me_res.status_code}).", 400

    me = me_res.json()
    # Store only what we need
    session["discord_user"] = {
        "id": me.get("id"),
        "username": me.get("username"),
        "discriminator": me.get("discriminator"),
        "global_name": me.get("global_name"),
        "avatar": me.get("avatar"),
    }
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/me")
def me():
    return jsonify({"user": session.get("discord_user")})


# ---- Clash of Clans roster ----
def coc_api_configured() -> bool:
    return bool(COC_BEARER_TOKEN and CLAN_TAG)


def normalize_tag(tag: str) -> str:
    tag = (tag or "").strip().upper()
    if not tag.startswith("#"):
        tag = "#" + tag
    return tag


def encode_tag(tag: str) -> str:
    # CoC API expects %23 instead of '#'
    return normalize_tag(tag).replace("#", "%23")


def fetch_clan_members():
    now = int(time.time())
    if _roster_cache["data"] is not None and (now - _roster_cache["ts"]) < ROSTER_CACHE_TTL:
        return _roster_cache["data"]

    if not coc_api_configured():
        # Fallback: return empty roster with an explanatory flag
        return {"ok": False, "error": "CoC API not configured", "items": []}

    url = f"https://api.clashofclans.com/v1/clans/{encode_tag(CLAN_TAG)}/members"
    res = requests.get(
        url,
        headers={"Authorization": f"Bearer {COC_BEARER_TOKEN}"},
        timeout=20,
    )
    if res.status_code != 200:
        return {
            "ok": False,
            "error": f"CoC API error ({res.status_code})",
            "items": [],
            "details": res.text[:2000],
        }

    data = res.json()
    # Normalize output for the front-end
    items = []
    for m in data.get("items", []):
        items.append(
            {
                "name": m.get("name"),
                "tag": m.get("tag"),
                "role": m.get("role"),
                "expLevel": m.get("expLevel"),
                "trophies": m.get("trophies"),
                "league": (m.get("league") or {}).get("name"),
            }
        )

    payload = {"ok": True, "clanTag": CLAN_TAG, "items": items}
    _roster_cache["ts"] = now
    _roster_cache["data"] = payload
    return payload


@app.route("/api/members")
def api_members():
    return jsonify(fetch_clan_members())


@app.route("/debug/egress-ip")
def debug_egress_ip():
    """Helps you figure out which public IP Render is using for outbound requests."""
    try:
        res = requests.get("https://api.ipify.org?format=json", timeout=10)
        return jsonify({"ok": True, "ip": res.json().get("ip")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---- App pages ----
@app.route("/")
def home():
    return render_template(
        "index.html",
        clan_tag=CLAN_TAG,
        discord_enabled=discord_configured(),
        coc_enabled=coc_api_configured(),
    )


# ---- Voting ----
@app.route("/vote", methods=["POST"])
def vote():
    if not require_login():
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    self_tag = data.get("selfTag")
    self_name = data.get("selfName")
    candidate_tag = data.get("candidateTag")
    candidate_name = data.get("candidateName")
    if not self_tag or not self_name:
        return jsonify({"error": "Missing voter identity"}), 400
    if not candidate_tag or not candidate_name:
        return jsonify({"error": "Missing candidate"}), 400

    # Normalize tags
    self_tag = normalize_tag(self_tag)
    candidate_tag = normalize_tag(candidate_tag)

    if self_tag == candidate_tag:
        return (
            jsonify(
                {
                    "error": "self_vote_not_allowed",
                    "message": "Self-votes are not allowed. Pick a different clan member.",
                }
            ),
            400,
        )

    user = session.get("discord_user")
    discord_id = str(user.get("id"))
    discord_username = (
        user.get("global_name")
        or user.get("username")
        or "unknown"
    )

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT candidate_tag, candidate_name FROM votes WHERE discord_id = ?",
        (discord_id,),
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        return (
            jsonify(
                {
                    "error": "already_voted",
                    "message": "You already voted.",
                    "yourVote": {
                        "candidateTag": existing["candidate_tag"],
                        "candidateName": existing["candidate_name"],
                    },
                }
            ),
            409,
        )

    cur.execute(
        "INSERT INTO votes (discord_id, discord_username, self_tag, self_name, candidate_tag, candidate_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            discord_id,
            discord_username,
            self_tag,
            self_name,
            candidate_tag,
            candidate_name,
            int(time.time()),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Vote received"})


@app.route("/results")
def results():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT candidate_name, candidate_tag, COUNT(*) as votes FROM votes GROUP BY candidate_tag, candidate_name ORDER BY votes DESC, candidate_name ASC"
    )
    rows = cur.fetchall()
    conn.close()
    return jsonify(
        [
            {
                "candidateName": r["candidate_name"],
                "candidateTag": r["candidate_tag"],
                "votes": r["votes"],
            }
            for r in rows
        ]
    )


@app.route("/reset", methods=["POST"])
def reset():
    if ADMIN_RESET_TOKEN:
        provided = request.headers.get("X-Admin-Token") or (request.get_json(silent=True) or {}).get(
            "token"
        )
        if provided != ADMIN_RESET_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM votes")
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Votes reset"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
