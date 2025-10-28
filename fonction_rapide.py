import json 
import sqlite3
from mail_fonction import  envoyer_email

import qrcode, os

from database.database import get_user_exam,get_user_exams

from telegram import InputFile, Update
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
        await Context.bot.send_document(
            chat_id=user_id,
            document=InputFile(f, filename=file_name),
            caption="Voici le JSON de l'exam_id=2 📄"
        )

    return CommandHandler.End



async def qr_code_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id =update.effective_user.id

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return

    try:
        email = context.args[0]
    except:
        await update.message.reply_text("❌ Fournis un mail Telegram valide après la commande.")
        return
    
   

    
    data_user = get_user_exams(user_id )

    result = await send_exam_result_email(data_user['qr_code'],data_user['email'], data_user['user_name'], data_user['last_name'],
                 'Test Niveau b',data_user['moyenne'], data_user['created_at']  )
    
    if result :

        await update.message.reply_text("code qr renvoyer avec succès")

    else : 
        
        await update.message.reply_text("❌ echec")    

    return ConversationHandler.END    
       



async def send_exam_result_email(qr_codes,user_email, user_name, user_surname, exam_name, note, created_at):
    # 1️⃣ Génération du contenu du QR code
    user_id_unique = qr_codes
    exam_date = created_at
    qr_data = f"""
Nom: {user_name}
Prénom: {user_surname}
Mail: {user_email}
Examen: {exam_name}
Note: {note}/20
Id Unique : {user_id_unique}
Date: {exam_date}
"""

    # 2️⃣ Création du QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data.strip())
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

   

    # 3️⃣ Préparation du dossier pour stocker le QR code
    qr_dir = "qrcode"  # dossier relatif au projet
    os.makedirs(qr_dir, exist_ok=True)  # crée le dossier s'il n'existe pas

    # 4️⃣ Nom du fichier QR
    file_path = os.path.join(qr_dir, f"{user_name}_{user_surname}_qr.png")
    img.save(file_path)

    # 5️⃣ Préparation de l’e-mail
    subject = f"🎓 Résultat de ton examen — {exam_name}"
    msg = f"""
Salut {user_name},

Félicitations 🎉 !

Voici ton résultat :
- Examen : {exam_name}
- Note totale : {note}/20
- Date : {exam_date}

Un QR code contenant tes informations est joint à cet e-mail. 
Garde-le précieusement, il te servira pour ton prochain coaching.

Bien à toi,
Fiacre KPANOU
"""

    # 6️⃣ Envoi de l’email (fonction existante)
    result = await envoyer_email(subject, msg, user_email, file_path)
    return result   


