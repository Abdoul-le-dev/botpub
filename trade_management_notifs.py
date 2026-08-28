"""
trade_management_notifs.py — Notifications TP personnalisées, opt-in (v8).

Pour les membres qui ont explicitement sauvegardé leur capital via
Money management (voir member_capital.py + interactive_tools.py).
Reproduit le comportement de l'ancien tp_notifier.py — paliers
d'objectif selon la taille du compte (gold_tp_rules), messages
personnalisés par palier — mais uniquement pour ce sous-ensemble
opt-in, et SEULEMENT pour TP1/TP2/TP3. Le SL reste totalement
silencieux pour tout le monde (comportement conservé de l'ancien
système — voir sa propre docstring : "NE NOTIFIE PAS les users
individuellement").

Appelé depuis trade_watcher.py quand un niveau TP est détecté.

OPTIMISATION : les règles gold_tp_rules sont chargées UNE SEULE FOIS
par appel (pas par membre) — le palier de chaque membre est résolu
localement en RAM, pas via une requête SQL par membre comme le ferait
gold_engine.get_tp_level_for_capital() appelé en boucle.
"""

from __future__ import annotations

import asyncio
import logging

from telegram_page.gold.gold_engine import (
    calculate_lot, calculate_gains_losses, get_tp_rules,
)
from member_capital import get_all_capitals

logger = logging.getLogger(__name__)

NOTIFY_RATE = 20  # msg/s — comme l'ancien tp_notifier

_DEFAULT_TP_MSG = {
    1: "✅ *TP1 atteint sur XAU/USD !*\n\nSécurise tes gains 💪",
    2: "🎯 *TP2 atteint sur XAU/USD !*\n\nFélicitations 🎉",
    3: "🏆 *TP3 atteint sur XAU/USD !*\n\nTrade parfait 🚀",
}


def _resolve_tp_level(capital: float, rules: list[dict]) -> int:
    """Réplique gold_engine.get_tp_level_for_capital() mais en local
    (RAM), à partir des règles déjà chargées — évite un SELECT par
    membre lors d'une notification en masse."""
    for r in sorted(rules, key=lambda r: float(r["min_capital"])):
        mn = float(r["min_capital"])
        mx = float(r["max_capital"]) if r.get("max_capital") is not None else None
        if mn <= capital and (mx is None or capital <= mx):
            return int(r["tp_level"])
    if capital < 500:
        return 1
    elif capital < 2000:
        return 2
    return 3


async def notify_opted_in_members(bot, session: dict, tp_level: int) -> dict:
    """
    session : dict complet de gold_trade_sessions (entry_price, sl,
    tp1/2/3). tp_level : 1, 2 ou 3 — niveau qui vient d'être touché.

    Ne notifie que les membres dont le palier d'objectif (dérivé de
    leur capital sauvegardé) inclut CE niveau — un petit compte
    (objectif TP1 seul) ne reçoit rien à TP2/TP3.
    """
    if tp_level not in (1, 2, 3):
        return {"notified": 0}

    capitals = await get_all_capitals()
    if not capitals:
        return {"notified": 0}

    all_rules = await get_tp_rules()
    active_rules = [r for r in all_rules if r.get("is_active", 1)]
    rules_by_level = {int(r["tp_level"]): r for r in active_rules}

    entry = float(session["entry_price"])
    sl = float(session["sl"])
    tp1, tp2, tp3 = session.get("tp1"), session.get("tp2"), session.get("tp3")

    sem = asyncio.Semaphore(NOTIFY_RATE)
    notified = 0
    errors = 0

    async def _send_one(uid: int, capital: float):
        nonlocal notified, errors
        assigned_tp = _resolve_tp_level(capital, active_rules)
        # Palier : on ne notifie que si ce niveau fait partie de son objectif.
        if tp_level > assigned_tp:
            return

        lot = calculate_lot(capital, entry, sl)
        gains = calculate_gains_losses(lot, entry, sl, tp1, tp2, tp3)
        gain = gains.get(f"gain_tp{tp_level}")

        rule = rules_by_level.get(assigned_tp)
        text = rule.get(f"message_tp{tp_level}_reached") if rule else None
        if not text:
            text = _DEFAULT_TP_MSG[tp_level]
        if gain:
            text = f"{text}\n\n💰 *Gain estimé : +{gain}$* (lot {lot})"

        async with sem:
            try:
                await bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
                notified += 1
            except Exception as e:
                logger.debug(f"[trade_mgmt_notif] uid={uid}: {e}")
                errors += 1
            await asyncio.sleep(1)

    await asyncio.gather(*[_send_one(uid, cap) for uid, cap in capitals.items()])
    return {"notified": notified, "errors": errors, "eligible_pool": len(capitals)}