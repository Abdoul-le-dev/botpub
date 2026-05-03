"""
broadcast_engine.py — Moteur d'envoi de messages.

Gestion médias optimisée :
  - Fichier local (/media/uuid.jpg) → upload au premier envoi → file_id Telegram récupéré
  - Envois suivants (broadcast) → file_id réutilisé directement, pas de re-upload
  - Chat direct (1 user) → fichier local envoyé directement
"""

import asyncio
import sqlite3
import logging
import httpx

from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Configuration ──────────────────────────────────────────────────────────────

DB_PATH  = "preinscriptions.db"
ADMIN_ID = 571718066

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES
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
) -> list:
    exclude = set(exclude_user_ids or [])

    print(category)
    with _conn() as conn:
        if user_ids:
            return [uid for uid in user_ids if uid not in exclude]

        if category == "all":
            rows = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
            return [r["telegram_id"] for r in rows if r["telegram_id"] not in exclude]

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
# PERSONNALISATION
# ══════════════════════════════════════════════════════════════════════════════

def _get_prenom(telegram_id: int) -> str:
    with _conn() as conn:
        row = conn.execute(
            "SELECT name FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    if row and row["name"]:
        p = row["name"].strip()
        if 1 <= len(p) <= 15:
            return p
    return "l'ami"


def _inject_variables(text: str, telegram_id: int, variables: Optional[dict]) -> str:
    if not text:
        return text
    text = text.replace("+prenom", _get_prenom(telegram_id))
    if variables:
        for key, value in variables.items():
            text = text.replace(key, str(value))
    return text


# ══════════════════════════════════════════════════════════════════════════════
# GESTION MÉDIA — résolution fichier local → media Telegram
# ══════════════════════════════════════════════════════════════════════════════

def _is_local_file(media_url: str) -> bool:
    """Retourne True si media_url est un chemin local (/media/...) et non un file_id Telegram."""
    if not media_url:
        return False
    return media_url.startswith("/") or media_url.startswith("./") or Path(media_url).exists()


def _open_local_file(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ENVOI UNITAIRE — retourne (success, file_id_telegram)
# ══════════════════════════════════════════════════════════════════════════════

async def _send_one(
    bot,
    user_id:   int,
    fmt:       str,
    text:      str,
    media_url: Optional[str] = None,
) -> tuple:
    """
    Envoie un message à un seul utilisateur.

    Retourne : (success: bool, telegram_file_id: str | None)

    telegram_file_id est retourné après le premier envoi d'un fichier local.
    Le broadcast_engine le réutilise pour tous les envois suivants (pas de re-upload).

    Formats gérés :
      text         → send_message
      image        → send_photo
      video        → send_video
      document     → send_document  (PDF, Word, Excel, etc.)
      image+text   → send_message + send_photo
      video+text   → send_message + send_video
      document+text→ send_message + send_document
    """
    telegram_file_id = None
    if media: 
        media_url = media_url.lstrip("/")
        print(media_url)
    try:
        # ── Résoudre le média : fichier local ou file_id Telegram ────────────
        media = None
        print
        if media_url:
            if _is_local_file(media_url):
                media = _open_local_file(media_url)
                if media is None:
                    logger.warning(f"Fichier local introuvable : {media_url}")
                    # Fallback : envoyer le texte seul
                    if text:
                        await bot.send_message(chat_id=user_id, text=text)
                    return True, None
            else:
                # file_id Telegram ou URL publique
                media = media_url

        # ── Envoi selon le format ────────────────────────────────────────────
        if fmt == "text":
            await bot.send_message(chat_id=user_id, text=text)

        elif fmt == "image":
            msg = await bot.send_photo(chat_id=user_id, photo=media)
            

        elif fmt == "video":
            msg = await bot.send_video(chat_id=user_id, video=media)
            

        elif fmt == "document":
            msg = await bot.send_document(chat_id=user_id, document=media)
            

        elif fmt == "image+text":
            if len(text)> 1000:
                await bot.send_message(chat_id=user_id, text=text)
            msg = await bot.send_photo(chat_id=user_id, photo=media, caption=text)
            

        elif fmt == "video+text":
            if len(text)> 1000 : 
                await bot.send_message(chat_id=user_id, text=text)
            msg = await bot.send_video(chat_id=user_id, video=media, caption=text)
            

        elif fmt == "document+text":
            await bot.send_message(chat_id=user_id, text=text)
            msg = await bot.send_document(chat_id=user_id, document=media)
            

        else:
            # Format inconnu → texte seul
            await bot.send_message(chat_id=user_id, text=text)

      
        return True, telegram_file_id

    except Exception as e:
        logger.warning(f"Échec envoi uid={user_id} : {e}")
        return False, None


# ══════════════════════════════════════════════════════════════════════════════
# RAPPORT & WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

def _save_report(report: dict, category: str, fmt: str, message: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO broadcast_history
                (tag, category, format, message, total, sent, errors, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report["tag"], category, fmt, message,
            report["total"], report["sent"], report["errors"],
            report["started_at"], report["finished_at"]
        ))
        conn.commit()


async def _notify_admin(bot, admin_id: int, message: str):
    try:
        await bot.send_message(chat_id=admin_id, text=message)
    except Exception as e:
        logger.error(f"Impossible de notifier l'admin : {e}")


async def _call_webhook(callback_url: str, report: dict):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(callback_url, json=report)
    except Exception as e:
        logger.error(f"Webhook échoué ({callback_url}) : {e}")


def _limit_text(text: str, max_length: int = 4096) -> str:
    if len(text) > max_length:
        return text[:max_length - 1] + "…"
    return text


# ══════════════════════════════════════════════════════════════════════════════
# MOTEUR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

async def broadcast_engine(bot, payload: dict) -> dict:
    """
    Moteur central d'envoi de messages.

    Optimisation médias :
      - Si media_url est un fichier local → upload au premier envoi
      - file_id Telegram récupéré → réutilisé pour tous les envois suivants
      - Pas de re-upload pour chaque destinataire
    """
    print(payload)
    print("0")
    # ── 1. Extraction payload ─────────────────────────────────────────────────
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
    print("1")

    # ── 2. Validation ─────────────────────────────────────────────────────────
    if not message and fmt == "text":
        return {"error": "message vide"}

    if fmt in {"image", "video", "document", "image+text", "video+text", "document+text"} and not media_url:
        print(fmt)
        return {"error": "media_url manquant"}

    if not category and not user_ids:
        return {"error": "aucun destinataire défini"}

    # ── 3. Envoi différé ──────────────────────────────────────────────────────
    if scheduled_at:
        try:
            target_dt = datetime.strptime(scheduled_at, "%Y-%m-%d %H:%M:%S")
            wait_sec  = (target_dt - datetime.now()).total_seconds()
            if wait_sec > 0:
                tag_label = f"[{tag}] " if tag else ""
                await _notify_admin(bot, ADMIN_ID,
                    f"⏳ {tag_label}Envoi planifié dans {round(wait_sec/60, 1)} min ({scheduled_at})")
                await asyncio.sleep(wait_sec)
        except ValueError:
            return {"error": "format scheduled_at invalide, utilise YYYY-MM-DD HH:MM:SS"}

    # ── 4. Résolution destinataires ───────────────────────────────────────────
    print("2")
    final_ids = _resolve_user_ids(category, user_ids, exclude_user_ids, filters)
    print(final_ids)
    total     = len(final_ids)
    print(total)

    if total == 0:
        await _notify_admin(bot, ADMIN_ID, "❌ Aucun destinataire trouvé. Diffusion annulée.")
        return {"error": "aucun destinataire trouvé", "tag": tag}
    print("3")
    # ── 5. Démarrage ──────────────────────────────────────────────────────────
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag_label  = f"[{tag}] " if tag else ""
    est_min    = round(total * delay / 60, 2)

    print("4")
    await _notify_admin(bot, ADMIN_ID,
        f"📤 {tag_label}Diffusion démarrée\n"
        f"Destinataires : {total}\nFormat : {fmt}\nDurée estimée : {est_min} min")

    # ── 6. Boucle d'envoi ─────────────────────────────────────────────────────
    sent             = 0
    errors           = 0
    cached_file_id   = None   # file_id Telegram récupéré après le 1er envoi

    for idx, user_id in enumerate(final_ids, start=1):

        personalized_text = _inject_variables(message, user_id, variables)

        # Après le 1er envoi d'un fichier local, utiliser le file_id mis en cache
        effective_media = cached_file_id if cached_file_id else media_url

        success, new_file_id = await _send_one(
            bot, user_id, fmt, personalized_text, effective_media
        )

        # Mettre en cache le file_id Telegram récupéré au 1er envoi
        if new_file_id and not cached_file_id:
            cached_file_id = new_file_id
            logger.info(f"[{tag}] file_id Telegram mis en cache : {cached_file_id}")

        # Retry si échec
        if not success and retry:
            await asyncio.sleep(1)
            success, new_file_id = await _send_one(
                bot, user_id, fmt, personalized_text, effective_media
            )
            if new_file_id and not cached_file_id:
                cached_file_id = new_file_id

        if success:
            sent += 1
        else:
            errors += 1

        logger.debug(f"[{tag}] {idx}/{total} — uid={user_id} — {'✓' if success else '✗'}")

        # Notifications progression
        if idx == total // 3:
            await _notify_admin(bot, ADMIN_ID, f"📊 {tag_label}1/3 envoyé ({sent}/{total})")
        elif idx == (2 * total) // 3:
            await _notify_admin(bot, ADMIN_ID, f"📊 {tag_label}2/3 envoyé ({sent}/{total})")

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

    await _notify_admin(bot, ADMIN_ID,
        f"✅ {tag_label}Diffusion terminée\n"
        f"Envoyés : {sent}/{total}\nErreurs : {errors}\n"
        f"Durée : {started_at} → {finished_at}")

    if callback_url:
        await _call_webhook(callback_url, report)

    _save_report(report, category, fmt, message)
    return report