import os
from telegram import Update
import  threading
from jeu import export_and_send_pdf
from database.database import init_db
from database.database import save_user
from database.database import user_exists,delete_user_data_from_db
from database.database import save_message, get_user_categories
from database.database import update_user_info,reset_all_mail_counts
from database.database import add_categorie,verify_categorie,add_new_user
from database.database import user_has_categorie
from database.database import save_user_default,delete_all_exercices
from user_data import user_info
from database.database import get_file_id
from database.database import save_file_id
from mail import send_email,send_none_email,envoyer_base_par_email
from start import start
from start import get_name
from start import get_phone     
from start import get_country
#from start import get_email
#from start import get_motivation
from start import get_level
from start import get_why, get_email
from start import get_what, get_expectations,get_discovery
from user_data import start_delete
from qcmprocess import start_qcm_creation
from qcmprocess import set_categorie
from qcmprocess import set_nb_questions
from qcmprocess import set_question     
from qcmprocess import set_nb_choix
from qcmprocess import add_choix
from qcmprocess import validate_choix
from qcmprocess import set_categorie
from qcmprocess import set_question
from qcmprocess import set_nb_choix
from qcmprocess import continue_choices
from qcmprocess import validate_bad_reason  
from challenge1000usd import send_short_link,send_mail_admin
from constance import WAITING_ANSWER_EXAM,LEVEL_WELCOME, WHY_WELCOME, NUMERO_WHATSAPP_WELCOME, MAIL_WELCOME, NOM_WELCOME
USER,PWD = range(2) 
#from mail_fonction import save_mail_id,save_mail_pwd, save_mail
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
import sqlite3
import pandas as pd
import random
from testing import user_list_in_categorie, user_list_in_categories, choose_format,handle_format_choice,handle_who, get_media, get_text,user_list_in_categorie
import string
from message_de_masse import broadcast_message
from stats import last_message
from telegram import Update
from qcmsemainaire import start_exam,start_exams, verification_email, receive_answer_exam


from telegram.error import BadRequest

from dotenv import load_dotenv
from telegram.ext import filters


import asyncio

import time

import json

from start import button_callback_waiting_1, button_callback_waiting_2

from constance import NAME_EXAM , ARGS_1, ARGS_2 , EMAIL_EXAM, CHOISIR_CATEGORIE,NAME, PHONE, COUNTRY, LEVEL, WHY, WHAT, ASK_IDS,EMAIL, EXPECTATIONS,DISCOVERY,WAITING_ANSWER

from constance import ASK_USER_ID, GET_MAIL ,CATEGORIE, NOMBRE_QUESTIONS, QUESTION, NB_CHOIX, CHOIX, REPONSE_SUIVANTE

from constance import QUESTION, ANSWER, EXPLANATION, CATEGORIE,  NOM_CATEGORIE, WAITING_ANSWER_1, WAITING_ANSWER_2

from exercice import start_add_exam,get_ars_1 , get_ars_2, get_ars_3, recevoir_categorie,start_rapport,start_add_exercice, get_question, get_answer, get_explanation, get_categorie, cmd_verify_categorie,start_exercice,receive_answer,start_add_categorie, get_nom_categorie

from seminaire import get_level_welcome,get_why_welcome,get_numero_whatsapp_welcome,get_mail_welcome,get_name_welcome,last_step_welcome
type =""
load_dotenv()

ADMIN_ID = 571718066  # Remplace par ton ID Telegram

CANAL_B_ID = -1002705005402
ASK_BROADCAST = 99


CHOOSE_TYPES =range(1)

async def wait_5_seconds():
    await asyncio.sleep(5)

WHO, CHOOSE_FORMAT,GET_MEDIA, GET_TEXT = range(4)

async def categories_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Commande /categories pour afficher toutes les catégories de l'utilisateur
    """
    user_id = update.effective_user.id
    categories = get_user_categories(user_id)
    
    if not categories:
        await update.message.reply_text(
            "📂 Vous n'avez aucune catégorie enregistrée.",
            parse_mode="Markdown"
        )
        return
    
    # Construire le message
    message = "📂 **Vos catégories :**\n\n"
    
    for i, (cat_id, name, created_at) in enumerate(categories, 1):
        message += f"{i}. **{name}**\n"
        message += f"   └ ID: `{cat_id}` | Créée: {created_at}\n\n"
    
    await update.message.reply_text(message, parse_mode="Markdown")

def build_answer_keyboards():
    keyboard = [
        [
            InlineKeyboardButton("✅ Je m'enregistre ", callback_data="enregistre")
               
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def approve_join_request(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    user = update.chat_join_request.from_user
    user_id = user.id
    user_name = update.effective_user.first_name or " CONFERENCE 1"

    chat_id = update.chat_join_request.chat.id

    save_user_default(user_id)

    args = Context.args
    print("chat_id")
    print(chat_id)
    if args : print(args)
    
    if user_has_categorie(user_id,"leseminaires"):
        print("L'utilisateur a déjà une catégorie, il est déjà membre.")
        try:
            await update.chat_join_request.approve()
        except BadRequest as e:
            if "User_already_participant" in str(e):
                print("Déjà membre, il est membre.") 
                print(e)     
                return
        await Context.bot.send_message(
            chat_id=user_id,
            text=(
                "👌 **C'est bon je t'ai intégré au canal ✅**\n"
                "*C'est pour bientôt et prépare toi, je te dirai tout !*\n\n"
                "📌 *Épingle ce canal* pour rester à l'affût des **nouvelles informations**."
            ),
            parse_mode="Markdown"
        )

        return 
    
    
    if chat_id == CANAL_B_ID:
       
        await Context.bot.send_message(
            chat_id=user.id,
            text=(
                "❌ *Doucement, on ne triche pas !* \n\n"
                "Tu n'es **pas autorisé** à rejoindre ce canal.\n"
                "🚫 Tu es maintenant *banni à vie* de ce canal.\n\n"
                "🔒 Toute tentative future entraînera aussi le bannissement de la personne qui t’a transmis le lien."
            ),
            parse_mode="Markdown"
        )

        await Context.bot.decline_chat_join_request(chat_id, user.id)
        return
    else:
        try:
            await update.chat_join_request.approve() 
        except BadRequest as e:
            if "User_already_participant" in str(e):
                print("Déjà membre, pas besoin d’approuver.") 

        async def safe_task(coro):
            """Exécute une tâche async sans bloquer et log les erreurs."""
            try:
                await coro
            except Exception as e:
                print(f"[ERREUR TÂCHE] {e}")

        if user_has_categorie(user_id,"Grande Conference JOIN CANAL"):
            return
        asyncio.create_task(safe_task(add_categorie(user.id, "Grande Conference JOIN CANAL")))  

                  

        # Envoie un message privé
        try:
            video_name = "welcomes"

            file_id = get_file_id(video_name)

            if file_id:
                # Réutiliser le file_id
                await Context.bot.send_video(chat_id=user_id , video=file_id, reply_markup= build_answer_keyboards())
                

                


                
            else:
                # Envoyer depuis fichier local, puis sauvegarder le file_id
                video_path = "welcomes.mp4"
                msg = await Context.bot.send_video(chat_id=user_id , video=video_path, reply_markup= build_answer_keyboards())
                new_file_id = msg.video.file_id
                save_file_id(video_name, new_file_id)
                
                

            
            #await Context.bot.send_message(
            #chat_id=user_id,
            #text="🔥🔥✍️  Clique sur /JeMEnregistre Maintenant"
            #)

            
        except Exception as e:
            print(f"Impossible d’envoyer un message à {user_id} : {e}")
     

async def user_imformation(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user

async def log_unhandled_message(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if not user:
        return
    user_id = user.id

    if not msg:
        print("⚠️ Mise à jour sans message TEXTe. Ignorée.")
        return
    message_id = msg.message_id
    message_text = msg.text or "<non-TEXT>"
    if msg.text:
        message_type = "TEXT"
    elif msg.photo:
        message_type = "photo"
    elif msg.document:
        message_type = "document"
    elif msg.video:
        message_type = "video"
    elif msg.audio:
        message_type = "audio"
    else:
        message_type = "other"
    

    await save_message(user_id, message_id, message_text, None, message_type)

token = os.getenv("token")

async def cancel(update: Update, Context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Annulé.")
    return ConversationHandler.END

# États du formulaire
#NAME, PHONE, COUNTRY, LEVEL,EMAIL,MOTIVATION = range(6)
ASK_ID, ASK_TEXT = range(2)

async def start_message(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🆔 Envoie l’ID Telegram du destinataire :")
    return ASK_ID

async def receive_id(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    Context.user_data["target_id"] = update.message.text.strip()
    await update.message.reply_text("✏️ Quel message veux-tu envoyer ?")
    return ASK_TEXT

async def receive_TEXT(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    target_id = Context.user_data["target_id"]
    TEXT = update.message.text

    try:
        await Context.bot.send_message(chat_id=int(target_id), text=TEXT)
        await update.message.reply_text("✅ Message envoyé !")
    except:
        await update.message.reply_text("❌ Erreur : ID invalide ou l’utilisateur n’a pas démarré le bot.")
    return ConversationHandler.END




async def start_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if  update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return
    await update.message.reply_text("🗑 Entrez l'ID de l'utilisateur à supprimer :")
    return ASK_USER_ID

async def get_user_id_to_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        delete_user_data_from_db(user_id)
        await update.message.reply_text(f"✅ Toutes les données de l'utilisateur {user_id} ont été supprimées.")
    except ValueError:
        await update.message.reply_text("⚠ Veuillez entrer un nombre valide.")
        return ASK_USER_ID

    return ConversationHandler.END


def scheduler_thread():
    # Envoi immédiat
    #envoyer_base_par_email()
    #reset_all_mail_counts()
    while True:
        time.sleep(12 * 3600)  # 12 heures
       # envoyer_base_par_email()
        reset_all_mail_counts()


async def save_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📩 *E-mail :*",
        parse_mode="Markdown"
    )
    return USER


async def save_mail_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mail'] = update.message.text

    await update.message.reply_text(
        "🔑 *Mot de passe :*",
        parse_mode="Markdown"
    )
    return PWD

# Étape 3 : récupérer le mot de passe et enregistrer l'utilisateur
async def save_mail_pwd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['psw'] = update.message.text

    # Appel de ta fonction d'ajout utilisateur
    add_new_user(context.user_data.get('mail'), context.user_data.get('psw'))

    await update.message.reply_text("✅ Adresse e-mail et mot de passe enregistrés.")
    return ConversationHandler.END


if __name__ == '__main__':

    init_db()

    
    app = Application.builder().token(token).read_timeout(30).write_timeout(30).build()

    #app.add_handler(MessageHandler(filters.ALL, detect_channel))
    
    app.add_handler(ChatJoinRequestHandler(approve_join_request))
    conv_handler_mail_user = ConversationHandler(
    entry_points=[CommandHandler('addemail', save_mail)],
    states={
        USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_mail_id)],
        PWD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_mail_pwd)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    allow_reentry=True,
    )

    conv_handler_process_exam1 = ConversationHandler(
    entry_points=[CommandHandler('CommencerMonExamen', start_exam)],#[CallbackQueryHandler(start_exam)],
    
    states={
        EMAIL_EXAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, verification_email)],
        
    }, fallbacks=[CommandHandler('cancel', cancel)])

    app.add_handler(conv_handler_process_exam1)


    conv_handler_process_exam2 = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_exams, pattern='premiere')],
    
    states={
        WAITING_ANSWER_EXAM: [
            CallbackQueryHandler(receive_answer_exam),
        ],
    },
    
    fallbacks=[CommandHandler('cancel', cancel)],
    #per_chat=True,
    )

    app.add_handler(conv_handler_process_exam2)


    conv_handler_process_exam3 = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_exams, pattern='second')],
    
    states={
      # WAITING_ANSWER_EXAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, verification_email)],
       WAITING_ANSWER_EXAM: [ CallbackQueryHandler(receive_answer),],
        
    }, fallbacks=[CommandHandler('cancel', cancel)])

    app.add_handler(conv_handler_process_exam3)


    conv_handler_welcome = ConversationHandler(
    entry_points=[CallbackQueryHandler(get_level_welcome, pattern='^(enregistre)$')],
    
    states={
        LEVEL_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why_welcome)],
        WHY_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_numero_whatsapp_welcome)],
        NUMERO_WHATSAPP_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mail_welcome)],
        MAIL_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_welcome)],
        NOM_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, last_step_welcome)],
        


    }, fallbacks=[CommandHandler('cancel', cancel)])

    app.add_handler(conv_handler_welcome)

    app.add_handler(conv_handler_mail_user)
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("JeMEnregistre", start)],
        states={
            WHY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why)],
            WHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_what)],
            LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_level)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
          
            EXPECTATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_expectations)],
            
            DISCOVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_discovery)]
            
            
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    conv_handlerstart = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WHY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why)],
            WHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_what)],
            LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_level)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            EXPECTATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_expectations)],
            DISCOVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_discovery)],
            WAITING_ANSWER_1: [CallbackQueryHandler(button_callback_waiting_1, pattern='^Poursuivre$')],
            WAITING_ANSWER_2: [CallbackQueryHandler(button_callback_waiting_2, pattern='^(Accepte|Refus)$')]
            
            
        },  
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    qcm_handler = ConversationHandler(
    entry_points=[CommandHandler("creer_qcm", start_qcm_creation)],
    states={
        CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_categorie)],
        NOMBRE_QUESTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_nb_questions)],
        QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_question)],
        NB_CHOIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_nb_choix)],
        CHOIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_choix)],
        REPONSE_SUIVANTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_choix)],
        validate_bad_reason: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_bad_reason)]
    },
    fallbacks=[CommandHandler("cancel", cancel)])

    conv_handler_jeu = ConversationHandler(
        entry_points=[CommandHandler("JeParticipeAuJeuConcours", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    convs_handler = ConversationHandler(
    entry_points=[CommandHandler("message", start_message)],
    states={
        ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_id)],
        ASK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_TEXT)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
    )
    

    conv_handlerMsg = ConversationHandler(
    entry_points=[CommandHandler('msgMasse', handle_who)],
    states={
        WHO: [MessageHandler(filters.Regex('^[1-2]$'), choose_format)],
        CHOOSE_FORMAT: [MessageHandler(filters.Regex('^[1-5]$'), handle_format_choice)],
        GET_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, get_media)],
        GET_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_text)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler("peopleCategorie", user_list_in_categories))
    app.add_handler(CommandHandler("peopleCategories", user_list_in_categorie))
    
    app.add_handler(conv_handlerMsg)
   # app.add_handler(convs_handler)

    #app.add_handler(conv_handler_jeu)
    



    app.add_handler(conv_handler)
    app.add_handler(conv_handlerstart)
    #app.add_handler(CommandHandler("LesGagnants", export_and_send_pdf))
    #app.add_handler(CommandHandler("categoriesss", categories_user))

    #app.add_handler(CommandHandler("lastMessage", last_message))
    #app.add_handler(CommandHandler("delete_all_exercices", delete_all_exercices))

    #app.add_handler(CommandHandler("userInfo", user_info))
    #app.add_handler(CommandHandler("mail_all_participant", send_email))
    #app.add_handler(CommandHandler("mail_none_participant", send_none_email))

    #app.add_handler(CommandHandler("userDelete", start_delete))
  

    conv_handler_add = ConversationHandler(
        entry_points=[CommandHandler('add_categorie', start_add_categorie)],
        states={
            NOM_CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_nom_categorie)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )


    app.add_handler(conv_handler_add)

    conv_handler_mail = ConversationHandler(
    entry_points=[CommandHandler('send_mail_user', send_mail_admin)],
    states={
        GET_MAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_short_link)],
    },
    fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler_mail)

   
   

    conv_handler_exercice = ConversationHandler(
        entry_points=[CommandHandler('add_exercice', start_add_exercice)],
        states={
            QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_question)],
            ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_answer)],
            EXPLANATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_explanation)],
            CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_categorie)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler_exercice)

    conv_handler_exam = ConversationHandler(
        entry_points=[CommandHandler('add_exam', start_add_exam)],
        states={ 
           NAME_EXAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ars_1)],
           ARGS_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ars_2)],
           ARGS_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ars_3)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler_exam)

    app.add_handler(CommandHandler('verify_categorie', cmd_verify_categorie))
    conv_handler_exercice_user = ConversationHandler(
        entry_points=[CommandHandler('commencerMesExerciesDuSeminaire', start_exercice)],
        states={
            #WAITING_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
            WAITING_ANSWER: [ CallbackQueryHandler(receive_answer),],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler_exercice_user)
    conv_handler_exercice_users = ConversationHandler(
        entry_points=[CommandHandler('jeRecommence', start_exercice)],
        states={
            #WAITING_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
            WAITING_ANSWER: [ CallbackQueryHandler(receive_answer),],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler_exercice_users)
    conv_handler_rapport = ConversationHandler(
    entry_points=[CommandHandler('rapport', start_rapport)],
    states={
        CHOISIR_CATEGORIE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recevoir_categorie)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
    app.add_handler(conv_handler_rapport)

    conv_handler_delete_user = ConversationHandler(
    entry_points=[CommandHandler("delete_user", start_delete_user)],
    states={
        ASK_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_user_id_to_delete)]
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)


    app.add_handler(conv_handler_delete_user)

    

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, log_unhandled_message))

    

    

    

    #app.add_handler(conv_handler_mail_user)
    #threading.Thread(target=scheduler_thread, daemon=True).start()
    #app.add_handler(qcm_handler)
    print('running...')
    
    
    

    
    app.run_polling(poll_interval=1)
