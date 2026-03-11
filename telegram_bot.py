from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from datetime import datetime
import os, psycopg2, psycopg2.extras

# ── Bay config ───────────────────────────────────────
BAYS = {
    "1": "universal", "2": "universal",
    "3": "universal", "4": "universal",
    "5": "tesla",     "6": "tesla",
    "7": "tesla",
}

# ── Admin config ─────────────────────────────────────
# Your Telegram user ID — only you can force-release bays
# Find your ID by messaging @userinfobot on Telegram
ADMIN_ID = os.environ.get("ADMIN_TELEGRAM_ID", "")

# ── Database ─────────────────────────────────────────
def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")

def init_db():
    with get_db() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bays (
                id         TEXT PRIMARY KEY,
                type       TEXT NOT NULL,
                user_phone TEXT,
                claimed_at FLOAT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                phone TEXT PRIMARY KEY,
                name  TEXT NOT NULL
            )
        """)
        for bid, btype in BAYS.items():
            cur.execute("""
                INSERT INTO bays (id, type, user_phone, claimed_at)
                VALUES (%s, %s, NULL, NULL)
                ON CONFLICT (id) DO NOTHING
            """, (bid, btype))
        conn.commit()

def get_state():
    with get_db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM bays ORDER BY id")
        return {r["id"]: dict(r) for r in cur.fetchall()}

def get_user_name(user_id):
    with get_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM users WHERE phone=%s", (str(user_id),))
        row = cur.fetchone()
        return row[0] if row else None

def save_user_name(user_id, name):
    with get_db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO users (phone, name) VALUES (%s, %s)
            ON CONFLICT (phone) DO UPDATE SET name=%s
        """, (str(user_id), name, name))
        conn.commit()

def claim(bid, user_id):
    with get_db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE bays SET user_phone=%s, claimed_at=%s WHERE id=%s",
            (str(user_id), datetime.now().timestamp(), bid)
        )
        conn.commit()

def release(bid):
    with get_db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE bays SET user_phone=NULL, claimed_at=NULL WHERE id=%s", (bid,)
        )
        conn.commit()

def elapsed(ts):
    if not ts: return ""
    diff = int((datetime.now().timestamp() - float(ts)) / 60)
    return f"{diff}m" if diff < 60 else f"{diff//60}h {diff%60}m"

# ── Overtime alert ────────────────────────────────────
async def check_overtime(context: ContextTypes.DEFAULT_TYPE):
    try:
        state = get_state()
        now   = datetime.now().timestamp()
        for bid, bay in state.items():
            if not bay["user_phone"] or not bay["claimed_at"]: continue
            hours = (now - float(bay["claimed_at"])) / 3600
            if hours >= 5:
                name  = get_user_name(bay["user_phone"]) or "there"
                btype = "Tesla only" if BAYS[bid] == "tesla" else "Universal"
                await context.bot.send_message(
                    chat_id=bay["user_phone"],
                    text=(
                        f"⏰  Overtime Alert\n\n"
                        f"Hi {name}, Bay {bid} ({btype}) has been occupied for {int(hours)}h.\n\n"
                        f"Please unplug if you're done so others can charge 🙏"
                    )
                )
    except Exception as e:
        print(f"Overtime check error: {e}")

# ── Message handler ───────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    body    = update.message.text.strip()
    parts   = body.split()
    cmd     = parts[0].lower() if parts else ""

    # plain text — no markdown to avoid escaping issues
    async def reply(text):
        await update.message.reply_text(text)

    name = get_user_name(user_id)

    # ── First time user ───────────────────────────────
    if not name:
        if body and not any(body.lower().startswith(c) for c in
                            ["status","claim","release","who","help","myname","/start"]):
            save_user_name(user_id, body.strip())
            await reply(
                f"👋 Welcome, {body.strip()}!\n\n"
                "━━━━━━━━━━━━━━━\n"
                "⚡ BELK CHARGING STATION\n"
                "━━━━━━━━━━━━━━━\n\n"
                "🔌 Universal  →  Bays 1 2 3 4\n"
                "🚗 Tesla only →  Bays 5 6 7\n\n"
                "Commands:\n"
                "· status      see all bays\n"
                "· claim 1     grab Bay 1\n"
                "· release 1   free Bay 1\n"
                "· who         who's charging\n"
                "· help        show this menu"
            )
        else:
            await reply(
                "👋 Welcome to Belk Charging Station!\n\n"
                "What's your name?\n"
                "(e.g. reply: Sarah)"
            )
        return

    # ── Update name ───────────────────────────────────
    if cmd == "myname" and len(parts) >= 2:
        new_name = " ".join(parts[1:])
        save_user_name(user_id, new_name)
        await reply(f"✅  Name updated to {new_name}!")
        return

    state = get_state()

    # ── Status ────────────────────────────────────────
    if cmd == "status":
        lines = ["⚡ BELK CHARGING STATION", "─────────────────────", "", "⚡ Universal —  Bays 1 to 4", ""]
        for b in ["1","2","3","4"]:
            s = state[b]
            if s["user_phone"]:
                n = get_user_name(s["user_phone"]) or "Someone"
                lines.append(f"🔴 Bay {b}  {n}  ({elapsed(s['claimed_at'])})")
            else:
                lines.append(f"🟢 Bay {b}  Free")
        lines += ["", "⚡ Tesla Only — Bays 5 to 7", ""]
        for b in ["5","6","7"]:
            s = state[b]
            if s["user_phone"]:
                n = get_user_name(s["user_phone"]) or "Someone"
                lines.append(f"🔴 Bay {b}  {n}  ({elapsed(s['claimed_at'])})")
            else:
                lines.append(f"🟢 Bay {b}  Free")
        fu = sum(1 for b in ["1","2","3","4"] if not state[b]["user_phone"])
        ft = sum(1 for b in ["5","6","7"] if not state[b]["user_phone"])
        lines += ["", "─────────────────────", f"⚡ {fu}/4 universal free    ⚡ {ft}/3 Tesla free"]
        await reply("\n".join(lines))

    # ── Claim ─────────────────────────────────────────
    elif cmd == "claim" and len(parts) == 2:
        bid = parts[1]
        # Check if this person already has a bay
        already = [b for b, s in state.items() if s["user_phone"] == user_id]
        if already:
            await reply(f"⚠️  You already have Bay {already[0]} claimed!\n\nType  release {already[0]}  first before claiming another.")
        elif bid not in BAYS:
            await reply("❌  Invalid bay.\n\nUniversal: 1 2 3 4\nTesla only: 5 6 7")
        elif state[bid]["user_phone"]:
            n = get_user_name(state[bid]["user_phone"]) or "Someone"
            await reply(f"⚠️  Bay {bid} is taken by {n} ({elapsed(state[bid]['claimed_at'])}).\n\nType  status  to find a free bay.")
        else:
            claim(bid, user_id)
            await reply(f"✅  Bay {bid} claimed, {name}!\n\nType  release {bid}  when you're done.")

    # ── Release ───────────────────────────────────────
    elif cmd == "release" and len(parts) == 2:
        bid = parts[1]
        if bid not in BAYS:
            await reply("❌  Invalid bay.")
        elif not state[bid]["user_phone"]:
            await reply(f"ℹ️  Bay {bid} is already free!")
        elif state[bid]["user_phone"] != user_id:
            n = get_user_name(state[bid]["user_phone"]) or "someone else"
            await reply(f"⚠️  Bay {bid} was claimed by {n}.\nOnly they can release it.")
        else:
            t = elapsed(state[bid]["claimed_at"])
            release(bid)
            await reply(f"✅  Bay {bid} released after {t}. Thanks, {name}!")

    # ── Who ───────────────────────────────────────────
    elif cmd == "who":
        lines = ["👥  Currently Charging", "━━━━━━━━━━━━━━━", ""]
        found = False
        for bid, btype in BAYS.items():
            s = state[bid]
            if s["user_phone"]:
                n    = get_user_name(s["user_phone"]) or "Someone"
                icon = "🚗" if btype == "tesla" else "🔌"
                lines.append(f"{icon}  Bay {bid}  —  {n}  ({elapsed(s['claimed_at'])})")
                found = True
        if not found:
            await reply("🟢  All 7 bays are free!")
        else:
            await reply("\n".join(lines))

    # ── Admin force release ───────────────────────────
    elif cmd == "admin" and len(parts) == 3 and parts[1].lower() == "release":
        if user_id != ADMIN_ID:
            await reply("❌  You are not authorized to use admin commands.")
        else:
            bid = parts[2]
            if bid not in BAYS:
                await reply("❌  Invalid bay.")
            elif not state[bid]["user_phone"]:
                await reply(f"ℹ️  Bay {bid} is already free.")
            else:
                n = get_user_name(state[bid]["user_phone"]) or "Someone"
                t = elapsed(state[bid]["claimed_at"])
                # Notify the person being force-released
                try:
                    await context.bot.send_message(
                        chat_id=state[bid]["user_phone"],
                        text=f"⚠️  Bay {bid} has been released by admin after {t}.\nPlease unplug your car."
                    )
                except:
                    pass
                release(bid)
                await reply(f"✅  Bay {bid} force-released. Was held by {n} for {t}.")

    # ── Help ──────────────────────────────────────────
    else:
        await reply(
            "⚡ BELK CHARGING STATION\n"
            "━━━━━━━━━━━━━━━\n\n"
            "🔌 Universal  →  Bays 1 2 3 4\n"
            "🚗 Tesla only →  Bays 5 6 7\n\n"
            "Commands:\n"
            "· status        see all bays\n"
            "· claim 1       grab Bay 1\n"
            "· release 1     free Bay 1\n"
            "· who           who's charging\n"
            "· myname John   update your name\n"
            "· help          show this menu"
        )

# ── Main ──────────────────────────────────────────────
def main():
    init_db()
    TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    app   = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.job_queue.run_repeating(check_overtime, interval=1800, first=60)
    print("⚡ Telegram EV Bot is running!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
