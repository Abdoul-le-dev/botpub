"""
gold_followup.py — Gold v8, ce qui se passe après l'envoi du signal.

Fusionne interactive_tools.py + trade_watcher.py + trade_management_notifs.py :
les trois gèrent la vie du signal une fois envoyé, jusqu'à sa clôture.

1. MONEY MANAGEMENT (bouton "💰 Money management")
   À la demande, par signal : le membre tape un capital → calcul
   immédiat du lot + scénarios de gain/perte. RIEN n'est stocké, sauf
   clic explicite sur "💾 Sauvegarder ce capital" → capital stocké de
   façon permanente (gold_core.save_capital) et le membre reçoit
   désormais une notification à chaque TP atteint (SL toujours
   silencieux).

2. BESOIN D'AIDE (bouton "🆘 Besoin d'aide")
   Message de contact + alerte admin.

3. SURVEILLANCE PRIX + FERMETURE
   Sondage du prix live en tâche de fond, démarré automatiquement par
   gold_broadcast.send_signal() (plus besoin de déclenchement manuel) :
     - SL touché  → fermeture définitive, notif ADMIN uniquement.
     - TP3 touché → fermeture définitive, notif ADMIN + notif opt-in.
     - TP1/TP2    → phase mise à jour (stats), notif opt-in, la
       surveillance continue.

4. NOTIFICATIONS TP OPT-IN
   Uniquement pour les membres ayant sauvegardé leur capital (Money
   management) — selon leur palier d'objectif (gold_tp_rules). Le SL
   reste totalement silencieux pour tout le monde.

5. FERMETURE MANUELLE ADMIN
   admin_force_close() — remplace les anciens endpoints séparés
   /confirm, /tp/{n}, /sl : une seule fonction, appelée depuis
   routes_gold.py, qui écrit directement la phase. La boucle
   watch_and_close en cours la détectera au prochain sondage et
   s'arrêtera d'elle-même (elle relit la phase en base, pas une copie
   RAM) — aucune synchronisation à gérer entre les deux.

Intégration (dans script.py) :
    from telegram_page.gold.gold_followup import (
        register_gold_followup_handlers, set_bot,
    )
    register_gold_followup_handlers(app)
    set_bot(app.bot)
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

from telegram_page.gold.gold_core import (
    get_session_row, calculate_lot, calculate_gains_losses,
    get_live_gold_price, watch_interval, close_simulation_trades,
    get_tp_rules, save_capital, get_all_capitals,
)
from telegram_page.gold.gold_broadcast import (
    handle_disclaimer_weekly_ok, cmd_je_valide_mon_engagement,
)
from db import get_db

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066
SUPPORT_CONTACT = "@Fiacrekpanou"   # TODO: ajuster au contact réel
NOTIFY_RATE = 20  # msg/s pour les notifs TP en masse

_bot = None


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


# ══════════════════════════════════════════════════════════════════════════════
# 1. MONEY MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def _build_result_message(session: dict, capital: float, lot: float) -> str:
    entry, sl = float(session["entry_price"]), float(session["sl"])
    gains = calculate_gains_losses(lot, entry, sl,
                                    session.get("tp1"), session.get("tp2"), session.get("tp3"))

    lines = [
        "💰 *Money management — résultat*",
        "",
        f"Capital utilisé : *{capital:g}$*",
        f"Lot recommandé : *{lot}*",
        "",
        f"❌ Si SL touché → *{gains['perte_sl']}$*",
    ]
    for level in (1, 2, 3):
        gain = gains.get(f"gain_tp{level}")
        if gain:
            lines.append(f"✅ Si TP{level} touché → *+{gain}$*")

    lines += ["", "_Rien n'est enregistré — relance l'outil quand tu veux._"]
    return "\n".join(lines)


async def handle_mm_open(update, context):
    query = update.callback_query
    if query is None:
        return
    session_id = int(query.data.rsplit("_", 1)[-1])
    await query.answer()

    session = await get_session_row(session_id)
    if session is None:
        await context.bot.send_message(query.from_user.id,
            "⏰ Ce signal n'est plus disponible pour ce calcul.")
        return

    context.user_data["mm_pending_session_id"] = session_id
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=("💰 *Money management*\n\n"
              "Indique ton capital actuel en $ pour recevoir le lot "
              "recommandé sur ce signal.\n\n_Ex : 500 ou 1250_"),
        parse_mode="Markdown",
    )


async def handle_mm_capital_input(update, context) -> bool:
    """True si le message a été consommé par Money management — à
    appeler AVANT d'autres handlers texte génériques."""
    session_id = context.user_data.get("mm_pending_session_id")
    if session_id is None:
        return False

    msg = update.effective_message
    raw = (msg.text or "").strip()
    clean = raw.replace(",", ".").replace(" ", "").replace("$", "")

    if not clean.replace(".", "", 1).isdigit() or clean.count(".") > 1:
        await msg.reply_text("⚠️ Entre uniquement un chiffre. Ex : `500`", parse_mode="Markdown")
        return True

    capital = float(clean)
    if capital <= 0:
        await msg.reply_text("⚠️ Capital invalide.")
        return True

    session = await get_session_row(session_id)
    context.user_data.pop("mm_pending_session_id", None)

    if session is None:
        await msg.reply_text("⏰ Ce signal n'est plus disponible pour ce calcul.")
        return True

    lot = calculate_lot(capital, float(session["entry_price"]), float(session["sl"]))
    kbd = InlineKeyboardMarkup([[InlineKeyboardButton(
        "💾 Sauvegarder mon capital",
        callback_data=f"mm_save_{session_id}_{capital:g}",
    )]])
    await msg.reply_text(_build_result_message(session, capital, lot),
                          parse_mode="Markdown", reply_markup=kbd)
    return True


async def handle_mm_save(update, context):
    """Sauvegarde EXPLICITE (opt-in) du capital → active les futures
    notifs TP1/2/3. Le SL reste toujours silencieux."""
    query = update.callback_query
    if query is None:
        return
    try:
        _, _, session_id_str, capital_str = query.data.split("_", 3)
        session_id = int(session_id_str)
        capital = float(capital_str)
    except (ValueError, IndexError):
        await query.answer("Erreur — réessaie depuis Money management.", show_alert=True)
        return

    uid = query.from_user.id
    await save_capital(uid, capital)
    await query.answer("✅ Capital sauvegardé.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    session = await get_session_row(session_id)
    header = "🔔 *Notifications de gestion du trade activées.*\n\n"
    if session is None:
        await context.bot.send_message(
            chat_id=uid,
            text=(header + "Tu recevras un message à chaque niveau important "
                  "(TP1, TP2, TP3) sur tes prochains trades."),
            parse_mode="Markdown",
        )
        return

    lot = calculate_lot(capital, float(session["entry_price"]), float(session["sl"]))
    await context.bot.send_message(
        chat_id=uid,
        text=header + _build_result_message(session, capital, lot),
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. BESOIN D'AIDE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_help_request(update, context):
    query = update.callback_query
    if query is None:
        return
    session_id = query.data.rsplit("_", 1)[-1]
    uid = query.from_user.id
    name = query.from_user.full_name or str(uid)

    await query.answer("Un membre de l'équipe va te contacter.", show_alert=True)

    await context.bot.send_message(
        chat_id=uid,
        text=(f"🆘 *Besoin d'aide reçu.*\n\n"
              f"Tu peux aussi nous écrire directement : {SUPPORT_CONTACT}"),
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆘 Demande d'aide — {name} (id={uid}) — signal #{session_id}",
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# 3. SURVEILLANCE PRIX + FERMETURE AUTOMATIQUE
# ══════════════════════════════════════════════════════════════════════════════

async def _close_trade_definitively(session_id: int, close_type: str):
    """SL ou TP3 — fermeture définitive. Écriture directe (évènement
    terminal et rare, pas de write-behind nécessaire)."""
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
        logger.error(f"[gold_followup] close_simulation_trades sid={session_id}: {e}",
                     exc_info=True)


async def _set_intermediate_phase(session_id: int, phase: str):
    """TP1/TP2 : juste pour les stats — aucune notification membre ici
    (voir _notify_opted_in_tp, séparé, opt-in uniquement)."""
    async with get_db() as cur:
        await cur.execute(
            "UPDATE gold_trade_sessions SET current_phase = %s WHERE id = %s",
            (phase, session_id),
        )


async def _notify_admin_closed(session_id: int, close_type: str):
    if not _bot:
        return
    label = {"sl": "SL touché", "tp3": "TP3 atteint 🏆",
              "manual": "Fermeture manuelle"}.get(close_type, close_type)
    try:
        await _bot.send_message(chat_id=ADMIN_ID,
                                 text=f"📉 Session #{session_id} clôturée — {label}")
    except Exception:
        pass


async def _notify_opted_in_tp(session: dict, tp_level: int):
    """Best-effort — une erreur ici ne doit jamais interrompre la
    surveillance SL/TP3."""
    if not _bot:
        return
    try:
        report = await notify_opted_in_members(_bot, session, tp_level)
        if report.get("notified"):
            logger.info(f"[gold_followup] TP{tp_level} — "
                        f"{report['notified']} membre(s) opt-in notifié(s)")
    except Exception as e:
        logger.error(f"[gold_followup] notify_opted_in_members TP{tp_level}: {e}",
                     exc_info=True)


async def watch_and_close(session_id: int):
    """
    Démarrée automatiquement par gold_broadcast.send_signal() à la fin
    de l'envoi. Ne dépend d'aucun état RAM partagé — relit tout depuis
    MySQL à chaque cycle, ce qui la rend compatible avec une fermeture
    manuelle déclenchée depuis un autre process (voir admin_force_close).
    """
    logger.info(f"[gold_followup] démarrage surveillance session #{session_id}")

    while True:
        session = await get_session_row(session_id)
        if not session:
            break
        phase = session["current_phase"]
        if phase in ("closed", "sl_touched", "tp3_reached", "cancelled"):
            break

        price = await get_live_gold_price()
        interval = watch_interval()
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

        # ── SL touché → fermeture définitive (silencieux, tout le monde)
        if (direction == "buy" and price <= sl) or (direction == "sell" and price >= sl):
            await _close_trade_definitively(session_id, "sl")
            await _notify_admin_closed(session_id, "sl")
            break

        # ── TP3 touché → fermeture définitive + notif opt-in
        if tp3 and phase not in ("tp3_reached", "closed"):
            if (direction == "buy" and price >= tp3) or (direction == "sell" and price <= tp3):
                await _close_trade_definitively(session_id, "tp3")
                await _notify_admin_closed(session_id, "tp3")
                await _notify_opted_in_tp(session, 3)
                break

        # ── TP2 touché → phase mise à jour + notif opt-in, on continue
        if tp2 and phase not in ("tp2_reached", "tp3_reached", "closed"):
            if (direction == "buy" and price >= tp2) or (direction == "sell" and price <= tp2):
                await _set_intermediate_phase(session_id, "tp2_reached")
                phase = "tp2_reached"
                await _notify_opted_in_tp(session, 2)

        # ── TP1 touché → idem
        if tp1 and phase not in ("tp1_reached", "tp2_reached", "tp3_reached", "closed"):
            if (direction == "buy" and price >= tp1) or (direction == "sell" and price <= tp1):
                await _set_intermediate_phase(session_id, "tp1_reached")
                phase = "tp1_reached"
                await _notify_opted_in_tp(session, 1)

        await asyncio.sleep(interval)


# ══════════════════════════════════════════════════════════════════════════════
# 4. NOTIFICATIONS TP OPT-IN
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_TP_MSG = {
    1: "✅ *TP1 atteint sur XAU/USD !*\n\nSécurise tes gains 💪",
    2: "🎯 *TP2 atteint sur XAU/USD !*\n\nFélicitations 🎉",
    3: "🏆 *TP3 atteint sur XAU/USD !*\n\nTrade parfait 🚀",
}


def _resolve_tp_level(capital: float, rules: list[dict]) -> int:
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
    Ne notifie que les membres dont le palier d'objectif (dérivé de
    leur capital sauvegardé) inclut CE niveau — un petit compte
    (objectif TP1 seul) ne reçoit rien à TP2/TP3. Règles chargées UNE
    fois par appel (pas par membre) — résolution du palier en RAM.
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
    notified = errors = 0

    async def _send_one(uid: int, capital: float):
        nonlocal notified, errors
        assigned_tp = _resolve_tp_level(capital, active_rules)
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
                logger.debug(f"[gold_followup] notify uid={uid}: {e}")
                errors += 1
            await asyncio.sleep(1)

    await asyncio.gather(*[_send_one(uid, cap) for uid, cap in capitals.items()])
    return {"notified": notified, "errors": errors, "eligible_pool": len(capitals)}


# ══════════════════════════════════════════════════════════════════════════════
# 5. FERMETURE MANUELLE ADMIN — remplace confirm/tp/{n}/sl éclatés
# ══════════════════════════════════════════════════════════════════════════════

async def admin_force_close(session_id: int, close_type: str) -> dict:
    """
    Fermeture déclenchée depuis le dashboard admin (routes_gold.py).
    close_type: 'manual' | 'tp1' | 'tp2' | 'tp3' | 'sl'.

    Écrit directement la phase — la boucle watch_and_close en cours
    pour cette session (si elle tourne encore, dans le process bot) la
    détectera au prochain sondage et s'arrêtera d'elle-même. Pas de
    synchronisation RAM entre process nécessaire : c'est le sondage en
    base qui fait le lien.
    """
    phase_map = {"manual": "closed", "tp1": "tp1_reached", "tp2": "tp2_reached",
                 "tp3": "tp3_reached", "sl": "sl_touched"}
    if close_type not in phase_map:
        return {"ok": False, "error": "close_type invalide"}

    session = await get_session_row(session_id)
    if session is None:
        return {"ok": False, "error": "session_introuvable"}

    new_phase = phase_map[close_type]
    async with get_db() as cur:
        await cur.execute("""
            UPDATE gold_trade_sessions
            SET current_phase = %s, closed_at = COALESCE(closed_at, NOW())
            WHERE id = %s
        """, (new_phase, session_id))

    try:
        await close_simulation_trades(session_id, close_type if close_type != "manual" else "manual")
    except Exception as e:
        logger.error(f"[gold_followup] admin_force_close sim sid={session_id}: {e}", exc_info=True)

    await _notify_admin_closed(session_id, close_type)

    if close_type in ("tp1", "tp2", "tp3") and _bot:
        await _notify_opted_in_tp(session, int(close_type[-1]))

    return {"ok": True, "session_id": session_id, "close_type": close_type, "phase": new_phase}


# ══════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT DES HANDLERS TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

async def _text_router(update, context):
    """Un seul routeur texte : priorité à Money management (état en
    attente), sinon ignoré."""
    if await handle_mm_capital_input(update, context):
        return


def register_gold_followup_handlers(app):
    app.add_handler(CallbackQueryHandler(handle_mm_open, pattern=r"^mm_open_\d+$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_mm_save, pattern=r"^mm_save_\d+_[\d.]+$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_help_request, pattern=r"^help_request_\d+$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_disclaimer_weekly_ok,
                                          pattern=r"^disclaimer_weekly_ok(_\d+)?$"), group=3)
    app.add_handler(CommandHandler("je_valide_mon_engagement", cmd_je_valide_mon_engagement))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        _text_router,
    ), group=3)

    logger.info("[gold_followup] Handlers enregistrés "
                "(money management + aide + disclaimer + /je_valide_mon_engagement) ✓")