import os
from telegram import Update
from database.database import init_db
from database.database import save_user
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
import sqlite3
import pandas as pd
import random
import string
from telegram import Update
from telegram.ext import ContextTypes

from dotenv import load_dotenv

load_dotenv()

ADMIN_ID = 123456789  # Remplace par ton ID Telegram

def generate_filename():
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"preinscriptions_{suffix}.xlsx"

async def export_and_send_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("SELECT id, name, phone, country, created_at FROM users")
    rows = cursor.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=["ID", "Nom", "Téléphone", "Pays", "Inscrit le"])
    filename = generate_filename()
    df.to_excel(filename, index=False)

    # Envoi à l’administrateur
    await context.bot.send_document(chat_id=update.effective_user.id , document=open(filename, "rb"))

    await update.message.reply_text("📤 Exportation réussie. Fichier envoyé à l’administrateur.")

async def approve_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chat_join_request.from_user
    user_id = user.id
    user_name = update.effective_user.first_name or " Futur trader"

    await update.chat_join_request.approve() 

    # Envoie un message privé
    try:
        
        with open("video.mp4", "rb") as video:
            await context.bot.send_video(chat_id=user_id, video=video)

        
        await context.bot.send_message(
        chat_id=user_id,
        text="🔥 Clique sur /maFormation !",
        )

        
    except Exception as e:
        print(f"Impossible d’envoyer un message à {user_id} : {e}")
     

async def user_imformation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user



token = os.getenv("token")

# États du formulaire
NAME, PHONE, COUNTRY = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    if chat_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Ta préinscription va se dérouler en 3 étapes.\n...Ça prendra maximum 2 minutes, alors on y va à fond !\n\nÉtape 1/3 :👤  Quel est ton nom et prénom ?\n\n..."
        )
    else:
        await update.message.reply_text(
            "Ta préinscription va se dérouler en 3 étapes.\n...Ça prendra maximum 2 minutes, alors on y va à fond !\n\nÉtape 1/3 :👤  Quel est ton nom et prénom ?\n\n..."
        )
    return NAME

async def starts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    await update.message.reply_text(
    "Ta préinscription va se dérouler en 3 étapes.\n"
        "Ça prendra maximum 2 minutes, alors on y va à fond !\n\n"
        "\nÉtape 1/3 :👤  Quel est ton nom et prénom ?\n\n...")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Étape 2/3 :📞 Quel est ton numéro de téléphone ?"
                                    "\n\n Format international recommandé, ex : +22997203304")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("Étape 3/3 :🌍 Dans quel pays vis-tu ?")
    return COUNTRY

async def get_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["country"] = update.message.text
    data = context.user_data
    await update.message.reply_text(
        f"✅ Inscription terminée !\n\n"
        f"Nom : {data['name']}\n"
        f"Téléphone : {data['phone']}\n"
        f"Pays : {data['country']}"
    )

    save_user(data["name"], data["phone"], data["country"])
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Aller sur rmiclass.net", url="https://app.rmiclass.net/reff/538699")]
    ])

    await update.message.reply_text(
        "✅ Votre préinscription a été effectuée avec succès !\n"
        "Rends-toi sur https://app.rmiclass.net/reff/538699, crée ton compte, puis découvre notre initiation au trading.",
        reply_markup=keyboard
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Formulaire annulé.")
    return ConversationHandler.END


async def handle_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("⛔ Désolé, cette commande est réservée à l’administrateur.")
        return

    await export_and_send_excel(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cta_start":
       
        chat_id = query.from_user.id
        await start(update, context, chat_id=chat_id)
        




if __name__ == '__main__':

    init_db()
    
    app = Application.builder().token(token).build()
    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("/maFormation", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("data", handle_data_command))

    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(conv_handler)
    print('running...')
    
    app.run_polling(poll_interval=1)
