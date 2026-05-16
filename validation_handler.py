"""
validation_handler.py — Flow de validation d'abonnement FDK Signal via Telegram.

Déclenchement : t.me/TradingBot?start=validation

Étapes :
  1. Demande l'email
  2. Cherche le dernier paiement non validé pour cet email
     ├─ Tous déjà validés → message informatif
     ├─ Aucun paiement   → bouton remboursement
     └─ Paiement trouvé  → affiche infos + clauses + bouton "Je valide"
  3a. Validation → upsert users + active subscription_info
                 + insère subscriptions + ajoute catégorie
  3b. Remboursement → confirmation → email support@fdksignal.com + /suivi
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
CATEGORIE    = "PRELANCEMENT FDK GOLD SAISON 1"
FORM_COMMAND = "/suivi"

# ── États ──────────────────────────────────────────────────────────────────
ASK_EMAIL, SHOW_RESULT, CONFIRM_REFUND = range(300, 303)

CLAUSES = (
    "📋 *Politique de confidentialité — FDK Signal*\n\n"
    "• Vos données personnelles sont utilisées uniquement dans le cadre de votre abonnement FDK Signal.\n"
    "• Elles ne sont jamais revendues à des tiers.\n"
    "• Vous pouvez demander leur suppression à tout moment via fdksignal.com.\n\n"
    "⚠️ *Avertissement sur les risques*\n\n"
    "• Le trading comporte des risques de perte en capital.\n"
    "• Les performances passées ne garantissent pas les performances futures.\n"
    "• Vous tradez sous votre entière responsabilité.\n\n"
    "En cliquant sur *Je valide mon abonnement*, vous confirmez avoir lu "
    "et accepté ces conditions ainsi que celles disponibles sur fdksignal.com."
)


# ── DB ─────────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _find_last_unvalidated(email: str):
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
    return dict(row) if row else None


def _all_validated(email: str) -> bool:
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
    return total > 0 and total == validated


def _upsert_user(c, telegram_id: int, pay: dict):
    """
    Crée ou met à jour le user dans la table users.
    On utilise les données disponibles dans subscription_info :
      name, email, phone, country_code
    Règle : on n'écrase jamais une valeur déjà renseignée.
    """
    name         = pay.get("name") or ""
    email        = pay.get("email") or ""
    phone        = pay.get("phone") or ""
    country      = pay.get("country_code") or ""

    existing = c.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()

    if existing:
        # Mise à jour uniquement des champs vides
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
    else:
        # Création du user avec toutes les infos dispo
        c.execute(
            """
            INSERT INTO users (telegram_id, name, email, phone, country, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (telegram_id, name, email, phone, country)
        )


def _activate(pay: dict, telegram_id: int, email: str):
    with _conn() as c:
        # 1. Upsert users
        _upsert_user(c, telegram_id, pay)

        # 2. Activer dans subscription_info
        c.execute(
            """
            UPDATE subscription_info
            SET status = 'active', note = 'valide par telegram',
                user_id = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (telegram_id, pay["id"])
        )

        # 3. Inserer dans subscriptions si pas déjà actif
        existing = c.execute(
            "SELECT id FROM subscriptions WHERE user_id = ? AND status = 'active'",
            (telegram_id,)
        ).fetchone()
        if not existing:
            c.execute(
                """
                INSERT OR IGNORE INTO subscriptions
                    (user_id, plan, duration_days, started_at, expires_at,
                     status, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', 'valide telegram',
                        datetime('now'), datetime('now'))
                """,
                (telegram_id, pay["plan"], pay["duration_days"],
                 pay["started_at"], pay["expires_at"])
            )
        c.commit()


def _format_date(raw) -> str:
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(str(raw)).strftime("%d/%m/%Y")
    except Exception:
        return str(raw)


# ── Étape 1 — déclenchement ────────────────────────────────────────────────

async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Bienvenue dans le processus de validation FDK Signal.\n\n"
        "📧 Veuillez saisir l'adresse email utilisée lors de votre paiement :",
        reply_markup=ReplyKeyboardRemove()
    )
    return ASK_EMAIL


# ── Étape 2 — réception email ──────────────────────────────────────────────

async def _receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()

    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Adresse email invalide. Veuillez réessayer :")
        return ASK_EMAIL

    context.user_data["email"] = email

    # Cas 1 — tous déjà validés
    if _all_validated(email):
        await update.message.reply_text(
            "ℹ️ *Tous vos paiements sont déjà validés.*\n\n"
            "Votre abonnement FDK Gold est actif.\n"
            "Si vous avez des questions, notre équipe est disponible ici.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # Cas 2 — paiement non validé trouvé
    pay = _find_last_unvalidated(email)
    context.user_data["pay"] = pay

    if pay:
        await update.message.reply_text(
            f"✅ *Paiement trouvé*\n\n"
            f"📦 Plan      : *{pay.get('plan', '—')}*\n"
            f"💰 Montant   : {pay.get('amount_usd', '—')} $\n"
            f"📅 Paiement  : {_format_date(pay.get('paid_at'))}\n"
            f"⏳ Expire le : {_format_date(pay.get('expires_at'))}\n\n"
            f"{CLAUSES}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Je valide mon abonnement", callback_data="val_confirm")
            ]])
        )
        return SHOW_RESULT

    # Cas 3 — aucun paiement
    await update.message.reply_text(
        "❌ Aucun paiement trouvé pour cet email.\n\n"
        "Si vous pensez qu'il s'agit d'une erreur, vous pouvez demander un remboursement.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💸 Je souhaite un remboursement", callback_data="val_refund")
        ]])
    )
    return SHOW_RESULT


# ── Étape 3a — validation confirmée ───────────────────────────────────────

async def _confirm_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    email   = context.user_data.get("email", "")
    pay     = context.user_data.get("pay", {})

    # Upsert user + activer abonnement
    _activate(pay, user_id, email)

    # Ajouter à la catégorie
    try:
        from telegram_page.categorie import add_members_to_category
        await add_members_to_category(CATEGORIE, [user_id])
    except Exception as e:
        print(f"[validation_handler] categorie error: {e}")

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"🎉 *Votre abonnement FDK Gold est validé !*\n\n"
        f"⏳ Il est actif jusqu'au *{_format_date(pay.get('expires_at'))}*.\n\n"
        "Si vous avez des questions, n'hésitez pas à les poser ici — "
        "un membre de notre équipe sera disponible pour vous répondre.\n\n"
        f"📋 Merci de bien vouloir remplir ce formulaire afin que nous "
        f"puissions suivre votre progression :\n👉 {FORM_COMMAND}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ── Étape 3b — demande remboursement ──────────────────────────────────────

async def _request_refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "⚠️ *Confirmez-vous votre demande de remboursement ?*\n\n"
        "Cette action notifiera notre équipe.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Oui, confirmer", callback_data="refund_yes"),
            InlineKeyboardButton("❌ Non, annuler",   callback_data="refund_no"),
        ]])
    )
    return CONFIRM_REFUND


# ── Étape 4a — remboursement confirmé ─────────────────────────────────────

async def _refund_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pay        = context.user_data.get("pay") or {}
    expires_at = _format_date(pay.get("expires_at"))
    msg_expiry = (
        f"Votre accès reste disponible jusqu'au *{expires_at}*."
        if expires_at != "—"
        else "Votre accès a déjà expiré."
    )

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ *Demande de remboursement enregistrée.*\n\n"
        f"{msg_expiry}\n\n"
        "Pour finaliser votre demande, envoyez un email à :\n"
        "📩 *support@fdksignal.com*\n\n"
        "Notre équipe reviendra vers vous dans les plus brefs délais. "
        "N'hésitez pas à poser vos questions ici — un membre de notre équipe "
        "est disponible pour vous répondre.\n\n"
        f"📋 En attendant, merci de remplir ce formulaire :\n👉 {FORM_COMMAND}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ── Étape 4b — remboursement annulé ───────────────────────────────────────

async def _refund_cancelled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "👍 Demande annulée. Si vous avez des questions, notre équipe est disponible ici."
    )
    return ConversationHandler.END


# ── Annulation ─────────────────────────────────────────────────────────────

async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Processus annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── Enregistrement ─────────────────────────────────────────────────────────

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
    app.add_handler(conv, group=2)
    print("[validation_handler] Handler enregistré.")