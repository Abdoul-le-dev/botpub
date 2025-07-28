import os
from telegram import Update, InputFile
from jeu import export_and_send_pdf
from database.database import init_db
from database.database import save_user
from database.database import user_exists
from database.database import save_message
from database.database import update_user_info
from database.database import add_categorie
from database.database import get_file_id
from database.database import save_file_id
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

from constance import NAME, PHONE, COUNTRY, LEVEL, WHAT,WHY

async def wait_5_seconds():
    await asyncio.sleep(20)
async def wait_5_minutes():
    await asyncio.sleep(300)    


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    user = update.effective_user
    user_id = user.id


   

  

    args = context.args
    print(args)
    #PromoV100
    '''
    if args and args[0] == "V100":
        if user_has_categorie(user_id):
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "👌 **Tu as terminé ton process ✅**\n"
                    "*Sois patient, l'ami, c'est pour bientôt !*\n\n"
                    "📌 *Épingle ce canal* pour rester à l'affût des **nouvelles informations**."
                ),
                parse_mode="Markdown"
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

    '''
            
    chat_id = update.effective_chat.id if chat_id is None else chat_id
    if user_exists(user_id):
        await update.message.reply_text("Tu es déjà inscrit ✅")
    else:
        
        if chat_id:
           
        
            video_name = "welcome_messagess"

            file_id = get_file_id(video_name)

            if file_id:
                # Réutiliser le file_id
                #await context.bot.send_video(chat_id=chat_id, video=file_id, caption="Bienvenue ! 🎉")

                await update.message.reply_text(
               
                "🔥 Dis-moi pourquoi t'intèresse tu au trading ? :\n\n"
                "1️⃣ POUR EN FAIRE UNE SOURCE DE REVENU PRINCIPALE\n\n"
                "2️⃣ POUR EN FAIRE UNE SOURCE DE REVENU SECONDAIRE\n\n"
                "3️⃣ POUR ATTEINDRE UNE LIBERTE FINANCIERE COMPLETE\n\n"
                "🚨 ATTENTION 🚨\n"
                "✍️ Réponds maintenant par **1**, **2** ou **3**.\n"
                
                ,parse_mode='Markdown'
                
            )


                
            else:
                # Envoyer depuis fichier local, puis sauvegarder le file_id
                video_path = "welcome.mp4"
               # msg = await context.bot.send_video(chat_id=chat_id, video=video_path, caption="Bienvenue ! 🎉")
               # new_file_id = msg.video.file_id
                #save_file_id(video_name, new_file_id)
                print("1")

                await update.message.reply_text(
               
                "🔥 Dis-moi pourquoi t'intèresse tu au trading ? :\n\n"
                "1️⃣ POUR EN FAIRE UNE SOURCE DE REVENU PRINCIPALE\n\n"
                "2️⃣ POUR EN FAIRE UNE SOURCE DE REVENU SECONDAIRE\n\n"
                "3️⃣ POUR ATTEINDRE UNE LIBERTE FINANCIERE COMPLETE\n\n"
                "🚨 ATTENTION 🚨\n"
                "✍️ Réponds maintenant par **1**, **2** ou **3**.\n"
                
                ,parse_mode='Markdown'
                
            )


        
        return WHY

async def get_why(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = update.message.text.strip()

    why_map = {
        "1": "Source de revenu principale",
        "2": "Source de revenu secondaire",
        "3": "Liberté financière complète"
    }

    if response not in why_map:
        await update.message.reply_text(
            "❌ Réponse invalide. Merci de répondre uniquement avec : 1, 2 ou 3.\n\n"
            "1️⃣ Source de revenu principale\n"
            "2️⃣ Source de revenu secondaire\n"
            "3️⃣ Liberté financière complète"
        )
        return WHY

    context.user_data["why"] = why_map[response]

    await update.message.reply_text(
    "🎯Parfait, chacun rejoint ce canal avec une attente différente… \n\n"
    "Alors dis-moi ce que tu espères trouver ici :\n\n"
    "1️⃣ UNE INITIATION GRATUITE POUR DÉCOUVRIR LE TRADING\n\n"
    "2️⃣ DES OPPORTUNITÉS DE TRADE CONCRÈTES ET RÉGULIÈRES\n\n"
    "3️⃣ UN ACCOMPAGNEMENT PLUS POUSSÉ, AVEC DU COACHING\n\n"

    "Je suis impatient 🔥🔥🔥"
)

    return WHAT

async def get_what(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = update.message.text.strip()

    expectation_map = {
        "1": "Une initiation gratuite",
        "2": "Des opportunités de trade",
        "3": "Un coaching"
    }

    if response not in expectation_map:
        await update.message.reply_text(
            "❌ Réponse invalide. Merci de répondre uniquement avec : 1, 2 ou 3.\n\n"
            "1️⃣ Une initiation gratuite\n"
            "2️⃣ Des opportunités de trade\n"
            "3️⃣ Un coaching"
        )
        return WHAT

    context.user_data["what"] = expectation_map[response]

    # Ensuite tu peux rediriger vers la prochaine étape, par exemple vers `get_level`
    await update.message.reply_text(
    "📊 J’ai besoin de connaître ton niveau actuel en trading.\n\n"
    "Dis-moi où tu te situes aujourd’hui :\n\n"
    "1️⃣ DÉBUTANT – JE DÉCOUVRE À PEINE LE TRADING\n\n"
    "2️⃣ INTERMÉDIAIRE – J’AI DES BASES, MAIS JE NE SUIS PAS ENCORE RENTABLE\n\n"
    "3️⃣ AVANCÉ – JE SUIS DÉJÀ RENTABLE ET JE CHERCHE À ALLER PLUS LOIN\n\n"

   
    "Peu importe où tu démarres… c’est la suite qui compte 🔥"
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

    await update.message.reply_text(
    "👋 J’aimerais savoir comment t’appeler.\n\n"
    "✍️ Envoie-moi ton prénom et ton nom.\n"
    "📌 Exemple : Fiacre Kpanou\n\n"
    "✅ On va sûrement échanger, alors autant se connaître 😉",
    parse_mode="Markdown"
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
    await update.message.reply_text(
    "📞 Quel est ton numéro de téléphone ?\n\n"
    "🌍 Voici le mien : +22997203304 \n"
    
)

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

    user = update.effective_user
    user_id = user.id
    context.user_data["country"] = update.message.text
    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer le nom de ton pays."
            )
        return COUNTRY
    # Enregistre les informations de l'utilisateur dans la base de données
    save_user(
            context.user_data["name"],
            context.user_data["phone"],
            context.user_data["country"],
            user_id,
            email=context.user_data.get("email"),
            motivation=context.user_data.get("motivation"),
            level=context.user_data.get("level"),
            why= context.user_data.get("why"),
            what= context.user_data.get("what")

        )  

    await update.message.reply_text(
    "🎯 Tu es allé au bout de cet échange.\n\n"
    "Dans une époque où l’attention s’effondre, tu choisis de t’investir dans ce qui compte vraiment.\n\n"
    "📌 Épingle mon assistant bot — car très bientôt, il aura de belles surprises pour toi… et pour toute notre communauté. 🔥"
    "🚀 Ce n’est que le début. Garde les yeux ouverts 👀 je suis Fiacre KPANOU"
)

    
    
    if context.user_data.get("level") =="Débutant":
        await wait_5_seconds()
        await update.message.reply_text(
            "🎓 Tu m’as indiqué être débutant ? Parfait !\n\n"
            "J’ai justement préparé une initiation 100% gratuite pour t’aider à poser de bonnes bases dans le trading.\n\n"
            "👇 Clique ici pour créer ton compte et suivre le cours :\n"
            "🔗 https://app.rmiclass.net/course/Initiation-au-Trading \n\n"
            "📬 Tu veux m’écrire ou me laisser un message ? C’est ici : @Fiacrekpanou",
            parse_mode="Markdown"
        )
    


    
    return ConversationHandler.END
    
    data = context.user_data
    '''
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

    '''    

    
'''
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

'''

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
            "⏳ Ton lien a expiré !\n\n"
            "⏳⏰ En attendant dimanche, profite *GRATUITEMENT* de notre initiation au trading ici 👉\n\n"
            "Rends-toi sur https://app.rmiclass.net/reff/538699, crée ton compte, puis découvre notre initiation au trading."
        ),
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
