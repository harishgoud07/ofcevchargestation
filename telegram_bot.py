from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import os, psycopg2, psycopg2.extras, asyncio

# ── Bay config ───────────────────────────────────────
BAYS = {
    "1": "universal", "2": "universal",
    "3": "universal", "4": "universal",
    "5": "tesla",     "6": "tesla",
    "7": "tesla",
}
# ─────────────────────────────────────────────────────

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

# ── Bot logic ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    body    = update.message.text.strip()
    parts   = body.split()
    cmd     = parts[0].lower() if parts else ""

    async def reply(text):
        await update.message.reply_text(text, parse_mode="Markdown")

    name = get_user_name(user_id)

    # ── First time user ───────────────────────────────
    if not name:
        if body and not any(body.lower().startswith(c) for c in
                            ["status","claim","release","who","help","myname","/start"]):
            save_user_name(user_id, body.strip())
            await reply(
                f"👋 Welcome, *{body.strip()}*!\n\n"
                "You're all set!\n\n"
                "⚡ *Belk Charging Station*\n"
                "🔌 Universal: Bays 1–4\n"
                "⚡ Tesla only: Bays 5–7\n\n"
                "• *status* — see all bays\n"
                "• *claim 1* — claim Bay 1\n"
                "• *release 1* — free Bay 1\n"
                "• *who* — see who's charging\n"
                "• *help* — show this menu"
            )
        else:
            await reply(
                "👋 Welcome to *Belk Charging Station*!\n\n"
                "What's your name? _(e.g. reply: Sarah)_"
            )
        return

    # ── Update name ───────────────────────────────────
    if cmd == "myname" and len(parts) >= 2:
        new_name = " ".join(parts[1:])
        save_user_name(user_id, new_name)
        await reply(f"✅ Name updated to *{new_name}*!")
        return

    state = get_state()

    # ── Status ────────────────────────────────────────
    if cmd == "status":
        lines = ["⚡ *Belk Charging Station*\n", "🔌 *Universal (Bays 1–4)*"]
        for b in ["1","2","3","4"]:
            s = state[b]
            if s["user_phone"]:
                n = get_user_name(s["user_phone"]) or "Someone"
                lines.append(f"  🔴 Bay {b} — {n} ({elapsed(s['claimed_at'])})")
            else:
                lines.append(f"  ✅ Bay {b} — Free")
        lines.append("\n⚡ *Tesla Only (Bays 5–7)*")
        for b in ["5","6","7"]:
            s = state[b]
            if s["user_phone"]:
                n = get_user_name(s["user_phone"]) or "Someone"
                lines.append(f"  🔴 Bay {b} — {n} ({elapsed(s['claimed_at'])})")
            else:
                lines.append(f"  ✅ Bay {b} — Free")
        fu = sum(1 for b in ["1","2","3","4"] if not state[b]["user_phone"])
        ft = sum(1 for b in ["5","6","7"] if not state[b]["user_phone"])
        lines.append(f"\n🔌 {fu}/4 universal  ⚡ {ft}/3 Tesla free")
        await reply("\n".join(lines))

    # ── Claim ─────────────────────────────────────────
    elif cmd == "claim" and len(parts) == 2:
        bid = parts[1]
        if bid not in BAYS:
            await reply("❌ Invalid bay. Universal: 1–4 🔌  Tesla: 5–7 ⚡")
        elif state[bid]["user_phone"]:
            n = get_user_name(state[bid]["user_phone"]) or "Someone"
            await reply(
                f"⚠️ Bay {bid} is taken by *{n}* "
                f"({elapsed(state[bid]['claimed_at'])}).\n"
                f"Send *status* to find a free bay."
            )
        else:
            label = "Tesla-only ⚡" if BAYS[bid] == "tesla" else "Universal 🔌"
            warn  = "\n⚠️ This is a *Tesla-only* bay." if BAYS[bid] == "tesla" else ""
            claim(bid, user_id)
            await reply(f"✅ Bay {bid} ({label}) claimed, *{name}*!{warn}\nSend *release {bid}* when done.")

    # ── Release ───────────────────────────────────────
    elif cmd == "release" and len(parts) == 2:
        bid = parts[1]
        if bid not in BAYS:
            await reply("❌ Invalid bay. Universal: 1–4  Tesla: 5–7")
        elif not state[bid]["user_phone"]:
            await reply(f"Bay {bid} is already free!")
        elif state[bid]["user_phone"] != user_id:
            n = get_user_name(state[bid]["user_phone"]) or "someone else"
            await reply(f"⚠️ Bay {bid} was claimed by *{n}*. Only they can release it.")
        else:
            t = elapsed(state[bid]["claimed_at"])
            release(bid)
            await reply(f"🔌 Bay {bid} released after {t}. Thanks, *{name}*!")

    # ── Who ───────────────────────────────────────────
    elif cmd == "who":
        lines = ["👤 *Currently charging:*\n"]
        found = False
        for bid, btype in BAYS.items():
            s = state[bid]
            if s["user_phone"]:
                n    = get_user_name(s["user_phone"]) or "Someone"
                icon = "⚡" if btype == "tesla" else "🔌"
                lines.append(f"{icon} Bay {bid}: *{n}* · {elapsed(s['claimed_at'])}")
                found = True
        await reply("\n".join(lines) if found else "All 7 bays are free! 🎉")

    # ── Help / default ────────────────────────────────
    else:
        await reply(
            "⚡ *Belk Charging Station*\n\n"
            "🔌 Universal: Bays 1–4\n"
            "⚡ Tesla only: Bays 5–7\n\n"
            "• *status* — see all bays\n"
            "• *claim [1-7]* — claim a bay\n"
            "• *release [1-7]* — free your bay\n"
            "• *who* — see who's charging\n"
            "• *myname John* — update your name\n"
            "• *help* — show this menu"
        )

# ── Overtime alert ────────────────────────────────────
bot_app = None

def check_overtime():
    try:
        state = get_state()
        now   = datetime.now().timestamp()
        for bid, bay in state.items():
            if not bay["user_phone"] or not bay["claimed_at"]: continue
            hours = (now - float(bay["claimed_at"])) / 3600
            if hours >= 7:
                name  = get_user_name(bay["user_phone"]) or "there"
                btype = "Tesla-only ⚡" if BAYS[bid] == "tesla" else "Universal 🔌"
                if bot_app:
                    asyncio.run_coroutine_threadsafe(
                        bot_app.bot.send_message(
                            chat_id=bay["user_phone"],
                            text=(
                                f"⏰ *Belk Charging Station Alert*\n\n"
                                f"Hi {name}, you've had Bay {bid} ({btype}) "
                                f"for *{int(hours)} hours*.\n\n"
                                f"Please unplug if you're done so others can charge. 🙏"
                            ),
                            parse_mode="Markdown"
                        ),
                        asyncio.get_event_loop()
                    )
    except Exception as e:
        print(f"Overtime check error: {e}")

# ── Main ──────────────────────────────────────────────
def main():
    global bot_app

    init_db()

    TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot_app.add_handler(MessageHandler(filters.COMMAND, handle_message))

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_overtime, "interval", minutes=30)
    scheduler.start()

    print("Telegram EV Bot is running ⚡")
    bot_app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
