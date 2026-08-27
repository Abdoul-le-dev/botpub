"""
signal_broadcast.py — Envoi brut du signal Gold (v8).

REMPLACE : broadcast_send.py (teaser) + broadcast_v7.py (disclaimer_ok /
access / capital_input / _process_trade_full).

PRINCIPE
  Le signal part IMMÉDIATEMENT et EN BRUT (entry/SL/TP) à toute la
  catégorie ciblée — exactement comme un envoi manuel. Il n'y a plus
  aucune étape intermédiaire (pas de disclaimer par clic, pas de
  saisie de capital, pas de calcul de lot personnalisé à l'envoi).

  Seule condition avant réception : le membre doit avoir validé le
  disclaimer hebdomadaire (voir disclaimer_gate.py). Si ce n'est pas
  le cas, il ne reçoit PAS le signal — il reçoit (ou a déjà reçu) la
  demande de validation, et recevra les signaux suivants dès qu'il
  aura validé.

  Le calcul de lot personnalisé n'existe plus à l'envoi : il devient
  un outil à la demande (voir interactive_tools.py — "Money
  management"), qui ne stocke rien.

CLAVIER SOUS LE SIGNAL
  [💰 Money management]  [🆘 Besoin d'aide]
  [🎁 Me réabonner -30%]           ← seulement si abonnement à J-10

Intégration :
    from signal_broadcast import send_signal
    await send_signal(bot, session_id)   # session_id déjà créé via
                                          # gold_engine.create_gold_session()
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import Forbidden

from db import get_db
from telegram_page.gold.disclaimer_gate import split_by_consent, send_consent_request

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066

BROADCAST_RATE  = 25
CATEGORY_TARGET = "clients_actifs"
CATEGORY_BLOCKED = "clients_bloquer"

RESUB_WINDOW_DAYS = 10
RESUB_URL = "https://fdkvip.com/reabonnement"   # TODO: ajuster si besoin


# ══════════════════════════════════════════════════════════════════════════════
# Formatage du message — brut, minimal
# ══════════════════════════════════════════════════════════════════════════════

def build_signal_message(session: dict) -> str:
    """
    Message brut, minimal : paire, sens, niveaux. Rien d'autre.
    Un seul point d'aération entre l'en-tête et les niveaux — pas de
    séparateurs, pas de rappel de risque, pas de mise en forme en trop.
    """
    direction = session["direction"]
    dir_label = "BUY 📈" if direction == "buy" else "SELL 📉"

    lines = [
        f"🟡 *XAU/USD*  ·  {dir_label}",
        "",
        f"Entrée   `{session['entry_price']}`",
    ]
    if session.get("tp1"):
        lines.append(f"TP1        `{session['tp1']}`")
    if session.get("tp2"):
        lines.append(f"TP2        `{session['tp2']}`")
    if session.get("tp3"):
        lines.append(f"TP3        `{session['tp3']}`")
    lines.append(f"SL          `{session['sl']}`")
    return "\n".join(lines)


def build_signal_keyboard(session_id: int, *, show_resub: bool) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("💰 Money management",
                              callback_data=f"mm_open_{session_id}"),
        InlineKeyboardButton("🆘 Besoin d'aide",
                              callback_data=f"help_request_{session_id}"),
    ]]
    if show_resub:
        rows.append([InlineKeyboardButton(
            "🎁 Me réabonner -30%", url=RESUB_URL,
        )])
    return InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Destinataires
# ══════════════════════════════════════════════════════════════════════════════

async def _get_category_user_ids(category: str) -> list[int]:
    async with get_db() as cur:
        if category == "all":
            await cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
            return [r["telegram_id"] for r in await cur.fetchall()]
        await cur.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s", (category,)
        )
        return [r["id_user"] for r in await cur.fetchall()]


async def _preload_resub_flags(user_ids: list[int]) -> dict[int, bool]:
    """
    Précharge, pour tous les destinataires, si leur abonnement se
    termine dans <= RESUB_WINDOW_DAYS jours.

    NOTE : ajuster le nom de colonne `subscription_end_at` selon le
    schéma réel de la table `users`.
    """
    if not user_ids:
        return {}
    flags: dict[int, bool] = {}
    chunk_size = 1000
    async with get_db() as cur:
        for i in range(0, len(user_ids), chunk_size):
            chunk = user_ids[i:i + chunk_size]
            ph = ",".join(["%s"] * len(chunk))
            await cur.execute(f"""
                SELECT telegram_id,
                       DATEDIFF(subscription_end_at, NOW()) AS days_left
                FROM users
                WHERE telegram_id IN ({ph})
            """, chunk)
            for r in await cur.fetchall():
                days_left = r["days_left"]
                flags[int(r["telegram_id"])] = (
                    days_left is not None and 0 <= days_left <= RESUB_WINDOW_DAYS
                )
    return flags


async def _handle_blocked_users(blocked_ids: list[int], source_category: str) -> dict:
    result = {"blocked": len(blocked_ids), "removed": 0, "added": 0, "already_in": 0}
    if not blocked_ids:
        return result
    try:
        from telegram_page.categorie import (
            add_members_to_category, remove_member_from_category,
        )
        add_res = await add_members_to_category(
            CATEGORY_BLOCKED, blocked_ids, added_by="signal_broadcast_blocked"
        )
        result["added"] = add_res.get("added", 0)
        result["already_in"] = add_res.get("ignored", 0)
        if source_category and source_category != "all":
            for uid in blocked_ids:
                try:
                    await remove_member_from_category(source_category, uid)
                    result["removed"] += 1
                except Exception as e:
                    logger.warning(f"[blocked] retrait uid={uid} de '{source_category}' échoué: {e}")
    except Exception as e:
        logger.error(f"[blocked] traitement échoué: {e}", exc_info=True)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Envoi
# ══════════════════════════════════════════════════════════════════════════════

async def _notify_pending(bot, pending_ids: list[int], session_id: int):
    """Envoie la demande de validation aux membres pas à jour, en la liant
    au signal en cours pour qu'il leur soit envoyé dès qu'ils valident."""
    for uid in pending_ids:
        try:
            await send_consent_request(bot, uid, pending_session_id=session_id)
        except Exception as e:
            logger.debug(f"[signal_broadcast] consent request uid={uid}: {e}")
        await asyncio.sleep(0.04)


async def send_signal_to_user(bot, uid: int, session_id: int):
    """
    Envoie le signal brut à UN seul membre — utilisé quand un membre
    valide son disclaimer après coup et doit recevoir le signal en cours.
    """
    session = await _get_session(session_id)
    resub = await _preload_resub_flags([uid])
    kbd = build_signal_keyboard(session_id, show_resub=resub.get(uid, False))
    try:
        await bot.send_message(chat_id=uid, text=build_signal_message(session),
                                parse_mode="Markdown", reply_markup=kbd)
    except Forbidden:
        pass


async def _get_session(session_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
    if not row:
        raise RuntimeError(f"Session #{session_id} introuvable.")
    return dict(row)


async def send_signal(bot, session_id: int, *, category: str = None) -> dict:
    """
    Envoi brut et immédiat du signal à toute la catégorie ciblée.

    - Les membres SANS consentement disclaimer valide cette semaine ne
      reçoivent PAS le signal (voir disclaimer_gate.split_by_consent) ;
      ils reçoivent/ont reçu la demande de validation à part.
    - Aucun calcul de lot, aucune saisie de capital ici.
    """
    category = category or CATEGORY_TARGET
    session = await _get_session(session_id)

    all_ids = await _get_category_user_ids(category)
    consented_ids, pending_ids = await split_by_consent(all_ids)

    total = len(consented_ids)
    if pending_ids:
        # Pas de signal pour eux tout de suite : on leur envoie la
        # validation hebdo à la place. Dès qu'ils valident (voir
        # disclaimer_gate.handle_disclaimer_weekly_ok), ils reçoivent
        # AUTOMATIQUEMENT ce même signal (session_id retenu dans la
        # demande de consentement).
        logger.info(
            f"[signal_broadcast] {len(pending_ids)} membres en attente de "
            f"validation disclaimer — reçoivent la demande à la place du signal."
        )
        asyncio.create_task(_notify_pending(bot, pending_ids, session_id))

    if total == 0:
        return {"total": 0, "sent": 0, "errors": 0,
                "pending_consent": len(pending_ids), "session_id": session_id}

    resub_flags = await _preload_resub_flags(consented_ids)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"📤 *Envoi signal Gold démarré*\n"
                  f"Session : #{session_id}\n"
                  f"Cible : {category} | Destinataires : {total} "
                  f"(en attente disclaimer : {len(pending_ids)})"),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    text = build_signal_message(session)
    sent = errors = 0
    blocked_ids: list[int] = []
    sem = asyncio.Semaphore(BROADCAST_RATE)

    async def _send_one(uid: int):
        nonlocal sent, errors
        async with sem:
            try:
                kbd = build_signal_keyboard(session_id, show_resub=resub_flags.get(uid, False))
                await bot.send_message(chat_id=uid, text=text,
                                        parse_mode="Markdown", reply_markup=kbd)
                sent += 1
            except Forbidden:
                blocked_ids.append(uid)
            except Exception as e:
                logger.debug(f"[signal] uid={uid}: {e}")
                errors += 1
            await asyncio.sleep(1)

    tasks = [asyncio.create_task(_send_one(uid)) for uid in consented_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

    blocked_report = await _handle_blocked_users(blocked_ids, category)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"✅ *Signal Gold terminé — session #{session_id}*\n\n"
                  f"Envoyés : {sent}/{total}\n"
                  f"Erreurs : {errors}\n"
                  f"En attente disclaimer : {len(pending_ids)}\n"
                  f"🚫 Bloqués : {blocked_report['blocked']}"),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # Surveillance prix live — inchangé, ne dépend pas du parcours membre
    try:
        from telegram_page.gold.gold_engine import watch_gold_price
        asyncio.create_task(watch_gold_price(session_id))
    except Exception as e:
        logger.error(f"[signal_broadcast] watch_gold_price: {e}")

    return {"total": total, "sent": sent, "errors": errors,
            "pending_consent": len(pending_ids),
            "blocked": blocked_report, "session_id": session_id}