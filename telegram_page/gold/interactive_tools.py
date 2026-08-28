"""
interactive_tools.py — Outils à la demande sous le signal (v8).

REMPLACE : le calcul de lot automatique à l'envoi (build_calc_context,
CalcContext, StateManagerV7, gold_buffer par confirmation).

1. MONEY MANAGEMENT (bouton "💰 Money management")
   Mini-outil interactif, à la demande, PAR SIGNAL :
     - clic → le bot demande le capital
     - le membre tape un nombre
     - le bot renvoie IMMÉDIATEMENT le lot recommandé + les scénarios
       de gain/perte pour CE signal, avec ce capital
     - RIEN n'est stocké — SAUF si le membre clique explicitement sur
       "💾 Sauvegarder ce capital" (bouton proposé après le calcul).
       Dans ce cas, son capital est stocké de façon PERMANENTE
       (member_capital.py) et il reçoit désormais une notification à
       chaque TP1/TP2/TP3 atteint, selon son palier d'objectif —
       voir trade_management_notifs.py. Le SL reste toujours silencieux.
   Le membre peut relancer l'outil autant de fois qu'il veut avec un
   capital différent, avec ou sans sauvegarder.

2. BESOIN D'AIDE (bouton "🆘 Besoin d'aide")
   Message statique de contact/support + notification à l'admin.

Intégration (register_gold_handlers) :
    from interactive_tools import register_interactive_handlers
    register_interactive_handlers(app)
"""

from __future__ import annotations

import logging
import math

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

from db import get_db
from telegram_page.gold.disclaimer_gate import handle_disclaimer_weekly_ok, cmd_je_valide_mon_engagement
from member_capital import save_capital

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066
SUPPORT_CONTACT = "@fdksupport"   # TODO: ajuster au contact réel


# ══════════════════════════════════════════════════════════════════════════════
# Calcul (identique à l'ancien calc_lot/calc_gain_dollar — pas de session RAM)
# ══════════════════════════════════════════════════════════════════════════════

def calc_lot(capital: float, entry: float, sl: float) -> float:
    if capital < 250:
        return 0.01
    if capital < 500:
        return 0.015
    sl_pips = abs(entry - sl)
    if sl_pips <= 0:
        return 0.01
    diviseur = 12 if capital < 1500 else 12 + math.floor((capital - 1001) / 500)
    perte_par_trade = capital / diviseur
    lot = math.floor(((perte_par_trade * 0.01) / sl_pips) * 100) / 100
    return max(0.01, lot)


def calc_gain_dollar(lot: float, pips: float) -> float:
    return round((lot / 0.01) * pips, 2)


async def _get_session(session_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


def _build_result_message(session: dict, capital: float, lot: float) -> str:
    entry, sl = float(session["entry_price"]), float(session["sl"])
    sl_pips = abs(entry - sl)
    perte_sl = -calc_gain_dollar(lot, sl_pips)

    lines = [
        "💰 *Money management — résultat*",
        "",
        f"Capital utilisé : *{capital:g}$*",
        f"Lot recommandé : *{lot}*",
        "",
        f"❌ Si SL touché → *{perte_sl}$*",
    ]
    for level, key in ((1, "tp1"), (2, "tp2"), (3, "tp3")):
        if session.get(key):
            gain = calc_gain_dollar(lot, abs(float(session[key]) - entry))
            lines.append(f"✅ Si TP{level} touché → *+{gain}$*")

    lines += ["", "_Rien n'est enregistré — relance l'outil quand tu veux._"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Handlers — Money management
# ══════════════════════════════════════════════════════════════════════════════

async def handle_mm_open(update, context):
    query = update.callback_query
    if query is None:
        return
    session_id = int(query.data.rsplit("_", 1)[-1])
    await query.answer()

    session = await _get_session(session_id)
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
    """
    Renvoie True si le message a été consommé par l'outil Money
    management (à appeler AVANT d'autres handlers texte génériques).
    """
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

    session = await _get_session(session_id)
    context.user_data.pop("mm_pending_session_id", None)

    if session is None:
        await msg.reply_text("⏰ Ce signal n'est plus disponible pour ce calcul.")
        return True

    lot = calc_lot(capital, float(session["entry_price"]), float(session["sl"]))
    kbd = InlineKeyboardMarkup([[InlineKeyboardButton(
        "💾 Sauvegarder ce capital pour mes notifs de trade",
        callback_data=f"mm_save_{session_id}_{capital:g}",
    )]])
    await msg.reply_text(_build_result_message(session, capital, lot),
                          parse_mode="Markdown", reply_markup=kbd)
    return True


async def handle_mm_save(update, context):
    """
    Sauvegarde EXPLICITE (opt-in) du capital calculé dans Money
    management. Envoie immédiatement une notification de gestion du
    trade pour la session en cours, et active les futures notifs
    TP1/TP2/TP3 (selon palier — voir trade_management_notifs.py).
    Le SL reste toujours silencieux.
    """
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

    session = await _get_session(session_id)
    header = "🔔 *Notifications de gestion du trade activées.*\n\n"
    if session is None:
        await context.bot.send_message(
            chat_id=uid,
            text=(header + "Tu recevras un message à chaque niveau important "
                  "(TP1, TP2, TP3) sur tes prochains trades."),
            parse_mode="Markdown",
        )
        return

    lot = calc_lot(capital, float(session["entry_price"]), float(session["sl"]))
    await context.bot.send_message(
        chat_id=uid,
        text=header + _build_result_message(session, capital, lot),
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Handler — Besoin d'aide
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
# Enregistrement
# ══════════════════════════════════════════════════════════════════════════════

async def _text_router(update, context):
    """
    Un seul routeur texte : priorité à Money management (état en attente),
    sinon on ignore (pas de flow de saisie capital global à gérer en v8).
    """
    if await handle_mm_capital_input(update, context):
        return


def register_interactive_handlers(app):
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

    logger.info("[interactive_tools] Handlers enregistrés "
                "(money management + aide + /je_valide_mon_engagement) ✓")