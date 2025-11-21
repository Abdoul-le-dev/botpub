import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Update, InputFile
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
import asyncio
import secrets
from constance import GET_MAIL
from database.database import get_mail_and_name, mail_user, update_mail_status, find_category_duplicates,delete_user_duplicates
ADMIN_ID = 571718066
def send_consent_email(to_email, username):
    """
    Envoie automatiquement un e-mail de confirmation de consentement
    après que l'utilisateur ait accepté les clauses du challenge.
    """

    # --- Paramètres de l'expéditeur ---
    from_email = "challenge10000usd@iastreamnow.com"
    from_password = "Testing@1#test"  # App Password recommandé pour Gmail

    # --- Création du message ---
    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = "Confirmation de votre inscription au Challenge Trading 200 → 10.000 USD"

    # --- Corps du mail ---
    body = f"""
Cher participant {username},

✅ CONFIRMATION DE VOTRE CONSENTEMENT AU CHALLENGE TRADING 200 → 10.000 USD : 

Nous confirmons que vous avez accepté les clauses du Challenge Trading 200 → 10.000 USD .

📌 INSTRUCTIONS ET SUIVI DU CHALLENGE :

- Épinglez le bot sur Telegram.  
- Activez les notifications pour ne manquer aucune information.  
- Dès que le challenge commencera, vous recevrez **un message directement dans Telegram** ou **e-mail de rappel**.

Merci pour votre confiance.

Je nous souhaite bonne chance et restez attentif aux instructions !

Cordialement,  
L’assistant bot du coach Fiacre KPANOU
"""

    msg.attach(MIMEText(body, 'plain'))

    # --- Envoi de l'e-mail ---
    try:
        #with smtplib.SMTP('smtp.gmail.com', 587) as server:
       with smtplib.SMTP_SSL("smtp.hostinger.com", 465) as server:
            server.login(from_email, from_password)
            server.send_message(msg)
            print(f"E-mail de consentement envoyé à {to_email}")
            return True
    except Exception as e:
        print(f"Erreur lors de l'envoi de l'e-mail à {to_email} : {e}")
        #fichier log pour les erreurs d'envoi
        with open("email_errors.log", "a") as log_file:
            log_file.write(f"Erreur pour {to_email} : {e}\n")
        return False



async def send_short_link(update: Update, context: ContextTypes.DEFAULT_TYPE,):

    if not update.message or not update.message.text or "@" not in update.message.text:
        await update.message.reply_text("❌ Merci d’envoyer une adresse email valide.")
        return GET_MAIL
    # Enregistre l'email de l'utilisateur
    to_email= update.message.text
    """
    Envoie automatiquement un e-mail de link short
    """

    # --- Paramètres de l'expéditeur ---
    from_email = "fiacrekpanou@fiacrekpanoutrade.com"
    from_password = "Assistant@#1"  # App Password recommandé pour Gmail

    # --- Création du message ---
    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = "Confirmation de votre inscription "
    token = secrets.token_urlsafe(16)
    # --- Corps du mail ---

    body = f"""
Cher(e) participant(e),

Félicitations 🎉 Votre inscription à la RMI CLASS – Saison des Performances a bien été confirmée.

Vous venez d’intégrer un programme intensif d’1 mois, conçu pour accélérer vos résultats en trading, renforcer vos compétences et vous exposer aux meilleures opportunités du marché (Forex & Indices).

Voici votre lien d’accès personnel pour rejoindre l’espace réservé aux membres :

🔗 https://t.me/FIACRE_D_KPANOU_ASSISTANCE_bot?start={token}

⚠️ Ce lien est strictement personnel. Ne le partagez en aucun cas. Tout partage entraînera votre retrait immédiat du programme, sans remboursement.

Étapes à suivre :
1. Cliquez sur le lien ci-dessus
2. Démarrez le bot
3. Suivez les instructions pour accéder au challenge

Donnez tout 💯

À très vite à l’intérieur.

Cordialement,  
Assistant Bot du Coach Fiacre KPANOU
"""


    msg.attach(MIMEText(body, 'plain'))

    # --- Envoi de l'e-mail ---
    try:
        # Fonction sync
        def send_email_sync():
            with smtplib.SMTP_SSL("smtp.hostinger.com", 465) as server:
                server.login(from_email, from_password)
                server.send_message(msg)
                mail_user('manuel', to_email, token)
                update_mail_status(to_email)

        # Exécuter la fonction sync sans bloquer le bot
        await asyncio.get_running_loop().run_in_executor(None, send_email_sync)

        await update.message.reply_text(f"✅ Succès de l'envoi de l'e-mail à {to_email}.")

        return ConversationHandler.END

    except Exception as e:
        print(f"Erreur pour {to_email} : {e}")
        with open("email_errors.log", "a") as log_file:
            log_file.write(f"Erreur pour {to_email} : {e}\n")
        await update.message.reply_text(f"❌ Échec de l'envoi de l'e-mail à {to_email} : {e}")
        return ConversationHandler.END

async def send_mail_admin(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
            "`📩 *E-mail du destinataire(shoort link)*`\n\n",
            parse_mode="Markdown"
        )



    return GET_MAIL


async def check_user_doublon(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return

    try:
        args =Context.args[0]
    except:
        await update.message.reply_text("❌ une categorie est attendu.")
        return

    

    resultat = find_category_duplicates(args)

    await update.message.reply_text(
            f"{resultat}",
            parse_mode="Markdown"
        )
    
async def delete_user_doublon(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    if  update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return

    try:
        args = int(Context.args[0])
    except:
        await update.message.reply_text("❌ Fournis un ID Telegram valide après la commande.")
        return

    resultat = delete_user_duplicates(args)

    await update.message.reply_text(
            f"{resultat}",
            parse_mode="Markdown"
        )    