import sqlite3
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from datetime import datetime

DB = "preinscriptions.db"

def get_conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys = ON")
    return c

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    args = context.args

    start_param = args[0] if args else None

    # Enregistrer le user + récupérer le lien en une seule connexion
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (telegram_id, name) VALUES (?,?)",
        (user_id, user.first_name)
    )
    conn.commit()

    link = None
    if start_param:
        link = conn.execute(
            "SELECT * FROM invite_links WHERE start_param=? AND is_active=1",
            (start_param,)
        ).fetchone()

    if link:
        link = dict(link)

        if link["quota_max"] and link["quota_used"] >= link["quota_max"]:
            conn.close()
            await update.message.reply_text("Ce lien a atteint sa limite d'utilisation.")
            return

        if link["expires_at"] and link["expires_at"] < datetime.now().isoformat():
            conn.close()
            await update.message.reply_text("Ce lien a expiré.")
            return

        conn.execute(
            "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (?,?,?)",
            (link["id"], user_id, "click")
        )
        conn.execute(
            "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (?,?,?)",
            (link["id"], user_id, "register")
        )
        conn.execute(
            "UPDATE invite_links SET quota_used=quota_used+1 WHERE id=?",
            (link["id"],)
        )
        conn.commit()
        conn.close()  # ← FERMÉ avant tout appel externe

        # Appels externes APRÈS fermeture connexion
        if link["auto_category"]:
            try:
                from telegram_page.categorie import add_members_to_category
                await add_members_to_category(link["auto_category"], [user_id])
            except Exception as e:
                print(f"[start_handler] categorie error: {e}")

        if link.get("form_id"):
            try:
                from form.form_engine import send_form_to_user
                await send_form_to_user(context.bot, user_id, link["form_id"], context=context)
                context.user_data["pending_link_id"] = link["id"]
                return
            except Exception as e:
                print(f"[start_handler] form error: {e}")

    else:
        conn.close()

    await update.message.reply_text(f"Bienvenue {user.first_name} ! 👋")


async def record_form_completion(bot, user_id: int, link_id: int):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (?,?,?)",
            (link_id, user_id, "subscribe")
        )
        conn.commit()
    finally:
        conn.close()