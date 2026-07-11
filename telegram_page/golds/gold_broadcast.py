"""
broadcast_v7.py — Handlers Telegram Gold v7.1 (workflow simplifié).

CHANGEMENTS v7.1 vs v7.0
  1. Suppression de l'étape "confirmer" : ouvrir = accepter. Après le
     clic sur "Accéder au trade", si le capital est en cache, le trade
     est calculé + affiché + enregistré IMMÉDIATEMENT en un seul flow.
  2. Weekly Capital Cache intégré : le formulaire capital n'apparaît que
     si RAM absent ET SQL absent (ou capital expiré).
  3. Machine d'état simplifiée : teaser → processed (fast path) OU
     teaser → waiting_capital → processed (slow path premier user).

INVARIANTS PRÉSERVÉS de v7.0
  - Callback guard : toute action passe par @guard, callbacks obsolètes
    rejetés automatiquement.
  - Snapshot immutable : entry/sl/tp du trade actif, jamais mélangés.
  - CalcContext figé : capital du cache + paramètres du snapshot. Une
    fois figé, plus jamais recalculé — écrit tel quel en base.
  - Idempotence : try_begin/end + machine d'état empêchent tout doublon.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from telegram_page.gold.callback_guard import guard, make_callback_data
from telegram_page.gold.lifecycle import current_snapshot, current_version, is_ready_for_confirmations
from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.session_snapshot import SessionSnapshot
from telegram_page.gold.gold_state import user_state_v7, CalcContext
from telegram_page.gold.gold_buffer import gold_buffer
from .weekly_capital_cache import weekly_capital, MIN_CAPITAL

logger = logging.getLogger(__name__)
ADMIN_ID = 571718066


# ══════════════════════════════════════════════════════════════════════════════
# CALCULS DÉTERMINISTES
# ══════════════════════════════════════════════════════════════════════════════

def calc_lot(capital: float, entry: float, sl: float) -> float:
    if capital < 250:
        return 0.01
    if capital < 500:
        return 0.015
    sl_pips = abs(entry - sl)
    if sl_pips <= 0:
        return 0.01
    if capital < 1500:
        diviseur = 12
    else:
        diviseur = 12 + math.floor((capital - 1001) / 500)
    perte_par_trade = capital / diviseur
    lot = (perte_par_trade * 0.01) / sl_pips
    lot = math.floor(lot * 100) / 100
    return max(0.01, lot)


def calc_gain_dollar(lot: float, pips: float) -> float:
    return round((lot / 0.01) * pips, 2)


def build_calc_context(snap: SessionSnapshot, user_id: int, capital: float,
                       effective_entry: float, effective_sl: float) -> CalcContext:
    """
    Cette fonction est le SEUL point où capital et snapshot fusionnent.
    Le snapshot est immutable → les params techniques (entry, sl,
    tp1/2/3) viennent TOUJOURS du trade actif. Le capital vient du
    Weekly Capital Cache. Aucun mélange possible avec une session
    précédente.
    """
    lot = calc_lot(capital, effective_entry, effective_sl)

    sl_pips_effective = abs(effective_entry - effective_sl)
    perte_sl = -calc_gain_dollar(lot, sl_pips_effective)

    # Gains TP : depuis l'entry ORIGINAL du snapshot
    gain_tp1 = calc_gain_dollar(lot, abs(snap.tp1 - snap.entry_price)) if snap.tp1 else None
    gain_tp2 = calc_gain_dollar(lot, abs(snap.tp2 - snap.entry_price)) if snap.tp2 else None
    gain_tp3 = calc_gain_dollar(lot, abs(snap.tp3 - snap.entry_price)) if snap.tp3 else None

    tp_level, risk_pct = snap.tp_level_for_capital(capital)
    risk_usd = round(capital * risk_pct / 100, 2)

    return CalcContext(
        session_id=snap.session_id, version=snap.version,
        effective_entry=effective_entry, effective_sl=effective_sl,
        capital=capital, lot=lot,
        tp_level=tp_level, risk_pct=risk_pct, risk_usd=risk_usd,
        perte_sl=perte_sl, gain_tp1=gain_tp1, gain_tp2=gain_tp2, gain_tp3=gain_tp3,
    )


def adjust_entry_sl(snap: SessionSnapshot, live_price: float | None) -> tuple[float, float, bool]:
    if live_price is None:
        return snap.entry_price, snap.sl, False
    if snap.direction == "sell" and live_price > snap.entry_price:
        return live_price, round(live_price + snap.sl_pips, 2), True
    if snap.direction == "buy" and live_price < snap.entry_price:
        return live_price, round(live_price - snap.sl_pips, 2), True
    return snap.entry_price, snap.sl, False


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _safe_answer(query, text=None, show_alert=False):
    try:
        if text:
            await query.answer(text, show_alert=show_alert)
        else:
            await query.answer()
    except Exception:
        pass


async def _safe_delete(bot, chat_id, message_id):
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def _fmt_now_discrete() -> str:
    now = datetime.now()
    jours = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    mois  = ["jan", "fév", "mar", "avr", "mai", "juin",
             "juil", "août", "sep", "oct", "nov", "déc"]
    return f"{jours[now.weekday()]} {now.day:02d} {mois[now.month-1]} · {now.strftime('%H:%M')}"


async def _get_live_price_safe() -> float | None:
    try:
        from telegram_page.gold.gold_engine import get_live_gold_price
        return await get_live_gold_price()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@guard("disclaimer_ok")
async def handle_disclaimer_ok(update, context, *, session_id, version):
    query   = update.callback_query
    user_id = query.from_user.id
    snap    = current_snapshot()
    if snap is None:
        await _safe_answer(query, "⏰ Ce trade n'est plus disponible.")
        return

    if not user_state_v7.try_begin(session_id, version, user_id, "disclaimer"):
        await _safe_answer(query)
        return
    try:
        await _safe_answer(query)
        await _safe_delete(context.bot, user_id, query.message.message_id)
        text = _build_teaser_message(snap)
        kbd  = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📊 Accéder au trade →",
            callback_data=make_callback_data("access", session_id, version),
        )]])
        await context.bot.send_message(chat_id=user_id, text=text,
                                        parse_mode="Markdown", reply_markup=kbd)
        gold_buffer.add_event(session_id, version, user_id, "teaser_shown")
    finally:
        user_state_v7.end(user_id, "disclaimer")


@guard("access")
async def handle_teaser_access(update, context, *, session_id, version):
    """
    ⚡ Point d'entrée principal du workflow v7.1.

    Ouvrir = prendre le trade. Décision :
      - capital en cache RAM  → calc + affiche + enregistre immédiatement
      - capital en SQL        → charge en RAM puis idem
      - capital absent/expiré → demande à l'utilisateur (formulaire)
    """
    query   = update.callback_query
    user_id = query.from_user.id

    # Idempotence : si déjà traité, on ne refait rien
    if user_state_v7.is_processed(session_id, version, user_id):
        await _safe_answer(query, "✅ Ce trade est déjà enregistré pour toi.",
                            show_alert=True)
        return

    if not user_state_v7.try_begin(session_id, version, user_id, "access"):
        await _safe_answer(query)
        return

    try:
        await _safe_answer(query)
        snap = current_snapshot()
        if snap is None:
            await context.bot.send_message(user_id, "⏰ Ce trade n'est plus disponible.")
            return

        # Fast path RAM
        capital = weekly_capital.get_ram(user_id)
        if capital is None:
            # Slow path : SQL — protégé par lock par-user dans le cache
            try:
                capital = await weekly_capital.get_or_load(user_id)
            except Exception as e:
                logger.error(f"[access] get_or_load uid={user_id}: {e}", exc_info=True)
                capital = None

        if capital is None:
            # Nouveau user OU capital expiré → demande le capital
            user_state_v7.transition(session_id, version, user_id, "waiting_capital")
            gold_buffer.add_step(session_id, version, user_id, "waiting_capital")
            gold_buffer.add_event(session_id, version, user_id, "capital_needed")
            await _safe_delete(context.bot, user_id, query.message.message_id)
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=("💼 *Quel est ton capital actuel en $ ?*\n\n"
                      "Cette info est enregistrée pour 7 jours — tu n'auras "
                      "plus à la saisir avant la semaine prochaine.\n\n"
                      "_Ex : 500 ou 1250_"),
                parse_mode="Markdown",
            )
            context.user_data[f"capital_msg_{session_id}_{version}"] = msg.message_id
            return

        # ⚡ Fast path : capital connu → traitement complet en une passe
        await _process_trade_full(context.bot, user_id, snap, capital,
                                    capital_source="cache",
                                    delete_message_id=query.message.message_id)
    finally:
        user_state_v7.end(user_id, "access")


async def handle_capital_input(update, context):
    """
    Handler texte SESSION — pas de guard car pas de callback_data.
    Ne s'applique QUE si l'user est en waiting_capital sur la session
    active.
    """
    msg = update.effective_message
    if msg is None or msg.from_user is None:
        return
    user_id = msg.from_user.id

    snap = current_snapshot()
    ver  = current_version()
    if snap is None or ver is None or not is_ready_for_confirmations():
        return

    st = user_state_v7.get(snap.session_id, ver, user_id)
    if st is None or st.step != "waiting_capital":
        return

    raw   = msg.text.strip()
    clean = raw.replace(",", ".").replace(" ", "").replace("$", "")
    await _safe_delete(context.bot, user_id, msg.message_id)

    is_numeric = clean.replace(".", "", 1).isdigit() and clean.count(".") <= 1
    if not is_numeric or not clean:
        await _safe_delete(context.bot, user_id,
                            context.user_data.get(f"capital_msg_{snap.session_id}_{ver}"))
        m = await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ *Entre uniquement des chiffres.*\n\nExemple : `500` ou `1250`",
            parse_mode="Markdown",
        )
        context.user_data[f"capital_msg_{snap.session_id}_{ver}"] = m.message_id
        return

    capital = float(clean)
    if capital < MIN_CAPITAL:
        await _safe_delete(context.bot, user_id,
                            context.user_data.get(f"capital_msg_{snap.session_id}_{ver}"))
        m = await context.bot.send_message(
            chat_id=user_id,
            text=f"⚠️ *Capital minimum : {int(MIN_CAPITAL)}$.*",
            parse_mode="Markdown",
        )
        context.user_data[f"capital_msg_{snap.session_id}_{ver}"] = m.message_id
        return

    # Enregistre le capital (RAM + SQL, TTL 7j)
    try:
        await weekly_capital.set(user_id, capital)
    except Exception as e:
        logger.error(f"[capital_input] uid={user_id} save échoué: {e}", exc_info=True)
        await context.bot.send_message(user_id,
            "⚠️ Erreur d'enregistrement. Réessaie dans un instant.")
        return

    await _safe_delete(context.bot, user_id,
                        context.user_data.get(f"capital_msg_{snap.session_id}_{ver}"))
    await _process_trade_full(context.bot, user_id, snap, capital,
                                capital_source="user_input",
                                delete_message_id=None)


# ══════════════════════════════════════════════════════════════════════════════
# Handlers hors-session (campagne hebdomadaire)
# ══════════════════════════════════════════════════════════════════════════════

async def handle_capital_update_form(update, context):
    """Bouton "💼 Mettre à jour mon capital" envoyé par la campagne hebdo."""
    query = update.callback_query
    if query is None:
        return
    uid = query.from_user.id
    await _safe_answer(query)
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        pass

    context.user_data["campaign_capital_pending"] = True
    await context.bot.send_message(
        chat_id=uid,
        text=("💼 *Nouveau capital ?*\n\nRenseigne le montant en chiffres uniquement.\n"
              "Il sera valable pour les 7 prochains jours.\n\n_Ex : 500 ou 1250_"),
        parse_mode="Markdown",
    )


async def handle_campaign_capital_input(update, context):
    """
    Handler texte HORS SESSION. Ne s'active que si :
      - le flag campagne est présent
      - ET l'user n'est PAS en waiting_capital d'une session en cours
    """
    msg = update.effective_message
    if msg is None or msg.from_user is None:
        return
    uid = msg.from_user.id

    if not context.user_data.get("campaign_capital_pending"):
        return

    snap = current_snapshot()
    ver  = current_version()
    if snap is not None and ver is not None:
        st = user_state_v7.get(snap.session_id, ver, uid)
        if st is not None and st.step == "waiting_capital":
            return   # priorité au handler session

    raw   = msg.text.strip()
    clean = raw.replace(",", ".").replace(" ", "").replace("$", "")
    is_numeric = clean.replace(".", "", 1).isdigit() and clean.count(".") <= 1
    if not is_numeric:
        await context.bot.send_message(uid,
            "⚠️ Chiffres uniquement. Ex : `500` ou `1250`", parse_mode="Markdown")
        return
    capital = float(clean)
    if capital < MIN_CAPITAL:
        await context.bot.send_message(uid,
            f"⚠️ Capital minimum : {int(MIN_CAPITAL)}$", parse_mode="Markdown")
        return

    try:
        await weekly_capital.set(uid, capital)
    except Exception as e:
        logger.error(f"[campaign_capital] uid={uid}: {e}", exc_info=True)
        await context.bot.send_message(uid, "⚠️ Erreur. Réessaie dans un instant.")
        return

    context.user_data.pop("campaign_capital_pending", None)
    await context.bot.send_message(
        uid,
        f"✅ *Capital enregistré : {capital}$*\n\nValable 7 jours. À la prochaine 👋",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Cœur : traitement complet du trade en une passe
# ══════════════════════════════════════════════════════════════════════════════

async def _process_trade_full(bot, user_id: int, snap: SessionSnapshot,
                                capital: float, *,
                                capital_source: str,
                                delete_message_id: int | None = None):
    """
    Enchaîne :
      1. ajustement entry/sl au prix live si meilleur point d'entrée
      2. build CalcContext (immutable) depuis (capital, snapshot)
      3. stockage du CalcContext dans le state manager
      4. transition en "processed" (état terminal)
      5. affichage du récap trade à l'user
      6. push dans le buffer (entry + step + event)
    """
    sid, ver = snap.session_id, snap.version

    live_price = await _get_live_price_safe()
    eff_entry, eff_sl, was_adjusted = adjust_entry_sl(snap, live_price)

    calc = build_calc_context(snap, user_id, capital, eff_entry, eff_sl)

    if not user_state_v7.set_calc(sid, ver, user_id, calc):
        await bot.send_message(user_id, "⏰ Ce trade vient de se fermer.")
        return

    if not user_state_v7.mark_processed(sid, ver, user_id, calc):
        await bot.send_message(user_id, "⏰ Ce trade vient de se fermer.")
        return

    gold_buffer.add_entry(
        sid, ver, user_id, snap.season_id, calc.capital,
        calc.risk_pct, calc.risk_usd, calc.lot, calc.tp_level,
        calc.perte_sl, calc.gain_tp1, calc.gain_tp2, calc.gain_tp3,
    )
    gold_buffer.add_step(sid, ver, user_id, "processed", capital)
    gold_buffer.add_event(sid, ver, user_id, "processed",
                              {"capital": capital, "lot": calc.lot,
                               "tp_level": calc.tp_level,
                               "capital_source": capital_source})

    if delete_message_id:
        await _safe_delete(bot, user_id, delete_message_id)

    text = _build_trade_recap(snap, calc, was_adjusted)
    await bot.send_message(user_id, text, parse_mode="Markdown")


def _build_trade_recap(snap: SessionSnapshot, calc: CalcContext,
                        was_adjusted: bool) -> str:
    tp_labels = {1: "TP1 seulement", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}
    dir_label = "📈 Achat (Buy)" if snap.direction == "buy" else "📉 Vente (Sell)"

    lines = [f"📊 *XAU/USD — Trade enregistré*", f"_{_fmt_now_discrete()}_", "",
             f"{dir_label}", "━━━━━━━━━━━━━━━━━━━━"]
    if was_adjusted:
        lines.append(f"🎯 Entrée ajustée : *{calc.effective_entry}*")
    else:
        lines.append(f"🎯 Entrée : *{calc.effective_entry}*")
    if snap.tp1:                        lines.append(f"✅ TP1 : *{snap.tp1}*")
    if snap.tp2 and calc.tp_level >= 2: lines.append(f"🎯 TP2 : *{snap.tp2}*")
    if snap.tp3 and calc.tp_level >= 3: lines.append(f"🏆 TP3 : *{snap.tp3}*")
    lines += [f"❌ SL  : *{calc.effective_sl}*",
              "━━━━━━━━━━━━━━━━━━━━",
              f"💼 Lot : *{calc.lot}*",
              f"🎯 Objectif : *{tp_labels[calc.tp_level]}*",
              f"💰 Capital : *{calc.capital}$*",
              "━━━━━━━━━━━━━━━━━━━━",
              "📊 *Scénarios estimés :*",
              f"❌ Si SL touché → *{calc.perte_sl}$*",
              f"✅ Si TP1 touché → *+{calc.gain_tp1}$*"]
    if calc.gain_tp2 and calc.tp_level >= 2:
        lines.append(f"🎯 Si TP2 touché → *+{calc.gain_tp2}$*")
    if calc.gain_tp3 and calc.tp_level >= 3:
        lines.append(f"🏆 Si TP3 touché → *+{calc.gain_tp3}$*")
    lines += ["━━━━━━━━━━━━━━━━━━━━",
              "_Tu recevras les instructions en temps réel._"]
    return "\n".join(lines)


def _build_teaser_message(snap: SessionSnapshot) -> str:
    dir_label  = "📈 Achat (Buy)" if snap.direction == "buy" else "📉 Vente (Sell)"
    conf_stars = "⭐" * snap.confidence_level
    return "\n".join([
        f"🔔 *Le trade du jour est disponible !*", "",
        f"📊 Paire : *XAU/USD*",
        f"{dir_label}", f"Confiance : {conf_stars}", "",
        "─────────────────────",
        "💡 *Rappel — gestion du risque :*",
        "• Respectez toujours votre SL",
        "• Ne risquez que ce que vous pouvez perdre",
        "─────────────────────", "",
        "_Cliquez ci-dessous pour accéder au trade._",
    ])


# ══════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT
# ══════════════════════════════════════════════════════════════════════════════

def register_gold_handlers_v7(app):
    app.add_handler(CallbackQueryHandler(handle_disclaimer_ok,
        pattern=r"^gold_disclaimer_ok_\d+(_v\d+)?$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_teaser_access,
        pattern=r"^gold_access_\d+(_v\d+)?$"), group=3)

    # Campagne hebdo — bouton "mettre à jour capital"
    app.add_handler(CallbackQueryHandler(handle_capital_update_form,
        pattern=r"^capital_update_form$"), group=3)

    # Handler texte SESSION — priorité haute
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND
        & filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE,
        handle_capital_input,
    ), group=3)

    # Handler texte CAMPAGNE HORS SESSION — priorité plus faible
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND
        & filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE,
        handle_campaign_capital_input,
    ), group=4)

    logger.info("[broadcast_v7.1] Handlers enregistrés (workflow simplifié + capital cache) ✓")