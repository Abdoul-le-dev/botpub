from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)

# États
USER, PWD = range(2)

# Étape 1 : demander l'email
async def save_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📩 *E-mail :*",
        parse_mode="Markdown"
    )
    return USER

# Étape 2 : récupérer l'email et demander le mot de passe
async def save_mail_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mail'] = update.message.text

    await update.message.reply_text(
        "🔑 *Mot de passe :*",
        parse_mode="Markdown"
    )
    return PWD

# Étape 3 : récupérer le mot de passe et enregistrer l'utilisateur
async def save_mail_pwd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['psw'] = update.message.text

    # Appel de ta fonction d'ajout utilisateur
    # add_new_user(context.user_data.get('mail'), context.user_data.get('psw'))

    await update.message.reply_text("✅ Adresse e-mail et mot de passe enregistrés.")
    return ConversationHandler.END

# Handler de conversation
conv_handler_mail_user = ConversationHandler(
    entry_points=[CommandHandler('addemail', save_mail)],
    states={
        USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_mail_id)],
        PWD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_mail_pwd)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    allow_reentry=True,
)

app.add_handler(conv_handler_mail_user)


conv_handler_mail_user = ConversationHandler(
    entry_points=[CommandHandler('addemail', save_mail)],
    states={
        USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_mail_id)],
        PWD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_mail_pwd)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    allow_reentry=True,
)

convs_handler = ConversationHandler(
    entry_points=[CommandHandler("message", start_message)],
    states={
        ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_id)],
        ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_TEXT)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
    )