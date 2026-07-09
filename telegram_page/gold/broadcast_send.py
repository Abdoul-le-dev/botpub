"""
broadcast_send.py — Envoi massif du teaser Gold v7.1.

Remplace send_gold_teaser (v6) qui touchait à signal_cache / user_state v6.

Chaîne d'appel côté route POST /gold/sessions :
    1. open_new_session()         → registry + snapshot + state + buffer
    2. send_teaser_broadcast()    → envoi massif à débit contrôlé
    3. mark_broadcast_done()      → status = ACTIVE, clics acceptés

Notes :
  - Le callback du disclaimer inclut désormais la VERSION (via make_callback_data)
    → tout click sur un vieux message d'une session précédente est rejeté par le guard.
  - Les users ayant bloqué le bot sont retirés de la catégorie source
    et ajoutés à clients_bloquer, comme en v6.
  - Les comptes simulation restent appliqués via _apply_to_simulation_accounts
    de gold_engine (v5) — logique inchangée.
"""

import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import Forbidden

from db import get_db

from telegram_page.gold.callback_guard import make_callback_data
from telegram_page.gold.session_snapshot import SessionSnapshot
from telegram_page.gold.gold_buffer import gold_buffer_v7

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066

BROADCAST_RATE   = 25
CATEGORY_TARGET  = "clients_actifs"
CATEGORY_BLOCKED = "clients_bloquer"


DISCLAIMER_TEXT = (
    "📌 *Avant d'accéder au trade du jour*\n\n"
    "Ce que nous partageons ici est le fruit de notre propre analyse — "
    "ce n'est pas un conseil financier, ni une recommandation d'investissement.\n\n"
    "Le trading comporte des risques réels, y compris la perte de votre capital. "
    "Chaque décision que vous prenez vous appartient entièrement.\n\n"
    "En continuant, vous confirmez que :\n"
    "✅ Vous tradez avec des fonds que vous pouvez vous permettre de perdre\n"
    "✅ Vous suivez nos recommandations à titre informatif uniquement\n"
    "✅ Vous êtes seul responsable de vos positions\n\n"
    "_Nous partageons nos analyses par passion et transparence. "
    "Traitez chaque trade comme une opportunité d'apprentissage._"
)


def _fmt_now_discrete() -> str:
    now   = datetime.now()
    jours = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    mois  = ["jan", "fév", "mar", "avr", "mai", "juin",
             "juil", "août", "sep", "oct", "nov", "déc"]
    return f"{jours[now.weekday()]} {now.day:02d} {mois[now.month-1]} · {now.strftime('%H:%M')}"


def _disclaimer_message(snap: SessionSnapshot) -> str:
    dir_label = "Achat (Buy)" if snap.direction == "buy" else "Vente (Sell)"
    return (f"🔔 ─────────────────────\n"
            f"*Signal Gold disponible*\n"
            f"_{dir_label} · {_fmt_now_discrete()}_\n"
            f"─────────────────────\n\n"
            + DISCLAIMER_TEXT)


def _disclaimer_keyboard(session_id: int, version: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ J'ai compris — Voir le trade",
        callback_data=make_callback_data("disclaimer_ok", session_id, version),
    )]])


async def _get_category_user_ids(category: str) -> list:
    async with get_db() as cur:
        if category == "all":
            await cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
            return [r["telegram_id"] for r in await cur.fetchall()]
        await cur.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s", (category,)
        )
        return [r["id_user"] for r in await cur.fetchall()]


async def _handle_blocked_users(blocked_ids: list, source_category: str) -> dict:
    """Retire les users bloqués de la catégorie source, les ajoute à clients_bloquer."""
    result = {"blocked": len(blocked_ids), "removed": 0, "added": 0, "already_in": 0}
    if not blocked_ids:
        return result
    try:
        from telegram_page.categorie import (
            add_members_to_category, remove_member_from_category,
        )
        add_res = await add_members_to_category(
            CATEGORY_BLOCKED, blocked_ids, added_by="broadcast_blocked"
        )
        result["added"]      = add_res.get("added", 0)
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


async def send_teaser_broadcast(bot, snap: SessionSnapshot, *,
                                  category: str = None,
                                  preload_capital: bool = True) -> dict:
    """
    Envoi massif du disclaimer à toute la catégorie cible.

    Étapes :
      1. Récupère la liste des destinataires
      2. Précharge le Weekly Capital Cache pour tous → 0 SQL au click
      3. Applique le trade aux comptes simulation (une fois, hors chemin chaud)
      4. Envoi concurrent à BROADCAST_RATE msg/s
      5. Retire les users bloqués de la catégorie source

    Retourne un rapport avec les métriques d'envoi.
    """
    session_id = snap.session_id
    version    = snap.version
    category   = category or CATEGORY_TARGET

    # 1. Destinataires
    user_ids = await _get_category_user_ids(category)
    total = len(user_ids)

    # 2. Précharge le Weekly Capital Cache — critique pour le fast path v7.1.
    #    Après cet appel, la majorité des users auront leur capital en RAM.
    if preload_capital:
        try:
            from .weekly_capital_cache import weekly_capital
            n_loaded = await weekly_capital.preload(user_ids)
            logger.info(f"[broadcast] capital préchargé pour {n_loaded}/{total} users")
        except Exception as e:
            logger.error(f"[broadcast] preload capital échoué: {e}", exc_info=True)

    # 3. Comptes simulation (logique inchangée v5)
    try:
        from telegram_page.gold.gold_engine import _apply_to_simulation_accounts
        # On passe le dict équivalent à un enregistrement SQL de session pour
        # que la logique v5 fonctionne à l'identique.
        session_dict = {
            "id":          snap.session_id,
            "season_id":   snap.season_id,
            "direction":   snap.direction,
            "entry_price": snap.entry_price,
            "sl":          snap.sl,
            "tp1":         snap.tp1, "tp2": snap.tp2, "tp3": snap.tp3,
        }
        await _apply_to_simulation_accounts(session_id, session_dict)
    except Exception as e:
        logger.error(f"[broadcast] simulation: {e}", exc_info=True)

    if total == 0:
        return {"total": 0, "sent": 0, "errors": 0, "session_id": session_id}

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"📤 *Envoi teaser Gold v7 démarré*\n"
                  f"Session : #{session_id}v{version}\n"
                  f"Cible : {category} | Destinataires : {total}\n"
                  f"Débit : {BROADCAST_RATE} msg/s → ~{total // BROADCAST_RATE // 60 + 1} min"),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # 4. Envoi
    text = _disclaimer_message(snap)
    kbd  = _disclaimer_keyboard(session_id, version)
    sent = errors = 0
    blocked_ids: list = []
    sem = asyncio.Semaphore(BROADCAST_RATE)

    async def _send_one(uid: int):
        nonlocal sent, errors
        async with sem:
            try:
                await bot.send_message(chat_id=uid, text=text,
                                        parse_mode="Markdown", reply_markup=kbd)
                gold_buffer_v7.add_step(session_id, version, uid, "teaser")
                sent += 1
            except Forbidden:
                blocked_ids.append(uid)
            except Exception as e:
                logger.debug(f"[teaser] uid={uid}: {e}")
                errors += 1
            await asyncio.sleep(1)   # 1 slot = 1s → débit = BROADCAST_RATE/s

    tasks = [asyncio.create_task(_send_one(uid)) for uid in user_ids]

    async def _progress():
        while any(not t.done() for t in tasks):
            await asyncio.sleep(60)
            try:
                await bot.send_message(ADMIN_ID,
                    f"📊 Teaser Gold — {sent}/{total} envoyés...")
            except Exception:
                pass

    progress_task = asyncio.create_task(_progress())
    await asyncio.gather(*tasks, return_exceptions=True)
    progress_task.cancel()

    # 5. Traitement des bloqués (après envoi, pas pendant)
    blocked_report = await _handle_blocked_users(blocked_ids, category)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"✅ *Teaser Gold terminé — session #{session_id}v{version}*\n\n"
                  f"Envoyés : {sent}/{total}\n"
                  f"Erreurs : {errors}\n\n"
                  f"🚫 Bloqués : {blocked_report['blocked']} "
                  f"(retirés {blocked_report['removed']}, "
                  f"ajoutés {blocked_report['added']})"),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # Watch prix live (inchangé)
    try:
        from telegram_page.gold.gold_engine import watch_gold_price
        asyncio.create_task(watch_gold_price(session_id))
    except Exception as e:
        logger.error(f"[broadcast] watch_gold_price: {e}")

    return {
        "total":      total,
        "sent":       sent,
        "errors":     errors,
        "blocked":    blocked_report,
        "session_id": session_id,
        "version":    version,
    }