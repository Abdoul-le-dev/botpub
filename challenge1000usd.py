import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Update, InputFile
from database.database import get_mail_and_name

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
- Dès que le challenge commencera, vous recevrez **un message directement dans Telegram** ainsi qu’un **e-mail de rappel**.

Bonne chance et restez attentif aux instructions !

Cordialement,  
L’assistant bot IA du coach Fiacre KPANOU
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

        