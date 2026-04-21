"""
broadcast_engine.py — Moteur d'envoi de messages de masse.

Fonction principale : broadcast_engine(bot, payload)

Payload attendu :
{
    "message":          "Bonjour +prenom, profite de +offre !",
    "format":           "text" | "image" | "video" | "image+text" | "video+text",
    "media_url":        "file_id_telegram_ou_path_local" | None,
    "category":         "nom_categorie" | None,
    "user_ids":         [123, 456] | None,
    "scheduled_at":     "2026-04-20 14:30:00" | None,
    "delay":            0.1,
    "retry":            True,
    "exclude_user_ids": [789],
    "variables":        {"+offre": "50%", "+lien": "https://..."},
    "filters":          {"created_after": "2025-01-01", "created_before": "2026-01-01"},
    "tag":              "promo_avril",
    "callback_url":     "https://monsite.com/webhook" | None
}

Usage depuis le bot Telegram :
    asyncio.create_task(broadcast_engine(context.bot, payload))

Usage depuis FastAPI :
    await broadcast_engine(bot, payload)
"""

import asyncio
import sqlite3
import logging
import httpx

from datetime import datetime
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

DB_PATH  = "preinscriptions.db"
ADMIN_ID = 571718066



logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# ── BASE DE DONNÉES ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _resolve_user_ids(
    category: Optional[str],
    user_ids: Optional[list],
    exclude_user_ids: Optional[list],
    filters: Optional[dict]
) -> list[int]:
    """
    Résout la liste finale des destinataires selon :
    - category  → tous les users de cette catégorie (avec filtres optionnels)
    - user_ids  → liste directe fournie
    - exclude   → soustrait les IDs exclus
    - filters   → created_after / created_before appliqués sur la catégorie
    """
    exclude = set(exclude_user_ids or [])

    with _conn() as conn:

        # ── Cas 1 : liste directe ────────────────────────────────────────────
        if user_ids:
            return [uid for uid in user_ids if uid not in exclude]

        # ── Cas 2 : tous les users ───────────────────────────────────────────
        if category == "all":
            rows = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
            return [r["telegram_id"] for r in rows if r["telegram_id"] not in exclude]

        # ── Cas 3 : par catégorie avec filtres optionnels ────────────────────
        if category:
            query  = "SELECT id_user FROM categories WHERE name_categorie = ?"
            params = [category]

            if filters:
                if filters.get("created_after"):
                    query += " AND created_at >= ?"
                    params.append(filters["created_after"])
                if filters.get("created_before"):
                    query += " AND created_at <= ?"
                    params.append(filters["created_before"])

            rows = conn.execute(query, params).fetchall()
            return [r["id_user"] for r in rows if r["id_user"] not in exclude]

    return []


# ══════════════════════════════════════════════════════════════════════════════
# ── PERSONNALISATION ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _get_prenom(telegram_id: int) -> str:
    """Retourne le prénom depuis la DB, ou 'l'ami' si absent/trop long."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT name FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    if row and row["name"]:
        p = row["name"].strip()
        if 1 <= len(p) <= 15:

            print(p)
            return p
    return "l'ami"


def _inject_variables(text: str, telegram_id: int, variables: Optional[dict]) -> str:
    """
    Injecte dans le texte :
    - +prenom  → prénom du user
    - toutes les clés du dict variables (ex: {"+offre": "50%"})
    """
    if not text:
        return text

    prenom = _get_prenom(telegram_id)
    text   = text.replace("+prenom", prenom)

    if variables:
        for key, value in variables.items():
            text = text.replace(key, str(value))

    return text


# ══════════════════════════════════════════════════════════════════════════════
# ── ENVOI UNITAIRE ────────────────────────────────────────────════════════════
# ══════════════════════════════════════════════════════════════════════════════

async def _send_one(bot, user_id: int, fmt: str, text: str, media_url: Optional[str]) -> bool:
    """
    Envoie un message à un seul utilisateur selon le format.
    Retourne True si succès, False si échec.
    """

    print('yes')
    # try:
    #     if fmt == "text":
    #         await bot.send_message(chat_id=user_id, text=text)

    #     elif fmt == "image":
    #         await bot.send_photo(chat_id=user_id, photo=media_url)

    #     elif fmt == "video":
    #         await bot.send_video(chat_id=user_id, video=media_url)

    #     elif fmt == "image+text":
    #         await bot.send_message(chat_id=user_id, text=text)
    #         await bot.send_photo(chat_id=user_id, photo=media_url)

    #     elif fmt == "video+text":
    #         await bot.send_message(chat_id=user_id, text=text)
    #         await bot.send_video(chat_id=user_id, video=media_url)

    #     else:
    #         # Format inconnu → texte seul par sécurité
    #         await bot.send_message(chat_id=user_id, text=text)

    #     return True

    # except Exception as e:
    #     logger.warning(f"Échec envoi uid={user_id} : {e}")
    #     return False


# ══════════════════════════════════════════════════════════════════════════════
# ── RAPPORT & WEBHOOK ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def _save_report(report: dict, category: str, fmt: str, message: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO broadcast_history 
                (tag, category, format, message, total, sent, errors, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report["tag"],
            category,
            fmt,
            message,
            report["total"],
            report["sent"],
            report["errors"],
            report["started_at"],
            report["finished_at"]
        ))
        conn.commit()
async def _notify_admin(bot, admin_id: int, message: str):
    """Envoie un message de suivi à l'admin Telegram."""
    try:
        await bot.send_message(chat_id=admin_id, text=message)
    except Exception as e:
        logger.error(f"Impossible de notifier l'admin : {e}")


async def _call_webhook(callback_url: str, report: dict):
    """Appelle le webhook avec le rapport final en JSON."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(callback_url, json=report)
        logger.info(f"Webhook appelé : {callback_url}")
    except Exception as e:
        logger.error(f"Webhook échoué ({callback_url}) : {e}")


def _limit_text(text: str, max_length: int = 4096) -> str:
    """Coupe le texte à max_length caractères si nécessaire."""
    if len(text) > max_length:
        return text[:max_length - 1] + "…"
    return text


# ══════════════════════════════════════════════════════════════════════════════
# ── MOTEUR PRINCIPAL ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

async def broadcast_engine(bot, payload: dict) -> dict:
    """
    Moteur central d'envoi de messages de masse.

    Paramètres (via payload dict) :
        message          : str             — texte du message (supporte +prenom et variables)
        format           : str             — "text"|"image"|"video"|"image+text"|"video+text"
        media_url        : str | None      — file_id Telegram ou chemin local
        category         : str | None      — nom de la catégorie en DB (ou "all")
        user_ids         : list | None     — liste directe d'IDs Telegram
        scheduled_at     : str | None      — "YYYY-MM-DD HH:MM:SS" ou None pour envoi immédiat
        delay            : float           — délai en secondes entre chaque envoi (défaut 0.1)
        retry            : bool            — retenter une fois en cas d'échec (défaut True)
        exclude_user_ids : list | None     — IDs à exclure de l'envoi
        variables        : dict | None     — variables à injecter ex: {"+offre": "50%"}
        filters          : dict | None     — {"created_after": "...", "created_before": "..."}
        tag              : str | None      — label de la campagne pour le suivi
        callback_url     : str | None      — URL webhook appelée à la fin de l'envoi

    Retourne un dict rapport :
        {
            "tag":      "promo_avril",
            "total":    2000,
            "sent":     1980,
            "errors":   20,
            "started_at":  "...",
            "finished_at": "..."
        }
    """

    # ── 1. Extraction du payload ──────────────────────────────────────────────
    message          = _limit_text(payload.get("message", ""))
    fmt              = payload.get("format", "text")
    media_url        = payload.get("media_url")
    category         = payload.get("category")
    user_ids         = payload.get("user_ids")
    scheduled_at     = payload.get("scheduled_at")
    delay            = float(payload.get("delay", 0.1))
    retry            = bool(payload.get("retry", True))
    exclude_user_ids = payload.get("exclude_user_ids") or []
    variables        = payload.get("variables") or {}
    filters          = payload.get("filters") or {}
    tag              = payload.get("tag", "")
    callback_url     = payload.get("callback_url")

    # ── 2. Validation de base ─────────────────────────────────────────────────
    if not message and fmt == "text":
        logger.error("broadcast_engine : message vide pour format texte.")
        return {"error": "message vide"}

    if fmt in {"image", "video", "image+text", "video+text"} and not media_url:
        logger.error("broadcast_engine : media_url manquant pour format média.")
        return {"error": "media_url manquant"}

    if not category and not user_ids:
        logger.error("broadcast_engine : ni category ni user_ids fournis.")
        return {"error": "aucun destinataire défini"}

    # ── 3. Envoi différé si scheduled_at ─────────────────────────────────────
    if scheduled_at:
        try:
            target_dt = datetime.strptime(scheduled_at, "%Y-%m-%d %H:%M:%S")
            now       = datetime.now()
            wait_sec  = (target_dt - now).total_seconds()
            print(wait_sec)

            if wait_sec > 0:
                tag_label = f"[{tag}] " if tag else ""
                await _notify_admin(
                    bot, ADMIN_ID,
                    f"⏳ {tag_label}Envoi planifié dans {round(wait_sec/60, 1)} min "
                    f"({scheduled_at})"
                )
                await asyncio.sleep(wait_sec)

        except ValueError:
            logger.error(f"broadcast_engine : format scheduled_at invalide → {scheduled_at}")
            return {"error": "format scheduled_at invalide, utilise YYYY-MM-DD HH:MM:SS"}

    # ── 4. Résolution des destinataires ───────────────────────────────────────
    final_ids = _resolve_user_ids(category, user_ids, exclude_user_ids, filters)
    total     = len(final_ids)

    if total == 0:
        await _notify_admin(bot, ADMIN_ID, "❌ Aucun destinataire trouvé. Diffusion annulée.")
        return {"error": "aucun destinataire trouvé", "tag": tag}

    # ── 5. Rapport de démarrage ───────────────────────────────────────────────
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    est_min    = round(total * delay / 60, 2)
    tag_label  = f"[{tag}] " if tag else ""

    await _notify_admin(
        bot, ADMIN_ID,
        f"📤 {tag_label}Diffusion démarrée\n"
        f"Destinataires : {total}\n"
        f"Format : {fmt}\n"
        f"Durée estimée : {est_min} min"
    )

    print(f"📤 {tag_label}Diffusion démarrée\n")

    # ── 6. Boucle d'envoi ─────────────────────────────────────────────────────
    sent   = 0
    errors = 0

    for idx, user_id in enumerate(final_ids, start=1):

        # Injection des variables personnalisées pour ce user
        personalized_text = _inject_variables(message, user_id, variables)

        print(personalized_text)

        # Tentative d'envoi
        success = await _send_one(bot, user_id, fmt, personalized_text, media_url)

        # Retry si échec et retry activé
        if not success and retry:
            await asyncio.sleep(1)
            success = await _send_one(bot, user_id, fmt, personalized_text, media_url)

        if success:
            sent += 1
        else:
            errors += 1

        logger.debug(f"[{tag}] {idx}/{total} — uid={user_id} — {'✓' if success else '✗'}")

        # Notifications de progression (1/3, 2/3, fin)
        if idx == total // 3:
            await _notify_admin(bot, ADMIN_ID, f"📊 {tag_label}1/3 envoyé ({sent}/{total})")
        elif idx == (2 * total) // 3:
            await _notify_admin(bot, ADMIN_ID, f"📊 {tag_label}2/3 envoyé ({sent}/{total})")
        elif idx == total:
            pass  # rapport final juste après la boucle

        await asyncio.sleep(delay)

    # ── 7. Rapport final ──────────────────────────────────────────────────────
    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report = {
        "tag":         tag,
        "total":       total,
        "sent":        sent,
        "errors":      errors,
        "started_at":  started_at,
        "finished_at": finished_at,
    }

    await _notify_admin(
        bot, ADMIN_ID,
        f"✅ {tag_label}Diffusion terminée\n"
        f"Envoyés  : {sent}/{total}\n"
        f"Erreurs  : {errors}\n"
        f"Durée    : {started_at} → {finished_at}"
    )

    # ── 8. Webhook callback ───────────────────────────────────────────────────
    if callback_url:
        await _call_webhook(callback_url, report)

    _save_report(report, category, fmt, message)
    return report


# ══════════════════════════════════════════════════════════════════════════════
# ── EXEMPLES D'UTILISATION ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

"""
# ── Depuis le ConversationHandler Telegram ────────────────────────────────────

async def get_text(update, context):
    context.user_data["text_content"] = update.message.text

    payload = {
        "message":      context.user_data["text_content"],
        "format":       context.user_data["format"],
        "media_url":    context.user_data.get("media_file_id"),
        "category":     context.user_data["who"],
        "user_ids":     None,
        "scheduled_at": None,
        "delay":        0.1,
        "retry":        True,
        "tag":          "envoi_manuel",
        "callback_url": None,
    }

    asyncio.create_task(broadcast_engine(context.bot, payload))
    await update.message.reply_text("✅ Diffusion lancée !")
    return ConversationHandler.END


# ── Depuis FastAPI ─────────────────────────────────────────────────────────────

from fastapi import FastAPI
from broadcast_engine import broadcast_engine

app = FastAPI()

@app.post("/broadcast")
async def api_broadcast(payload: dict):
    report = await broadcast_engine(bot, payload)
    return report


# ── Envoi planifié ─────────────────────────────────────────────────────────────

payload = {
    "message":      "Bonjour +prenom, ton accès expire bientôt !",
    "format":       "text",
    "category":     "challenge10000usd",
    "scheduled_at": "2026-04-21 09:00:00",
    "tag":          "rappel_expiration",
    "retry":        True,
    "delay":        0.15,
}
asyncio.create_task(broadcast_engine(bot, payload))


# ── Envoi avec variables et exclusions ────────────────────────────────────────

payload = {
    "message":          "Bonjour +prenom ! Profite de +offre avec le code +code",
    "format":           "image+text",
    "media_url":        "AgACAgIAAxk...",   # file_id Telegram
    "category":         "challenge10000usd",
    "exclude_user_ids": [123456, 789012],
    "variables":        {"+offre": "50% de réduction", "+code": "PROMO50"},
    "filters":          {"created_after": "2025-01-01"},
    "delay":            0.2,
    "retry":            True,
    "tag":              "promo_avril",
    "callback_url":     "https://monsite.com/webhook/broadcast",
}
asyncio.create_task(broadcast_engine(bot, payload))
"""