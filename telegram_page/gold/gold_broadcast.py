"""
gold_broadcast_v2.py — Flux Telegram Gold corrigé.

Corrections v2 :
  - direction Buy/Sell (plus Long/Short)
  - Messages depuis gold_tp_rules (configurables dashboard)
  - Persistance étapes dans gold_user_sessions (survie redémarrage)
  - Reprise automatique si bot redémarre en cours de flux
"""

import asyncio
import logging

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
    calculate_recommended_lot,
    _log_flow_event,
    get_conn,
)

logger  = logging.getLogger(__name__)
ADMIN_ID = 571718066


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_prenom(user_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
    if row and row["name"]:
        p = row["name"].strip()
        if 1 <= len(p) <= 20:
            return p
    return "l'ami"


def _get_last_capital(user_id: int) -> float | None:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT capital FROM member_capital
            WHERE user_id = ? ORDER BY declared_at DESC LIMIT 1
        """, (user_id,)).fetchone()
    finally:
        conn.close()
    return float(row["capital"]) if row else None


def _get_category_user_ids(category: str) -> list[int]:
    conn = get_conn()
    try:
        if category == "all":
            rows = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?", (category,)
            ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 — TEASER
# Message configurable depuis le dashboard (gold_tp_rules.message_teaser)
# ══════════════════════════════════════════════════════════════════════════════

def _build_teaser_message(session: dict, prenom: str = "") -> str:
    """
    Message teaser. Priorité :
    1. message_teaser depuis gold_tp_rules (configuré par l'admin)
    2. Message par défaut généré ici
    direction : 'buy' → 'Achat (Buy)' / 'sell' → 'Vente (Sell)'
    """
    # On prend le message du niveau 1 par défaut pour le teaser (commun à tous)
    rule_msgs  = get_rule_messages(1)
    tpl        = rule_msgs.get("message_teaser")

    dir_label  = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"
    conf_stars = "⭐" * session.get("confidence_level", 3)

    if tpl:
        # Injecter les variables dans le template configuré
        msg = (tpl
               .replace("{direction}", dir_label)
               .replace("{confiance}", conf_stars)
               .replace("{prenom}", prenom)
               .replace("{pair}", session.get("pair", "XAU/USD")))
        return msg

    # Message par défaut si pas de template configuré
    lines = [
        f"🔔 *Le trade du jour est disponible{' — ' + prenom if prenom else ''} !*",
        "",
        f"📊 Paire : *{session.get('pair', 'XAU/USD')}*",
        f"{dir_label}",
        f"Confiance : {conf_stars}",
        "",
        "─────────────────────",
        "💡 *Rappel — gestion du risque :*",
        "",
        "• Ne risquez jamais plus que ce que vous pouvez vous permettre de perdre",
        "• Respectez toujours votre SL — sans exception",
        "• Suivez les instructions en temps réel",
        "─────────────────────",
        "",
        "_Cliquez ci-dessous pour accéder au trade._",
    ]
    return "\n".join(lines)


def _build_teaser_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Accéder au trade →", callback_data=f"gold_access_{session_id}")]
    ])


async def send_gold_teaser(bot, session: dict, category: str = "clients_actifs", delay: float = 0.08) -> dict:
    """Envoie le teaser + démarre la surveillance prix."""
    session_id = session["id"]
    user_ids   = _get_category_user_ids(category)
    total      = len(user_ids)

    if total == 0:
        return {"total": 0, "sent": 0, "errors": 0, "session_id": session_id}

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"📤 *Teaser Gold envoi démarré*\n"
                  f"Destinataires : {total} | Session : {session_id}\n"
                  f"Direction : {'Achat (Buy)' if session['direction'] == 'buy' else 'Vente (Sell)'}\n"
                  f"Entrée : {session['entry_price']}"),
            parse_mode="Markdown"
        )
    except Exception:
        pass

    sent = errors = 0

    for idx, user_id in enumerate(user_ids, start=1):
        try:
            prenom  = _get_prenom(user_id)
            message = _build_teaser_message(session, prenom)
            kbd     = _build_teaser_keyboard(session_id)

            if session.get("screenshot_url"):
                try:
                    await bot.send_photo(
                        chat_id=user_id, photo=session["screenshot_url"],
                        caption=message, parse_mode="Markdown", reply_markup=kbd
                    )
                except Exception:
                    await bot.send_message(
                        chat_id=user_id, text=message,
                        parse_mode="Markdown", reply_markup=kbd
                    )
            else:
                await bot.send_message(
                    chat_id=user_id, text=message,
                    parse_mode="Markdown", reply_markup=kbd
                )

            # Sauvegarder l'étape 'teaser' pour chaque user (persistance)
            await save_user_step(session_id, user_id, "teaser")
            await _log_flow_event(session_id, user_id, "teaser_sent", None)
            sent += 1

        except Exception as e:
            logger.warning(f"[teaser] uid={user_id}: {e}")
            errors += 1

        if idx == total // 2:
            try:
                await bot.send_message(ADMIN_ID, f"📊 Teaser Gold — {sent}/{total} envoyés")
            except Exception:
                pass

        await asyncio.sleep(delay)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"✅ *Teaser Gold terminé*\nEnvoyés : {sent}/{total} | Erreurs : {errors}",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    # Lancer la surveillance prix en arrière-plan
    asyncio.create_task(watch_gold_price(session_id))

    return {"total": total, "sent": sent, "errors": errors, "session_id": session_id}


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 — CLIC TEASER → DEMANDE CAPITAL OBLIGATOIRE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_teaser_click(update, context):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data  # "gold_access_{session_id}"

    try:
        session_id = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.answer("❌ Erreur — réessaie.", show_alert=True)
        return

    session = await get_active_gold_session()
    if not session or session["id"] != session_id:
        await query.answer("⏰ Ce trade n'est plus disponible.", show_alert=True)
        return

    await query.answer()
    await _log_flow_event(session_id, user_id, "teaser_clicked", None)

    # Stocker session_id dans le contexte
    context.user_data["gold_session_id"]  = session_id
    context.user_data["waiting_capital"]  = True

    # Sauvegarder l'étape en DB (persistance)
    await save_user_step(session_id, user_id, "waiting_capital")

    # Toujours demander le capital — afficher le dernier connu en indication
    last_capital = _get_last_capital(user_id)
    hint = f"\n\n_Ton dernier capital enregistré : {last_capital}$_" if last_capital else ""

    await query.message.reply_text(
        f"💼 *Quel est ton capital actuel en $ ?*\n\n"
        f"Renseigne le montant exact en dollars.{hint}\n\n"
        f"_Ex : 500 ou 1250_",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 3 — RÉCEPTION CAPITAL → AFFICHAGE TRADE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_capital_input(update, context):
    """Reçoit le capital saisi, affiche le trade personnalisé."""
    user_id = update.message.from_user.id

    # Vérifier qu'on attend bien un capital pour cet user
    if not context.user_data.get("waiting_capital"):
        # Essayer de restaurer depuis la DB si contexte perdu
        restored = await restore_user_context(user_id)
        if restored and restored["step"] == "waiting_capital":
            context.user_data["gold_session_id"] = restored["session_id"]
            context.user_data["waiting_capital"]  = True
        else:
            return

    session_id = context.user_data.get("gold_session_id")
    if not session_id:
        return

    text = update.message.text.strip()
    try:
        capital = float(text.replace(",", ".").replace(" ", "").replace("$", ""))
        if capital <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Montant invalide. Renseigne uniquement le nombre en dollars.\n_Ex : 500_",
            parse_mode="Markdown",
        )
        return

    context.user_data["waiting_capital"] = False
    await save_user_step(session_id, user_id, "trade_shown", capital)
    await _show_trade_detail(update.message, session_id, user_id, capital)


async def _show_trade_detail(message_obj, session_id: int, user_id: int, capital: float):
    """Affiche le trade complet personnalisé avec lot et scénarios."""
    session = await get_active_gold_session()
    if not session or session["id"] != session_id:
        await message_obj.reply_text("⏰ Ce trade n'est plus disponible.")
        return

    tp_level, risk_pct = get_tp_level_for_capital(capital)
    sl_pips   = session.get("sl_pips") or 0
    pip_value = 1.0
    lot       = calculate_recommended_lot(capital, session["confidence_level"], sl_pips, pip_value)

    perte_sl = round(lot * sl_pips * pip_value, 2)
    gain_tp1 = round(lot * (session.get("tp1_pips") or 0) * pip_value, 2)
    gain_tp2 = round(lot * (session.get("tp2_pips") or 0) * pip_value, 2) if tp_level >= 2 and session.get("tp2_pips") else None
    gain_tp3 = round(lot * (session.get("tp3_pips") or 0) * pip_value, 2) if tp_level >= 3 and session.get("tp3_pips") else None

    dir_label  = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"
    tp_labels  = {1: "TP1 seulement", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}

    lines = [
        f"📊 *XAU/USD — Trade du jour*",
        "",
        f"{dir_label}",
        f"🎯 Entrée : *{session['entry_price']}*",
    ]
    if session.get("tp1"):  lines.append(f"✅ TP1 : *{session['tp1']}*")
    if tp_level >= 2 and session.get("tp2"): lines.append(f"🎯 TP2 : *{session['tp2']}*")
    if tp_level >= 3 and session.get("tp3"): lines.append(f"🏆 TP3 : *{session['tp3']}*")
    if session.get("sl"):   lines.append(f"❌ SL  : *{session['sl']}*")

    lines += [
        "",
        "─────────────────────",
        f"💼 *Lot recommandé : {lot}*",
        f"🎯 Objectif : *{tp_labels[tp_level]}*",
        f"💰 Capital déclaré : {capital}$",
        "",
        "📊 *Scénarios :*",
        f"❌ Si SL touché → *-{perte_sl}$*",
        f"✅ Si TP1 touché → *+{gain_tp1}$*",
    ]
    if gain_tp2: lines.append(f"🎯 Si TP2 touché → *+{gain_tp2}$*")
    if gain_tp3: lines.append(f"🏆 Si TP3 touché → *+{gain_tp3}$*")

    if session.get("note"):
        lines += ["", f"📝 _{session['note']}_"]

    lines += ["", "─────────────────────",
              "_Tu recevras les instructions en temps réel._"]

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

    await message_obj.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kbd)
    await _log_flow_event(session_id, user_id, "trade_shown", {"capital": capital, "lot": lot})


# ══════════════════════════════════════════════════════════════════════════════
# ÉTAPE 4 — CONFIRMATION FINALE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_gold_confirm(update, context):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data  # "gold_confirm_{session_id}_{capital}"

    try:
        parts      = data.split("_")
        session_id = int(parts[2])
        capital    = float(parts[3])
    except (IndexError, ValueError):
        await query.answer("❌ Erreur — réessaie.", show_alert=True)
        return

    await query.answer("⏳ Enregistrement en cours...", show_alert=False)

    result = await confirm_gold_entry(session_id, user_id, capital)

    if "error" in result:
        await query.message.reply_text(f"❌ {result['error']}")
        return

    # Remplacer les boutons
    new_kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Trade confirmé — Bonne chance !", callback_data="gold_done")]
    ])
    try:
        await query.edit_message_reply_markup(reply_markup=new_kbd)
    except Exception:
        pass

    await query.message.reply_text(result["message"], parse_mode="Markdown")

    # Alerte compte cramé immédiate si nécessaire
    danger = await check_cramed_accounts(session_id)
    for c in danger.get("already_cramed", []):
        if c["user_id"] == user_id:
            await query.message.reply_text(
                "⚠️ *Attention — Capital très faible !*\n\n"
                "Si le SL est touché sur ce trade, ton compte sera en très grande difficulté.\n\n"
                "_Assure-toi d'être à l'aise avec ce risque avant de continuer._",
                parse_mode="Markdown",
            )


async def handle_gold_skip(update, context):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data  # "gold_skip_{session_id}"

    try:
        session_id = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.answer()
        return

    await query.answer("👌 Compris — trade non pris.", show_alert=False)
    new_kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Trade non pris — Noté 👌", callback_data="gold_done")]
    ])
    try:
        await query.edit_message_reply_markup(reply_markup=new_kbd)
    except Exception:
        pass

    await save_user_step(session_id, user_id, "cancelled")
    await _log_flow_event(session_id, user_id, "cancelled", None)


async def handle_gold_done(update, context):
    await update.callback_query.answer("Tu as déjà répondu à ce trade.", show_alert=False)


# ══════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT DES HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def register_gold_handlers(app):
    app.add_handler(CallbackQueryHandler(handle_teaser_click,  pattern=r"^gold_access_\d+$"),           group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_confirm,  pattern=r"^gold_confirm_\d+_[\d.]+$"),   group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_skip,     pattern=r"^gold_skip_\d+$"),             group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_done,     pattern=r"^gold_done$"),                 group=3)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_capital_input
    ), group=3)
    print("[gold_broadcast] Handlers Gold v2 enregistrés ✓")