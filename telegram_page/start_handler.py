from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

from db import get_db

VALIDATION_START_PARAM = "fdkgoldsaison"


async def process_start_link(update, context, user_id: int, first_name: str, start_param: str):
    print(f"[start_handler] process_start_link user={user_id} param={start_param}")

    async with get_db() as cur:
        await cur.execute(
            "INSERT IGNORE INTO users (telegram_id, name) VALUES (%s, %s)",
            (user_id, first_name)
        )

    if not start_param:
        return None

    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM invite_links WHERE start_param=%s AND is_active=1",
            (start_param,)
        )
        link = await cur.fetchone()

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

        await cur.execute(
            "SELECT id FROM invite_link_stats WHERE link_id=%s AND user_id=%s AND event='register'",
            (link["id"], user_id)
        )
        already = await cur.fetchone()

        if not already:
            await cur.execute(
                "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (%s,%s,%s)",
                (link["id"], user_id, "click")
            )
            await cur.execute(
                "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (%s,%s,%s)",
                (link["id"], user_id, "register")
            )
            await cur.execute(
                "UPDATE invite_links SET quota_used = quota_used + 1 WHERE id=%s",
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
    async with get_db() as cur:
        await cur.execute(
            "INSERT INTO invite_link_stats (link_id, user_id, event) VALUES (%s,%s,%s)",
            (link_id, user_id, "subscribe")
        )