import json 
import sqlite3

from telegram import InputFile, Update, context
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler

ADMIN_ID = 571718066

db_path = "preinscriptions.db"


conn = sqlite3.connect(db_path)

def json_exam_user():

    # Connexion à la base

    cursor = conn.cursor()

    # Sélection uniquement des enregistrements où exam_id = 2
    cursor.execute("SELECT * FROM exam_user WHERE exam_id = ?", (2,))

    # Récupère toutes les lignes + noms de colonnes
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]

    # Convertit les lignes en dictionnaires
    data = [dict(zip(columns, row)) for row in rows]

    # Sauvegarde dans un fichier JSON
    output_file = "exam_user_exam2.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    conn.close()

    return output_file


async def send_file_user_exam(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    user_id =update.effective_user.id 

    if  update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return


    file_name = json_exam_user()

    with open(file_name, "rb") as f:
        await context.bot.send_document(
            chat_id=user_id,
            document=InputFile(f, filename=file_name),
            caption="Voici le JSON de l'exam_id=2 📄"
        )

    return CommandHandler.End    