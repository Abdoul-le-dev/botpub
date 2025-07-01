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
from telegram.error import TimedOut

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

from constance import NAME, PHONE, COUNTRY, LEVEL, EMAIL, MOTIVATION

async def wait_5_seconds():
    await asyncio.sleep(5)
async def wait_5_minutes():
    await asyncio.sleep(300)    

# États du formulaire
#NAME, PHONE, COUNTRY, LEVEL,EMAIL,MOTIVATION = range(6)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    user = update.effective_user
    user_id = user.id

    args = context.args
    print(args)
    #PromoV100
    if args and args[0] == "V100":
        if user_has_categorie(user_id):
            await context.bot.send_message(
                chat_id=user_id,
                text="👌 **Tu as terminé ton process ✅**\n"
                "*Sois patient, l\\'ami, c\\'est pour bientôt \\!*\n\n"
                "📌 __Épingle ce canal__ pour rester à l\\'affût des **nouvelles informations**\\.",
                parse_mode="MarkdownV2"
        )



            return ConversationHandler.END

        else:

            if user_exists(user_id):

                context.user_data["args"] = args[0]
                type = "V100"

                try:
                    await update.message.reply_text("🎉 Félicitations à toi, jeune trader ambitieux !\n\n"
                            "Je viens de valider ton paiement : tout est parfait ✅.\n\n"
                            "Tu fais désormais partie des privilégiés qui accèderont à la formation V100 Master — un programme exclusif, direct et transformateur.\n\n"
                            "🔥 Dans quelques jours, tu vas vivre une immersion intense où l’on te révèle tout ce que tu dois savoir sur le V100 : sans filtre, sans blabla.\n\n"
                            "💡 Stratégie, vision, passage à l’action — on va droit au but.\n\n"
                            "---\n"
                            "Mais avant de t’ouvrir les portes du canal privé,\n\n"
                            "on aimerait te connaître un peu plus…\n\n"
                            "Pour que cette séance soit 100% adaptée à ton profil\n\n"
                            "et que tu en ressortes boosté, concentré et prêt à passer un cap.\n\n"
                            "🚀📈🔥"
                        )
                    

                    await wait_5_seconds()


                    await update.message.reply_text("__**😂 Ne réponds pas au message précédent, je sais que tu es ravi, moi aussi d’ailleurs !**__\n\n"
                            "📧 `Pour traiter tes demandes en priorité, donne-nous ton adresse mail, vu que tu fais maintenant partie de la famille.`\n\n"
                            "➡️ `Envoie uniquement ton mail, par exemple : fiacrekpanou@gmail.com`",
                            parse_mode='Markdown'
                        )

                    return EMAIL   
                except TimedOut: 

                    await context.bot.send_message(
                        chat_id=user_id,
                        text ="__**😂 Ne réponds pas au message précédent, je sais que tu es ravi, moi aussi d’ailleurs !**__\n\n"
                                    "📧 `Pour traiter tes demandes en priorité, donne-nous ton adresse mail, vu que tu fais maintenant partie de la famille.`\n\n"
                                    "➡️ `Envoie uniquement ton mail, par exemple : fiacrekpanou@gmail.com`",
                                    parse_mode='Markdown'
                                )

                    return EMAIL 


            else:

                context.user_data["args"] = args[0]

                await update.message.reply_text("🎉 Félicitations à toi, jeune trader ambitieux !\n\n"
                        "Nous venons de valider ta facture : tout est parfait ✅.\n\n"
                        "Tu viens de franchir une étape clé en rejoignant la formation V100 Master — un programme exclusif, clair et puissant.\n\n"
                        "🔥 Très bientôt, tu vas plonger dans une immersion totale où chaque détail du V100 te sera révélé, sans détour ni perte de temps.\n\n"
                        "💡 Stratégie, vision, passage à l’action — ici, on va droit au but.\n\n"
                        "---\n"
                        "Comme c’est ta première fois avec notre assistant,\n"
                        "🚀 on va t’enregistrer rapidement pour pouvoir t’ajouter au canal privé.\n\n"
                        "✅ Suis les étapes suivantes pour t’enregistrer et démarre cette aventure avec nous !"
                    )
                

                

                await update.message.reply_text("Étape 1 sur 5...\n\n"
                        "👤 Envoie uniquement ton nom et prénom, par exemple : Fiacre Kpanou\n\n"
                        "Juste ton nom et prénom, rien d’autre."
                    )
                    
                
                return NAME


            

    if user_exists(user_id):
        await update.message.reply_text("Tu es déjà inscrit ✅")
    else:
        if chat_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Ta préinscription va se dérouler en 3 étapes ⏳✨ pour le prochain jeu concours 🎉🎁.\n\n...Ça prendra maximum 2 minutes, alors on y va à fond !\n\n👤  Quel est ton nom et prénom ?\n\n..."
            )
        else:
            await update.message.reply_text(
                "Ta préinscription va se dérouler en quelque petite étapes ⏳✨ pour le prochain jeu concours 🎉🎁.\n\n Ça prendra maximum 2 minutes, alors on y va à fond !\n\n👤  Quel est ton nom et prénom ?\n\n..."
            )
        return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer un texte valide."
            )
        return NAME
    
    context.user_data["name"] = update.message.text
    await update.message.reply_text("📞 Quel est ton numéro de téléphone ?"
                                    "\n\n Format international recommandé, ex : +22997203304")
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer ton numéro."
            )
        return PHONE
    
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("🌍 Dans quel pays vis-tu ?")
    return COUNTRY

async def get_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["country"] = update.message.text
    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer le nom de ton pays."
            )
        return COUNTRY
    
    data = context.user_data
    if "args" in data and data["args"] == "V100":

        print(f"Argument trouvé : {data['args']}")

        await update.message.reply_text(
            "📩 Entre uniquement ton adresse e-mail (exemple : fiacreKpanou@gmail.com)"
        )


        return EMAIL
    else:

        data = context.user_data
        await update.message.reply_text(
            f"✅ Bravo, votre inscription est confirmée ! 🥳✅\n\n"
            f"Nom : {data['name']}\n"
            f"Téléphone : {data['phone']}\n"
            f"Pays : {data['country']}"
        )
        user = update.effective_user
        user_id = user.id
        save_user(data["name"], data["phone"], data["country"],user_id)

    
        keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎓 Suivre la formation gratuite 📈 ", url="https://app.rmiclass.net/reff/538699")]
            ])

        await update.message.reply_text(
            "✅🎉 Inscription au prochain jeu concours validée ! 🎊🔥!\n\n"
            "📌👉 Épingle vite notre assistant bot pour recevoir toutes les notif’s importantes 📲🔔 \n\n"
            "⏳⏰ En attendant dimanche, profite GRATUITEMENT de notre initiation au trading ici 👉 \n\n"
            "Rends-toi sur https://app.rmiclass.net/reff/538699, crée ton compte, puis découvre notre initiation au trading.",
            reply_markup=keyboard
        )
        return ConversationHandler.END

    

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Enregistre l'email de l'utilisateur
    context.user_data["email"] = update.message.text

    # Demande la motivation pour rejoindre la formation
    await update.message.reply_text(
        "🤔 Pourquoi as-tu rejoint cette formation ?\n\n"
        "Donne-nous ta motivation en quelques mots."
    )
    return MOTIVATION

async def get_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Enregistre la motivation de l’utilisateur
    context.user_data["motivation"] = update.message.text

    # Demande maintenant le niveau en trading
    await update.message.reply_text(
        "📊 Pour mieux t’accompagner, dis-nous où tu te situes en trading :\n\n"
        "1️⃣ Débutant (tu découvres à peine l’univers du trading)\n"
        "2️⃣ Intermédiaire (tu trades, mais t’es pas encore rentable)\n"
        "3️⃣ Rentable (tu gagnes déjà régulièrement)\n\n"
        "✍️ Réponds simplement avec : 1, 2 ou 3"
    )
    return LEVEL

async def get_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = update.message.text.strip()

    niveau_map = {
        "1": "Débutant",
        "2": "Intermédiaire (non rentable)",
        "3": "Rentable"
    }

    if response not in niveau_map:
        await update.message.reply_text(
            "❌ Réponse invalide. Merci de répondre uniquement avec : 1, 2 ou 3."
        )
        return LEVEL

    context.user_data["level"] = niveau_map[response]

    user = update.effective_user
    user_id = user.id
    # Enregistre les informations de l'utilisateur dans la base de données

    if user_exists(user_id):

        update_user_info(user_id,context.user_data.get("email"),context.user_data.get("motivation"),context.user_data.get("level"))

        print( 
            user_id,
            context.user_data.get("email"),
            context.user_data.get("motivation"),
            context.user_data.get("level"))  
        
    else :
        save_user(
            context.user_data["name"],
            context.user_data["phone"],
            context.user_data["country"],
            user_id,
            email=context.user_data.get("email"),
            motivation=context.user_data.get("motivation"),
            level=context.user_data.get("level")
        )  

        print( context.user_data["name"],
            context.user_data["phone"],
            context.user_data["country"],
            user_id,
            context.user_data.get("email"),
            context.user_data.get("motivation"),
            context.user_data.get("level"))

    #Ajoute la catégorie "V100" à l'utilisateur    

    if not user_has_categorie(user_id):
        add_categorie(user_id, "V100")
    # Envoie un message de confirmation     

    chat_id=update.effective_chat.id

    message = await update.message.reply_text(
    "🔗 *Voici ton lien unique d’accès au canal :*\n"
    "[👉 Rejoins le canal maintenant](https://t.me/+Wwu28BoDMOUzMTQ0)\n\n"
    "⏳ La formation démarre bientôt, sois prêt !\n\n"
    "⚠️ Ce lien est personnel. Si quelqu’un d’autre l’utilise, tu seras automatiquement banni.",
    parse_mode="Markdown"
)

    asyncio.create_task(delete_and_offer_later(context, chat_id, message.message_id))

   


    return ConversationHandler.END

async def delete_and_offer_later(context, chat_id, message_id):
    await asyncio.sleep(300)  # 5 minutes

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"❌ Erreur suppression : {e}")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 Suivre la formation gratuite 📈", url="https://app.rmiclass.net/reff/538699")]
    ])

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⏳ Ton lien a expiré\\!\n\n"
            "⏳⏰ En attendant dimanche, profite *GRATUITEMENT* de notre initiation au trading ici 👉\n\n"
            "Rends\\-toi sur https://app.rmiclass.net/reff/538699, crée ton compte, puis découvre notre initiation au trading\\."
        ),
        reply_markup=keyboard,
        parse_mode="MarkdownV2"
    )