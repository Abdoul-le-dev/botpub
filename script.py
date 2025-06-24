import os
from telegram import Update
from jeu import export_and_send_pdf
from database.database import init_db
from database.database import save_user
from database.database import user_exists
from database.database import save_message
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
import sqlite3
import pandas as pd
import random
from testing import  choose_format,handle_format_choice, get_media, get_text
import string
from message_de_masse import broadcast_message
from stats import last_message
from telegram import Update
from telegram.ext import ContextTypes

from telegram.error import BadRequest

from dotenv import load_dotenv
from telegram.ext import filters

load_dotenv()

ADMIN_ID = 571718066  # Remplace par ton ID Telegram


ASK_BROADCAST = 99

CHOOSE_FORMAT, GET_MEDIA, GET_TEXT = range(3)

def generate_filename():
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"preinscriptions_{suffix}.xlsx"

async def export_and_send_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("SELECT id, name, phone, country, created_at, telegram_id FROM users")
    rows = cursor.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=["ID", "Nom", "Téléphone", "Pays", "Inscrit le","ID_TELEGRAM" ])
    filename = generate_filename()
    df.to_excel(filename, index=False)

    # Envoi à l’administrateur
    await context.bot.send_document(chat_id=update.effective_user.id , document=open(filename, "rb"))

    await update.message.reply_text("📤 Exportation réussie. Fichier envoyé à l’administrateur.")

async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chat_join_request.from_user
    user_id = user.id
    user_name = update.effective_user.first_name or " Futur trader"
    
    try:
        await update.chat_join_request.approve() 
    except BadRequest as e:
        if "User_already_participant" in str(e):
            print("Déjà membre, pas besoin d’approuver.")    

    # Envoie un message privé
    try:
        
        with open("video3.mp4", "rb") as video:
            await context.bot.send_video(chat_id=user_id, video=video)

        
        await context.bot.send_message(
        chat_id=user_id,
        text="🔥🔥 Participe au jeu concours ! Clique sur /JeParticipeAuJeuConcours 🎉🎁"
        )

        
    except Exception as e:
        print(f"Impossible d’envoyer un message à {user_id} : {e}")
     

async def user_imformation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user

async def log_unhandled_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    user_id = user.id
    if not msg:
        print("⚠️ Mise à jour sans message texte. Ignorée.")
        return
    message_id = msg.message_id
    message_text = msg.text or "<non-text>"
    if msg.text:
        message_type = "text"
    elif msg.photo:
        message_type = "photo"
    elif msg.document:
        message_type = "document"
    elif msg.video:
        message_type = "video"
    elif msg.audio:
        message_type = "audio"
    else:
        message_type = "other"
    

    save_message(user_id, message_id, message_text, None, message_type)

token = os.getenv("token")

# États du formulaire
NAME, PHONE, COUNTRY = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    user = update.effective_user
    user_id = user.id
    if user_exists(user_id):
        await update.message.reply_text("Tu es déjà inscrit ✅")
    else:
        if chat_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Ta préinscription va se dérouler en 3 étapes ⏳✨ pour le jeu concours 🎉🎁.\n...Ça prendra maximum 2 minutes, alors on y va à fond !\n\nÉtape 1/3 :👤  Quel est ton nom et prénom ?\n\n..."
            )
        else:
            await update.message.reply_text(
                "Ta préinscription va se dérouler en 3 étapes ⏳✨ pour le jeu concours 🎉🎁.\n...Ça prendra maximum 2 minutes, alors on y va à fond !\n\nÉtape 1/3 :👤  Quel est ton nom et prénom ?\n\n..."
            )
        return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer un texte valide."
            )
        return NAME
    
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Étape 2/3 :📞 Quel est ton numéro de téléphone ?"
                                    "\n\n Format international recommandé, ex : +22997203304")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer ton numéro."
            )
        return PHONE
    
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("Étape 3/3 :🌍 Dans quel pays vis-tu ?")
    return COUNTRY

async def get_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["country"] = update.message.text
    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer le nom de ton pays."
            )
        return COUNTRY
    data = context.user_data
    await update.message.reply_text(
        f"✅ Bravo, votre inscription est confirmée ! 🥳✅\n\n"
        f"Nom : {data['name']}\n"
        f"Téléphone : {data['phone']}\n"
        f"Pays : {data['country']}"
    )
    user = update.effective_user
    user_id = user.id
    save_user(data["name"], data["phone"], data["country"],user_id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 Suivre la formation gratuite 📈 ", url="https://app.rmiclass.net/reff/538699")]
    ])

    await update.message.reply_text(
        "✅🎉 Inscription au jeu concours validée ! 🎊🔥!\n\n"
        "📌👉 Épingle vite notre assistant bot pour recevoir toutes les notif’s importantes 📲🔔 \n\n"
        "⏳⏰ En attendant dimanche, profite GRATUITEMENT de notre initiation au trading ici 👉 \n\n"
        "Rends-toi sur https://app.rmiclass.net/reff/538699, crée ton compte, puis découvre notre initiation au trading.",
        reply_markup=keyboard
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Annulé.")
    return ConversationHandler.END


async def handle_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or update.effective_user.id== 6992809421: 
        await update.message.reply_text("⛔ Désolé, cette commande est réservée à l’administrateur.")
        return

    await export_and_send_excel(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cta_start":
       
        chat_id = query.from_user.id
        await start(update, context, chat_id=chat_id)
        


ASK_ID, ASK_TEXT = range(2)

async def start_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🆔 Envoie l’ID Telegram du destinataire :")
    return ASK_ID

async def receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["target_id"] = update.message.text.strip()
    await update.message.reply_text("✏️ Quel message veux-tu envoyer ?")
    return ASK_TEXT

async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_id = context.user_data["target_id"]
    text = update.message.text

    try:
        await context.bot.send_message(chat_id=int(target_id), text=text)
        await update.message.reply_text("✅ Message envoyé !")
    except:
        await update.message.reply_text("❌ Erreur : ID invalide ou l’utilisateur n’a pas démarré le bot.")
    return ConversationHandler.END


async def ask_broadcast(update, context):
    if update.effective_user.id != ADMIN_ID or update.effective_user.id== 6992809421: 
        await update.message.reply_text("⛔ Désolé, cette commande est réservée à l’administrateur.")
        return
    await update.message.reply_text("📨 Quel message veux-tu envoyer à tous ?")
    return ASK_BROADCAST

async def send_broadcast(update, context):
    message = update.message.text
    await broadcast_message(context.bot, update.effective_user.id, message)
    return ConversationHandler.END




if __name__ == '__main__':

    init_db()
    
    app = Application.builder().token(token).build()
    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    conv_handler_jeu = ConversationHandler(
        entry_points=[CommandHandler("JeParticipeAuJeuConcours", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    convs_handler = ConversationHandler(
    entry_points=[CommandHandler("message", start_message)],
    states={
        ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_id)],
        ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
    )
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("MessageDeMasse", ask_broadcast)],
        states={ASK_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    ))

    conv_handlerMsg = ConversationHandler(
    entry_points=[CommandHandler('msgMasse', choose_format)],
    states={
        CHOOSE_FORMAT: [MessageHandler(filters.Regex('^[1-5]$'), handle_format_choice)],
        GET_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, get_media)],
        GET_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_text)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
)
    app.add_handler(conv_handlerMsg)
    app.add_handler(convs_handler)

    app.add_handler(conv_handler_jeu)
    

    app.add_handler(CommandHandler("data", handle_data_command))

    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(conv_handler)

    app.add_handler(CommandHandler("gagnants", export_and_send_pdf))

    app.add_handler(CommandHandler("lastMessage", last_message))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_unhandled_message))

    print('running...')
    
    app.run_polling(poll_interval=1)
