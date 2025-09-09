import pandas as pd
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from database.database import update_mail_status, mail_user

import os
from telegram import Update
from telegram.ext import ContextTypes

import asyncio

ADMIN_ID = 571718066

EXCEL_FILE = "contacts.xlsx"
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

        # Sauvegarde dans la base
        mail_user(nom, email, token)

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

        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            server.sendmail(SMTP_USER, email, msg.as_string())
            update_mail_status(email)
            n +=1
            print(f"✅ Mail envoyé à {email}")
        except Exception as e:
            print(f"❌ Erreur pour {email} : {e}")

        # petite pause pour éviter de surcharger le serveur SMTP
        await asyncio.sleep(1)

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

    
