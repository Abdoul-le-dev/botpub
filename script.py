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
from database.database import save_user_default
from user_data import user_info
from database.database import get_file_id
from database.database import save_file_id
from start import start
from start import get_name
from start import get_phone     
from start import get_country
#from start import get_email
#from start import get_motivation
from start import get_level
from start import get_why, get_email
from start import get_what, get_expectations,get_discovery
from user_data import start_delete
from qcmprocess import start_qcm_creation
from qcmprocess import set_categorie
from qcmprocess import set_nb_questions
from qcmprocess import set_question     
from qcmprocess import set_nb_choix
from qcmprocess import add_choix
from qcmprocess import validate_choix
from qcmprocess import set_categorie
from qcmprocess import set_question
from qcmprocess import set_nb_choix
from qcmprocess import continue_choices
from qcmprocess import validate_bad_reason   

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

import json

from constance import NAME, PHONE, COUNTRY, LEVEL, WHY, WHAT, ASK_IDS,EMAIL, EXPECTATIONS,DISCOVERY

from constance import CATEGORIE, NOMBRE_QUESTIONS, QUESTION, NB_CHOIX, CHOIX, REPONSE_SUIVANTE

type =""
load_dotenv()

ADMIN_ID = 571718066  # Remplace par ton ID Telegram

CANAL_B_ID = 1002039134942
ASK_BROADCAST = 99



async def wait_5_seconds():
    await asyncio.sleep(5)

CHOOSE_FORMAT, GET_MEDIA, GET_TEXT = range(3)

def generate_filename():
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"preinscriptions_{suffix}.xlsx"

async def export_and_send_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Connexion et récupération des messages
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages")
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    messages = [dict(zip(columns, row)) for row in rows]
    conn.close()

    # Export en JSON
    with open("messages.json", "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    print("✅ Fichier JSON généré.")

    
    with open("messages.json", "rb") as file:
        await context.bot.send_document(chat_id=ADMIN_ID, document=file, caption="📄 Fichier des messages")


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

    chat_id = update.chat_join_request.chat.id

    save_user_default(user_id)

    args = context.args
    print("chat_id")
    print(chat_id)
    print(args)
    
    if user_has_categorie(user_id,"leseminaire"):
        print("L'utilisateur a déjà une catégorie, il est déjà membre.")
        try:
            await update.chat_join_request.approve()
        except BadRequest as e:
            if "User_already_participant" in str(e):
                print("Déjà membre, il est membre.") 
                print(e)     
                return
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "👌 **C'est bon je t'ai intégré au canal ✅**\n"
                "*C'est pour bientôt et prépare toi, je te dirai tout !*\n\n"
                "📌 *Épingle ce canal* pour rester à l'affût des **nouvelles informations**."
            ),
            parse_mode="Markdown"
        )

        return 
    
        
    
    
    
    print(chat_id)
    
    if chat_id == CANAL_B_ID:
        # Par exemple, tu interdis l’entrée
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                "❌ *Doucement, on ne triche pas !* \n\n"
                "Tu n'es **pas autorisé** à rejoindre ce canal.\n"
                "🚫 Tu es maintenant *banni à vie* de ce canal.\n\n"
                "🔒 Toute tentative future entraînera aussi le bannissement de la personne qui t’a transmis le lien."
            ),
            parse_mode="Markdown"
        )

        await context.bot.decline_chat_join_request(chat_id, user.id)
        return
    else:
        try:
            await update.chat_join_request.approve() 
        except BadRequest as e:
            if "User_already_participant" in str(e):
                print("Déjà membre, pas besoin d’approuver.")    

        # Envoie un message privé
        try:
            video_name = "welcome_messagess"

            file_id = get_file_id(video_name)

            if file_id:
                # Réutiliser le file_id
                await context.bot.send_video(chat_id=user_id , video=file_id, caption="")
                

                


                
            else:
                # Envoyer depuis fichier local, puis sauvegarder le file_id
                video_path = "welcome.mp4"
                msg = await context.bot.send_video(chat_id=user_id , video=video_path, caption="Bienvenue ! 🎉")
                new_file_id = msg.video.file_id
                save_file_id(video_name, new_file_id)
                
                

            
            await context.bot.send_message(
            chat_id=user_id,
            text="🔥🔥✍️  Clique sur /JeMEnregistre Maintenant"
            )

            
        except Exception as e:
            print(f"Impossible d’envoyer un message à {user_id} : {e}")
     

async def user_imformation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user

async def log_unhandled_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if not user:
        return
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
        entry_points=[CommandHandler("JeMEnregistre", start)],
        states={
            WHY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why)],
            WHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_what)],
            LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_level)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
          
            EXPECTATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_expectations)],
            
            DISCOVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_discovery)]
            
            
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    conv_handlerstart = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WHY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why)],
            WHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_what)],
            LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_level)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            EXPECTATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_expectations)],
            DISCOVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_discovery)]
            
            
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    qcm_handler = ConversationHandler(
    entry_points=[CommandHandler("creer_qcm", start_qcm_creation)],
    states={
        CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_categorie)],
        NOMBRE_QUESTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_nb_questions)],
        QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_question)],
        NB_CHOIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_nb_choix)],
        CHOIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_choix)],
        REPONSE_SUIVANTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_choix)],
        validate_bad_reason: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_bad_reason)]
    },
    fallbacks=[CommandHandler("cancel", cancel)])

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
    app.add_handler(conv_handlerstart)
    app.add_handler(CommandHandler("LesGagnants", export_and_send_pdf))

    app.add_handler(CommandHandler("lastMessage", last_message))

    app.add_handler(CommandHandler("userInfo", user_info))

    app.add_handler(CommandHandler("userDelete", start_delete))

    app.add_handler(CommandHandler("exportMessages", export_and_send_messages))

   
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_unhandled_message))

    print('running...')

   

    app.add_handler(qcm_handler)
    

    
    app.run_polling(poll_interval=1)
