"""
formation_handler.py — Flow de validation de formation FDK Signal.
Déclenchement : /formation_valider

Flow :
  1. Demande l'email
  2. Vérifie dans formation_validation via email_exists()
     → Trouvé  : compte validé, signaux accessibles
     → Non trouvé : branching avec boutons
        a) A suivi la formation ?
           → Non : bouton pour suivre la formation
           → Oui : a créé le compte ?
              → Non : bouton pour créer le compte + soumettre le screen
              → Oui : a soumis le screen ?
                 → Non : bouton pour soumettre + patienter
                 → Oui : demander de patienter, support contacté
"""

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)

from formation_validation import email_exists

# ─────────────────────────────────────────────
#  États
# ─────────────────────────────────────────────

(
    FV_ASK_EMAIL,
    FV_SUIVI_FORMATION,
    FV_CREE_COMPTE,
    FV_SOUMIS_SCREEN,
) = range(400, 404)

# ─────────────────────────────────────────────
#  Constantes
# ─────────────────────────────────────────────

URL_FORMATION    = "https://fdksignal.com/formation"
URL_CREER_COMPTE = "https://affs.click/hw88e"
URL_SCREEN       = "https://fdksignal.com/screen"
CONTACT_SUPPORT  = "@Fiacrekpanou"


# ─────────────────────────────────────────────
#  Étape 1 — Commande /formation_valider
# ─────────────────────────────────────────────

async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    print(f"[formation] _start user={update.effective_user.id}")
    await update.message.reply_text(
        "📧 Veuillez saisir l'adresse email utilisée lors de votre paiement :",
        reply_markup=ReplyKeyboardRemove()
    )
    return FV_ASK_EMAIL


# ─────────────────────────────────────────────
#  Étape 2 — Réception de l'email
# ─────────────────────────────────────────────

async def _receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip().lower()
    print(f"[formation] _receive_email email={email}")

    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Adresse email invalide. Veuillez réessayer :")
        return FV_ASK_EMAIL

    context.user_data["email"] = email
    found = await email_exists(email)

    # ── Email présent → validé ───────────────────────────────────────────
    if found:
        await update.message.reply_text(
            "🎉 *Votre compte de formation a été validé \\!*\n\n"
            "✅ Vous pouvez dès à présent recevoir les signaux FDK\\.\n\n"
            "Bienvenue dans la communauté — bon trading \\! 📈",
            parse_mode="MarkdownV2"
        )
        return ConversationHandler.END

    # ── Email absent → branching ─────────────────────────────────────────
    await update.message.reply_text(
        "ℹ️ *Votre email n'est pas encore dans notre système\\.*\n\n"
        "Avez\\-vous suivi la formation FDK jusqu'à la fin ?",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Oui, j'ai suivi la formation", callback_data="fv_suivi_oui"),
                InlineKeyboardButton("❌ Non, pas encore",              callback_data="fv_suivi_non"),
            ]
        ])
    )
    return FV_SUIVI_FORMATION


# ─────────────────────────────────────────────
#  Branche A — N'a PAS suivi la formation
# ─────────────────────────────────────────────

async def _suivi_non(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "📚 *Commencez par suivre la formation FDK\\.*\n\n"
        "La formation est obligatoire avant de pouvoir recevoir les signaux\\. "
        "Cliquez sur le bouton ci\\-dessous pour y accéder dès maintenant :",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎓 Accéder à la formation", url=URL_FORMATION)]
        ])
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  Branche B — A suivi la formation → a créé le compte ?
# ─────────────────────────────────────────────

async def _suivi_oui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "👍 Parfait \\!\n\n"
        "Avez\\-vous créé votre compte sur la plateforme après la formation ?",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Oui, j'ai créé mon compte", callback_data="fv_compte_oui"),
                InlineKeyboardButton("❌ Non, pas encore",           callback_data="fv_compte_non"),
            ]
        ])
    )
    return FV_CREE_COMPTE


# ─────────────────────────────────────────────
#  Branche B1 — N'a PAS créé le compte
# ─────────────────────────────────────────────

async def _compte_non(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "📝 *Créez votre compte dès maintenant\\.*\n\n"
        "1\\. Cliquez sur le bouton ci\\-dessous pour créer votre compte\n"
        "2\\. Une fois créé, soumettez une capture d'écran ici : "
        f"{URL_SCREEN}\n\n"
        "Revenez ensuite avec /formation\\_valider pour finaliser votre validation\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Créer mon compte", url=URL_CREER_COMPTE)]
        ])
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  Branche B2 — A créé le compte → a soumis le screen ?
# ─────────────────────────────────────────────

async def _compte_oui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "📸 Avez\\-vous soumis la capture d'écran de votre compte ?",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Oui, j'ai soumis le screen", callback_data="fv_screen_oui"),
                InlineKeyboardButton("❌ Non, pas encore",            callback_data="fv_screen_non"),
            ]
        ])
    )
    return FV_SOUMIS_SCREEN


# ─────────────────────────────────────────────
#  Branche B2a — N'a PAS soumis le screen
# ─────────────────────────────────────────────

async def _screen_non(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "📸 *Soumettez votre capture d'écran\\.*\n\n"
        "Cliquez sur le bouton ci\\-dessous pour soumettre la capture de votre compte\\. "
        "Un admin vérifiera que le compte a bien été créé et crédité\\.\n\n"
        "⏳ Vous serez notifié dès la validation\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Soumettre mon screen", url=URL_SCREEN)]
        ])
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  Branche B2b — A tout fait → escalade support
# ─────────────────────────────────────────────

async def _screen_oui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "✅ *Merci, toutes les étapes ont bien été complétées\\.*\n\n"
        "Nous remontons votre demande au support pour une réponse rapide\\. "
        "Vous serez notifié dès que votre accès aux signaux sera activé\\.\n\n"
        f"Pour toute question, envoyez\\-les directement en privé à {CONTACT_SUPPORT}\\.",
        parse_mode="MarkdownV2"
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

def register_formation_handler(app):
    conv = ConversationHandler(
        entry_points=[CommandHandler("formation_valider", _start)],
        states={
            FV_ASK_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_email)
            ],
            FV_SUIVI_FORMATION: [
                CallbackQueryHandler(_suivi_oui, pattern="^fv_suivi_oui$"),
                CallbackQueryHandler(_suivi_non, pattern="^fv_suivi_non$"),
            ],
            FV_CREE_COMPTE: [
                CallbackQueryHandler(_compte_oui, pattern="^fv_compte_oui$"),
                CallbackQueryHandler(_compte_non, pattern="^fv_compte_non$"),
            ],
            FV_SOUMIS_SCREEN: [
                CallbackQueryHandler(_screen_oui, pattern="^fv_screen_oui$"),
                CallbackQueryHandler(_screen_non, pattern="^fv_screen_non$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        per_user=True,
        per_chat=False,
        allow_reentry=True,
    )
    app.add_handler(conv, group=0)
    print("[formation_handler] Handler enregistré.")