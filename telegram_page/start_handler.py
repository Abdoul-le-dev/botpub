"""
Patch à appliquer dans start_handler.py
─────────────────────────────────────────
Ajoute la détection du start_param "validation" pour lancer
le flow de validation d'abonnement au lieu d'un formulaire classique.
"""

import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

DB = "preinscriptions.db"
VALIDATION_START_PARAM = "fdkgoldsaison"   # ← doit correspondre au start_param dans invite_links


def get_conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys = ON")
    return c


async def process_start_link(update, context, user_id: int, first_name: str, start_param: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (telegram_id, name) VALUES (?,?)",
        (user_id, first_name)
    )
    conn.commit()

    if not start_param:
        conn.close()
        return None

    # ── Interception du flow validation ───────────────────────────────────
    if start_param == VALIDATION_START_PARAM:
        conn.close()
        # Importer ici pour éviter les imports circulaires
        from validation_handler import _start as validation_start
        await validation_start(update, context)
        return "__validation__"   # signal à form_engine de ne pas continuer

    # ── Logique existante ──────────────────────────────────────────────────
    link = conn.execute(
        "SELECT * FROM invite_links WHERE start_param=? AND is_active=1",
        (start_param,)
    ).fetchone()

    if not link:
        conn.close()
        return None

    link = dict(link)

    if link["quota_max"] and link["quota_used"] >= link["quota_max"]:
        conn.close()
        await update.message.reply_text("Ce lien a atteint sa limite d'utilisation.")
        return None

    if link["expires_at"] and link["expires_at"] < datetime.now().isoformat():
        conn.close()
        await update.message.reply_text("Ce lien a expiré.")
        return None

    already = conn.execute(
        "SELECT id FROM invite_link_stats WHERE link_id=? AND user_id=? AND event='register'",
        (link["id"], user_id)
    ).fetchone()

    if not already:
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
    conn.close()

    if link["auto_category"]:
        try:
            from telegram_page.categorie import add_members_to_category
            await add_members_to_category(link["auto_category"], [user_id])
        except Exception as e:
            print(f"[start_handler] categorie error: {e}")

    if link.get("form_id"):
        context.user_data["pending_link_id"] = link["id"]
        return link["form_id"]

    return None


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