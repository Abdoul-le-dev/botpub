"""
tp_notifier.py — Notifications TP/SL v7 (remplace trigger_tp_reached / trigger_sl_touched v5).

Différences clés vs v5 :
  1. Lit d'abord les CalcContext en RAM (user_state_v7.confirmed_calcs).
     Aucun SELECT dans le chemin de notification. Le "montant gagné"
     que reçoit chaque user vient du calc qui a été FIGÉ à son moment
     de trade — donc cohérent avec le trade qu'il a réellement pris.
  2. Fallback SQL : si la session est déjà fermée (RAM purgée), on lit
     gold_member_entries pour reconstruire la liste des users.
  3. Messages TP/SL viennent du snapshot immutable (snap.rule_for) —
     jamais d'un cache qui aurait pu changer entre-temps.
  4. Débit d'envoi 20 msg/s pour respecter les limites Telegram sur
     de très gros volumes de confirmés.

Point d'entrée :
    await notify_tp_reached(bot, session_id, tp_level)
    await notify_sl_touched(bot, session_id)
    await notify_admin_session_closed(bot, session_id, close_type)
"""

from __future__ import annotations

import asyncio
import logging

from db import get_db
from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.session_snapshot import snapshot_store, SessionSnapshot
from telegram_page.gold.gold_state import user_state_v7, CalcContext
from telegram_page.gold.gold_buffer import gold_buffer

logger   = logging.getLogger(__name__)
ADMIN_ID = 571718066

NOTIFY_RATE = 20   # msg/s


# ══════════════════════════════════════════════════════════════════════════════
# Chargement des cibles (RAM prioritaire, SQL fallback)
# ══════════════════════════════════════════════════════════════════════════════

async def _load_targets(session_id: int) -> tuple[SessionSnapshot | None, list[dict]]:
    """
    Retourne (snapshot, liste de dicts {user_id, tp_level_assigned, gain_tp1/2/3}).

    Priorité RAM : si la session est encore active, on lit user_state_v7.
    Sinon fallback SQL sur gold_member_entries.
    """
    reg = session_registry.current()
    snap = snapshot_store.get_active()

    # ── Cas 1 : session en RAM ────────────────────────────────────────────
    if reg is not None and reg.session_id == session_id and snap is not None:
        targets = []
        for uid, calc in user_state_v7.confirmed_calcs().items():
            targets.append({
                "user_id":            uid,
                "tp_level_assigned":  calc.tp_level,
                "gain_tp1":           calc.gain_tp1,
                "gain_tp2":           calc.gain_tp2,
                "gain_tp3":           calc.gain_tp3,
            })
        return snap, targets

    # ── Cas 2 : session déjà fermée → fallback SQL ────────────────────────
    async with get_db() as cur:
        await cur.execute("""
            SELECT user_id, tp_level_assigned, gain_tp1, gain_tp2, gain_tp3
            FROM gold_member_entries
            WHERE session_id = %s AND step_reached IN ('processed', 'confirmed')
        """, (session_id,))
        rows = [dict(r) for r in await cur.fetchall()]

        # On lit aussi la session pour reconstruire un "quasi-snapshot"
        # (assez pour connaître les niveaux TP à notifier)
        await cur.execute(
            "SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,)
        )
        session_row = await cur.fetchone()

    if not session_row:
        return None, []

    # Récupère les messages TP/SL depuis les règles pour ce session_id
    return None, rows   # snapshot = None → messages génériques (voir _build_msg)


# ══════════════════════════════════════════════════════════════════════════════
# Construction des messages
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_TP_MSG = {
    1: "✅ *TP1 atteint sur XAU/USD !*\n\nSécurise tes gains 💪",
    2: "🎯 *TP2 atteint sur XAU/USD !*\n\nFélicitations 🎉",
    3: "🏆 *TP3 atteint sur XAU/USD !*\n\nTrade parfait 🚀",
}
_DEFAULT_SL_MSG = ("❌ *SL touché sur XAU/USD*\n\n"
                   "Ton SL a bien protégé ton compte. Discipline 💪")


def _build_tp_msg(snap: SessionSnapshot | None, target: dict, tp_level: int) -> str | None:
    """Utilise le message custom du snapshot si dispo, sinon un fallback générique."""
    assigned = int(target.get("tp_level_assigned") or 0)
    # On ne notifie que si l'user a effectivement souscrit ce TP
    if tp_level > assigned:
        return None

    text = None
    if snap is not None:
        rule = snap.rule_for(assigned)
        if rule:
            text = getattr(rule, f"message_tp{tp_level}_reached", None)
    if not text:
        text = _DEFAULT_TP_MSG.get(tp_level)

    gain = {1: target.get("gain_tp1"),
            2: target.get("gain_tp2"),
            3: target.get("gain_tp3")}.get(tp_level)
    if gain:
        text = f"{text}\n\n💰 *Ton gain estimé : +{gain}$*"
    return text


def _build_sl_msg(snap: SessionSnapshot | None, target: dict) -> str:
    if snap is not None:
        assigned = int(target.get("tp_level_assigned") or 0)
        rule = snap.rule_for(assigned)
        if rule and rule.message_sl_touched:
            return rule.message_sl_touched
    return _DEFAULT_SL_MSG


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS TP
# ══════════════════════════════════════════════════════════════════════════════

async def notify_tp_reached(bot, session_id: int, tp_level: int) -> dict:
    """
    Notifie tous les users concernés qu'un TP est atteint. Met à jour la phase
    en base. NE fait PAS la clôture globale (celle-ci relève du lifecycle).
    """
    if tp_level not in (1, 2, 3):
        return {"error": "tp_level doit être 1, 2 ou 3"}

    snap, targets = await _load_targets(session_id)

    # ── Update phase SQL
    phase_map = {1: "tp1_reached", 2: "tp2_reached", 3: "tp3_reached"}
    new_phase = phase_map[tp_level]
    tp_field  = f"tp{tp_level}_reached_at"
    async with get_db() as cur:
        await cur.execute(f"""
            UPDATE gold_trade_sessions
            SET current_phase = %s, {tp_field} = NOW()
            WHERE id = %s
        """, (new_phase, session_id))

    # ── Envoi
    sent_exit = sent_continue = errors = 0
    sem = asyncio.Semaphore(NOTIFY_RATE)

    async def _send_one(t: dict):
        nonlocal sent_exit, sent_continue, errors
        text = _build_tp_msg(snap, t, tp_level)
        if not text:
            return
        async with sem:
            try:
                await bot.send_message(chat_id=t["user_id"], text=text,
                                        parse_mode="Markdown")
                if int(t.get("tp_level_assigned") or 0) == tp_level:
                    sent_exit += 1
                else:
                    sent_continue += 1
                # Log event si session active
                reg = session_registry.current()
                if reg is not None and reg.session_id == session_id:
                    gold_buffer.add_event(reg.session_id, reg.version,
                                              t["user_id"], f"tp{tp_level}_notified",
                                              {"gain": t.get(f"gain_tp{tp_level}")})
            except Exception as e:
                logger.warning(f"[notify_tp] uid={t['user_id']}: {e}")
                errors += 1
            await asyncio.sleep(1)   # 1 slot/s ⇒ débit = NOTIFY_RATE/s

    await asyncio.gather(*[_send_one(t) for t in targets])

    return {
        "session_id":    session_id,
        "tp_level":      tp_level,
        "sent_exit":     sent_exit,
        "sent_continue": sent_continue,
        "errors":        errors,
        "new_phase":     new_phase,
    }


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION SL — l'admin uniquement, comme en v5/v6
# ══════════════════════════════════════════════════════════════════════════════

async def notify_sl_touched(bot, session_id: int) -> dict:
    """
    Marque la session en SL en base + met à jour les entries.
    NE NOTIFIE PAS les users individuellement (comportement v5/v6 conservé).
    L'admin est notifié via notify_admin_session_closed.
    """
    async with get_db() as cur:
        await cur.execute("""
            UPDATE gold_trade_sessions
            SET current_phase = 'sl_touched', sl_touched_at = NOW(), closed_at = NOW()
            WHERE id = %s
        """, (session_id,))
        await cur.execute("""
            UPDATE gold_member_entries
            SET result_usd    = perte_sl,
                capital_after = capital_before + perte_sl,
                exit_tp_level = NULL,
                exited_at     = NOW()
            WHERE session_id = %s
        """, (session_id,))
        await cur.execute("""
            UPDATE simulation_trades
            SET result_usd    = perte_sl,
                capital_after = capital_before + perte_sl,
                status        = 'closed',
                closed_at     = NOW()
            WHERE session_id = %s
        """, (session_id,))

        await cur.execute("""
            SELECT COUNT(*) as n FROM gold_member_entries
            WHERE session_id = %s AND step_reached IN ('processed', 'confirmed')
        """, (session_id,))
        n = (await cur.fetchone())["n"]

    return {"session_id": session_id, "phase": "sl_touched", "concerned": n}


async def apply_tp_closure_in_db(session_id: int, tp_level: int) -> dict:
    """
    Applique la clôture SQL pour un TP donné : met à jour result_usd et
    capital_after pour toutes les entries de la session, selon le niveau
    de TP effectivement atteint.
    """
    async with get_db() as cur:
        await cur.execute("""
            UPDATE gold_trade_sessions
            SET current_phase = 'closed', closed_at = NOW()
            WHERE id = %s
        """, (session_id,))
        # La CASE mimique exactement la logique v5 : chaque user sort au
        # niveau qui lui a été assigné, capé par le TP effectivement touché.
        await cur.execute(f"""
            UPDATE gold_member_entries
            SET result_usd = CASE
                    WHEN tp_level_assigned >= {tp_level} AND gain_tp{tp_level} IS NOT NULL
                        THEN gain_tp{tp_level}
                    WHEN tp_level_assigned >= 2 AND {tp_level} >= 2 AND gain_tp2 IS NOT NULL
                        THEN gain_tp2
                    ELSE gain_tp1
                END,
                exit_tp_level = CASE
                    WHEN tp_level_assigned >= {tp_level} THEN {tp_level}
                    WHEN tp_level_assigned >= 2 AND {tp_level} >= 2 THEN 2
                    ELSE 1
                END,
                capital_after = capital_before + CASE
                    WHEN tp_level_assigned >= {tp_level} AND gain_tp{tp_level} IS NOT NULL
                        THEN gain_tp{tp_level}
                    WHEN tp_level_assigned >= 2 AND {tp_level} >= 2 AND gain_tp2 IS NOT NULL
                        THEN gain_tp2
                    ELSE gain_tp1
                END,
                exited_at = NOW()
            WHERE session_id = %s
        """, (session_id,))

    return {"session_id": session_id, "close_type": f"tp{tp_level}"}


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION ADMIN — récap de clôture
# ══════════════════════════════════════════════════════════════════════════════

async def notify_admin_session_closed(bot, session_id: int, close_type: str,
                                       notified: int = 0):
    """Envoie un récap de clôture à l'admin (comportement v5)."""
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        session = await cur.fetchone()
        await cur.execute("""
            SELECT
                ROUND(SUM(result_usd), 2) AS total_result,
                ROUND(SUM(CASE WHEN result_usd > 0 THEN result_usd ELSE 0 END), 2) AS total_gains,
                ROUND(SUM(CASE WHEN result_usd < 0 THEN result_usd ELSE 0 END), 2) AS total_losses
            FROM gold_member_entries WHERE session_id = %s
        """, (session_id,))
        agg = await cur.fetchone()

    if not session:
        return
    session = dict(session)
    agg     = dict(agg)
    emoji   = {"tp1": "✅", "tp2": "🎯", "tp3": "🏆", "sl": "❌"}.get(close_type, "📊")

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"{emoji} *Session Gold clôturée — {close_type.upper()}*\n\n"
                  f"Membres notifiés : {notified}\n"
                  f"Résultat global : {agg.get('total_result')}$\n"
                  f"Gains : +{agg.get('total_gains') or 0}$ | "
                  f"Pertes : {agg.get('total_losses') or 0}$\n"
                  f"Lots engagés : {session.get('total_lots_engaged')}"),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"[notify_admin] {e}")