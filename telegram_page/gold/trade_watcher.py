"""
trade_watcher.py — Surveillance prix + fermeture automatique (v8).

REMPLACE gold_engine.watch_gold_price() pour la partie fermeture de
session. Ne dépend plus de session_registry / lifecycle / tp_notifier
(fichiers non fournis pour ce refactor, et dont le comportement —
notifications personnalisées par membre basées sur un capital/lot
stocké — n'a plus de sens depuis le passage au signal brut).

COMPORTEMENT (confirmé explicitement) :
  - SL touché  → session fermée (phase 'sl_touched'), comptes
    simulation clôturés, notif ADMIN uniquement. AUCUN message membre.
  - TP3 touché → session fermée (phase 'tp3_reached'), comptes
    simulation clôturés, notif ADMIN uniquement. AUCUN message membre.
  - TP1/TP2 touchés → phase mise à jour (utile pour les stats/dashboard
    admin), mais AUCUNE notification — ni membre, ni admin. La
    surveillance continue vers TP3/SL.

Intégration (remplace l'ancien appel à watch_gold_price dans
signal_broadcast.py) :
    from trade_watcher import watch_and_close, set_bot
    set_bot(application.bot)              # une fois, au démarrage
    asyncio.create_task(watch_and_close(session_id))
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from db import get_db
from telegram_page.gold.gold_engine import get_live_gold_price, close_simulation_trades
from telegram_page.gold.trade_management_notifs import notify_opted_in_members

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066

_bot = None


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def _watch_interval() -> int:
    """Cadence de surveillance selon l'heure — copie locale volontaire
    (évite une dépendance à un symbole privé de gold_engine.py)."""
    h = datetime.now().hour
    if 8 <= h < 20:
        return 120
    elif h < 8:
        return 1800
    else:
        return 300


async def _get_session(session_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def _close_session(session_id: int, close_type: str):
    """
    Fermeture définitive (SL ou TP3) : écriture directe (événement
    terminal et rare, pas besoin de write-behind ici, contrairement à
    gold_buffer qui gère les mises à jour de phase à haute fréquence).
    """
    phase = {"sl": "sl_touched", "tp3": "tp3_reached"}[close_type]
    async with get_db() as cur:
        await cur.execute("""
            UPDATE gold_trade_sessions
            SET current_phase = %s, closed_at = COALESCE(closed_at, NOW())
            WHERE id = %s
        """, (phase, session_id))
    try:
        await close_simulation_trades(session_id, close_type)
    except Exception as e:
        logger.error(f"[trade_watcher] close_simulation_trades sid={session_id}: {e}",
                     exc_info=True)


async def _set_intermediate_phase(session_id: int, phase: str):
    """TP1/TP2 : juste pour les stats — aucune notification."""
    async with get_db() as cur:
        await cur.execute(
            "UPDATE gold_trade_sessions SET current_phase = %s WHERE id = %s",
            (phase, session_id),
        )


async def _notify_admin_closed(session_id: int, close_type: str):
    if not _bot:
        return
    label = {"sl": "SL touché", "tp3": "TP3 atteint 🏆"}[close_type]
    try:
        await _bot.send_message(chat_id=ADMIN_ID,
                                 text=f"📉 Session #{session_id} clôturée — {label}")
    except Exception:
        pass


async def _notify_opted_in_tp(session: dict, tp_level: int):
    """Notif TP personnalisée pour les membres opt-in (voir
    trade_management_notifs.py). Best-effort — une erreur ici ne doit
    jamais interrompre la surveillance SL/TP3."""
    if not _bot:
        return
    try:
        report = await notify_opted_in_members(_bot, session, tp_level)
        if report.get("notified"):
            logger.info(f"[trade_watcher] TP{tp_level} — "
                        f"{report['notified']} membre(s) opt-in notifié(s)")
    except Exception as e:
        logger.error(f"[trade_watcher] notify_opted_in_members TP{tp_level}: {e}",
                     exc_info=True)


async def watch_and_close(session_id: int):
    logger.info(f"[trade_watcher] démarrage surveillance session #{session_id}")

    while True:
        session = await _get_session(session_id)
        if not session:
            break
        phase = session["current_phase"]
        if phase in ("closed", "sl_touched", "tp3_reached", "cancelled"):
            break

        price = await get_live_gold_price()
        interval = _watch_interval()
        if price is None:
            await asyncio.sleep(interval)
            continue

        async with get_db() as cur:
            await cur.execute("""
                UPDATE gold_trade_sessions
                SET live_price_last = %s, live_price_updated_at = NOW()
                WHERE id = %s
            """, (price, session_id))

        direction = session["direction"]
        tp1, tp2, tp3 = session.get("tp1"), session.get("tp2"), session.get("tp3")
        sl = session["sl"]

        # ── SL touché → fermeture définitive (silencieux, tout le monde) ──
        if (direction == "buy" and price <= sl) or (direction == "sell" and price >= sl):
            await _close_session(session_id, "sl")
            await _notify_admin_closed(session_id, "sl")
            break

        # ── TP3 touché → fermeture définitive + notif opt-in ────────────
        if tp3 and phase not in ("tp3_reached", "closed"):
            if (direction == "buy" and price >= tp3) or (direction == "sell" and price <= tp3):
                await _close_session(session_id, "tp3")
                await _notify_admin_closed(session_id, "tp3")
                await _notify_opted_in_tp(session, 3)
                break

        # ── TP2 touché → phase mise à jour + notif opt-in, on continue ──
        if tp2 and phase not in ("tp2_reached", "tp3_reached", "closed"):
            if (direction == "buy" and price >= tp2) or (direction == "sell" and price <= tp2):
                await _set_intermediate_phase(session_id, "tp2_reached")
                phase = "tp2_reached"
                await _notify_opted_in_tp(session, 2)

        # ── TP1 touché → idem ─────────────────────────────────────────────
        if tp1 and phase not in ("tp1_reached", "tp2_reached", "tp3_reached", "closed"):
            if (direction == "buy" and price >= tp1) or (direction == "sell" and price <= tp1):
                await _set_intermediate_phase(session_id, "tp1_reached")
                phase = "tp1_reached"
                await _notify_opted_in_tp(session, 1)

        await asyncio.sleep(interval)