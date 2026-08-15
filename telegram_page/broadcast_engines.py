"""
broadcast_engine.py — v5 MySQL async
Moteur d'envoi de messages.

Gestion médias optimisée :
  - Fichier local (/media/uuid.jpg) → upload au premier envoi → file_id Telegram récupéré
  - Envois suivants (broadcast) → file_id réutilisé directement, pas de re-upload
"""

import asyncio
import logging
import httpx

from datetime import datetime
from pathlib import Path
from typing import Optional

from db import get_db

ADMIN_ID = 571718066
logger   = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# RÉSOLUTION DES DESTINATAIRES
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_user_ids(
    category:         Optional[str],
    user_ids:         Optional[list],
    exclude_user_ids: Optional[list],
    filters:          Optional[dict],
) -> list:
    exclude = set(exclude_user_ids or [])

    if user_ids:
        return [uid for uid in user_ids if uid not in exclude]

    async with get_db() as cur:
        if category == "all":
            await cur.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            )
            rows = await cur.fetchall()
            return [r["telegram_id"] for r in rows if r["telegram_id"] not in exclude]

        if category:
            query  = "SELECT id_user FROM categories WHERE name_categorie = %s"
            params = [category]

            if filters:
                if filters.get("created_after"):
                    query += " AND created_at >= %s"
                    params.append(filters["created_after"])
                if filters.get("created_before"):
                    query += " AND created_at <= %s"
                    params.append(filters["created_before"])

            await cur.execute(query, params)
            rows = await cur.fetchall()
            return [r["id_user"] for r in rows if r["id_user"] not in exclude]

    return []


# ══════════════════════════════════════════════════════════════════════════════
# PERSONNALISATION
# ══════════════════════════════════════════════════════════════════════════════

async def _get_prenom(telegram_id: int) -> str:
    try:
        async with get_db() as cur:
            await cur.execute(
                "SELECT name FROM users WHERE telegram_id = %s", (telegram_id,)
            )
            row = await cur.fetchone()
        if row and row["name"]:
            p = row["name"].strip()
            if 1 <= len(p) <= 15:
                return p
    except Exception:
        pass
    return "l'ami"


async def _inject_variables(text: str, telegram_id: int, variables: Optional[dict]) -> str:
    if not text:
        return text
    text = text.replace("+prenom", await _get_prenom(telegram_id))
    if variables:
        for key, value in variables.items():
            text = text.replace(key, str(value))
    return text


# ══════════════════════════════════════════════════════════════════════════════
# GESTION MÉDIA
# ══════════════════════════════════════════════════════════════════════════════

def _is_local_file(media_url: str) -> bool:
    if not media_url:
        return False
    return media_url.startswith("/") or media_url.startswith("./") or Path(media_url).exists()


def _open_local_file(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _extract_file_id(msg, fmt: str) -> Optional[str]:
    try:
        if fmt == "image"         and msg.photo:    return msg.photo[-1].file_id
        if fmt == "video"         and msg.video:    return msg.video.file_id
        if fmt == "document"      and msg.document: return msg.document.file_id
        if fmt == "image+text"    and msg.photo:    return msg.photo[-1].file_id
        if fmt == "video+text"    and msg.video:    return msg.video.file_id
        if fmt == "document+text" and msg.document: return msg.document.file_id
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ENVOI UNITAIRE
# ══════════════════════════════════════════════════════════════════════════════

async def _send_one(
    bot,
    user_id:   int,
    fmt:       str,
    text:      str,
    media_url: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    if media_url:
        media_url = media_url.lstrip("/")

    try:
        media = None
        if media_url:
            if _is_local_file(media_url):
                media = _open_local_file(media_url)
                if media is None:
                    logger.warning(f"Fichier local introuvable : {media_url}")
                    if text:
                        await bot.send_message(chat_id=user_id, text=text)
                    return True, None
            else:
                media = media_url

        msg              = None
        telegram_file_id = None

        if fmt == "text":
            await bot.send_message(chat_id=user_id, text=text)
        elif fmt == "image":
            msg = await bot.send_photo(chat_id=user_id, photo=media)
        elif fmt == "video":
            msg = await bot.send_video(chat_id=user_id, video=media)
        elif fmt == "document":
            msg = await bot.send_document(chat_id=user_id, document=media)
        elif fmt == "image+text":
            if len(text) > 1000:
                await bot.send_message(chat_id=user_id, text=text)
            msg = await bot.send_photo(chat_id=user_id, photo=media, caption=text)
        elif fmt == "video+text":
            if len(text) > 1000:
                await bot.send_message(chat_id=user_id, text=text)
            msg = await bot.send_video(chat_id=user_id, video=media, caption=text)
        elif fmt == "document+text":
            await bot.send_message(chat_id=user_id, text=text)
            msg = await bot.send_document(chat_id=user_id, document=media)
        else:
            await bot.send_message(chat_id=user_id, text=text)

        if msg and _is_local_file(media_url or ""):
            telegram_file_id = _extract_file_id(msg, fmt)

        return True, telegram_file_id

    except Exception as e:
        logger.warning(f"Échec envoi uid={user_id} : {e}")
        return False, None


# ══════════════════════════════════════════════════════════════════════════════
# RAPPORT & WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

async def _save_report(report: dict, category: str, fmt: str, message: str):
    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO broadcast_history
                (tag, category, format, message, total, sent, errors, started_at, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            report["tag"], category, fmt, message,
            report["total"], report["sent"], report["errors"],
            report["started_at"], report["finished_at"],
        ))


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
    return text[:max_length - 1] + "…" if len(text) > max_length else text


# ══════════════════════════════════════════════════════════════════════════════
# MOTEUR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

async def broadcast_engine(bot, payload: dict) -> dict:
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

    # ── 2. Validation ─────────────────────────────────────────────────────────
    if not message and fmt == "text":
        return {"error": "message vide"}

    if fmt in {"image", "video", "document", "image+text", "video+text", "document+text"} and not media_url:
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
    final_ids = await _resolve_user_ids(category, user_ids, exclude_user_ids, filters)
    print(final_ids)
    total     = len(final_ids)

    if total == 0:
        await _notify_admin(bot, ADMIN_ID, "❌ Aucun destinataire trouvé. Diffusion annulée.")
        return {"error": "aucun destinataire trouvé", "tag": tag}

    # ── 5. Démarrage ──────────────────────────────────────────────────────────
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag_label  = f"[{tag}] " if tag else ""
    est_min    = round(total * delay / 60, 2)

    await _notify_admin(bot, ADMIN_ID,
        f"📤 {tag_label}Diffusion démarrée\n"
        f"Destinataires : {total}\nFormat : {fmt}\nDurée estimée : {est_min} min")

    # ── 6. Boucle d'envoi ─────────────────────────────────────────────────────
    sent           = 0
    errors         = 0
    cached_file_id = None

    for idx, user_id in enumerate(final_ids, start=1):
        personalized_text = await _inject_variables(message, user_id, variables)
        effective_media   = cached_file_id if cached_file_id else media_url

        success, new_file_id = await _send_one(bot, user_id, fmt, personalized_text, effective_media)

        if new_file_id and not cached_file_id:
            cached_file_id = new_file_id
            logger.info(f"[{tag}] file_id Telegram mis en cache : {cached_file_id}")

        if not success and retry:
            await asyncio.sleep(1)
            success, new_file_id = await _send_one(bot, user_id, fmt, personalized_text, effective_media)
            if new_file_id and not cached_file_id:
                cached_file_id = new_file_id

        if success: sent   += 1
        else:       errors += 1

        logger.debug(f"[{tag}] {idx}/{total} — uid={user_id} — {'✓' if success else '✗'}")

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

    await _save_report(report, category or "", fmt, message)
    return report