"""
validation_handler.py — Flow de validation d'abonnement FDK Signal via Telegram.
Déclenchement : t.me/TradingBot?start=fdkgoldsaison
"""

from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)
from telegram.helpers import escape_markdown

from db import get_db  # ← pool aiomysql

import asyncio

CATEGORIE    = "PRELANCEMENT FDK GOLD SAISON"
FORM_COMMAND = "/suivi"

ASK_EMAIL, SHOW_RESULT, CONFIRM_REFUND = range(300, 303)

CLAUSES = (
    "📋 *Politique de confidentialité — FDK Signal*\n\n"
    "• Vos données personnelles sont utilisées uniquement dans le cadre de votre abonnement FDK Signal\\.\n"
    "• Elles ne sont jamais revendues à des tiers\\.\n"
    "• Vous pouvez demander leur suppression à tout moment via fdksignal\\.com\\.\n\n"
    "⚠️ *Avertissement sur les risques*\n\n"
    "• Le trading comporte des risques de perte en capital\\.\n"
    "• Les performances passées ne garantissent pas les performances futures\\.\n"
    "• Vous tradez sous votre entière responsabilité\\.\n\n"
    "En cliquant sur *Je valide mon abonnement*, vous confirmez avoir lu "
    "et accepté ces conditions ainsi que celles disponibles sur fdksignal\\.com\\."
)


# ─────────────────────────────────────────────
#  Helper
# ─────────────────────────────────────────────

def _escape_md(text) -> str:
    if not text:
        return "—"
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = str(text).replace(ch, f"\\{ch}")
    return text


# ─────────────────────────────────────────────
#  DB helpers (async — aiomysql)
# ─────────────────────────────────────────────

async def _find_last_unvalidated(email: str):
    print(f"[validation] _find_last_unvalidated email={email}")
    async with get_db() as cur:
        await cur.execute(
            """
            SELECT * FROM subscription_info
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
              AND (note IS NULL OR note NOT LIKE '%%valide%%')
            ORDER BY paid_at DESC LIMIT 1
            """,
            (email,)
        )
        row = await cur.fetchone()
    result = dict(row) if row else None
    print(f"[validation] _find_last_unvalidated result={result}")
    return result


async def _all_validated(email: str) -> bool:
    print(f"[validation] _all_validated email={email}")
    async with get_db() as cur:
        await cur.execute(
            "SELECT COUNT(*) AS cnt FROM subscription_info WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))",
            (email,)
        )
        total = (await cur.fetchone())["cnt"]

        await cur.execute(
            """
            SELECT COUNT(*) AS cnt FROM subscription_info
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(%s))
              AND note LIKE '%%valide%%'
            """,
            (email,)
        )
        validated = (await cur.fetchone())["cnt"]

    print(f"[validation] total={total} validated={validated}")
    return total > 0 and total == validated


async def _upsert_user(cur, telegram_id: int, pay: dict):
    """Crée ou met à jour l'utilisateur. Reçoit le curseur déjà ouvert."""
    print(f"[validation] _upsert_user telegram_id={telegram_id}")
    name    = pay.get("name") or ""
    email   = pay.get("email") or ""
    phone   = pay.get("phone") or ""
    country = pay.get("country_code") or ""

    await cur.execute(
        "SELECT * FROM users WHERE telegram_id = %s", (telegram_id,)
    )
    existing = await cur.fetchone()

    if existing:
        updates = {}
        if not existing.get("name")    and name:    updates["name"]    = name
        if not existing.get("email")   and email:   updates["email"]   = email
        if not existing.get("phone")   and phone:   updates["phone"]   = phone
        if not existing.get("country") and country: updates["country"] = country
        if updates:
            sets = ", ".join(f"{k} = %s" for k in updates)
            vals = list(updates.values()) + [telegram_id]
            await cur.execute(f"UPDATE users SET {sets} WHERE telegram_id = %s", vals)
            print(f"[validation] user mis à jour: {updates}")
    else:
        await cur.execute(
            "INSERT INTO users (telegram_id, name, email, phone, country, created_at) VALUES (%s,%s,%s,%s,%s, NOW())",
            (telegram_id, name, email, phone, country)
        )
        print(f"[validation] user créé telegram_id={telegram_id}")


async def _activate(pay: dict, telegram_id: int, email: str):
    print(f"[validation] _activate pay_id={pay['id']} telegram_id={telegram_id}")
    async with get_db() as cur:
        await _upsert_user(cur, telegram_id, pay)

        await cur.execute(
            "UPDATE subscription_info SET status='active', note='valide par telegram', updated_at=NOW() WHERE id=%s",
            (pay["id"],)
        )

        await cur.execute(
            "SELECT id FROM subscriptions WHERE user_id=%s AND status='active'", (telegram_id,)
        )
        existing = await cur.fetchone()

        if not existing:
            await cur.execute(
                """
                INSERT IGNORE INTO subscriptions
                    (user_id, plan, duration_days, started_at, expires_at,
                     status, note, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,'active','valide telegram', NOW(), NOW())
                """,
                (telegram_id, pay["plan"], pay["duration_days"], pay["started_at"], pay["expires_at"])
            )
    print(f"[validation] _activate done")


def _format_date(raw) -> str:
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(str(raw)).strftime("%d/%m/%Y")
    except Exception:
        return str(raw)


# ─────────────────────────────────────────────
#  Étape 1 — Demander l'email
# ─────────────────────────────────────────────

async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_validation"] = True
    print(f"[validation] _start user={update.effective_user.id}")
    context.user_data.clear()
    await update.message.reply_text(
        "📧 Veuillez saisir l'adresse email utilisée lors de votre paiement :",
        reply_markup=ReplyKeyboardRemove()
    )
    return ASK_EMAIL


# ─────────────────────────────────────────────
#  Étape 2 — Recevoir l'email et chercher
# ─────────────────────────────────────────────

async def _receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_validation"] = True
    email = update.message.text.strip()
    print(f"[validation] _receive_email email={email}")

    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Adresse email invalide. Veuillez réessayer :")
        return ASK_EMAIL

    context.user_data["email"] = email

    try:
        all_done = await _all_validated(email)
    except Exception as e:
        print(f"[validation] ERREUR _all_validated: {e}")
        await update.message.reply_text("❌ Erreur interne. Contactez support@fdksignal.com")
        return ConversationHandler.END

    if all_done:
        await update.message.reply_text(
            "ℹ️ *Tous vos paiements sont déjà validés\\.*\n\n"
            "Votre abonnement FDK Gold est actif\\.\n"
            "Si vous avez des questions, notre équipe est disponible ici\\.",
            parse_mode="MarkdownV2"
        )
        return ConversationHandler.END

    try:
        pay = await _find_last_unvalidated(email)
    except Exception as e:
        print(f"[validation] ERREUR _find_last_unvalidated: {e}")
        await update.message.reply_text("❌ Erreur interne. Contactez support@fdksignal.com")
        return ConversationHandler.END

    context.user_data["pay"] = pay

    if pay:
        try:
            await update.message.reply_text(
                f"✅ *Paiement trouvé*\n\n"
                f"📦 Plan      : *{_escape_md(pay.get('plan', '—'))}*\n"
                f"💰 Montant   : {_escape_md(pay.get('amount_usd', '—'))} \\$\n"
                f"📅 Paiement  : {_escape_md(_format_date(pay.get('paid_at')))}\n"
                f"⏳ Expire le : {_escape_md(_format_date(pay.get('expires_at')))}\n\n"
                f"{CLAUSES}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Je valide mon abonnement", callback_data="val_confirm")
                ]])
            )
            print(f"[validation] message envoyé → SHOW_RESULT")
        except Exception as e:
            print(f"[validation] ERREUR envoi message: {e}")
            return ConversationHandler.END
        return SHOW_RESULT

    await update.message.reply_text(
        "❌ Aucun paiement trouvé pour cet email.\n\n"
        "Si vous pensez qu'il s'agit d'une erreur, vous pouvez nous contactez via support@fdksignal.com pour reclamation.",
    )
    context.user_data.pop("in_validation", None)
    return SHOW_RESULT


# ─────────────────────────────────────────────
#  Étape 3a — Confirmer l'abonnement
# ─────────────────────────────────────────────

async def _confirm_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print(f"[validation] _confirm_subscription user={query.from_user.id}")

    user_id = query.from_user.id
    email   = context.user_data.get("email", "")
    pay     = context.user_data.get("pay", {})

    try:
        await _activate(pay, user_id, email)
    except Exception as e:
        print(f"[validation] ERREUR _activate: {e}")

    try:
        from telegram_page.categorie import add_members_to_category
        await add_members_to_category(CATEGORIE, [user_id])
        print(f"[validation] catégorie ajoutée")
    except Exception as e:
        print(f"[validation] categorie error: {e}")

    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        f"🎉 *Votre abonnement FDK Gold est validé \\!*\n\n"
        f"⏳ Il est actif jusqu'au *{escape_markdown(_format_date(pay.get('expires_at')), version=2)}*\\.\n\n"
        "📚 Avant de recevoir les signaux, deux étapes sont obligatoires :\n\n"
        "1\\. Suivre la *formation FDK* jusqu'à la fin, créer son compte et le faire valider\n"
        "2\\. Compléter votre *Profil Trader* via /mon\\_profil\\_trader\\_fdk\n\n"
        "⚠️ Les signaux ne seront accessibles qu'une fois ces deux étapes complétées\\.\n\n"
        "💬 Pour toute question, contactez\\-moi directement sur @Fiacrekpanou\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎓 Accéder à ma formation", url="https://fdksignal.com/formation")]
        ])
    )

    await asyncio.sleep(15 * 60)
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  Étape 3b — Demande de remboursement
# ─────────────────────────────────────────────

async def _request_refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print(f"[validation] _request_refund user={query.from_user.id}")

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "⚠️ *Confirmez\\-vous votre demande de remboursement ?*\n\n"
        "Cette action notifiera notre équipe\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Oui, confirmer", callback_data="refund_yes"),
            InlineKeyboardButton("❌ Non, annuler",   callback_data="refund_no"),
        ]])
    )
    return CONFIRM_REFUND


# ─────────────────────────────────────────────
#  Étape 4a — Remboursement confirmé
# ─────────────────────────────────────────────

async def _refund_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print(f"[validation] _refund_confirmed user={query.from_user.id}")

    pay        = context.user_data.get("pay") or {}
    expires_at = _format_date(pay.get("expires_at"))
    msg_expiry = (
        f"Votre accès reste disponible jusqu'au *{_escape_md(expires_at)}*\\."
        if expires_at != "—"
        else "Votre accès a déjà expiré\\."
    )

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ *Demande de remboursement enregistrée\\.*\n\n"
        f"{msg_expiry}\n\n"
        "Pour finaliser votre demande, envoyez un email à :\n"
        "📩 *support@fdksignal\\.com*\n\n"
        "Notre équipe reviendra vers vous dans les plus brefs délais\\. "
        "N'hésitez pas à poser vos questions ici — un membre de notre équipe "
        "est disponible pour vous répondre\\.\n\n",
        parse_mode="MarkdownV2"
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  Étape 4b — Remboursement annulé
# ─────────────────────────────────────────────

async def _refund_cancelled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "👍 Demande annulée. Si vous avez des questions, notre équipe est disponible ici."
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  Annulation
# ─────────────────────────────────────────────

async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Processus annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  Enregistrement
# ─────────────────────────────────────────────

def register_validation_handler(app):
    conv = ConversationHandler(
        entry_points=[CommandHandler("valider", _start)],
        states={
            ASK_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_email)
            ],
            SHOW_RESULT: [
                CallbackQueryHandler(_confirm_subscription, pattern="^val_confirm$"),
                CallbackQueryHandler(_request_refund,       pattern="^val_refund$"),
            ],
            CONFIRM_REFUND: [
                CallbackQueryHandler(_refund_confirmed, pattern="^refund_yes$"),
                CallbackQueryHandler(_refund_cancelled, pattern="^refund_no$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        per_user=True,
        per_chat=False,
        allow_reentry=True,
    )
    app.add_handler(conv, group=0)
    print("[validation_handler] Handler enregistré.")