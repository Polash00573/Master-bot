"""
✨ Nexus Master Bot — Premium Telegram Channel & Bot Manager
Requires: pip install "python-telegram-bot>=20"
Run:      export BOT_TOKEN="9282800632:AAG47PocZjz3f01si_U2YBbRF1-6iFusLik" && python main.py
"""
import os, re, json, time, html, asyncio, sqlite3, logging
from datetime import datetime

from telegram import Update, Bot, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest, RetryAfter
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

ADMIN_ID = 7402356367
HTML = ParseMode.HTML
esc = html.escape
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("nexus")

# ───────────────────────────── DATABASE ─────────────────────────────
_db = sqlite3.connect("nexus.db", check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.executescript("""
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    joined_at TEXT, notify INTEGER DEFAULT 1, is_premium INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER,
    title TEXT, username TEXT, added_at TEXT, UNIQUE(user_id, chat_id));
CREATE TABLE IF NOT EXISTS user_bots(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, bot_token TEXT,
    bot_username TEXT, bot_name TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS polls(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER,
    message_id INTEGER, question TEXT, options TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS votes(poll_id INTEGER, user_id INTEGER, option_idx INTEGER, PRIMARY KEY(poll_id, user_id));
CREATE TABLE IF NOT EXISTS scheduled_posts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER,
    bot_id INTEGER, ctype TEXT, content TEXT, file_id TEXT, send_at INTEGER, status TEXT DEFAULT 'pending');
""")


def dbq(sql, args=(), one=False, all=False):
    cur = _db.execute(sql, args)
    _db.commit()
    if one:
        return cur.fetchone()
    if all:
        return cur.fetchall()
    return cur


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def upsert(u):
    dbq("INSERT OR IGNORE INTO users(user_id,username,first_name,joined_at) VALUES(?,?,?,?)",
        (u.id, u.username, u.first_name, now()))
    dbq("UPDATE users SET username=?, first_name=? WHERE user_id=?", (u.username, u.first_name, u.id))


# ───────────────────────────── UI HELPERS ─────────────────────────────
DIV = "━━━━━━━━━━━━━━━━━━━━"
FOOT = f"\n\n{DIV}\n✨ <i>Powered by Nexus Master Bot</i>"


def panel(icon, title, body=""):
    return f"{icon} <b>{title}</b>\n{DIV}\n\n{body}{FOOT}"


def B(text, data):
    return Btn(text, callback_data=data)


BACK = [B("🏠 Main Menu", "menu")]
PROCESSING = panel("⏳", "Processing...", "<i>Please wait a moment</i>")

# ───────────────────────────── PREMIUM LIMITS ─────────────────────────────
# Edit these numbers to change the Free plan. Premium users & Admin are unlimited.
FREE_LIMITS = {"channels": 3, "bots": 1, "scheduled": 3, "polls_day": 5}
LIMIT_NAMES = {"channels": "connected channels", "bots": "connected bot", "scheduled": "pending scheduled posts",
               "polls_day": "polls per day"}
LIMIT_KB = Kb([[B("💰 View Premium", "premium")], BACK])


def is_premium(uid):
    if uid == ADMIN_ID:
        return True
    r = dbq("SELECT is_premium FROM users WHERE user_id=?", (uid,), one=True)
    return bool(r and r["is_premium"])


def usage(uid, key):
    if key == "polls_day":
        return dbq("SELECT COUNT(*) n FROM polls WHERE user_id=? AND created_at LIKE ?",
                   (uid, datetime.now().strftime("%Y-%m-%d") + "%"), one=True)["n"]
    sql = {"channels": "SELECT COUNT(*) n FROM channels WHERE user_id=?",
           "bots": "SELECT COUNT(*) n FROM user_bots WHERE user_id=?",
           "scheduled": "SELECT COUNT(*) n FROM scheduled_posts WHERE user_id=? AND status='pending'"}[key]
    return dbq(sql, (uid,), one=True)["n"]


def over_limit(uid, key):
    return (not is_premium(uid)) and usage(uid, key) >= FREE_LIMITS[key]


def limit_msg(key):
    return panel("🔒", "Free Limit Reached",
                 f"Free plan allows <b>{FREE_LIMITS[key]}</b> {LIMIT_NAMES[key]}.\n\n💎 Upgrade to <b>Premium</b> for unlimited access.")


def bc_counts():
    nu = dbq("SELECT COUNT(*) n FROM users", one=True)["n"]
    nc = dbq("SELECT COUNT(DISTINCT chat_id) n FROM channels", one=True)["n"]
    nb = dbq("SELECT COUNT(*) n FROM user_bots b JOIN channels c ON c.user_id=b.user_id", one=True)["n"]
    return nu, nc, nb


def main_kb(uid):
    rows = [
        [B("📝 Create Post", "post"), B("📊 Create Poll", "poll")],
        [B("🔗 My Channels", "channels"), B("👤 My Bots", "bots")],
        [B("📢 Broadcast", "bc"), B("💰 Premium", "premium")],
        [B("⚙️ Settings", "settings"), B("🆘 Help", "help")],
    ]
    if uid == ADMIN_ID:
        rows.append([B("🛡️ Admin Panel", "admin")])
    return Kb(rows)


def menu_text(u):
    return panel("🚀", "Nexus Master Bot",
                 f"Welcome, <b>{esc(u.first_name or 'there')}</b>! 👋\n\n"
                 "Your all-in-one control center to:\n"
                 "📝 Publish &amp; schedule posts\n📊 Run live polls\n"
                 "🔗 Manage unlimited channels\n🤖 Connect your own bots\n\n"
                 "<i>Choose an option below 👇</i>")


async def edit(q, text, kb=None):
    try:
        await q.edit_message_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def busy(q):
    try:
        await q.edit_message_text(PROCESSING, parse_mode=HTML)
    except TelegramError:
        pass


async def reply(m, text, kb=None):
    return await m.reply_text(text, parse_mode=HTML, reply_markup=kb, disable_web_page_preview=True)


# ───────────────────────────── CONTENT / SENDING ─────────────────────────────
def extract(m):
    if m.text:
        return "text", m.text_html, None
    if m.photo:
        return "photo", m.caption_html or "", m.photo[-1].file_id
    if m.video:
        return "video", m.caption_html or "", m.video.file_id
    if m.document:
        return "document", m.caption_html or "", m.document.file_id
    return None


async def deliver(master, token, chat_id, ctype, text, file_id):
    """Send content via Master Bot (token=None) or a user's own bot."""
    async def go(b, custom):
        if ctype == "text":
            return await b.send_message(chat_id, text, parse_mode=HTML)
        media = file_id
        if custom:  # file_ids are bot-specific -> re-upload bytes
            f = await master.get_file(file_id)
            media = bytes(await f.download_as_bytearray())
        fn = {"photo": b.send_photo, "video": b.send_video, "document": b.send_document}[ctype]
        return await fn(chat_id, **{ctype: media}, caption=text or None, parse_mode=HTML)

    if token is None:
        return await go(master, False)
    async with Bot(token) as b:
        return await go(b, True)


def resolve_sender(uid, bot_id):
    if not bot_id:
        return None, "Master Bot"
    r = dbq("SELECT * FROM user_bots WHERE id=? AND user_id=?", (bot_id, uid), one=True)
    return (r["bot_token"], f"@{r['bot_username']}") if r else (None, None)


def fwd_channel(m):
    fo = getattr(m, "forward_origin", None)
    if fo is not None and getattr(fo, "chat", None) is not None and fo.chat.type == "channel":
        return fo.chat
    c = getattr(m, "forward_from_chat", None)
    return c if c is not None and c.type == "channel" else None


# ───────────────────────────── RENDER SCREENS ─────────────────────────────
CONNECT_TXT = ("<b>How to connect a channel:</b>\n\n"
               "1️⃣ Add this bot as <b>Admin</b> in your channel\n"
               "2️⃣ <b>Forward any message</b> from that channel here\n\n"
               "✅ Channel ID, title &amp; username are detected automatically.\n"
               "♾️ Connect as many channels as you like.")


async def show_channels(q, uid):
    rows = dbq("SELECT * FROM channels WHERE user_id=? ORDER BY id DESC LIMIT 25", (uid,), all=True)
    if not rows:
        return await edit(q, panel("🔗", "My Channels", "📭 No channels connected yet.\n\n" + CONNECT_TXT), Kb([BACK]))
    body = "\n".join(f"📡 <b>{esc(r['title'])}</b> {('@' + r['username']) if r['username'] else '🔒 private'}" for r in rows)
    kb = []
    for r in rows:
        kb.append([B(f"📡 {r['title'][:30]}", "noop")])
        kb.append([B("📤 Post", f"pc:{r['id']}"), B("🗑️ Delete", f"dc:{r['id']}")])
    kb.append([B("➕ Connect Channel", "cc")])
    kb.append(BACK)
    await edit(q, panel("🔗", "My Channels", f"<b>{len(rows)}</b> connected\n\n{body}"), Kb(kb))


async def show_bots(q, uid):
    rows = dbq("SELECT * FROM user_bots WHERE user_id=? ORDER BY id DESC", (uid,), all=True)
    kb = []
    for r in rows:
        kb.append([B(f"🤖 @{r['bot_username']}", "noop")])
        kb.append([B("📤 Post With This Bot", f"pbot:{r['id']}")])
        kb.append([B("🧪 Test Bot", f"tb:{r['id']}"), B("🗑️ Delete Bot", f"db:{r['id']}")])
    kb.append([B("➕ Connect New Bot", "cb")])
    kb.append(BACK)
    body = "\n".join(f"🤖 <b>{esc(r['bot_name'])}</b> — @{esc(r['bot_username'])}" for r in rows) \
        or "📭 No bots connected yet.\nConnect your own bot to post from it."
    await edit(q, panel("👤", "My Bots", body), Kb(kb))


def poll_text(question, opts, counts):
    total = sum(counts)
    lines = []
    for o, c in zip(opts, counts):
        pct = round(c * 100 / total) if total else 0
        n = round(pct / 10)
        lines.append(f"<b>{esc(o)}</b>\n{'█' * n}{'░' * (10 - n)} {pct}% · {c}")
    return panel("📊", esc(question), "\n\n".join(lines) + f"\n\n👥 <b>{total}</b> votes")


def poll_kb(pid, opts, counts):
    return Kb([[B(f"{o} · {c}", f"v:{pid}:{i}")] for i, (o, c) in enumerate(zip(opts, counts))])


def poll_counts(pid, n):
    c = [0] * n
    for r in dbq("SELECT option_idx, COUNT(*) n FROM votes WHERE poll_id=? GROUP BY option_idx", (pid,), all=True):
        if r["option_idx"] < n:
            c[r["option_idx"]] = r["n"]
    return c


# ───────────────────────────── COMMAND ─────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    upsert(u)
    context.user_data.clear()
    await reply(update.effective_message, menu_text(u), main_kb(u.id))


# ───────────────────────────── MESSAGE HANDLER ─────────────────────────────
async def connect_channel(m, context, ch):
    note = await reply(m, PROCESSING)
    uid = m.from_user.id
    try:
        me = await context.bot.get_chat_member(ch.id, context.bot.id)
        if me.status not in ("administrator", "creator"):
            raise TelegramError("not admin")
    except TelegramError:
        return await note.edit_text(panel("❌", "Bot Is Not Admin",
                                          f"Add this bot as <b>Admin</b> in <b>{esc(ch.title or '')}</b>, then forward a message again."),
                                    parse_mode=HTML, reply_markup=Kb([BACK]))
    try:  # make sure the person connecting is really an admin of that channel
        you = await context.bot.get_chat_member(ch.id, uid)
        if you.status not in ("administrator", "creator"):
            raise TelegramError("user not admin")
    except TelegramError:
        return await note.edit_text(panel("🔒", "Permission Denied", "Only channel <b>admins</b> can connect a channel."),
                                    parse_mode=HTML, reply_markup=Kb([BACK]))
    if dbq("SELECT 1 FROM channels WHERE user_id=? AND chat_id=?", (uid, ch.id), one=True):
        return await note.edit_text(panel("ℹ️", "Already Connected", f"<b>{esc(ch.title)}</b> is already in your list."),
                                    parse_mode=HTML, reply_markup=Kb([[B("🔗 My Channels", "channels")], BACK]))
    if over_limit(uid, "channels"):
        return await note.edit_text(limit_msg("channels"), parse_mode=HTML, reply_markup=LIMIT_KB)
    dbq("INSERT INTO channels(user_id,chat_id,title,username,added_at) VALUES(?,?,?,?,?)",
        (uid, ch.id, ch.title, ch.username, now()))
    await note.edit_text(panel("✅", "Channel Connected!",
                               f"📡 <b>{esc(ch.title)}</b>\n🆔 <code>{ch.id}</code>\n"
                               f"🔗 {('@' + ch.username) if ch.username else 'Private channel'}"),
                         parse_mode=HTML, reply_markup=Kb([[B("📤 Post Now", f"pc:{dbq('SELECT id FROM channels WHERE user_id=? AND chat_id=?', (uid, ch.id), one=True)['id']}")],
                                                           [B("🔗 My Channels", "channels")], BACK]))


async def handle_token(m, context):
    tok = (m.text or "").strip()
    note = await reply(m, PROCESSING)
    try:
        await m.delete()  # hide the token from chat history
    except TelegramError:
        pass
    wrong = panel("❌", "Wrong Token, Try Again", "Send a valid token from <b>@BotFather</b>.")
    cancel = Kb([[B("✖️ Cancel", "bots")]])
    if not re.fullmatch(r"\d{5,}:[\w-]{30,}", tok):
        return await note.edit_text(wrong, parse_mode=HTML, reply_markup=cancel)
    try:
        async with Bot(tok) as b:
            me = await b.get_me()
    except TelegramError:
        return await note.edit_text(wrong, parse_mode=HTML, reply_markup=cancel)
    uid = m.from_user.id
    if dbq("SELECT 1 FROM user_bots WHERE user_id=? AND bot_username=?", (uid, me.username), one=True):
        context.user_data.clear()
        return await note.edit_text(panel("ℹ️", "Already Connected", f"@{esc(me.username)} is already in your list."),
                                    parse_mode=HTML, reply_markup=Kb([[B("👤 My Bots", "bots")], BACK]))
    if over_limit(uid, "bots"):
        context.user_data.clear()
        return await note.edit_text(limit_msg("bots"), parse_mode=HTML, reply_markup=LIMIT_KB)
    dbq("INSERT INTO user_bots(user_id,bot_token,bot_username,bot_name,added_at) VALUES(?,?,?,?,?)",
        (uid, tok, me.username, me.first_name, now()))
    context.user_data.clear()
    await note.edit_text(panel("✅", f"Bot Connected! @{esc(me.username)}",
                               f"🤖 <b>{esc(me.first_name)}</b>\n\nAdd it as <b>Admin</b> in your channel to post from it."),
                         parse_mode=HTML, reply_markup=Kb([[B("👤 My Bots", "bots")], BACK]))


async def on_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m, u = update.effective_message, update.effective_user
    if not m or not u or m.chat.type != "private":
        return
    upsert(u)
    ud = context.user_data
    st = ud.get("state")

    if st not in ("post", "bc"):
        ch = fwd_channel(m)
        if ch:
            return await connect_channel(m, context, ch)

    if st == "token":
        return await handle_token(m, context)

    if st == "post":
        c = extract(m)
        if not c:
            return await reply(m, panel("⚠️", "Unsupported", "Send <b>text, photo, video or document</b>."))
        ud["draft"], ud["state"] = c, None
        ctype, text, _ = c
        snippet = esc(re.sub(r"<[^>]+>", "", html.unescape(text))[:120]) or "—"
        return await reply(m, panel("📝", "Draft Ready", f"📎 Type: <b>{ctype}</b>\n💬 {snippet}\n\nWhat next?"),
                           Kb([[B("🚀 Publish Now", "pub"), B("⏰ Schedule", "sch")], [B("✖️ Cancel", "menu")]]))

    if st == "poll_q" and m.text:
        ud["poll"]["q"] = m.text.strip()[:250]
        ud["state"] = "poll_o"
        return await reply(m, panel("📊", "Poll Options", "Send options, <b>one per line</b> (2–10).\n\n<i>Example:</i>\nYes\nNo\nMaybe"),
                           Kb([[B("✖️ Cancel", "menu")]]))

    if st == "poll_o" and m.text:
        opts = [o.strip()[:60] for o in m.text.splitlines() if o.strip()][:10]
        if len(opts) < 2:
            return await reply(m, panel("⚠️", "Need 2+ Options", "Send at least <b>2 options</b>, one per line."))
        note = await reply(m, PROCESSING)
        p = ud.pop("poll")
        ud.clear()
        ch = dbq("SELECT * FROM channels WHERE id=? AND user_id=?", (p["ch"], u.id), one=True)
        if not ch:
            return await note.edit_text(panel("❌", "Channel Not Found"), parse_mode=HTML, reply_markup=Kb([BACK]))
        if over_limit(u.id, "polls_day"):
            return await note.edit_text(limit_msg("polls_day"), parse_mode=HTML, reply_markup=LIMIT_KB)
        pid = dbq("INSERT INTO polls(user_id,chat_id,question,options,created_at) VALUES(?,?,?,?,?)",
                  (u.id, ch["chat_id"], p["q"], json.dumps(opts), now())).lastrowid
        counts = [0] * len(opts)
        try:
            sent = await context.bot.send_message(ch["chat_id"], poll_text(p["q"], opts, counts), parse_mode=HTML,
                                                  reply_markup=poll_kb(pid, opts, counts))
            dbq("UPDATE polls SET message_id=? WHERE id=?", (sent.message_id, pid))
            return await note.edit_text(panel("✅", "Poll Published!", f"📡 <b>{esc(ch['title'])}</b>\n❓ {esc(p['q'])}"),
                                        parse_mode=HTML, reply_markup=Kb([BACK]))
        except TelegramError as e:
            dbq("DELETE FROM polls WHERE id=?", (pid,))
            return await note.edit_text(panel("❌", "Failed", f"<code>{esc(str(e))}</code>\n\nMake sure the bot is Admin."),
                                        parse_mode=HTML, reply_markup=Kb([BACK]))

    if st == "bc" and u.id == ADMIN_ID:
        c = extract(m)
        if not c:
            return await reply(m, panel("⚠️", "Unsupported", "Send <b>text, photo, video or document</b>."))
        ud["bc_msg"], ud["state"] = c, None
        nu, nc, nb = bc_counts()
        return await reply(m, panel("📢", "Choose Audience",
                                    f"👥 Users: <b>{nu}</b>\n📡 All connected channels: <b>{nc}</b>\n"
                                    f"🤖 User bots ➜ their channels: <b>{nb}</b>\n\nWho should receive this?"),
                           Kb([[B(f"👥 Users ({nu})", "bcgo:users"), B(f"📡 Channels ({nc})", "bcgo:channels")],
                               [B(f"🤖 User Bots ({nb})", "bcgo:bots")],
                               [B("🌍 Everyone", "bcgo:all")],
                               [B("✖️ Cancel", "menu")]]))

    if st == "pm" and u.id == ADMIN_ID:
        tid = (m.text or "").strip()
        r = dbq("SELECT * FROM users WHERE user_id=?", (int(tid),), one=True) if tid.isdigit() else None
        if not r:
            return await reply(m, panel("❌", "User Not Found", "Send a valid numeric <b>User ID</b> (the user must have started the bot)."),
                               Kb([[B("✖️ Cancel", "admin")]]))
        ud["state"] = None
        return await reply(m, panel("💎", "Manage Premium",
                                    f"👤 <b>{esc(r['first_name'] or '')}</b> — <code>{r['user_id']}</code>\n"
                                    f"Plan: <b>{'💎 Premium' if r['is_premium'] else '🆓 Free'}</b>"),
                           Kb([[B("💎 Grant", f"pmset:{tid}:1"), B("🚫 Revoke", f"pmset:{tid}:0")], [B("🛡️ Admin Panel", "admin")]]))

    await reply(m, menu_text(u), main_kb(u.id))


# ───────────────────────────── VOTING ─────────────────────────────
async def vote(q, d):
    _, pid, idx = d.split(":")
    pid, idx = int(pid), int(idx)
    p = dbq("SELECT * FROM polls WHERE id=?", (pid,), one=True)
    if not p:
        return await q.answer("Poll not found", show_alert=True)
    opts = json.loads(p["options"])
    old = dbq("SELECT option_idx FROM votes WHERE poll_id=? AND user_id=?", (pid, q.from_user.id), one=True)
    if old and old["option_idx"] == idx:
        return await q.answer("You already voted for this option ✅")
    dbq("INSERT OR REPLACE INTO votes(poll_id,user_id,option_idx) VALUES(?,?,?)", (pid, q.from_user.id, idx))
    counts = poll_counts(pid, len(opts))
    await q.answer("✅ Vote saved!")
    await edit(q, poll_text(p["question"], opts, counts), poll_kb(pid, opts, counts))


# ───────────────────────────── CALLBACK ROUTER ─────────────────────────────
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d, u = q.data, q.from_user
    uid, ud = u.id, context.user_data
    upsert(u)

    if d.startswith("v:"):
        return await vote(q, d)
    if d.split(":")[0] in ("bc", "admin", "bcgo", "pm", "pmset") and uid != ADMIN_ID:
        return await q.answer("🔒 Admin only", show_alert=True)
    await q.answer()

    if d == "noop":
        return

    if d == "menu":
        ud.clear()
        return await edit(q, menu_text(u), main_kb(uid))

    # ── Channels ──
    if d == "channels":
        return await show_channels(q, uid)
    if d == "cc":
        return await edit(q, panel("➕", "Connect Channel", CONNECT_TXT), Kb([[B("🔗 My Channels", "channels")], BACK]))
    if d.startswith("dc:"):
        await busy(q)
        dbq("DELETE FROM channels WHERE id=? AND user_id=?", (int(d[3:]), uid))
        return await show_channels(q, uid)

    # ── Bots ──
    if d == "bots":
        ud.clear()
        return await show_bots(q, uid)
    if d == "cb":
        ud["state"] = "token"
        return await edit(q, panel("🤖", "Connect New Bot", "Send your <b>Bot Token</b> from <b>BotFather</b>\n\n"
                                   "<i>Format:</i> <code>123456:ABC...</code>"), Kb([[B("✖️ Cancel", "bots")]]))
    if d.startswith("tb:"):
        await busy(q)
        r = dbq("SELECT * FROM user_bots WHERE id=? AND user_id=?", (int(d[3:]), uid), one=True)
        if not r:
            return await show_bots(q, uid)
        try:
            async with Bot(r["bot_token"]) as b:
                me = await b.get_me()
            txt = panel("🧪", "Bot Test: ✅ Online", f"🤖 <b>{esc(me.first_name)}</b>\n🔗 @{esc(me.username)}\n🆔 <code>{me.id}</code>")
        except TelegramError:
            txt = panel("🧪", "Bot Test: ❌ Offline", f"@{esc(r['bot_username'])} token is invalid or revoked.\nDelete and reconnect it.")
        return await edit(q, txt, Kb([[B("👤 My Bots", "bots")], BACK]))
    if d.startswith("db:"):
        await busy(q)
        dbq("DELETE FROM user_bots WHERE id=? AND user_id=?", (int(d[3:]), uid))
        return await show_bots(q, uid)

    # ── Post flow ──
    if d == "post" or d.startswith("pbot:"):
        bot_id = int(d[5:]) if d.startswith("pbot:") else None
        rows = dbq("SELECT * FROM channels WHERE user_id=? ORDER BY id DESC LIMIT 25", (uid,), all=True)
        if not rows:
            return await edit(q, panel("📭", "No Channels", CONNECT_TXT), Kb([BACK]))
        kb = [[B(f"📡 {r['title'][:35]}", f"ps:{r['id']}:{bot_id}" if bot_id else f"pc:{r['id']}")] for r in rows]
        return await edit(q, panel("📝", "Create Post", "Select the <b>target channel</b>:"), Kb(kb + [BACK]))
    if d.startswith("pc:"):
        cid = int(d[3:])
        bots = dbq("SELECT * FROM user_bots WHERE user_id=?", (uid,), all=True)
        kb = [[B("🤖 Post From Master Bot", f"ps:{cid}:0")]]
        kb += [[B(f"🤖 Post From My Bot @{b['bot_username']}", f"ps:{cid}:{b['id']}")] for b in bots]
        return await edit(q, panel("📤", "Choose Sender", "Post from which bot?"), Kb(kb + [BACK]))
    if d.startswith("ps:"):
        _, cid, bid = d.split(":")
        ch = dbq("SELECT * FROM channels WHERE id=? AND user_id=?", (int(cid), uid), one=True)
        _, label = resolve_sender(uid, int(bid))
        if not ch or not label:
            return await edit(q, panel("❌", "Not Found"), Kb([BACK]))
        ud.clear()
        ud["state"], ud["target"] = "post", {"ch": int(cid), "bot": int(bid)}
        return await edit(q, panel("✍️", "Send Your Content",
                                   f"📡 Channel: <b>{esc(ch['title'])}</b>\n🤖 Sender: <b>{esc(label)}</b>\n\n"
                                   "Now send the <b>text, photo, video or document</b> you want to post.\n"
                                   "<i>Formatting (bold, links...) is kept.</i>"), Kb([[B("✖️ Cancel", "menu")]]))
    if d == "pub":
        t, draft = ud.get("target"), ud.get("draft")
        if not t or not draft:
            return await edit(q, panel("⚠️", "Session Expired"), Kb([BACK]))
        await busy(q)
        ch = dbq("SELECT * FROM channels WHERE id=? AND user_id=?", (t["ch"], uid), one=True)
        tok, label = resolve_sender(uid, t["bot"])
        try:
            if not ch or not label:
                raise TelegramError("Channel or bot no longer exists")
            await deliver(context.bot, tok, ch["chat_id"], *draft)
            ud.clear()
            return await edit(q, panel("✅", "Post Published!", f"📡 <b>{esc(ch['title'])}</b>\n🤖 via <b>{esc(label)}</b>"),
                              Kb([[B("📝 New Post", "post")], BACK]))
        except TelegramError as e:
            return await edit(q, panel("❌", "Publish Failed", f"<code>{esc(str(e))}</code>\n\nMake sure the sender is <b>Admin</b> in the channel."),
                              Kb([[B("🔁 Retry", "pub")], BACK]))
    if d == "sch":
        kb = [[B("⏱ 15 min", "st:15"), B("⏱ 1 hour", "st:60")], [B("⏱ 6 hours", "st:360"), B("⏱ 24 hours", "st:1440")], BACK]
        return await edit(q, panel("⏰", "Schedule Post", "Publish this post in:"), Kb(kb))
    if d.startswith("st:"):
        t, draft = ud.get("target"), ud.get("draft")
        if not t or not draft:
            return await edit(q, panel("⚠️", "Session Expired"), Kb([BACK]))
        await busy(q)
        ch = dbq("SELECT * FROM channels WHERE id=? AND user_id=?", (t["ch"], uid), one=True)
        if over_limit(uid, "scheduled"):
            return await edit(q, limit_msg("scheduled"), LIMIT_KB)
        mins = int(d[3:])
        dbq("INSERT INTO scheduled_posts(user_id,chat_id,bot_id,ctype,content,file_id,send_at) VALUES(?,?,?,?,?,?,?)",
            (uid, ch["chat_id"], t["bot"] or None, draft[0], draft[1], draft[2], int(time.time()) + mins * 60))
        ud.clear()
        return await edit(q, panel("⏰", "Post Scheduled!", f"📡 <b>{esc(ch['title'])}</b>\n🕒 In <b>{mins}</b> minutes"),
                          Kb([[B("📝 New Post", "post")], BACK]))

    # ── Poll flow ──
    if d == "poll":
        rows = dbq("SELECT * FROM channels WHERE user_id=? ORDER BY id DESC LIMIT 25", (uid,), all=True)
        if not rows:
            return await edit(q, panel("📭", "No Channels", CONNECT_TXT), Kb([BACK]))
        kb = [[B(f"📡 {r['title'][:35]}", f"pl:{r['id']}")] for r in rows]
        return await edit(q, panel("📊", "Create Poll", "Select the <b>target channel</b>:"), Kb(kb + [BACK]))
    if d.startswith("pl:"):
        ud.clear()
        ud["state"], ud["poll"] = "poll_q", {"ch": int(d[3:])}
        return await edit(q, panel("❓", "Poll Question", "Send your <b>poll question</b>:"), Kb([[B("✖️ Cancel", "menu")]]))

    # ── Broadcast / Admin ──
    if d == "bc":
        ud.clear()
        ud["state"] = "bc"
        return await edit(q, panel("📢", "Broadcast", "Send the message to broadcast to <b>all users</b>\n<i>(text, photo, video or document)</i>\n\nNext you will choose the audience: users, channels, or user bots."),
                          Kb([[B("✖️ Cancel", "menu")]]))
    if d.startswith("bcgo:"):
        content, tgt = ud.get("bc_msg"), d[5:]
        if not content:
            return await edit(q, panel("⚠️", "Session Expired"), Kb([BACK]))
        await busy(q)
        jobs, master_chats = [], set()  # (bot_token or None, chat_id)
        if tgt in ("users", "all"):
            for r in dbq("SELECT user_id FROM users", all=True):
                jobs.append((None, r["user_id"]))
        if tgt in ("channels", "all"):
            for r in dbq("SELECT DISTINCT chat_id FROM channels", all=True):
                jobs.append((None, r["chat_id"]))
                master_chats.add(r["chat_id"])
        if tgt in ("bots", "all"):
            for r in dbq("SELECT b.bot_token, c.chat_id FROM user_bots b JOIN channels c ON c.user_id=b.user_id", all=True):
                if r["chat_id"] not in master_chats:  # avoid double-posting in the same channel
                    jobs.append((r["bot_token"], r["chat_id"]))
        ok = fail = 0
        for tok, chat_id in jobs:
            try:
                try:
                    await deliver(context.bot, tok, chat_id, *content)
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    await deliver(context.bot, tok, chat_id, *content)
                ok += 1
            except TelegramError:
                fail += 1
            await asyncio.sleep(0.05)
        ud.clear()
        return await edit(q, panel("✅", "Broadcast Complete",
                                   f"🎯 Targets: <b>{len(jobs)}</b>\n📬 Delivered: <b>{ok}</b>\n🚫 Failed: <b>{fail}</b>"), Kb([BACK]))
    if d == "pm":
        ud.clear()
        ud["state"] = "pm"
        return await edit(q, panel("💎", "Manage Premium", "Send the <b>User ID</b> to grant or revoke Premium."),
                          Kb([[B("✖️ Cancel", "admin")]]))
    if d.startswith("pmset:"):
        _, tid, val = d.split(":")
        dbq("UPDATE users SET is_premium=? WHERE user_id=?", (int(val), int(tid)))
        try:
            await context.bot.send_message(int(tid), panel("💎" if val == "1" else "ℹ️", "Plan Updated",
                                                          "🎉 You are now <b>Premium</b> — unlimited access!" if val == "1"
                                                          else "Your plan is now <b>Free</b>."), parse_mode=HTML)
        except TelegramError:
            pass
        return await edit(q, panel("✅", "Plan Updated", f"User <code>{tid}</code> is now <b>{'💎 Premium' if val == '1' else '🆓 Free'}</b>."),
                          Kb([[B("💎 Manage Another", "pm")], [B("🛡️ Admin Panel", "admin")]]))
    if d == "admin":
        c = lambda t, w="": dbq(f"SELECT COUNT(*) n FROM {t} {w}", one=True)["n"]
        pending = c("scheduled_posts", "WHERE status='pending'")
        body = (f"👥 Users: <b>{c('users')}</b>\n🔗 Channels: <b>{c('channels')}</b>\n🤖 User Bots: <b>{c('user_bots')}</b>\n"
                f"📊 Polls: <b>{c('polls')}</b>\n🗳 Votes: <b>{c('votes')}</b>\n"
                f"⏰ Pending posts: <b>{pending}</b>")
        return await edit(q, panel("🛡️", "Admin Panel", body),
                          Kb([[B("📢 Broadcast", "bc"), B("💎 Manage Premium", "pm")], BACK]))

    # ── Info screens ──
    if d == "premium":
        prem = is_premium(uid)
        L = FREE_LIMITS
        if prem:
            mine = "💎 <b>You are on Premium — everything is unlimited!</b>"
        else:
            mine = (f"📊 <b>Your usage</b>\n🔗 Channels: <b>{usage(uid, 'channels')}/{L['channels']}</b>\n"
                    f"🤖 Bots: <b>{usage(uid, 'bots')}/{L['bots']}</b>\n"
                    f"⏰ Scheduled: <b>{usage(uid, 'scheduled')}/{L['scheduled']}</b>\n"
                    f"📊 Polls today: <b>{usage(uid, 'polls_day')}/{L['polls_day']}</b>")
        body = (f"🆓 <b>Free</b>\n• {L['channels']} channels\n• {L['bots']} connected bot\n"
                f"• {L['scheduled']} scheduled posts\n• {L['polls_day']} polls / day\n\n"
                "💎 <b>Premium</b>\n• ♾️ Unlimited channels &amp; bots\n• ♾️ Unlimited scheduling &amp; polls\n"
                f"• Priority support\n\n{mine}\n\n<i>Contact the admin to upgrade.</i>")
        kb = [] if prem else [[Btn("💳 Upgrade Now", url=f"tg://user?id={ADMIN_ID}")]]
        return await edit(q, panel("💰", "Premium Plans", body), Kb(kb + [BACK]))
    if d == "settings":
        r = dbq("SELECT * FROM users WHERE user_id=?", (uid,), one=True)
        n = lambda t: dbq(f"SELECT COUNT(*) n FROM {t} WHERE user_id=?", (uid,), one=True)["n"]
        body = (f"🆔 ID: <code>{uid}</code>\n📅 Joined: <b>{r['joined_at']}</b>\n"
                f"🔗 Channels: <b>{n('channels')}</b>  🤖 Bots: <b>{n('user_bots')}</b>\n"
                f"🔔 Notifications: <b>{'ON' if r['notify'] else 'OFF'}</b>")
        return await edit(q, panel("⚙️", "Settings", body),
                          Kb([[B(f"🔔 Notifications: {'ON' if r['notify'] else 'OFF'}", "notif")], BACK]))
    if d == "notif":
        dbq("UPDATE users SET notify = 1 - notify WHERE user_id=?", (uid,))
        q.data = "settings"
        return await on_cb(update, context)
    if d == "help":
        body = ("1️⃣ <b>Connect a channel</b> — add the bot as Admin, forward any channel message here\n"
                "2️⃣ <b>Connect your bot</b> — My Bots ➜ Connect New Bot ➜ send token\n"
                "3️⃣ <b>Post</b> — Create Post ➜ channel ➜ sender ➜ send content ➜ publish or schedule\n"
                "4️⃣ <b>Poll</b> — Create Poll ➜ channel ➜ question ➜ options\n\n"
                "💬 Need help? Contact the admin.")
        return await edit(q, panel("🆘", "Help Center", body), Kb([BACK]))


# ───────────────────────────── SCHEDULER ─────────────────────────────
async def scheduler(app: Application):
    while True:
        await asyncio.sleep(20)
        try:
            rows = dbq("SELECT * FROM scheduled_posts WHERE status='pending' AND send_at<=?", (int(time.time()),), all=True)
            for r in rows:
                dbq("UPDATE scheduled_posts SET status='sending' WHERE id=?", (r["id"],))
                tok, label = resolve_sender(r["user_id"], r["bot_id"] or 0)
                try:
                    if r["bot_id"] and not tok:
                        raise TelegramError("Sender bot was deleted")
                    await deliver(app.bot, tok, r["chat_id"], r["ctype"], r["content"], r["file_id"])
                    status, msg = "sent", panel("✅", "Scheduled Post Published", "Your scheduled post went live.")
                except Exception as e:
                    status, msg = "failed", panel("❌", "Scheduled Post Failed", f"<code>{esc(str(e))}</code>")
                dbq("UPDATE scheduled_posts SET status=? WHERE id=?", (status, r["id"]))
                u = dbq("SELECT notify FROM users WHERE user_id=?", (r["user_id"],), one=True)
                if u and u["notify"]:
                    try:
                        await app.bot.send_message(r["user_id"], msg, parse_mode=HTML)
                    except TelegramError:
                        pass
        except Exception:
            log.exception("scheduler error")


async def post_init(app: Application):
    app.bot_data["scheduler"] = asyncio.create_task(scheduler(app))


async def on_error(update, context):
    log.error("Update error", exc_info=context.error)


def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("❌ Set the BOT_TOKEN environment variable first.")
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_msg))
    app.add_error_handler(on_error)
    log.info("✨ Nexus Master Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
