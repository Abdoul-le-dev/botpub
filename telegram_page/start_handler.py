from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

from db import get_db   # ← pool MySQL

VALIDATION_START_PARAM = "fdkgoldsaison"


async def process_start_link(update, context, user_id: int, first_name: str, start_param: str):
    print(f"[start_handler] process_start_link user={user_id} param={start_param}")

    with get_db() as conn:
        conn.execute(
            "INSERT IGNORE INTO users (telegram_id, name) VALUES (?, ?)",
            (user_id, first_name)
        )

    if not start_param:
        return None

    with get_db() as conn:
        link = conn.execute(
            "SELECT * FROM invite_links WHERE start_param=? AND is_active=1",
            (start_param,)
        ).fetchone()

        if not link:
            print(f"[start_handler] lien introuvable pour param={start_param}")
            return None

        link = dict(link)
        print(f"[start_handler] lien trouvé id={link['id']} form_id={link.get('form_id')}")

        if link["quota_max"] and link["quota_used"] >= link["quota_max"]:
            await update.message.reply_text("Ce lien a atteint sa limite d'utilisation.")
            return None

        if link["expires_at"] and str(link["expires_at"]) < datetime.now().isoformat():
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
                "UPDATE invite_links SET quota_used = quota_used + 1 WHERE id=?",
                (link["id"],)
            )

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
    print(f"[start_handler] record_form_completion user={user_id} link={link_id}")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (?,?,?)",
            (link_id, user_id, "subscribe")
        )