import os
from telegram import Update, InputFile
from jeu import export_and_send_pdf
from database.database import init_db
from database.database import save_user
from database.database import user_exists
from database.database import save_message,verify_categorie
from database.database import update_user_info
from database.database import add_categorie
from database.database import get_file_id
from database.database import save_file_id, create_args
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

from constance import NAME, PHONE, COUNTRY, LEVEL, WHAT,WHY, EMAIL, EXPECTATIONS,DISCOVERY

async def wait_5_seconds():
    await asyncio.sleep(10)
async def wait_5_minutes():
    await asyncio.sleep(300)    


async def start(update: Update, Context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    user = update.effective_user
    user_id = user.id

    args = Context.args
    print(args)
    #PromoV100
    name = "leseminaire" 
    if args and args[0] != name:
        if verify_categorie(args[0]) != None:
            #and user_has_categorie(user_id,name):

            if verify_categorie(args[0]) == 'non_verify':   
                await Context.bot.send_message(
                    chat_id=user_id,
                    text = (
                        "L'exercice du jour n'est pas encore valider merci de patienté.\n"
                        
                    ),
                    parse_mode="Markdown"  
                )
                return ConversationHandler.END

            print(create_args(user_id,args[0], 0))

            if create_args(user_id,args[0], 0) == 'already':
                await update.message.reply_text(
                    "Tu as déja traiter tes exercices"
                )
                return ConversationHandler.END

            await Context.bot.send_message(
                chat_id=user_id,
                text=(
                    "__**🔥 Le challenge du séminaire démarre maintenant !**__\n\n"
                    "💪 Clique sur :\n"
                    "/commencerMesExerciesDuSeminaire pour relever le défi.\n\n"
                    "🎯 10 jours, 100 points… 🚀"
                ),
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        else:
            if user_has_categorie(user_id,name) == None:
                await Context.bot.send_message(
                    chat_id=user_id,    
                    text = (
                        "⚠️⚠️⚠️ Tu n'as pas encore accès au séminaire.\n"
                        "Tu dois d'abord t'inscrire et payer pour y participer.\n\n"
                        "💰 Pour plus d'informations, contacte @Fiacrekpanou."
                    ),
                    parse_mode="Markdown" )
                return ConversationHandler.END
                

            if verify_categorie(args[0]) == 'non_verify':
                await Context.bot.send_message(
                    chat_id=user_id,
                    text = (
                        "L'exercice du jour n'est pas encore valider merci de patienté.\n"
                        
                    ),
                    parse_mode="Markdown"  
                )
                return ConversationHandler.END
            if verify_categorie(args[0]) == None:
                await Context.bot.send_message(
                    chat_id=user_id,
                    text = (
                        "Ce lien n'exite pas dans mon système.\n"
                        
                    ),
                    parse_mode="Markdown"  
                )
                return ConversationHandler.END
        
        
   
            

    if args and args[0] == name:
        
        #le user a fini son process
        if user_has_categorie(user_id,name):
            await Context.bot.send_message(
                chat_id=user_id,
                text=(
                    "👌 **Tu as terminé ton process ✅**\n"
                    "*Sois patient, l'ami, c'est pour bientôt !*\n\n"
                    "📌 *Épingle ce canal* pour rester à l'affût des **nouvelles informations**."
                ),
                parse_mode="Markdown"  
            )

            return ConversationHandler.END
        # le user n'a pas fini son process
        else:

            #le user existe dans la base
            if user_exists(user_id):

                Context.user_data["args"] = args[0]
                type = "leseminaire"

                try:
                    await update.message.reply_text(
                        "🎉 *Félicitations à toi*\n\n"
                        "__Ton paiement vient d’être validé avec succès ✅__\n"
                        
                        "🔥 *Bienvenue dans* __LE SÉMINAIRE DU TRADER GAGNANT__ — un format intensif de __2 semaines__ conçu pour t’apporter __plus de clarté, plus de résultats, et surtout plus de maîtrise__ sur les marchés.\n\n"
                        "Mais avant de t’ouvrir les portes du canal privé 🔐,\n"
                        "on aimerait en savoir un peu plus sur toi…\n\n"
                        "💬 Cela nous permettra d’*adapter au mieux l’expérience à ton profil*\n"
                        "et faire en sorte que tu ressortes __boosté, structuré et prêt à passer au niveau supérieur__.\n\n"
                        "🚀📈🔥"
                    , parse_mode='Markdown')

                    

                    await wait_5_seconds()


                    await update.message.reply_text("__**😂 Ne réponds pas au message précédent, je sais que tu es ravi, moi aussi d’ailleurs !**__\n\n"
                            "📧 `Pour traiter tes demandes en priorité à l'avenir, donne-nous ton adresse mail, vu que tu fais maintenant partie de la famille.`\n\n"
                            "➡️ `Envoie uniquement ton mail, par exemple : fiacrekpanou@gmail.com`",
                            parse_mode='Markdown'
                        )

                    return EMAIL   
                except TimedOut: 

                    await Context.bot.send_message(
                        chat_id=user_id,
                        text ="__**😂 Ne réponds pas au message précédent, je sais que tu es ravi, moi aussi d’ailleurs !**__\n\n"
                                    "📧 `Pour traiter tes demandes en priorité, donne-nous ton adresse mail, vu que tu fais maintenant partie de la famille.`\n\n"
                                    "➡️ `Envoie uniquement ton mail, par exemple : fiacrekpanou@gmail.com`",
                                    parse_mode='Markdown'
                                )

                    return EMAIL 


            else:

                Context.user_data["args"] = args[0]

                await update.message.reply_text(
                    "🎉 *Félicitations à toi!*\n\n"
                    "__Nous venons de valider ta facture : tout est parfait ✅.__\n\n"
                    
                    "*Comme c’est ta première fois avec notre assistant,*\n"
                    "🚀 nous allons d’abord t’enregistrer pour pouvoir t’ajouter au canal privé Telegram.\n\n"
                    "__✅ Suis simplement les prochaines étapes pour finaliser ton inscription et commence cette aventure avec nous !__\n\n"
                    "🔒 *Tu es à un pas de passer au niveau supérieur.*\n\n"
                    "🚀📈🔥"
                , parse_mode='Markdown')

                

                await wait_5_seconds()

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

    
            
    chat_id = update.effective_chat.id if chat_id is None else chat_id
    if user_exists(user_id):
        await update.message.reply_text("Tu es déjà inscrit ✅")
    else:
        
        if chat_id:
           
        
            video_name = "welcome_messagess"

            file_id = get_file_id(video_name)

            if file_id:
                # Réutiliser le file_id
                #await Context.bot.send_video(chat_id=chat_id, video=file_id, caption="Bienvenue ! 🎉")

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
               # msg = await Context.bot.send_video(chat_id=chat_id, video=video_path, caption="Bienvenue ! 🎉")
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

async def get_why(update: Update, Context: ContextTypes.DEFAULT_TYPE):
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

    Context.user_data["why"] = why_map[response]

    await update.message.reply_text(
    "🎯Parfait, chacun rejoint ce canal avec une attente différente… \n\n"
    "Alors dis-moi ce que tu espères trouver ici :\n\n"
    "1️⃣ UNE INITIATION GRATUITE POUR DÉCOUVRIR LE TRADING\n\n"
    "2️⃣ DES OPPORTUNITÉS DE TRADE CONCRÈTES ET RÉGULIÈRES\n\n"
    "3️⃣ UN ACCOMPAGNEMENT PLUS POUSSÉ, AVEC DU COACHING\n\n"

    "✍️ Réponds maintenant par **1**, **2** ou **3**.\n\n"

    "Je suis impatient 🔥🔥🔥",
    parse_mode='Markdown'
)

    return WHAT

async def get_what(update: Update, Context: ContextTypes.DEFAULT_TYPE):
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

    Context.user_data["what"] = expectation_map[response]

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

async def get_level(update: Update, Context: ContextTypes.DEFAULT_TYPE):
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
    Context.user_data["level"] = niveau_map[response]

    await update.message.reply_text(
    "👋 Alors, dis-moi comment on t’appelle ?\n\n"
    "Moi, je suis *l’assistant de Fiacre Kpanou*.\n"
    "✅ On va sûrement échanger, alors autant se connaître 😉\n\n"
    "✍️ Envoie-moi simplement ton *prénom et ton nom*.\n"
    "📌 Exemple : `Fiacre Kpanou`",
    parse_mode="Markdown"
    )


    return NAME


async def get_name(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        if update.effective_chat:
            await Context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer un texte valide."
            )
        return NAME
    
    Context.user_data["name"] = update.message.text
    await update.message.reply_text(
    "📞 Quel est ton numéro de téléphone ?\n\n"
    "🌍 Voici le mien : +22997203304 \n"
    
)

    return PHONE

async def get_phone(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        if update.effective_chat:
            await Context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer ton numéro."
            )
        return PHONE
    
    Context.user_data["phone"] = update.message.text
    await update.message.reply_text("🌍 Dans quel pays vis-tu ?")
    return COUNTRY

async def get_country(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    user_id = user.id
    Context.user_data["country"] = update.message.text
    if not update.message or not update.message.text:
        if update.effective_chat:
            await Context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer le nom de ton pays."
            )
        return COUNTRY
    # Enregistre les informations de l'utilisateur dans la base de données
    data = Context.user_data
    if  "args" in data and data["args"] == "leseminaire":

      

        await update.message.reply_text(
            "📩 *Entre ton adresse e-mail pour poursuivre.*\n\n"
            "✉️ Exemple : `fiacreKpanou@gmail`",
            parse_mode="Markdown"
        )



        return EMAIL
    else:
        save_user(
                Context.user_data["name"],
                Context.user_data["phone"],
                Context.user_data["country"],
                user_id,
                email=Context.user_data.get("email"),
                motivation=Context.user_data.get("motivation"),
                level=Context.user_data.get("level"),
                why= Context.user_data.get("why"),
                what= Context.user_data.get("what")

            )  

        await update.message.reply_text(
        "🎯 Tu viens de boucler cette première étape, et ce n’est pas rien.\n\n"
        "Dans un monde qui s’éparpille, tu choisis l’action. Mieux encore : tu choisis de suivre les bonnes personnes.\n\n"
        "📌 Épingle ce bot — il sera ton guide pendant toute l’immersion.\n"
        "🔥 Ressources, rappels, messages clés… tout passe par ici.\n\n"
        "🚀 Le vrai travail commence maintenant.\n"
        "— *Fiacre Kpanou*",
        parse_mode="Markdown"
        )



        
        
        if Context.user_data.get("level") =="Débutant":
            await wait_5_seconds()
            await update.message.reply_text(
            "🎓 *Tu m'as indiqué être débutant ! Parfait.*\n\n"
            "J’ai créé une *initiation 100% gratuite* pour t’aider à poser les bonnes bases et éviter les erreurs classiques.\n\n"
            "🚀 Commence dès maintenant :\n"
            "🔗 [Accède à la formation](https://app.rmiclass.net/course/Initiation-au-Trading)\n\n"
            "📬 Une question ? Envie de me parler ? Écris-moi ici 👉 @Fiacrekpanou",
            parse_mode="Markdown"
        )

        


    
    return ConversationHandler.END
    
    
    

async def get_email(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text or "@" not in update.message.text:
        await update.message.reply_text("❌ Merci d’envoyer une adresse email valide.")
        return EMAIL
    # Enregistre l'email de l'utilisateur
    Context.user_data["email"] = update.message.text

    # Demande la motivation pour rejoindre la formation
    await update.message.reply_text(
        "🎯 *Qu’attends-tu de cette masterclass ?*\n\n"
        "✅ Partage en une ou deux phrases ce que tu aimerais apprendre, corriger ou débloquer grâce à ce programme.",
        parse_mode="Markdown"
    )
    return EXPECTATIONS


async def get_expectations(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ N’hésite pas à nous dire ce que tu espères retirer de cette masterclass.")
        return EXPECTATIONS

    Context.user_data["expectations"] = update.message.text
    await update.message.reply_text(
        "📢 *Comment as-tu connu cette masterclass ?*\n\n"
        "Par exemple : Instagram, WhatsApp,Tiktok, recommandation, publicité, etc.",
        parse_mode="Markdown"
    )
    return DISCOVERY

async def get_discoverys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enregistrement de  comment l'utilisateur nous a découvert et finalise l'inscription."""
    
    # Vérification sécurisée du texte
    if not update.message or not update.message.text:
        await update.effective_chat.send_message("❌ Merci de préciser comment tu nous as découvert.")
        return DISCOVERY

    # Sauvegarde de la réponse
    context.user_data["discovery"] = update.message.text.strip()
    user = update.effective_user
    chat_id = update.effective_chat.id

    async def safe_task(coro):
        """Exécute une tâche async sans bloquer et log les erreurs."""
        try:
            await coro
        except Exception as e:
            print(f"[ERREUR TÂCHE] {e}")

    # Cas 1 : nouvel utilisateur
    if context.user_data.get("name"):
        asyncio.create_task(safe_task(save_user(
            name=context.user_data.get("name"),
            phone=context.user_data.get("phone"),
            country=context.user_data.get("country"),
            telegram_id=user.id,
            Contexte_user=context.user_data.get("args"), 
            email=context.user_data.get("email"),
            motivation=context.user_data.get("motivation"),
            level=context.user_data.get("level"),
            why=context.user_data.get("why"),
            what=context.user_data.get("what"),
            expectations=context.user_data.get("expectations"),
            discovery=context.user_data.get("discovery")
        )))
        asyncio.create_task(safe_task(add_categorie(user.id, context.user_data.get("args"))))

    # Cas 2 : utilisateur existant → mise à jour
    else:
        try:
            asyncio.create_task(safe_task(update_user_info(
                telegram_id=user.id,
                email=context.user_data.get("email"),
                expectations=context.user_data.get("expectations"),
                discovery=context.user_data.get("discovery")
            )))
            asyncio.create_task(safe_task(add_categorie(user.id, context.user_data.get("args"))))
        except Exception as e:
            print(f"❌ Erreur lors de la mise à jour des informations utilisateur : {e}")
            await update.effective_chat.send_message(
                "❌ Une erreur est survenue lors de la mise à jour de tes informations. Merci de réessayer plus tard."
            )
            return ConversationHandler.END

    # Message final unique (pas de duplication)
    message = await update.effective_chat.send_message(
        "✅ *Merci pour toutes ces infos précieuses !*\n\n"
        "✅ *Tu viens de finaliser avec succès ton inscription !*\n\n"
        "🚀 *Ton accès exclusif est prêt !*\n\n"
        "🔥 Clique vite sur ton lien unique pour rejoindre le canal privé :\n"
        "👉 *https://t.me/+yj_n_7oH43ZlOGNk*\n\n"
        "💥 C’est ici que commence ta transformation, entouré(e) de traders qui veulent réussir.\n"
        "⚡️ Ne perds pas une seconde, on t’attend pour passer à l’action !\n\n"
        "🚫 *Lien personnel.*",
        parse_mode="Markdown"
    )

    # Suppression programmée
    asyncio.create_task(safe_task(delete_and_offer_later(context, chat_id, message.message_id)))

    return ConversationHandler.END

async def get_discovery(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await update.message.reply_text("❌ Merci de préciser comment tu nous as découvert.")
        return DISCOVERY

    Context.user_data["discovery"] = update.message.text

    # Confirmation
    
    



    # Enregistrement (personnalise avec ta fonction save_user)

    data = Context.user_data
    user = update.effective_user
    if "name" in data and data["name"] is not None:
        user = update.effective_user
        asyncio.create_task(save_user(
            name=Context.user_data.get("name"),
            phone=Context.user_data.get("phone"),
            country=Context.user_data.get("country"),
            telegram_id=user.id,
            contexte_user=Context.user_data.get("args"),
            email=Context.user_data.get("email"),
            motivation=Context.user_data.get("motivation"),
            level=Context.user_data.get("level"),
            why= Context.user_data.get("why"),
            what= Context.user_data.get("what"),           
            expectations=Context.user_data.get("expectations"),
            discovery = Context.user_data.get("discovery")
        ))
        asyncio.create_task(add_categorie(user.id, Context.user_data.get("args")))
        chat_id=update.effective_chat.id

        message = await update.message.reply_text(
            "✅ *Merci pour toutes ces infos précieuses !*\n\n"    
            "✅ *Tu viens de finaliser avec succès ton inscription !*\n\n"
            "🚀 *Ton accès exclusif est prêt !*\n\n"
            "🔥 Clique vite sur ton lien unique pour rejoindre le canal privé :\n"
            "👉 *https://t.me/+yj_n_7oH43ZlOGNk*\n\n"
            "💥 C’est ici que commence ta transformation, entouré(e) de traders qui veulent réussir.\n"
            "⚡️ Ne perds pas une seconde, on t’attend pour passer à l’action !\n\n"
            "🚫 *Lien personnel.*",
            parse_mode="Markdown"
        )

    else:

        try:
            asyncio.create_task(update_user_info(
                telegram_id=user.id,
                email=Context.user_data.get("email"),
                expectations=Context.user_data.get("expectations"),
               
                discovery = Context.user_data.get("discovery")))
            
            chat_id=update.effective_chat.id

            message = await update.message.reply_text(
                "✅ *Merci pour toutes ces infos précieuses !*\n\n"    
                "✅ *Tu viens de finaliser avec succès ton inscription !*\n\n"
                "🚀 *Ton accès exclusif est prêt !*\n\n"
                "🔥 Clique vite sur ton lien unique pour rejoindre le canal privé :\n"
                "👉 *https://t.me/+yj_n_7oH43ZlOGNk*\n\n"
                "💥 C’est ici que commence ta transformation, entouré(e) de traders qui veulent réussir.\n"
                "⚡️ Ne perds pas une seconde, on t’attend pour passer à l’action !\n\n"
                "🚫 *Lien personnel.*",
                parse_mode="Markdown"
            )
        except BadRequest as e:
            print(f"❌ Erreur lors de la mise à jour des informations utilisateur : {e}")
            await update.message.reply_text(
                "❌ Une erreur est survenue lors de la mise à jour de tes informations. Merci de réessayer plus tard."
            )

            message = await update.message.reply_text('erreur lors de la mise a jour de vos informations ', parse_mode="Markdown")

            return ConversationHandler.END
        add_categorie(user.id, Context.user_data.get("args"))
        print("ok")


    asyncio.create_task(delete_and_offer_later(Context, chat_id, message.message_id))    
    return ConversationHandler.END



async def delete_and_offer_later(Context, chat_id, message_id):
    await asyncio.sleep(300)  # 5 minutes

    try:
        await Context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"❌ Erreur suppression : {e}")

