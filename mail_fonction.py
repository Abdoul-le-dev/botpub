import pandas as pd
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import json
from pathlib import Path
import json
from pathlib import Path
import os
from database.database import get_user_under_limit,update_mail_count, add_new_user
from telegram import Update
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
USER,PWD = range(2)
import asyncio

import asyncio

SMTP_SERVER = "smtp.hostinger.com"
SMTP_PORT = 465



import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders



async def envoyer_email(subjet, msge, mail, DB_FILE=None):

    # Récupérer un utilisateur disponible
    SMTP_USER, SMTP_PASSWORD = get_user_under_limit()

    if SMTP_USER is None:
        print("❌ Aucun utilisateur disponible pour l'envoi. Notification à l'admin.")
        return

    # Création du message
    msg = MIMEMultipart()
    msg['From'] = SMTP_USER
    msg['To'] = mail
    msg['Subject'] = subjet

    # Ajouter le corps du mail
    msg.attach(MIMEText(msge, 'plain'))

    # Ajouter la pièce jointe si fournie
    if DB_FILE:
        part = MIMEBase('application', 'octet-stream')
        with open(DB_FILE, 'rb') as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(DB_FILE)}')
        msg.attach(part)

    # Envoyer l’email via SMTP SSL
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, mail, msg.as_string())
            update_mail_count(SMTP_USER)
        print(f"✅ MAIL ENVOYER : {mail}")
        
        return 1
    except Exception as e:
        print(f"❌ Erreur lors de l’envoi : {e}")

        return 0


async def save_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
            "`📩 *E-mail:*`\n\n",
            parse_mode="Markdown"
        )



    return USER

async def save_mail_id(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.data['mail'] = update.message.text

    await update.message.reply_text(
            "`📩 *Psw:*`\n\n",
            parse_mode="Markdown"
        )



    return PWD

async def save_mail_pwd(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.data['psw'] = update.message.text

    add_new_user(context.data.get('mail'), context.data.get('pwd'))

    return ConversationHandler.END



    


