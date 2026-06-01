"""
gold_broadcast.py — Flux Telegram Gold v3

Corrections v3 :
  1. Suppression messages (flux propre) — teaser supprimé avant question capital
  2. Validation capital — chiffres uniquement, minimum 30$, redemande propre
  3. Étape 0 — Disclaimer risque trading avant le teaser
  4. Nouvelles formules calculate_lot() / calculate_gains_losses()
  5. Trade avec date/heure discrets
  6. Media (photo/vidéo) avec le teaser
  7. _get_last_capital → gold_member_entries (plus member_capital)
  8. Persistance étapes gold_user_sessions
"""

import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

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
    _log_flow_event,
    get_conn,
)

logger   = logging.getLogger(__name__)
ADMIN_ID = 571718066

CAPITAL_MIN = 30.0


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_prenom(user_id: int) -> str:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT name FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    if row and row["name"]:
        p = row["name"].strip().split()[0]
        if 1 <= len(p) <= 20:
            return p
    return ""


def _get_last_capital(user_id: int) -> float | None:
    """
    CORRECTION v3 : lit depuis gold_member_entries (member_capital supprimée).
    Retourne le dernier capital déclaré par ce user.
    """
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT capital_declared FROM gold_member_entries
            WHERE user_id = ?
            ORDER BY confirmed_at DESC LIMIT 1
        """, (user_id,)).fetchone()
    finally:
        conn.close()
    return float(row["capital_declared"]) if row else None


def _get_category_user_ids(category: str) -> list:
    conn = get_conn()
    try:
        if category == "all":
            rows = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?",
                (category,)
            ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _fmt_now_discrete() -> str:
    """Date/heure discrète : 'Lun 02 juin · 09:45'"""
    now    = datetime.now()
    jours  = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    mois   = ["jan", "fév", "mar", "avr", "mai", "juin",
               "juil", "août", "sep", "oct", "nov", "déc"]
    return f"{jours[now.weekday()]} {now.day:02d} {mois[now.month-1]} · {now.strftime('%H:%M')}"


async def _safe_delete(bot, chat_id: int, message_id: int):
    """Supprime un message sans lever d'exception."""
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _send_or_photo(bot, chat_id: int, text: str,
                          screenshot_url: str = None,
                          reply_markup=None,
                          parse_mode: str = "Markdown") -> object:
    """Envoie photo+caption ou texte seul selon screenshot_url."""
    if screenshot_url:
        try:
            return await bot.send_photo(
                chat_id      = chat_id,
                photo        = screenshot_url,
                caption      = text,
                parse_mode   = parse_mode,
                reply_markup = reply_markup,
            )
        except Exception:
            pass  # fallback texte si photo échoue
    return await bot.send_message(
        chat_id      = chat_id,
        text         = text,
        parse_mode   = parse_mode,
        reply_markup = reply_markup,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 0 — DISCLAIMER (avant le teaser)
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

def _build_teaser_message(session: dict, prenom: str = "") -> str:
    rule_msgs = get_rule_messages(1)
    tpl       = rule_msgs.get("message_teaser")

    dir_label  = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"
    conf_stars = "⭐" * session.get("confidence_level", 3)
    greeting   = f" — {prenom}" if prenom else ""

    if tpl:
        return (tpl
                .replace("{direction}", dir_label)
                .replace("{confiance}", conf_stars)
                .replace("{prenom}", prenom)
                .replace("{pair}", session.get("pair", "XAU/USD")))

    lines = [
        f"🔔 *Le trade du jour est disponible{greeting} !*",
        "",
        f"📊 Paire : *{session.get('pair', 'XAU/USD')}*",
        f"{dir_label}",
        f"Confiance : {conf_stars}",
        "",
        "─────────────────────",
        "💡 *Rappel — gestion du risque :*",
        "• Respectez toujours votre SL",
        "• Ne risquez que ce que vous pouvez perdre",
        "• Suivez les instructions en temps réel",
        "─────────────────────",
        "",
        "_Cliquez ci-dessous pour accéder au trade._",
    ]
    return "\n".join(lines)


def _build_teaser_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📊 Accéder au trade →",
            callback_data=f"gold_access_{session_id}"
        )
    ]])


# ══════════════════════════════════════════════════════════════════════════════
# ENVOI BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

async def send_gold_teaser(bot, session: dict,
                            category: str = "clients_actifs",
                            delay: float  = 0.08) -> dict:
    """
    Envoie le disclaimer + teaser à tous les membres de la catégorie.
    Démarre la surveillance prix en arrière-plan.
    """
    session_id = session["id"]
    user_ids   = _get_category_user_ids(category)
    total      = len(user_ids)

    if total == 0:
        return {"total": 0, "sent": 0, "errors": 0, "session_id": session_id}

    try:
        await bot.send_message(
            chat_id    = ADMIN_ID,
            text       = (
                f"📤 *Envoi teaser Gold démarré*\n"
                f"Destinataires : {total} | Session #{session_id}\n"
                f"Direction : {'Achat (Buy)' if session['direction'] == 'buy' else 'Vente (Sell)'}\n"
                f"Entrée : {session['entry_price']}"
            ),
            parse_mode = "Markdown",
        )
    except Exception:
        pass

    sent = errors = 0

    for idx, user_id in enumerate(user_ids, start=1):
        try:
            prenom = _get_prenom(user_id)

            # ── Séparateur visuel ─────────────────────────────────────────
            # Isole le nouveau flux des anciens messages dans le chat
            date_str = _fmt_now_discrete()
            dir_label = "Achat (Buy)" if session["direction"] == "buy" else "Vente (Sell)"
            separateur = (
                f"🔔 ─────────────────────\n"
                f"*Signal Gold disponible*\n"
                f"_{dir_label} · {date_str}_\n"
                f"─────────────────────"
            )
            try:
                await bot.send_message(
                    chat_id    = user_id,
                    text       = separateur,
                    parse_mode = "Markdown",
                )
            except Exception:
                pass  # ne pas bloquer si le séparateur échoue

            # ── Disclaimer ───────────────────────────────────────────────
            await bot.send_message(
                chat_id      = user_id,
                text         = DISCLAIMER_TEXT,
                parse_mode   = "Markdown",
                reply_markup = _build_disclaimer_keyboard(session_id),
            )

            await save_user_step(session_id, user_id, "teaser")
            await _log_flow_event(session_id, user_id, "disclaimer_sent", None)
            sent += 1

        except Exception as e:
            logger.warning(f"[teaser] uid={user_id}: {e}")
            errors += 1

        if idx == total // 2:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"📊 Teaser Gold — {sent}/{total} envoyés..."
                )
            except Exception:
                pass

        await asyncio.sleep(delay)

    try:
        await bot.send_message(
            chat_id    = ADMIN_ID,
            text       = f"✅ *Teaser Gold terminé*\nEnvoyés : {sent}/{total} | Erreurs : {errors}",
            parse_mode = "Markdown",
        )
    except Exception:
        pass

    asyncio.create_task(watch_gold_price(session_id))

    return {"total": total, "sent": sent, "errors": errors, "session_id": session_id}


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER — DISCLAIMER OK → ENVOIE LE TEASER
# ══════════════════════════════════════════════════════════════════════════════

async def handle_disclaimer_ok(update, context):
    query      = update.callback_query
    user_id    = query.from_user.id
    data       = query.data  # gold_disclaimer_ok_{session_id}

    try:
        session_id = int(data.split("_")[3])
    except (IndexError, ValueError):
        await query.answer("❌ Erreur.", show_alert=True)
        return

    # Prendre la session active — si l'ID ne correspond pas (reset entre temps)
    # on prend quand même la session active courante
    session = await get_active_gold_session()
    if not session:
        await query.answer("⏰ Aucun trade actif en ce moment.", show_alert=True)
        await _safe_delete(context.bot, user_id, query.message.message_id)
        return

    # Utiliser l'ID de la session active (peut différer si reset)
    real_session_id = session["id"]

    await query.answer()

    # Supprimer le disclaimer
    await _safe_delete(context.bot, user_id, query.message.message_id)

    # Envoyer le teaser
    prenom  = _get_prenom(user_id)
    message = _build_teaser_message(session, prenom)
    kbd     = _build_teaser_keyboard(real_session_id)

    msg = await _send_or_photo(
        bot            = context.bot,
        chat_id        = user_id,
        text           = message,
        screenshot_url = session.get("screenshot_url"),
        reply_markup   = kbd,
    )

    if msg:
        context.user_data[f"teaser_msg_id_{real_session_id}"] = msg.message_id

    await _log_flow_event(real_session_id, user_id, "teaser_shown", None)


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER — CLIC TEASER → DEMANDE CAPITAL
# ══════════════════════════════════════════════════════════════════════════════

async def handle_teaser_click(update, context):
    """
    User clique "Accéder au trade" →
    1. Supprimer le teaser
    2. Envoyer la question capital
    """
    query      = update.callback_query
    user_id    = query.from_user.id
    data       = query.data  # gold_access_{session_id}

    try:
        session_id = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.answer("❌ Erreur — réessaie.", show_alert=True)
        return

    # Prendre la session active — tolère un reset entre temps
    session = await get_active_gold_session()
    if not session:
        await query.answer("⏰ Aucun trade actif en ce moment.", show_alert=True)
        await _safe_delete(context.bot, user_id, query.message.message_id)
        return

    real_session_id = session["id"]

    await query.answer()
    await _log_flow_event(real_session_id, user_id, "teaser_clicked", None)

    # Supprimer le teaser
    await _safe_delete(context.bot, user_id, query.message.message_id)

    # Stocker état avec le bon session_id
    context.user_data["gold_session_id"] = real_session_id
    context.user_data["waiting_capital"] = True
    await save_user_step(real_session_id, user_id, "waiting_capital")

    last_capital = _get_last_capital(user_id)
    hint = f"\n\n_Dernier capital enregistré : *{last_capital}$*_" if last_capital else ""

    msg = await context.bot.send_message(
        chat_id    = user_id,
        text       = (
            f"💼 *Quel est ton capital actuel en $ ?*\n\n"
            f"Renseigne le montant en chiffres uniquement.{hint}\n\n"
            f"_Ex : 500 ou 1250_"
        ),
        parse_mode = "Markdown",
    )

    if msg:
        context.user_data[f"capital_msg_id_{real_session_id}"] = msg.message_id


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER — RÉCEPTION CAPITAL
# ══════════════════════════════════════════════════════════════════════════════

async def handle_capital_input(update, context):
    """
    Reçoit le capital saisi par le user.

    Validation :
      - Uniquement des chiffres (+ point/virgule)
      - Minimum 30$
      - Si invalide → supprimer message user + redemander proprement
    """
    user_id = update.message.from_user.id

    # Vérifier qu'on attend un capital
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

    raw   = update.message.text.strip()
    clean = raw.replace(",", ".").replace(" ", "").replace("$", "")

    # Supprimer le message du user (flux propre)
    await _safe_delete(context.bot, user_id, update.message.message_id)

    # ── Validation chiffres uniquement ────────────────────────────────────
    # Vérifier que clean ne contient que chiffres et au plus un point
    is_numeric = clean.replace(".", "", 1).isdigit() and clean.count(".") <= 1

    if not is_numeric or not clean:
        capital_msg_id = context.user_data.get(f"capital_msg_id_{session_id}")
        await _safe_delete(context.bot, user_id, capital_msg_id)

        msg = await context.bot.send_message(
            chat_id    = user_id,
            text       = (
                "⚠️ *Entre uniquement des chiffres.*\n\n"
                "Exemple : `500` ou `1250`\n\n"
                "_Quel est ton capital actuel en $ ?_"
            ),
            parse_mode = "Markdown",
        )
        context.user_data[f"capital_msg_id_{session_id}"] = msg.message_id
        return

    capital = float(clean)

    # ── Validation montant minimum ────────────────────────────────────────
    if capital < CAPITAL_MIN:
        capital_msg_id = context.user_data.get(f"capital_msg_id_{session_id}")
        await _safe_delete(context.bot, user_id, capital_msg_id)

        msg = await context.bot.send_message(
            chat_id    = user_id,
            text       = (
                f"⚠️ *Capital minimum requis : {int(CAPITAL_MIN)}$.*\n\n"
                f"Renseigne un montant supérieur ou égal à {int(CAPITAL_MIN)}$.\n\n"
                "_Quel est ton capital actuel en $ ?_"
            ),
            parse_mode = "Markdown",
        )
        context.user_data[f"capital_msg_id_{session_id}"] = msg.message_id
        return

    # ── Capital valide ─────────────────────────────────────────────────────
    context.user_data["waiting_capital"] = False

    # Supprimer la question capital
    capital_msg_id = context.user_data.get(f"capital_msg_id_{session_id}")
    await _safe_delete(context.bot, user_id, capital_msg_id)

    await save_user_step(session_id, user_id, "trade_shown", capital)
    await _show_trade_detail(context.bot, user_id, session_id, capital)


# ══════════════════════════════════════════════════════════════════════════════
# AFFICHAGE TRADE PERSONNALISÉ
# ══════════════════════════════════════════════════════════════════════════════

async def _show_trade_detail(bot, user_id: int, session_id: int, capital: float):
    """
    Affiche le trade complet personnalisé.
    CORRECTION v3 : utilise calculate_lot() et calculate_gains_losses().
    Inclut date/heure discrets.
    """
    session = await get_active_gold_session()
    if not session:
        await bot.send_message(
            chat_id    = user_id,
            text       = "⏰ Aucun trade actif en ce moment.",
            parse_mode = "Markdown",
        )
        return

    # Utiliser la session active (pas forcément celle du session_id initial)
    session_id = session["id"]

    # Nouvelle formule lot v3
    lot   = calculate_lot(capital, session["entry_price"], session["sl"])
    gains = calculate_gains_losses(
        lot   = lot,
        entry = session["entry_price"],
        sl    = session["sl"],
        tp1   = session.get("tp1"),
        tp2   = session.get("tp2"),
        tp3   = session.get("tp3"),
    )

    tp_level, _ = get_tp_level_for_capital(capital)
    tp_labels   = {1: "TP1 seulement", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}
    dir_label   = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"
    date_label  = _fmt_now_discrete()

    lines = [
        f"📊 *XAU/USD — Trade du jour*",
        f"_{date_label}_",
        "",
        f"{dir_label}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Entrée : *{session['entry_price']}*",
    ]

    if session.get("tp1"):
        lines.append(f"✅ TP1 : *{session['tp1']}*")
    if session.get("tp2") and tp_level >= 2:
        lines.append(f"🎯 TP2 : *{session['tp2']}*")
    if session.get("tp3") and tp_level >= 3:
        lines.append(f"🏆 TP3 : *{session['tp3']}*")

    lines += [
        f"❌ SL  : *{session['sl']}*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💼 Lot recommandé : *{lot}*",
        f"🎯 Objectif : *{tp_labels[tp_level]}*",
        f"💰 Capital déclaré : *{capital}$*",
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 *Scénarios estimés :*",
        f"❌ Si SL touché → *{gains['perte_sl']}$*",
        f"✅ Si TP1 touché → *+{gains['gain_tp1']}$*",
    ]

    if gains.get("gain_tp2") and tp_level >= 2:
        lines.append(f"🎯 Si TP2 touché → *+{gains['gain_tp2']}$*")
    if gains.get("gain_tp3") and tp_level >= 3:
        lines.append(f"🏆 Si TP3 touché → *+{gains['gain_tp3']}$*")

    if session.get("note"):
        lines += [
            "━━━━━━━━━━━━━━━━━━━━",
            f"📝 *Note :* _{session['note']}_",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "_Tu recevras les instructions en temps réel._",
    ]

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Je confirme — Je prends ce trade",
            callback_data=f"gold_confirm_{session_id}_{capital}"
        )],
        [InlineKeyboardButton(
            "❌ Je ne prends pas ce trade",
            callback_data=f"gold_skip_{session_id}"
        )],
    ])

    # Envoyer avec screenshot si disponible
    await _send_or_photo(
        bot            = bot,
        chat_id        = user_id,
        text           = "\n".join(lines),
        screenshot_url = session.get("screenshot_url"),
        reply_markup   = kbd,
    )

    await _log_flow_event(session_id, user_id, "trade_shown", {
        "capital": capital, "lot": lot, "tp_level": tp_level
    })


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER — CONFIRMATION TRADE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_gold_confirm(update, context):
    """
    User confirme le trade →
    1. Remplacer les boutons (plus de double clic)
    2. Enregistrer en DB
    3. Envoyer message de confirmation
    4. Alerte si capital en danger
    """
    query      = update.callback_query
    user_id    = query.from_user.id
    data       = query.data  # gold_confirm_{session_id}_{capital}

    try:
        parts      = data.split("_")
        session_id = int(parts[2])
        capital    = float(parts[3])
    except (IndexError, ValueError):
        await query.answer("❌ Erreur — réessaie.", show_alert=True)
        return

    await query.answer("⏳ Enregistrement...", show_alert=False)

    # Désactiver les boutons immédiatement
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        pass

    result = await confirm_gold_entry(session_id, user_id, capital)

    if "error" in result:
        await query.message.reply_text(
            f"❌ {result['error']}",
            parse_mode="Markdown"
        )
        return

    # Message de confirmation
    await query.message.reply_text(
        result["message"],
        parse_mode="Markdown"
    )

    # Alerte si compte en danger
    danger = await check_cramed_accounts(session_id)
    for c in danger.get("already_cramed", []):
        if c["user_id"] == user_id:
            await query.message.reply_text(
                "⚠️ *Attention — Capital très faible !*\n\n"
                "Si le SL est touché sur ce trade, ton compte sera en très grande difficulté.\n\n"
                "_Assure-toi d'être à l'aise avec ce risque avant de continuer._",
                parse_mode="Markdown",
            )
            break


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER — SKIP TRADE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_gold_skip(update, context):
    query      = update.callback_query
    user_id    = query.from_user.id
    data       = query.data  # gold_skip_{session_id}

    try:
        session_id = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.answer()
        return

    await query.answer("👌 Compris — trade non pris.", show_alert=False)

    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Trade non pris — Noté 👌", callback_data="gold_done")
        ]]))
    except Exception:
        pass

    await save_user_step(session_id, user_id, "cancelled")
    await _log_flow_event(session_id, user_id, "cancelled", None)


async def handle_gold_done(update, context):
    await update.callback_query.answer(
        "Tu as déjà répondu à ce trade.", show_alert=False
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def register_gold_handlers(app):
    app.add_handler(
        CallbackQueryHandler(
            handle_disclaimer_ok,
            pattern=r"^gold_disclaimer_ok_\d+$"
        ), group=3
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_teaser_click,
            pattern=r"^gold_access_\d+$"
        ), group=3
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_gold_confirm,
            pattern=r"^gold_confirm_\d+_[\d.]+$"
        ), group=3
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_gold_skip,
            pattern=r"^gold_skip_\d+$"
        ), group=3
    )
    app.add_handler(
        CallbackQueryHandler(
            handle_gold_done,
            pattern=r"^gold_done$"
        ), group=3
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_capital_input
        ), group=3
    )
    print("[gold_broadcast] Handlers Gold v3 enregistrés ✓")