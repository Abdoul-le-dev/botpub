"""
broadcast/cleanup.py — nettoyage de la base après diffusion.

Flow :
  1. À la fin d'un broadcast, si des utilisateurs sont BLOCKED ou DELETED,
     le moteur appelle propose_cleanup(bot, blocked_ids, deleted_ids, tag).
  2. Cette fonction stocke la liste sous un token en RAM (dict process-level),
     envoie un message admin avec 2 boutons inline (✅ Supprimer / ❌ Ignorer).
  3. Quand l'admin clique, le CallbackQueryHandler enregistré via
     register_broadcast_admin_handlers(app) exécute la suppression (users +
     categories) et renvoie un compte-rendu à tous les admins.

Le token expire au bout de CLEANUP_TOKEN_TTL_SECONDS (24h par défaut). Les
tokens sont uniquement en RAM : si le bot redémarre, le pending est perdu —
c'est acceptable car l'admin peut relancer un broadcast à sec pour re-détecter.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from db import get_db

from . import config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# STORAGE — tokens de nettoyage en attente
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingCleanup:
    blocked_ids: list[int] = field(default_factory=list)
    deleted_ids: list[int] = field(default_factory=list)
    tag: str = ""
    created_at: float = field(default_factory=time.monotonic)

    @property
    def total(self) -> int:
        return len(self.blocked_ids) + len(self.deleted_ids)


_pending: dict[str, PendingCleanup] = {}
_pending_lock = asyncio.Lock()


def _purge_expired() -> None:
    now = time.monotonic()
    dead = [
        k for k, v in _pending.items()
        if now - v.created_at > config.CLEANUP_TOKEN_TTL_SECONDS
    ]
    for k in dead:
        _pending.pop(k, None)


# ══════════════════════════════════════════════════════════════════════════════
# PROPOSITION AU MOMENT DU BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

async def propose_cleanup(
    bot,
    blocked_ids: list[int],
    deleted_ids: list[int],
    tag: str = "",
) -> None:
    """
    Envoie aux admins la proposition de nettoyage. Ne fait rien si les deux
    listes sont vides.
    """
    if not blocked_ids and not deleted_ids:
        return

    token = uuid.uuid4().hex[:12]
    async with _pending_lock:
        _purge_expired()
        _pending[token] = PendingCleanup(
            blocked_ids=list(blocked_ids),
            deleted_ids=list(deleted_ids),
            tag=tag,
        )

    tag_prefix = f"[{tag}] " if tag else ""
    text = (
        f"🧹 {tag_prefix}Nettoyage de base proposé\n\n"
        f"{len(blocked_ids)} utilisateurs ont bloqué le bot.\n"
        f"{len(deleted_ids)} utilisateurs semblent supprimés.\n\n"
        f"Souhaitez-vous les supprimer de la base ?"
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Supprimer", callback_data=f"{config.CB_CLEANUP_DELETE}{token}"),
        InlineKeyboardButton("❌ Ignorer",   callback_data=f"{config.CB_CLEANUP_IGNORE}{token}"),
    ]])

    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, reply_markup=kb)
        except Exception as e:
            logger.warning(f"[cleanup] échec envoi proposition à {admin_id} : {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SUPPRESSION EN DB
# ══════════════════════════════════════════════════════════════════════════════

async def _delete_users_from_db(telegram_ids: list[int]) -> tuple[int, int]:
    """
    Supprime les utilisateurs de `users` ET leurs entrées dans `categories`.
    Retourne (nb_users_deleted, nb_category_rows_deleted).

    Chunké par 1000 pour éviter les paquets MySQL trop volumineux.
    """
    if not telegram_ids:
        return 0, 0

    ids = list({int(i) for i in telegram_ids})
    users_deleted = 0
    cats_deleted = 0

    for start in range(0, len(ids), 1000):
        chunk = ids[start:start + 1000]
        placeholders = ",".join(["%s"] * len(chunk))
        try:
            async with get_db() as cur:
                # 1) categories
                await cur.execute(
                    f"DELETE FROM categories WHERE id_user IN ({placeholders})",
                    chunk,
                )
                cats_deleted += cur.rowcount or 0
                # 2) users
                await cur.execute(
                    f"DELETE FROM users WHERE telegram_id IN ({placeholders})",
                    chunk,
                )
                users_deleted += cur.rowcount or 0
        except Exception as e:
            logger.exception(f"[cleanup] échec DELETE chunk : {e}")

    return users_deleted, cats_deleted


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS CALLBACKQUERY
# ══════════════════════════════════════════════════════════════════════════════

async def _handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in config.ADMIN_IDS:
        try:
            await query.edit_message_text("⛔ Action réservée aux admins.")
        except Exception:
            pass
        return

    token = query.data[len(config.CB_CLEANUP_DELETE):]

    async with _pending_lock:
        _purge_expired()
        pending = _pending.pop(token, None)

    if pending is None:
        try:
            await query.edit_message_text("⚠️ Cette proposition a expiré ou a déjà été traitée.")
        except Exception:
            pass
        return

    all_ids = pending.blocked_ids + pending.deleted_ids
    tag_prefix = f"[{pending.tag}] " if pending.tag else ""

    logger.info(
        f"[cleanup] admin {query.from_user.id} → DELETE de {len(all_ids)} users "
        f"(blocked={len(pending.blocked_ids)}, deleted={len(pending.deleted_ids)})"
    )

    users_del, cats_del = await _delete_users_from_db(all_ids)

    recap = (
        f"🧹 {tag_prefix}Nettoyage effectué\n\n"
        f"Utilisateurs supprimés : {users_del}\n"
        f"Entrées catégories supprimées : {cats_del}\n"
        f"Blocked traités : {len(pending.blocked_ids)}\n"
        f"Deleted traités : {len(pending.deleted_ids)}\n"
        f"Effectué par : {query.from_user.id}"
    )

    try:
        await query.edit_message_text(recap)
    except Exception:
        pass

    # Diffuser aux autres admins
    for admin_id in config.ADMIN_IDS:
        if admin_id == query.from_user.id:
            continue
        try:
            await context.bot.send_message(chat_id=admin_id, text=recap)
        except Exception as e:
            logger.warning(f"[cleanup] échec envoi recap à {admin_id} : {e}")


async def _handle_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in config.ADMIN_IDS:
        return

    token = query.data[len(config.CB_CLEANUP_IGNORE):]

    async with _pending_lock:
        pending = _pending.pop(token, None)

    tag_prefix = f"[{pending.tag}] " if (pending and pending.tag) else ""
    try:
        await query.edit_message_text(f"❌ {tag_prefix}Nettoyage ignoré.")
    except Exception:
        pass

    logger.info(f"[cleanup] admin {query.from_user.id} a ignoré le nettoyage (token={token})")


def register_broadcast_admin_handlers(app: Application) -> None:
    """
    À appeler DEPUIS main.py après avoir créé `app` :

        from broadcast.cleanup import register_broadcast_admin_handlers
        register_broadcast_admin_handlers(app)

    Enregistre les 2 CallbackQueryHandlers pour les boutons de nettoyage.
    Les patterns sont préfixés (bcclean:) donc n'entrent pas en conflit avec
    les autres handlers du bot (level:, resume_registration, etc.).
    """
    app.add_handler(
        CallbackQueryHandler(_handle_delete, pattern=f"^{config.CB_CLEANUP_DELETE}")
    )
    app.add_handler(
        CallbackQueryHandler(_handle_ignore, pattern=f"^{config.CB_CLEANUP_IGNORE}")
    )
    logger.info("[cleanup] handlers admin enregistrés")
