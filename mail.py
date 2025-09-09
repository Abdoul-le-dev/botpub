import pandas as pd
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from database.database import update_mail_status, mail_user, get_unsent_emails,mail_token_utilise
from email.mime.base import MIMEBase
from email import encoders
import json
from pathlib import Path
import json
from pathlib import Path
import os
from telegram import Update
from telegram.ext import ContextTypes

import asyncio

ADMIN_ID = 571718066

EXCEL_FILE = "liste.xlsx"
SHEET_NAME = "Feuil1"
SMTP_SERVER = "smtp.hostinger.com"
SMTP_PORT = 465
SMTP_USER = "challenge10000usd@iastreamnow.com"
SMTP_PASSWORD = "Testing@1#test"


a = 0 
async def send_email_background():
    n = 0 
    """Envoi des emails en arrière-plan"""
    df = pd.read_excel(EXCEL_FILE)
    contacts = df[['CUSTOMERS_FIRSTNAME', 'CUSTOMER_EMAIL']]
    contacts['Token'] = [secrets.token_urlsafe(16) for _ in range(len(contacts))]

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SMTP_USER, SMTP_PASSWORD)
    except Exception as e:
        print(f"❌ Impossible de se connecter au serveur SMTP : {e}")
        return
    

    for index, row in contacts.iterrows():
        nom = row['CUSTOMERS_FIRSTNAME']
        email = row['CUSTOMER_EMAIL']
        token = row['Token']

       

        subject = "✅ Paiement confirmé – Accès à votre Challenge"

        body = f"""
Bonsoir {nom},

Félicitations 🎉 Nous avons bien reçu votre paiement et vous êtes désormais inscrit(e) pour notre challenge exclusif.

Voici votre lien d’accès unique pour rejoindre le challenge :

🔗 https://t.me/FIACRE_D_KPANOU_ASSISTANCE_bot?start={token}

⚠️ Ce lien est strictement personnel. Ne le partagez sous aucun prétexte : tout partage entraînera votre retrait immédiat de la liste des participants.

Cliquez sur le lien, suivez les instructions du bot et profitez pleinement de cette expérience unique.

À très vite de l'autre côté !

Cordialement,  
Assistant Bot IA du Coach Fiacre KPANOU
"""

        if mail_token_utilise(email):
            
            msg = MIMEMultipart()
            msg['From'] = SMTP_USER
            msg['To'] = email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))

            try:
                server.sendmail(SMTP_USER, email, msg.as_string())
                # Sauvegarde dans la base
                mail_user(nom, email, token)
                update_mail_status(email)
                n +=1
                print(f"✅ Mail envoyé à {email}")
            except Exception as e:
                print(f"❌ Erreur pour {email} : {e}")
                enregistrer_mail_non_envoye(email)
                

            # petite pause pour éviter de surcharger le serveur SMTP
            await asyncio.sleep(5)

            a = n

    server.quit()
   
    contacts.to_excel("contacts_avec_tokens.xlsx", index=False)
    print("✅ Tous les mails ont été envoyés.")

    return  n 


async def send_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande Telegram qui déclenche l’envoi en arrière-plan"""
    

    await update.message.reply_text("📨 L’envoi des mails a été lancé en arrière-plan...")
    # Lancer la tâche sans bloquer le bot
    task = asyncio.create_task(send_email_background())
    mails_envoyes = await task
    

    await update.message.reply_text(f"📨 Nombre total de mails envoyés : {mails_envoyes} envoyées")

async def send_none_email(update: Update, context: ContextTypes.DEFAULT_TYPE):  

    row =  get_unsent_emails()  

    for r in row:

            await update.message.reply_text(f"📨 mail non envoyer a: {r[2]}")
            



FAILED_MAILS_FILE = "mails.json"

def enregistrer_mail_non_envoye(email: str):
    """Enregistre un mail non envoyé dans un fichier JSON (liste simple)"""
    data = []

    # Si le fichier existe déjà, on lit le contenu existant
    if Path(FAILED_MAILS_FILE).exists():
        with open(FAILED_MAILS_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = []

    # Ajoute le nouvel email si pas déjà dans la liste
    if email not in data:
        data.append(email)

    # Sauvegarde la liste mise à jour
    with open(FAILED_MAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"📄 Mail non envoyé enregistré dans {FAILED_MAILS_FILE} : {email}")

TO_EMAIL = 'fiacrecontact@gmail.com'
DB_FILE = "/home/ubuntu/botbienvenu/botpub/preinscriptions.db"
def envoyer_base_par_email():
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = TO_EMAIL
    msg['Subject'] = "📄 Sauvegarde automatique de la base de données"

    # Attacher le fichier SQLite
    part = MIMEBase('application', 'octet-stream')
    with open(DB_FILE, 'rb') as f:
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(DB_FILE)}')
    msg.attach(part)

    # Envoyer l’email
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, TO_EMAIL, msg.as_string())
        print(f"✅ Base de données envoyée avec succès à {TO_EMAIL}")
    except Exception as e:
        print(f"❌ Erreur lors de l’envoi : {e}")


