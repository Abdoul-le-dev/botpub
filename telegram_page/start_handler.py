from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

from db import get_db

VALIDATION_START_PARAM = "fdkgoldsaison"

# Sentinelle retournée quand un scénario d'engagement a traité l'update.
# form_engine.py doit reconnaître cette valeur et terminer sa conversation.
ENGAGEMENT_SENTINEL = "__engagement__"


async def process_start_link(update, context, user_id: int, first_name: str, start_param: str):
    print(f"[start_handler] process_start_link user={user_id} param={start_param}")

    # ────────────────────────────────────────────────────────────────────
    # ROUTAGE ENGAGEMENT — priorité absolue.
    # Les deep links commençant par "fdk_concept_capital_" sont routés
    # vers le module d'engagement AVANT toute autre logique (pas d'INSERT
    # users bidon avec phone='0000', pas de lookup dans invite_links).
    # ────────────────────────────────────────────────────────────────────
    if start_param:
        try:
            from engagement import is_engagement_payload, handle_deeplink
            if is_engagement_payload(start_param):
                await handle_deeplink(update, context, start_param)
                return ENGAGEMENT_SENTINEL
        except Exception as e:
            # On log mais on ne casse pas le flow existant : si le module
            # engagement plante à l'import ou au dispatch, on retombe sur
            # le comportement historique.
            print(f"[start_handler] engagement routing error: {e}")

    async with get_db() as cur:
        await cur.execute(
            "SELECT 1 FROM users WHERE telegram_id = %s", (user_id,)
        )
        if not await cur.fetchone():
            await cur.execute(
                "INSERT INTO users (telegram_id, name, phone, created_at) VALUES (%s, %s, '0000', NOW())",
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