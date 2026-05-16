"""
validation_handler.py — Flow de validation d'abonnement FDK Signal via Telegram.
Déclenchement : t.me/TradingBot?start=fdkgoldsaison
"""

import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)

DB_PATH      = "preinscriptions.db"
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


# Helper

def _escape_md(text) -> str:
    if not text:
        return "—"
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = str(text).replace(ch, f"\\{ch}")
    return text


# DB

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _find_last_unvalidated(email: str):
    print(f"[validation] _find_last_unvalidated email={email}")
    with _conn() as c:
        row = c.execute(
            """
            SELECT * FROM subscription_info
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))
              AND (note IS NULL OR note NOT LIKE '%valide%')
            ORDER BY paid_at DESC LIMIT 1
            """,
            (email,)
        ).fetchone()
    result = dict(row) if row else None
    print(f"[validation] _find_last_unvalidated result={result}")
    return result


def _all_validated(email: str) -> bool:
    print(f"[validation] _all_validated email={email}")
    with _conn() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM subscription_info WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))",
            (email,)
        ).fetchone()[0]
        validated = c.execute(
            """
            SELECT COUNT(*) FROM subscription_info
            WHERE LOWER(TRIM(email)) = LOWER(TRIM(?))
              AND note LIKE '%valide%'
            """,
            (email,)
        ).fetchone()[0]
    print(f"[validation] total={total} validated={validated}")
    return total > 0 and total == validated


def _upsert_user(c, telegram_id: int, pay: dict):
    print(f"[validation] _upsert_user telegram_id={telegram_id}")
    name    = pay.get("name") or ""
    email   = pay.get("email") or ""
    phone   = pay.get("phone") or ""
    country = pay.get("country_code") or ""

    existing = c.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()

    if existing:
        user = dict(existing)
        updates = {}
        if not user.get("name")    and name:    updates["name"]    = name
        if not user.get("email")   and email:   updates["email"]   = email
        if not user.get("phone")   and phone:   updates["phone"]   = phone
        if not user.get("country") and country: updates["country"] = country
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [telegram_id]
            c.execute(f"UPDATE users SET {sets} WHERE telegram_id = ?", vals)
            print(f"[validation] user mis à jour: {updates}")
    else:
        c.execute(
            "INSERT INTO users (telegram_id, name, email, phone, country, created_at) VALUES (?,?,?,?,?,datetime('now'))",
            (telegram_id, name, email, phone, country)
        )
        print(f"[validation] user créé telegram_id={telegram_id}")


def _activate(pay: dict, telegram_id: int, email: str):
    print(f"[validation] _activate pay_id={pay['id']} telegram_id={telegram_id}")
    with _conn() as c:
        _upsert_user(c, telegram_id, pay)
        c.execute(
            "UPDATE subscription_info SET status='active', note='valide par telegram', updated_at=datetime('now') WHERE id=?",
            (pay["id"],)
        )
        existing = c.execute(
            "SELECT id FROM subscriptions WHERE user_id=? AND status='active'", (telegram_id,)
        ).fetchone()
        if not existing:
            c.execute(
                """
                INSERT OR IGNORE INTO subscriptions
                    (user_id, plan, duration_days, started_at, expires_at,
                     status, note, created_at, updated_at)
                VALUES (?,?,?,?,?,'active','valide telegram',datetime('now'),datetime('now'))
                """,
                (telegram_id, pay["plan"], pay["duration_days"], pay["started_at"], pay["expires_at"])
            )
        c.commit()
    print(f"[validation] _activate done")


def _format_date(raw) -> str:
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(str(raw)).strftime("%d/%m/%Y")
    except Exception:
        return str(raw)


# Étape 1

async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_validation"] = True
    print(f"[validation] _start user={update.effective_user.id}")
    context.user_data.clear()
    await update.message.reply_text(
    "📧 Veuillez saisir l'adresse email utilisée lors de votre paiement :",
    reply_markup=ReplyKeyboardRemove()
)
    return ASK_EMAIL


# Étape 2

async def _receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_validation"] = True
    email = update.message.text.strip()
    print(f"[validation] _receive_email email={email}")

    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Adresse email invalide. Veuillez réessayer :")
        return ASK_EMAIL

    context.user_data["email"] = email

    try:
        all_done = _all_validated(email)
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
        pay = _find_last_unvalidated(email)
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


# Étape 3a

async def _confirm_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print(f"[validation] _confirm_subscription user={query.from_user.id}")

    user_id = query.from_user.id
    email   = context.user_data.get("email", "")
    pay     = context.user_data.get("pay", {})

    try:
        _activate(pay, user_id, email)
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
        f"⏳ Il est actif jusqu'au *{_format_date(pay.get('expires_at'))}*\\.\n\n"
        "Si vous avez des questions, n'hésitez pas à les poser ici — "
        "un membre de notre équipe sera disponible pour vous répondre\\.\n\n",
        # f"📋 Un formulaire vous sera envoyer dans la suite de la journée afin que nous "
        # f"puissions suivre votre progression ",
        parse_mode="MarkdownV2"
    )

    await query.message.reply_text(
    "📋 Veuillez cliquer sur /mon_profil_trader_fdk afin de "
    "compléter votre *Profil Trader* ",
    parse_mode="MarkdownV2"
)
    return ConversationHandler.END


# Étape 3b

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


# Étape 4a

async def _refund_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    print(f"[validation] _refund_confirmed user={query.from_user.id}")

    pay        = context.user_data.get("pay") or {}
    expires_at = _format_date(pay.get("expires_at"))
    msg_expiry = (
        f"Votre accès reste disponible jusqu'au *{expires_at}*\\."
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


# Étape 4b

async def _refund_cancelled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "👍 Demande annulée. Si vous avez des questions, notre équipe est disponible ici."
    )
    return ConversationHandler.END


# Annulation

async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Processus annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# Enregistrement

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