"""
gold_broadcast_v6.py — Flux Telegram Gold v6 (haute charge, 30 000 users).

Différences majeures vs v5 :

  CHEMIN CHAUD 100 % RAM
    - Plus AUCUN SELECT dans les handlers : session, règles TP, messages,
      prénoms, derniers capitaux → signal_cache (gold_cache.py).
    - État utilisateur (étape, capital, entry/SL effectifs) → user_state
      (gold_state.py), plus context.user_data (qui ne survivait pas aux
      redémarrages).
    - Écritures → gold_buffer (append RAM + flush batch), plus jamais
      1 requête par action.

  IDEMPOTENCE TOTALE
    - try_begin/end : verrou RAM par (user, action) → doubles clics et
      callbacks dupliqués Telegram ignorés instantanément.
    - Machine d'état : un user 'confirmed' ne peut plus reconfirmer,
      même après restart (état restauré depuis la DB au démarrage).
    - callback_data ne transporte plus le capital (source de vérité :
      user_state) — l'ancien format reste accepté pour les messages
      déjà envoyés.

  BROADCAST CONCURRENT
    - 25 envois/s en parallèle (limite Telegram ~30 msg/s) au lieu d'un
      envoi séquentiel : 30 000 users ≈ 20 min au lieu de ~50 min,
      et un seul message par user (header fusionné dans le disclaimer).
    - Comptes simulation appliqués UNE fois au lancement du broadcast
      (ils ne dépendent pas des confirmations des membres).

Intégration :
    - remplacer register_gold_handlers de gold_broadcast par celui-ci ;
    - au démarrage (post_init) :
          await signal_cache.reload()
          if signal_cache.get_session():
              await user_state.restore(signal_cache.get_session()["id"])
          gold_buffer.start(app.bot)
"""

import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import Forbidden
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from db import get_db
from telegram_page.gold.gold_cache import signal_cache
from telegram_page.gold.gold_state import user_state
from telegram_page.gold.gold_buffer import gold_buffer
from telegram_page.gold.gold_engine import (
    calculate_lot,
    calculate_gains_losses,
    adjust_entry_sl_to_live_price,
    get_live_gold_price,
    watch_gold_price,
)

logger      = logging.getLogger(__name__)
ADMIN_ID    = 571718066
CAPITAL_MIN = 30.0

BROADCAST_RATE   = 25   # messages/seconde (limite Telegram ≈ 30/s)
CATEGORY_TARGET  = "clients_actifs"    # cible UNIQUE des broadcasts Gold — toujours
CATEGORY_BLOCKED = "clients_bloquer"   # catégorie des users ayant bloqué le bot


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_now_discrete() -> str:
    now   = datetime.now()
    jours = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    mois  = ["jan", "fév", "mar", "avr", "mai", "juin",
             "juil", "août", "sep", "oct", "nov", "déc"]
    return f"{jours[now.weekday()]} {now.day:02d} {mois[now.month-1]} · {now.strftime('%H:%M')}"


async def _safe_answer(query, text: str = None, show_alert: bool = False) -> bool:
    try:
        await query.answer(text, show_alert=show_alert) if text else await query.answer()
        return True
    except Exception as e:
        logger.debug(f"[_safe_answer] callback expiré: {e}")
        return False


async def _safe_delete(bot, chat_id: int, message_id: int):
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _notify_user_error(bot, user_id: int):
    try:
        await bot.send_message(
            chat_id=user_id,
            text="⚠️ Une erreur est survenue. Tape /start ou réessaie dans un instant.",
        )
    except Exception:
        pass


async def _send_or_photo(bot, chat_id, text, screenshot_url=None,
                         reply_markup=None, parse_mode="Markdown"):
    if screenshot_url:
        try:
            return await bot.send_photo(chat_id=chat_id, photo=screenshot_url,
                                        caption=text, parse_mode=parse_mode,
                                        reply_markup=reply_markup)
        except Exception:
            pass
    return await bot.send_message(chat_id=chat_id, text=text,
                                  parse_mode=parse_mode, reply_markup=reply_markup)


async def _get_category_user_ids(category: str) -> list:
    async with get_db() as cur:
        if category == "all":
            await cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
            return [r["telegram_id"] for r in await cur.fetchall()]
        await cur.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s", (category,)
        )
        return [r["id_user"] for r in await cur.fetchall()]


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGES
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


def _disclaimer_message(session: dict) -> str:
    """Header + disclaimer fusionnés : 1 message par user au lieu de 2
    → temps de broadcast divisé par 2."""
    dir_label = "Achat (Buy)" if session["direction"] == "buy" else "Vente (Sell)"
    return (f"🔔 ─────────────────────\n"
            f"*Signal Gold disponible*\n"
            f"_{dir_label} · {_fmt_now_discrete()}_\n"
            f"─────────────────────\n\n"
            + DISCLAIMER_TEXT)


def _build_disclaimer_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ J'ai compris — Voir le trade",
        callback_data=f"gold_disclaimer_ok_{session_id}")]])


def _build_teaser_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "📊 Accéder au trade →", callback_data=f"gold_access_{session_id}")]])


def _build_teaser_message(session: dict, prenom: str = "") -> str:
    """100 % RAM — le template vient du cache, plus de get_rule_messages()."""
    tpl        = signal_cache.rule_messages(1).get("message_teaser")
    dir_label  = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"
    conf_stars = "⭐" * session.get("confidence_level", 3)
    greeting   = f" — {prenom}" if prenom else ""

    if tpl:
        return (tpl.replace("{direction}", dir_label)
                   .replace("{confiance}", conf_stars)
                   .replace("{prenom}", prenom)
                   .replace("{pair}", session.get("pair", "XAU/USD")))

    return "\n".join([
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
    ])


# ══════════════════════════════════════════════════════════════════════════════
# BROADCAST — concurrent, à débit contrôlé
# ══════════════════════════════════════════════════════════════════════════════

async def send_gold_teaser(bot, session: dict, category: str = None,
                           delay: float = None, **_ignored) -> dict:
    """
    Le paramètre `category` est accepté pour compatibilité avec les routes
    existantes mais IGNORÉ : les trades Gold sont TOUJOURS envoyés à la
    catégorie CATEGORY_TARGET (clients_actifs). `delay` est ignoré aussi
    (débit géré par BROADCAST_RATE).
    """
    session_id = session["id"]
    category   = CATEGORY_TARGET   # cible forcée, quel que soit l'appelant

    # 1. Précharge TOUT ce que le pic va lire — quelques requêtes, une fois.
    await signal_cache.reload(session_id)
    session = signal_cache.get_session()
    user_ids = await _get_category_user_ids(category)
    await signal_cache.preload_users(user_ids)
    user_state.reset(session_id)

    # 2. Comptes simulation appliqués MAINTENANT (ne dépendent pas des
    #    confirmations) — sortis définitivement du chemin de confirmation.
    from telegram_page.gold.gold_engine import _apply_to_simulation_accounts
    try:
        await _apply_to_simulation_accounts(session_id, session)
    except Exception as e:
        logger.error(f"[broadcast] simulation: {e}")

    total = len(user_ids)
    if total == 0:
        return {"total": 0, "sent": 0, "errors": 0, "session_id": session_id}

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"📤 *Envoi teaser Gold démarré*\n"
                  f"Cible : {category} | Destinataires : {total} | Session #{session_id}\n"
                  f"Débit : {BROADCAST_RATE} msg/s → ~{total // BROADCAST_RATE // 60 + 1} min"),
            parse_mode="Markdown")
    except Exception:
        pass

    text = _disclaimer_message(session)
    kbd  = _build_disclaimer_keyboard(session_id)
    sent = errors = 0
    blocked_ids: list[int] = []
    sem  = asyncio.Semaphore(BROADCAST_RATE)

    async def _send_one(uid: int):
        nonlocal sent, errors
        async with sem:
            try:
                await bot.send_message(chat_id=uid, text=text,
                                       parse_mode="Markdown", reply_markup=kbd)
                user_state.get(uid).step = "teaser"
                gold_buffer.add_step(session_id, uid, "teaser")
                sent += 1
            except Forbidden as e:
                # "bot was blocked by the user" / "user is deactivated"
                # → injoignable définitivement : à sortir de la liste.
                logger.info(f"[teaser] uid={uid} a bloqué le bot: {e}")
                blocked_ids.append(uid)
            except Exception as e:
                logger.debug(f"[teaser] uid={uid}: {e}")
                errors += 1
            # Chaque slot du sémaphore tient 1 s → débit global = BROADCAST_RATE/s
            await asyncio.sleep(1)

    tasks = [asyncio.create_task(_send_one(uid)) for uid in user_ids]

    async def _progress():
        while any(not t.done() for t in tasks):
            await asyncio.sleep(60)
            try:
                await bot.send_message(ADMIN_ID, f"📊 Teaser Gold — {sent}/{total} envoyés...")
            except Exception:
                pass

    progress_task = asyncio.create_task(_progress())
    await asyncio.gather(*tasks, return_exceptions=True)
    progress_task.cancel()

    # ── Traitement des users ayant bloqué le bot ─────────────────────────
    # Fait APRÈS l'envoi (pas pendant) pour ne pas mélanger requêtes DB et
    # débit d'envoi. Retirés de la catégorie source, ajoutés à clients_bloquer.
    blocked_report = await _handle_blocked_users(blocked_ids, category)

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(f"✅ *Teaser Gold terminé*\n\n"
                  f"Envoyés : {sent}/{total}\n"
                  f"Erreurs : {errors}\n\n"
                  f"🚫 *Bot bloqué par : {blocked_report['blocked']} client(s)*\n"
                  f"  • retirés de « {category} » : {blocked_report['removed']}\n"
                  f"  • ajoutés à « {CATEGORY_BLOCKED} » : {blocked_report['added']}"
                  + (f" ({blocked_report['already_in']} déjà présents)"
                     if blocked_report["already_in"] else "")),
            parse_mode="Markdown")
    except Exception:
        pass

    asyncio.create_task(watch_gold_price(session_id))
    return {"total": total, "sent": sent, "errors": errors,
            "blocked": blocked_report, "session_id": session_id}


async def _handle_blocked_users(blocked_ids: list[int], source_category: str) -> dict:
    """
    Pour chaque user ayant bloqué le bot pendant le broadcast :
      1. ajout à la catégorie CATEGORY_BLOCKED (INSERT IGNORE → pas de doublon) ;
      2. retrait de la catégorie source du broadcast (sauf si "all",
         qui n'est pas une vraie catégorie).
    Utilise les fonctions existantes de telegram_page/categorie.py.
    """
    result = {"blocked": len(blocked_ids), "removed": 0, "added": 0, "already_in": 0}
    if not blocked_ids:
        return result

    try:
        from telegram_page.categorie import (
            add_members_to_category,
            remove_member_from_category,
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
                    logger.warning(f"[blocked] retrait uid={uid} de "
                                   f"'{source_category}' échoué: {e}")

        logger.info(f"[blocked] {result['blocked']} bloqués — "
                    f"{result['removed']} retirés de '{source_category}', "
                    f"{result['added']} ajoutés à '{CATEGORY_BLOCKED}'")
    except Exception as e:
        logger.error(f"[blocked] traitement échoué: {e}", exc_info=True)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS — chemin chaud : 0 requête SQL
# ══════════════════════════════════════════════════════════════════════════════

async def _get_open_session():
    """
    Retourne la session ouverte depuis le cache. Si le cache est vide ou
    périmé (ex : session créée via l'API dans un autre process il y a
    quelques secondes), tente UN reload — throttlé à 1 fois / 2 s pour
    qu'un pic de clics sans session ouverte ne bombarde pas MySQL.
    """
    import time as _t
    if not signal_cache.is_open() and _t.time() - signal_cache.loaded_at > 2:
        try:
            await signal_cache.reload()
        except Exception as e:
            logger.warning(f"[gold] reload de secours échoué: {e}")
    return signal_cache.get_session() if signal_cache.is_open() else None


async def handle_disclaimer_ok(update, context):
    query   = update.callback_query
    user_id = query.from_user.id

    if not user_state.try_begin(user_id, "disclaimer"):
        await _safe_answer(query)
        return
    try:
        await _safe_answer(query)

        session = await _get_open_session()
        if not session:
            await _safe_delete(context.bot, user_id, query.message.message_id)
            await context.bot.send_message(chat_id=user_id,
                                           text="⏰ Ce trade n'est plus disponible.")
            return

        sid = session["id"]
        await _safe_delete(context.bot, user_id, query.message.message_id)

        prenom  = signal_cache.prenom(user_id)         # RAM
        message = _build_teaser_message(session, prenom)  # RAM

        await _send_or_photo(context.bot, user_id, message,
                             screenshot_url=session.get("screenshot_url"),
                             reply_markup=_build_teaser_keyboard(sid))
        gold_buffer.add_event(sid, user_id, "teaser_shown")

    except Exception as e:
        logger.error(f"[handle_disclaimer_ok] uid={user_id}: {e}", exc_info=True)
        await _notify_user_error(context.bot, user_id)
    finally:
        user_state.end(user_id, "disclaimer")


async def handle_teaser_click(update, context):
    query   = update.callback_query
    user_id = query.from_user.id

    if not user_state.try_begin(user_id, "access"):
        await _safe_answer(query)
        return
    try:
        await _safe_answer(query)

        session = await _get_open_session()
        if not session:
            await _safe_delete(context.bot, user_id, query.message.message_id)
            await context.bot.send_message(chat_id=user_id,
                                           text="⏰ Ce trade n'est plus disponible.")
            return

        sid = session["id"]
        if user_state.is_confirmed(user_id):
            await _safe_answer(query, "✅ Tu as déjà confirmé ce trade.", show_alert=True)
            return

        user_state.transition(user_id, "waiting_capital")
        gold_buffer.add_step(sid, user_id, "waiting_capital")
        gold_buffer.add_event(sid, user_id, "teaser_clicked")
        await _safe_delete(context.bot, user_id, query.message.message_id)

        last_capital = signal_cache.last_capital(user_id)   # RAM
        hint = f"\n\n_Dernier capital enregistré : *{last_capital}$*_" if last_capital else ""

        msg = await context.bot.send_message(
            chat_id=user_id,
            text=(f"💼 *Quel est ton capital actuel en $ ?*\n\n"
                  f"Renseigne le montant en chiffres uniquement.{hint}\n\n"
                  f"_Ex : 500 ou 1250_"),
            parse_mode="Markdown")
        context.user_data[f"capital_msg_id_{sid}"] = msg.message_id if msg else None

    except Exception as e:
        logger.error(f"[handle_teaser_click] uid={user_id}: {e}", exc_info=True)
        await _notify_user_error(context.bot, user_id)
    finally:
        user_state.end(user_id, "access")


async def handle_capital_input(update, context):
    # Garde : ce handler ne traite que les NOUVEAUX messages privés d'un
    # utilisateur réel. Messages édités / posts de canal → update.message
    # est None (crash AttributeError sinon).
    incoming = update.effective_message
    if incoming is None or incoming.from_user is None:
        return
    user_id = incoming.from_user.id
    st      = user_state.get(user_id)

    # État en RAM, restauré au démarrage → plus de restore_user_context()
    # (1 SELECT JOIN) sur CHAQUE message texte de CHAQUE utilisateur du bot.
    if st.step != "waiting_capital":
        return
    session = await _get_open_session()
    if not session:
        return
    sid = session["id"]

    try:
        raw   = incoming.text.strip()
        clean = raw.replace(",", ".").replace(" ", "").replace("$", "")
        await _safe_delete(context.bot, user_id, incoming.message_id)

        is_numeric = clean.replace(".", "", 1).isdigit() and clean.count(".") <= 1

        if not is_numeric or not clean:
            await _safe_delete(context.bot, user_id,
                               context.user_data.get(f"capital_msg_id_{sid}"))
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=("⚠️ *Entre uniquement des chiffres.*\n\n"
                      "Exemple : `500` ou `1250`\n\n"
                      "_Quel est ton capital actuel en $ ?_"),
                parse_mode="Markdown")
            context.user_data[f"capital_msg_id_{sid}"] = msg.message_id
            return

        capital = float(clean)
        if capital < CAPITAL_MIN:
            await _safe_delete(context.bot, user_id,
                               context.user_data.get(f"capital_msg_id_{sid}"))
            msg = await context.bot.send_message(
                chat_id=user_id,
                text=(f"⚠️ *Capital minimum requis : {int(CAPITAL_MIN)}$.*\n\n"
                      f"Renseigne un montant supérieur ou égal à {int(CAPITAL_MIN)}$.\n\n"
                      "_Quel est ton capital actuel en $ ?_"),
                parse_mode="Markdown")
            context.user_data[f"capital_msg_id_{sid}"] = msg.message_id
            return

        await _safe_delete(context.bot, user_id,
                           context.user_data.get(f"capital_msg_id_{sid}"))

        user_state.transition(user_id, "trade_shown")
        st.capital = capital
        gold_buffer.add_step(sid, user_id, "trade_shown", capital)
        await _show_trade_detail(context.bot, user_id, session, capital)

    except Exception as e:
        logger.error(f"[handle_capital_input] uid={user_id}: {e}", exc_info=True)
        await _notify_user_error(context.bot, user_id)


async def _show_trade_detail(bot, user_id: int, session: dict, capital: float):
    sid = session["id"]

    # Prix live : cache TTL interne à get_live_gold_price → au pire 1 appel
    # HTTP par fenêtre, partagé entre TOUS les users. Aucune requête SQL.
    live_price = await get_live_gold_price()
    adj = adjust_entry_sl_to_live_price(
        direction=session["direction"], entry=session["entry_price"],
        sl=session["sl"], live_price=live_price)

    effective_entry, effective_sl = adj["entry"], adj["sl"]
    was_adjusted = adj["adjusted"]

    # Entry/SL effectifs stockés dans l'état RAM (persisté en write-behind
    # via steps si besoin) — survivent au restart via restore() + recalcul.
    st = user_state.get(user_id)
    st.effective_entry, st.effective_sl = effective_entry, effective_sl

    lot        = calculate_lot(capital, effective_entry, effective_sl)
    risk_gains = calculate_gains_losses(lot=lot, entry=effective_entry, sl=effective_sl)
    tp_gains   = calculate_gains_losses(lot=lot, entry=session["entry_price"], sl=effective_sl,
                                        tp1=session.get("tp1"), tp2=session.get("tp2"),
                                        tp3=session.get("tp3"))

    tp_level, _ = signal_cache.tp_level_for_capital(capital)   # RAM
    tp_labels   = {1: "TP1 seulement", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}
    dir_label   = "📈 Achat (Buy)" if session["direction"] == "buy" else "📉 Vente (Sell)"

    lines = [f"📊 *XAU/USD — Trade du jour*", f"_{_fmt_now_discrete()}_", "",
             f"{dir_label}", "━━━━━━━━━━━━━━━━━━━━"]
    if was_adjusted:
        lines.append(f"🎯 Entrée ajustée : *{effective_entry}*")
        lines.append(f"_(prix du jour {'plus haut' if session['direction'] == 'sell' else 'plus bas'} "
                     f"que prévu — meilleur point d'entrée pour toi)_")
    else:
        lines.append(f"🎯 Entrée : *{effective_entry}*")

    if session.get("tp1"):                   lines.append(f"✅ TP1 : *{session['tp1']}*")
    if session.get("tp2") and tp_level >= 2: lines.append(f"🎯 TP2 : *{session['tp2']}*")
    if session.get("tp3") and tp_level >= 3: lines.append(f"🏆 TP3 : *{session['tp3']}*")

    lines += [f"❌ SL  : *{effective_sl}*", "━━━━━━━━━━━━━━━━━━━━",
              f"💼 Lot recommandé : *{lot}*",
              f"🎯 Objectif : *{tp_labels[tp_level]}*",
              f"💰 Capital déclaré : *{capital}$*", "━━━━━━━━━━━━━━━━━━━━",
              "📊 *Scénarios estimés :*",
              f"❌ Si SL touché → *{risk_gains['perte_sl']}$*",
              f"✅ Si TP1 touché → *+{tp_gains['gain_tp1']}$*"]
    if tp_gains.get("gain_tp2") and tp_level >= 2:
        lines.append(f"🎯 Si TP2 touché → *+{tp_gains['gain_tp2']}$*")
    if tp_gains.get("gain_tp3") and tp_level >= 3:
        lines.append(f"🏆 Si TP3 touché → *+{tp_gains['gain_tp3']}$*")
    if session.get("note"):
        lines += ["━━━━━━━━━━━━━━━━━━━━", f"📝 *Note :* _{session['note']}_"]
    lines += ["━━━━━━━━━━━━━━━━━━━━", "_Tu recevras les instructions en temps réel._"]

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Je confirme — Je prends ce trade",
                              callback_data=f"gold_confirm_{sid}")],
        [InlineKeyboardButton("❌ Je ne prends pas ce trade",
                              callback_data=f"gold_skip_{sid}")],
    ])
    await _send_or_photo(bot, user_id, "\n".join(lines),
                         screenshot_url=session.get("screenshot_url"), reply_markup=kbd)
    gold_buffer.add_event(sid, user_id, "trade_shown",
                          {"capital": capital, "lot": lot, "tp_level": tp_level,
                           "entry": effective_entry, "sl": effective_sl,
                           "adjusted": was_adjusted})


async def handle_gold_confirm(update, context):
    query   = update.callback_query
    user_id = query.from_user.id

    # Idempotence niveau 1 : déjà confirmé → réponse immédiate, zéro traitement.
    if user_state.is_confirmed(user_id):
        await _safe_answer(query, "✅ Ce trade est déjà enregistré pour toi.")
        return
    # Idempotence niveau 2 : traitement identique déjà en cours (double clic).
    if not user_state.try_begin(user_id, "confirm"):
        await _safe_answer(query, "⏳ Enregistrement en cours...")
        return

    try:
        await _safe_answer(query, "⏳ Enregistrement...")
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
        except Exception:
            pass

        session = await _get_open_session()
        if not session:
            await query.message.reply_text("❌ Ce trade n'est plus ouvert aux participations.")
            return

        sid = session["id"]
        st  = user_state.get(user_id)

        # Capital : source de vérité = état RAM. Fallback : ancien format
        # de callback_data (messages envoyés avant la migration v6).
        capital = st.capital
        if capital is None:
            parts = query.data.split("_")
            if len(parts) >= 4:
                try:
                    capital = float(parts[3])
                except ValueError:
                    capital = None
        if capital is None:
            await query.message.reply_text("❌ Session expirée — retape ton capital.")
            user_state.transition(user_id, "waiting_capital")
            gold_buffer.add_step(sid, user_id, "waiting_capital")
            return

        effective_entry = st.effective_entry if st.effective_entry is not None else session["entry_price"]
        effective_sl    = st.effective_sl    if st.effective_sl    is not None else session["sl"]

        # ── Calcul 100 % Python, 0 SQL ────────────────────────────────────
        lot        = calculate_lot(capital, effective_entry, effective_sl)
        risk_gains = calculate_gains_losses(lot=lot, entry=effective_entry, sl=effective_sl)
        tp_gains   = calculate_gains_losses(lot=lot, entry=session["entry_price"], sl=effective_sl,
                                            tp1=session.get("tp1"), tp2=session.get("tp2"),
                                            tp3=session.get("tp3"))
        perte_sl = risk_gains["perte_sl"]
        gain_tp1, gain_tp2, gain_tp3 = (tp_gains["gain_tp1"],
                                        tp_gains["gain_tp2"], tp_gains["gain_tp3"])
        tp_level, risk_pct = signal_cache.tp_level_for_capital(capital)
        risk_usd = round(capital * risk_pct / 100, 2)

        # ── Réponse utilisateur IMMÉDIATE ─────────────────────────────────
        tp_labels = {1: "TP1", 2: "TP1 + TP2", 3: "TP1 + TP2 + TP3"}
        dir_label = "Achat (Buy)" if session["direction"] == "buy" else "Vente (Sell)"
        lines = ["✅ *Trade confirmé — XAU/USD*", "",
                 f"💼 Lot recommandé : *{lot}*",
                 f"🎯 Objectif : *{tp_labels[tp_level]}*",
                 f"📈 Direction : *{dir_label}*", "",
                 "📊 *Scénarios :*",
                 f"❌ Si SL touché → *{perte_sl}$*",
                 f"✅ Si TP1 touché → *+{gain_tp1}$*"]
        if gain_tp2: lines.append(f"🎯 Si TP2 touché → *+{gain_tp2}$*")
        if gain_tp3: lines.append(f"🏆 Si TP3 touché → *+{gain_tp3}$*")
        lines += ["", "_Tu recevras les instructions en temps réel._"]
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")

        # ── État + persistance write-behind (append RAM, aucune requête) ──
        user_state.mark_confirmed(user_id, {
            "lot": lot, "perte_sl": perte_sl,
            "gain_tp1": gain_tp1 or 0, "gain_tp2": gain_tp2, "gain_tp3": gain_tp3,
        })
        gold_buffer.add_entry(sid, user_id, session.get("season_id"), capital,
                              risk_pct, risk_usd, lot, tp_level,
                              perte_sl, gain_tp1, gain_tp2, gain_tp3)
        gold_buffer.add_step(sid, user_id, "confirmed", capital)
        gold_buffer.add_event(sid, user_id, "confirmed",
                              {"capital": capital, "lot": lot, "tp_level": tp_level})

        if session["current_phase"] == "teaser":
            signal_cache.set_phase("open")
            gold_buffer.set_phase(sid, "open")

        # Alerte compte cramé — calcul local, comme en v5.
        if capital - abs(perte_sl or 0) <= 0:
            await query.message.reply_text(
                "⚠️ *Attention — Capital très faible !*\n\n"
                "Si le SL est touché sur ce trade, ton compte sera en très grande difficulté.\n\n"
                "_Assure-toi d'être à l'aise avec ce risque avant de continuer._",
                parse_mode="Markdown")

    except Exception as e:
        logger.error(f"[handle_gold_confirm] uid={user_id}: {e}", exc_info=True)
        await _notify_user_error(context.bot, user_id)
    finally:
        user_state.end(user_id, "confirm")


async def handle_gold_skip(update, context):
    query   = update.callback_query
    user_id = query.from_user.id

    if not user_state.try_begin(user_id, "skip"):
        await _safe_answer(query)
        return
    try:
        await _safe_answer(query, "👌 Compris — trade non pris.")
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Trade non pris — Noté 👌", callback_data="gold_done")
            ]]))
        except Exception:
            pass

        session = signal_cache.get_session()
        if session and user_state.transition(user_id, "cancelled"):
            gold_buffer.add_step(session["id"], user_id, "cancelled")
            gold_buffer.add_event(session["id"], user_id, "cancelled")
    except Exception as e:
        logger.error(f"[handle_gold_skip] uid={user_id}: {e}", exc_info=True)
    finally:
        user_state.end(user_id, "skip")


async def handle_gold_done(update, context):
    await _safe_answer(update.callback_query, "Tu as déjà répondu à ce trade.")


# ══════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT
# ══════════════════════════════════════════════════════════════════════════════

def register_gold_handlers(app):
    app.add_handler(CallbackQueryHandler(handle_disclaimer_ok, pattern=r"^gold_disclaimer_ok_\d+$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_teaser_click,  pattern=r"^gold_access_\d+$"),        group=3)
    # accepte le nouveau format gold_confirm_<sid> ET l'ancien gold_confirm_<sid>_<capital>
    app.add_handler(CallbackQueryHandler(handle_gold_confirm,  pattern=r"^gold_confirm_\d+(_[\d.]+)?$"), group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_skip,     pattern=r"^gold_skip_\d+$"),           group=3)
    app.add_handler(CallbackQueryHandler(handle_gold_done,     pattern=r"^gold_done$"),               group=3)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND
            & filters.UpdateType.MESSAGE      # exclut edited_message / channel_post
            & filters.ChatType.PRIVATE,       # exclut groupes et canaux
            handle_capital_input,
        ),
        group=3,
    )
    print("[gold_broadcast] Handlers Gold v6 enregistrés ✓")