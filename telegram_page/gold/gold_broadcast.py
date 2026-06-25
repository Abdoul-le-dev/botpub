"""
gold_broadcast.py — Flux Telegram Gold v5 (MySQL async)

FIX appliqués :
  1. query.answer() appelé EN PREMIER dans chaque handler de callback,
     avant toute requête DB. Si answer() échoue (callback expiré),
     on log et on continue le traitement quand même — le clic reste utile.
  2. Sémaphore de concurrence sur les callbacks pour protéger le pool
     MySQL lors des pics de clics juste après un broadcast.
  3. Try/except enveloppant tout le corps métier de chaque handler,
     avec message de repli envoyé à l'utilisateur en cas d'erreur.
"""

import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from db import get_db

from telegram_page.gold.gold_engine import (
    confirm_gold_entry,
    get_active_gold_session,
    watch_gold_price,
    check_cramed_accounts,
    save_user_step,
    restore_user_context,
    get_rule_messages,
    get_tp_level_for_capital,
    calculate_lot,
    calculate_gains_losses,
    adjust_entry_sl_to_live_price,
    get_live_gold_price,
    _log_flow_event,
)
from telegram_page.gold.gold_write_queue import enqueue_write

logger      = logging.getLogger(__name__)
ADMIN_ID    = 571718066
CAPITAL_MIN = 30.0

# Limite le nombre de callbacks Gold traités en parallèle, pour éviter
# de saturer le pool MySQL quand tout le monde clique en même temps
# juste après un broadcast.
_callback_semaphore = asyncio.Semaphore(20)


# ══════════════════════════════════════════════════════════════════════════════
# WRAPPERS QUEUE — toutes les écritures DB de ce flux passent par la queue,
# pour qu'un seul worker les traite séquentiellement et qu'aucune ne soit
# jamais exécutée en parallèle massif lors d'un pic post-broadcast.
# ══════════════════════════════════════════════════════════════════════════════

async def _q_save_user_step(session_id: int, user_id: int, step: str, capital: float = None):
    await enqueue_write("save_user_step", save_user_step, session_id, user_id, step, capital)


async def _q_log_flow_event(session_id: int, user_id: int, event_type: str, payload: dict = None):
    await enqueue_write("log_flow_event", _log_flow_event, session_id, user_id, event_type, payload)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _get_prenom(user_id: int) -> str:
    async with get_db() as cur:
        await cur.execute(
            "SELECT name FROM users WHERE telegram_id = %s", (user_id,)
        )
        row = await cur.fetchone()
    if row and row["name"]:
        p = row["name"].strip().split()[0]
        if 1 <= len(p) <= 20:
            return p
    return ""


async def _get_last_capital(user_id: int) -> float | None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT capital_declared FROM gold_member_entries
            WHERE user_id = %s
            ORDER BY confirmed_at DESC LIMIT 1
        """, (user_id,))
        row = await cur.fetchone()
    return float(row["capital_declared"]) if row else None


async def _get_category_user_ids(category: str) -> list:
    async with get_db() as cur:
        if category == "all":
            await cur.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            )
            rows = await cur.fetchall()
            return [r["telegram_id"] for r in rows] if rows else []
        else:
            await cur.execute(
                "SELECT id_user FROM categories WHERE name_categorie = %s",
                (category,)
            )
            rows = await cur.fetchall()
            return [r["id_user"] for r in rows] if rows else []


def _fmt_now_discrete() -> str:
    now   = datetime.now()
    jours = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    mois  = ["jan", "fév", "mar", "avr", "mai", "juin",
              "juil", "août", "sep", "oct", "nov", "déc"]
    return f"{jours[now.weekday()]} {now.day:02d} {mois[now.month-1]} · {now.strftime('%H:%M')}"


async def _safe_delete(bot, chat_id: int, message_id: int):
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _safe_answer(query, text: str = None, show_alert: bool = False) -> bool:
    """
    Tente de répondre au callback. Retourne True si ça a marché,
    False si le callback a expiré (Query is too old) — dans ce cas
    on log mais on NE BLOQUE PAS le traitement qui suit.
    """
    try:
        if text:
            await query.answer(text, show_alert=show_alert)
        else:
            await query.answer()
        return True
    except Exception as e:
        logger.warning(f"[_safe_answer] callback expiré: {e}")
        return False


async def _notify_user_error(bot, user_id: int):
    try:
        await bot.send_message(
            chat_id=user_id,
            text="⚠️ Une erreur est survenue. Tape /start ou réessaie dans un instant. "
                 "Si ça persiste, contacte le support."
        )
    except Exception:
        pass


async def _send_or_photo(bot, chat_id: int, text: str,
                          screenshot_url: str = None,
                          reply_markup=None,
                          parse_mode: str = "Markdown") -> object:
    if screenshot_url:
        try:
            return await bot.send_photo(
                chat_id=chat_id, photo=screenshot_url,
                caption=text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        except Exception:
            pass
    return await bot.send_message(
        chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 0 — DISCLAIMER
# ══════════════════════════════════════════════════════════════════════════════

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


def _build_disclaimer_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ J'ai compris — Voir le trade",
            callback_data=f"gold_disclaimer_ok_{session_id}"
        )
    ]])


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 — TEASER
# ══════════════════════════════════════════════════════════════════════════════

async def _build_teaser_message(session: dict, prenom: str = "") -> str:
    rule_msgs = await get_rule_messages(1)
    tpl       = rule_msgs.get("message_teaser")
    dir_label = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"
    conf_stars = "⭐" * session.get("confidence_level", 3)
    greeting  = f" — {prenom}" if prenom else ""

    if tpl:
        return (tpl
                .replace("{direction}", dir_label)
                .replace("{confiance}", conf_stars)
                .replace("{prenom}", prenom)
                .replace("{pair}", session.get("pair", "XAU/USD")))

    lines = [
        f"🔔 *Le trade du jour est disponible{greeting} !*", "",
        f"📊 Paire : *{session.get('pair', 'XAU/USD')}*",
        f"{dir_label}", f"Confiance : {conf_stars}", "",
        "─────────────────────",
        "💡 *Rappel — gestion du risque :*",
        "• Respectez toujours votre SL",
        "• Ne risquez que ce que vous pouvez perdre",
        "• Suivez les instructions en temps réel",
        "─────────────────────", "",
        "_Cliquez ci-dessous pour accéder au trade._",
    ]
    return "\n".join(lines)


def _build_teaser_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 Accéder au trade →", callback_data=f"gold_access_{session_id}")
    ]])


# ══════════════════════════════════════════════════════════════════════════════
# ENVOI BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

async def send_gold_teaser(bot, session: dict,
                            category: str = "see",
                            delay: float  = 0.08) -> dict:
    session_id = session["id"]
    user_ids   = await _get_category_user_ids("clients_actifs")
    total      = len(user_ids)

    if total == 0:
        return {"total": 0, "sent": 0, "errors": 0, "session_id": session_id}

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"📤 *Envoi teaser Gold démarré*\n"
                  f"Destinataires : {total} | Session #{session_id}\n"
                  f"Direction : {'Achat (Buy)' if session['direction'] == 'buy' else 'Vente (Sell)'}\n"
                  f"Entrée : {session['entry_price']}"),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    sent = errors = 0

    for idx, user_id in enumerate(user_ids, start=1):
        try:
            prenom   = await _get_prenom(user_id)
            date_str = _fmt_now_discrete()
            dir_label = "Achat (Buy)" if session["direction"] == "buy" else "Vente (Sell)"

            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(f"🔔 ─────────────────────\n"
                          f"*Signal Gold disponible*\n"
                          f"_{dir_label} · {date_str}_\n"
                          f"─────────────────────"),
                    parse_mode="Markdown",
                )
            except Exception:
                pass

            await bot.send_message(
                chat_id=user_id, text=DISCLAIMER_TEXT,
                parse_mode="Markdown",
                reply_markup=_build_disclaimer_keyboard(session_id),
            )

            await _q_save_user_step(session_id, user_id, "teaser")
            await _q_log_flow_event(session_id, user_id, "disclaimer_sent", None)
            sent += 1

        except Exception as e:
            logger.warning(f"[teaser] uid={user_id}: {e}")
            errors += 1

        if idx == total // 2:
            try:
                await bot.send_message(ADMIN_ID, f"📊 Teaser Gold — {sent}/{total} envoyés...")
            except Exception:
                pass

        await asyncio.sleep(delay)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"✅ *Teaser Gold terminé*\nEnvoyés : {sent}/{total} | Erreurs : {errors}",
            parse_mode="Markdown",
        )
    except Exception:
        pass

    asyncio.create_task(watch_gold_price(session_id))
    return {"total": total, "sent": sent, "errors": errors, "session_id": session_id}


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def handle_disclaimer_ok(update, context):
    async with _callback_semaphore:
        query   = update.callback_query
        user_id = query.from_user.id

        try:
            session_id = int(query.data.split("_")[3])
        except (IndexError, ValueError):
            await _safe_answer(query, "❌ Erreur.", show_alert=True)
            return

        # On répond TOUT DE SUITE, avant toute requête DB.
        # Même si ça échoue (callback expiré), on continue le traitement :
        # le clic de l'utilisateur reste valide et doit produire un effet.
        await _safe_answer(query)

        try:
            session = await get_active_gold_session()
            if not session:
                await _safe_delete(context.bot, user_id, query.message.message_id)
                try:
                    await context.bot.send_message(
                        chat_id=user_id, text="⏰ Ce trade n'est plus disponible."
                    )
                except Exception:
                    pass
                return

            real_session_id = session["id"]
            await _safe_delete(context.bot, user_id, query.message.message_id)

            prenom  = await _get_prenom(user_id)
            message = await _build_teaser_message(session, prenom)
            kbd     = _build_teaser_keyboard(real_session_id)

            msg = await _send_or_photo(
                bot=context.bot, chat_id=user_id, text=message,
                screenshot_url=session.get("screenshot_url"), reply_markup=kbd,
            )
            if msg:
                context.user_data[f"teaser_msg_id_{real_session_id}"] = msg.message_id

            await _q_log_flow_event(real_session_id, user_id, "teaser_shown", None)

        except Exception as e:
            logger.error(f"[handle_disclaimer_ok] uid={user_id}: {e}", exc_info=True)
            await _notify_user_error(context.bot, user_id)


async def handle_teaser_click(update, context):
    async with _callback_semaphore:
        query   = update.callback_query
        user_id = query.from_user.id

        try:
            session_id = int(query.data.split("_")[2])
        except (IndexError, ValueError):
            await _safe_answer(query, "❌ Erreur — réessaie.", show_alert=True)
            return

        await _safe_answer(query)

        try:
            session = await get_active_gold_session()
            if not session:
                await _safe_delete(context.bot, user_id, query.message.message_id)
                try:
                    await context.bot.send_message(
                        chat_id=user_id, text="⏰ Ce trade n'est plus disponible."
                    )
                except Exception:
                    pass
                return

            real_session_id = session["id"]
            await _q_log_flow_event(real_session_id, user_id, "teaser_clicked", None)
            await _safe_delete(context.bot, user_id, query.message.message_id)

            context.user_data["gold_session_id"] = real_session_id
            context.user_data["waiting_capital"] = True
            await _q_save_user_step(real_session_id, user_id, "waiting_capital")

            last_capital = await _get_last_capital(user_id)
            hint = f"\n\n_Dernier capital enregistré : *{last_capital}$*_" if last_capital else ""

            msg = await context.bot.send_message(
                chat_id=user_id,
                text=(f"💼 *Quel est ton capital actuel en $ ?*\n\n"
                      f"Renseigne le montant en chiffres uniquement.{hint}\n\n"
                      f"_Ex : 500 ou 1250_"),
                parse_mode="Markdown",
            )
            if msg:
                context.user_data[f"capital_msg_id_{real_session_id}"] = msg.message_id

        except Exception as e:
            logger.error(f"[handle_teaser_click] uid={user_id}: {e}", exc_info=True)
            await _notify_user_error(context.bot, user_id)


async def handle_capital_input(update, context):
    user_id = update.message.from_user.id

    if not context.user_data.get("waiting_capital"):
        restored = await restore_user_context(user_id)
        if restored and restored["step"] == "waiting_capital":
            context.user_data["gold_session_id"] = restored["session_id"]
            context.user_data["waiting_capital"]  = True
        else:
            return

    session_id = context.user_data.get("gold_session_id")
    if not session_id:
        return

    # Même protection de concurrence que les callbacks Gold : ce handler
    # est aussi exposé au pic de charge post-broadcast (des centaines
    # d'utilisateurs qui tapent leur capital en même temps), donc il doit
    # passer par le même sémaphore pour ne pas saturer le pool MySQL.
    async with _callback_semaphore:
        try:
            await _process_capital_input(update, context, user_id, session_id)
        except Exception as e:
            logger.error(f"[handle_capital_input] uid={user_id}: {e}", exc_info=True)
            await _notify_user_error(context.bot, user_id)


async def _process_capital_input(update, context, user_id: int, session_id: int):
    raw   = update.message.text.strip()
    clean = raw.replace(",", ".").replace(" ", "").replace("$", "")
    await _safe_delete(context.bot, user_id, update.message.message_id)

    is_numeric = clean.replace(".", "", 1).isdigit() and clean.count(".") <= 1

    if not is_numeric or not clean:
        capital_msg_id = context.user_data.get(f"capital_msg_id_{session_id}")
        await _safe_delete(context.bot, user_id, capital_msg_id)
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=("⚠️ *Entre uniquement des chiffres.*\n\n"
                  "Exemple : `500` ou `1250`\n\n"
                  "_Quel est ton capital actuel en $ ?_"),
            parse_mode="Markdown",
        )
        context.user_data[f"capital_msg_id_{session_id}"] = msg.message_id
        return

    capital = float(clean)

    if capital < CAPITAL_MIN:
        capital_msg_id = context.user_data.get(f"capital_msg_id_{session_id}")
        await _safe_delete(context.bot, user_id, capital_msg_id)
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=(f"⚠️ *Capital minimum requis : {int(CAPITAL_MIN)}$.*\n\n"
                  f"Renseigne un montant supérieur ou égal à {int(CAPITAL_MIN)}$.\n\n"
                  "_Quel est ton capital actuel en $ ?_"),
            parse_mode="Markdown",
        )
        context.user_data[f"capital_msg_id_{session_id}"] = msg.message_id
        return

    context.user_data["waiting_capital"] = False
    capital_msg_id = context.user_data.get(f"capital_msg_id_{session_id}")
    await _safe_delete(context.bot, user_id, capital_msg_id)
    await _q_save_user_step(session_id, user_id, "trade_shown", capital)
    await _show_trade_detail(context.bot, user_id, session_id, capital, context=context)


async def _show_trade_detail(bot, user_id: int, session_id: int, capital: float, context=None):
    session = await get_active_gold_session()
    if not session:
        await bot.send_message(chat_id=user_id, text="⏰ Ce trade n'est plus disponible.",
                                parse_mode="Markdown")
        return

    session_id = session["id"]

    # Récupère le prix live et ajuste entry/sl si l'utilisateur a un
    # meilleur point d'entrée que le prix prévu à l'origine. Les TP
    # restent toujours ceux d'origine.
    live_price = await get_live_gold_price()
    adjustment = adjust_entry_sl_to_live_price(
        direction=session["direction"],
        entry=session["entry_price"],
        sl=session["sl"],
        live_price=live_price,
    )
    effective_entry = adjustment["entry"]
    effective_sl     = adjustment["sl"]
    was_adjusted     = adjustment["adjusted"]

    # Mémorise les valeurs effectives pour cet utilisateur — nécessaires
    # à la confirmation (handle_gold_confirm) pour calculer lot/gains
    # avec les BONNES valeurs, pas les valeurs d'origine de la session.
    if context is not None:
        context.user_data[f"effective_entry_{session_id}"] = effective_entry
        context.user_data[f"effective_sl_{session_id}"]     = effective_sl

    lot = calculate_lot(capital, effective_entry, effective_sl)

    # Même règle qu'à la confirmation : la perte SL utilise l'entry/sl
    # effectifs (ajustés), les gains TP utilisent l'entry ORIGINAL — le
    # gain potentiel annoncé ne change jamais, seul le risque s'ajuste.
    risk_gains = calculate_gains_losses(lot=lot, entry=effective_entry, sl=effective_sl)
    tp_gains   = calculate_gains_losses(lot=lot, entry=session["entry_price"], sl=effective_sl,
                                         tp1=session.get("tp1"), tp2=session.get("tp2"),
                                         tp3=session.get("tp3"))
    gains = {
        "perte_sl": risk_gains["perte_sl"],
        "gain_tp1": tp_gains["gain_tp1"],
        "gain_tp2": tp_gains["gain_tp2"],
        "gain_tp3": tp_gains["gain_tp3"],
    }

    tp_level, _ = await get_tp_level_for_capital(capital)
    tp_labels   = {1: "TP1 seulement", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}
    dir_label   = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"
    date_label  = _fmt_now_discrete()

    lines = [
        f"📊 *XAU/USD — Trade du jour*", f"_{date_label}_", "",
        f"{dir_label}", "━━━━━━━━━━━━━━━━━━━━",
    ]

    if was_adjusted:
        lines.append(f"🎯 Entrée ajustée : *{effective_entry}*")
        lines.append(f"_(prix du jour {'plus haut' if session['direction'] == 'sell' else 'plus bas'} que prévu — meilleur point d'entrée pour toi)_")
    else:
        lines.append(f"🎯 Entrée : *{effective_entry}*")

    if session.get("tp1"):                   lines.append(f"✅ TP1 : *{session['tp1']}*")
    if session.get("tp2") and tp_level >= 2: lines.append(f"🎯 TP2 : *{session['tp2']}*")
    if session.get("tp3") and tp_level >= 3: lines.append(f"🏆 TP3 : *{session['tp3']}*")

    lines += [
        f"❌ SL  : *{effective_sl}*", "━━━━━━━━━━━━━━━━━━━━",
        f"💼 Lot recommandé : *{lot}*",
        f"🎯 Objectif : *{tp_labels[tp_level]}*",
        f"💰 Capital déclaré : *{capital}$*", "━━━━━━━━━━━━━━━━━━━━",
        "📊 *Scénarios estimés :*",
        f"❌ Si SL touché → *{gains['perte_sl']}$*",
        f"✅ Si TP1 touché → *+{gains['gain_tp1']}$*",
    ]
    if gains.get("gain_tp2") and tp_level >= 2:
        lines.append(f"🎯 Si TP2 touché → *+{gains['gain_tp2']}$*")
    if gains.get("gain_tp3") and tp_level >= 3:
        lines.append(f"🏆 Si TP3 touché → *+{gains['gain_tp3']}$*")
    if session.get("note"):
        lines += ["━━━━━━━━━━━━━━━━━━━━", f"📝 *Note :* _{session['note']}_"]
    lines += ["━━━━━━━━━━━━━━━━━━━━", "_Tu recevras les instructions en temps réel._"]

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Je confirme — Je prends ce trade",
                               callback_data=f"gold_confirm_{session_id}_{capital}")],
        [InlineKeyboardButton("❌ Je ne prends pas ce trade",
                               callback_data=f"gold_skip_{session_id}")],
    ])

    await _send_or_photo(bot=bot, chat_id=user_id, text="\n".join(lines),
                          screenshot_url=session.get("screenshot_url"), reply_markup=kbd)
    await _q_log_flow_event(session_id, user_id, "trade_shown",
                           {"capital": capital, "lot": lot, "tp_level": tp_level,
                            "entry": effective_entry, "sl": effective_sl, "adjusted": was_adjusted})


async def handle_gold_confirm(update, context):
    async with _callback_semaphore:
        query   = update.callback_query
        user_id = query.from_user.id

        try:
            parts      = query.data.split("_")
            session_id = int(parts[2])
            capital    = float(parts[3])
        except (IndexError, ValueError):
            await _safe_answer(query, "❌ Erreur — réessaie.", show_alert=True)
            return

        await _safe_answer(query, "⏳ Enregistrement...", show_alert=False)

        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
        except Exception:
            pass

        # Récupère l'entrée/SL effectifs calculés au moment où le détail
        # du trade a été montré (peuvent différer des valeurs de session
        # si le prix live offrait un meilleur point d'entrée). Si absents
        # (ex: contexte perdu après redémarrage du bot), on retombe sur
        # None — confirm_gold_entry utilisera alors les valeurs de session.
        effective_entry = context.user_data.get(f"effective_entry_{session_id}")
        effective_sl    = context.user_data.get(f"effective_sl_{session_id}")

        try:
            result = await confirm_gold_entry(
                session_id, user_id, capital,
                override_entry=effective_entry,
                override_sl=effective_sl,
            )

            if "error" in result:
                await query.message.reply_text(f"❌ {result['error']}", parse_mode="Markdown")
                return

            await query.message.reply_text(result["message"], parse_mode="Markdown")

            # Alerte "compte cramé" calculée directement à partir du résultat
            # déjà en main (capital + perte_sl), SANS relire la DB — l'écriture
            # de cette confirmation est en queue et pas encore garantie d'être
            # visible si on refaisait un SELECT ici.
            entry        = result["entry"]
            capital_apres = entry["capital"] - abs(entry["perte_sl"] or 0)
            if capital_apres <= 0:
                await query.message.reply_text(
                    "⚠️ *Attention — Capital très faible !*\n\n"
                    "Si le SL est touché sur ce trade, ton compte sera en très grande difficulté.\n\n"
                    "_Assure-toi d'être à l'aise avec ce risque avant de continuer._",
                    parse_mode="Markdown",
                )

        except Exception as e:
            logger.error(f"[handle_gold_confirm] uid={user_id}: {e}", exc_info=True)
            await _notify_user_error(context.bot, user_id)


async def handle_gold_skip(update, context):
    async with _callback_semaphore:
        query   = update.callback_query
        user_id = query.from_user.id

        try:
            session_id = int(query.data.split("_")[2])
        except (IndexError, ValueError):
            await _safe_answer(query)
            return

        await _safe_answer(query, "👌 Compris — trade non pris.", show_alert=False)

        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Trade non pris — Noté 👌", callback_data="gold_done")
            ]]))
        except Exception:
            pass

        try:
            await _q_save_user_step(session_id, user_id, "cancelled")
            await _q_log_flow_event(session_id, user_id, "cancelled", None)
        except Exception as e:
            logger.error(f"[handle_gold_skip] uid={user_id}: {e}", exc_info=True)


async def handle_gold_done(update, context):
    await _safe_answer(update.callback_query, "Tu as déjà répondu à ce trade.", show_alert=False)


# ══════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def register_gold_handlers(app):
    app.add_handler(CallbackQueryHandler(handle_disclaimer_ok,  pattern=r"^gold_disclaimer_ok_\d+$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_teaser_click,   pattern=r"^gold_access_\d+$"),        group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_confirm,   pattern=r"^gold_confirm_\d+_[\d.]+$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_skip,      pattern=r"^gold_skip_\d+$"),           group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_done,      pattern=r"^gold_done$"),               group=3)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_capital_input), group=3)
    print("[gold_broadcast] Handlers Gold v5 enregistrés ✓")