import os
from telegram import Update
from jeu import export_and_send_pdf
from database.database import init_db
from database.database import save_user
from database.database import user_exists
from database.database import save_message
from database.database import update_user_info
from database.database import add_categorie
from database.database import user_has_categorie
from user_data import user_info

from start import start
from start import get_name
from start import get_phone     
from start import get_country
from start import get_email
from start import get_motivation
from start import get_level
from user_data import start_delete


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

import asyncio

import time

from constance import NAME, PHONE, COUNTRY, LEVEL, EMAIL, MOTIVATION, ASK_IDS

import tracemalloc
tracemalloc.start()
type =""
load_dotenv()

ADMIN_ID = 571718066  # Remplace par ton ID Telegram


ASK_BROADCAST = 99



async def wait_5_seconds():
    await asyncio.sleep(5)

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

    args = context.args

    print(args)

    if user_has_categorie(user_id):
        try:
            await update.chat_join_request.approve()
        except BadRequest as e:
            if "User_already_participant" in str(e):
                print("Déjà membre, il est membre.") 
                print(e)     
        await update.message.reply_text(
            f"👌 **C'est bon je t'ai intégrer au canal ✅**\n"
            f"*C'est pour bientôt et prépare toi, je te dirai tout !*\n\n"
            f"📌 __Épingle ce canal__ pour rester à l'affût des **nouvelles informations**.",
            parse_mode="MarkdownV2"
        )
        return
    
    
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

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Annulé.")
    return ConversationHandler.END

# États du formulaire
#NAME, PHONE, COUNTRY, LEVEL,EMAIL,MOTIVATION = range(6)



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


async def detect_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    print(f"✅ Canal : {chat.title} — chat_id : {chat.id}")
    await update.message.reply_text(f"ID du canal : `{chat.id}`", parse_mode="Markdown")


if __name__ == '__main__':

    init_db()
    
    app = Application.builder().token(token).read_timeout(30).write_timeout(30).build()
    #app.add_handler(MessageHandler(filters.ALL, detect_channel))
    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            MOTIVATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_motivation)],
            LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_level)],
            
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

    app.add_handler(CommandHandler("LesGagnants", export_and_send_pdf))

    app.add_handler(CommandHandler("lastMessage", last_message))

    app.add_handler(CommandHandler("userInfo", user_info))

    app.add_handler(CommandHandler("userDelete", start_delete))

   
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_unhandled_message))

    print('running...')
    
    app.run_polling(poll_interval=1)
