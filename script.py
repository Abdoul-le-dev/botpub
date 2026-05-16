import os
from telegram import Update
import  threading
from database.database import user_exists,delete_user_data_from_db
from database.database import save_message, get_user_categories
from database.database import update_user_info,reset_all_mail_counts
from database.database import add_categorie,verify_categorie,add_new_user
from ai_agent import set_bot, log_unhandled_message
from database.database import save_user_default,delete_all_exercices
from user_data import user_info
from database.database import get_file_id
from database.database import save_file_id

from form.form import init_forms_db
from form.form_engine import register_form_handlers

USER,PWD = range(2) 

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler

from telegram.error import BadRequest

from dotenv import load_dotenv
from telegram.ext import filters


import asyncio

from seminaire import get_level_welcome,get_why_welcome,get_numero_whatsapp_welcome,get_mail_welcome,get_name_welcome,last_step_welcome
from telegram_page.signal_broadcast import register_signal_handlers
#new 
#from message.save_message import log_unhandled_message
type =""
load_dotenv()



ADMIN_ID = 571718066  # Remplace par ton ID Telegram

CANAL_B_ID = -1002705005402
ASK_BROADCAST = 99
token = os.getenv("tokens")


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

    try:
        video_name = "welcomes"

        file_id = get_file_id(video_name)

        if file_id:
                # Réutiliser le file_id
                await Context.bot.send_video(chat_id=user_id , video=file_id, reply_markup= build_answer_keyboards())
                

                


                
        else:
            
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









def scheduler_thread():
    print('yes')
    # Envoi immédiat
    #envoyer_base_par_email()
    #reset_all_mail_counts()
    #while True:
       # time.sleep(12 * 3600)  # 12 heures
       # envoyer_base_par_email()
        #reset_all_mail_counts()


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

    
    init_forms_db()

    
    app = Application.builder().token(token).read_timeout(30).write_timeout(30).build()

    
    
    app.add_handler(ChatJoinRequestHandler(approve_join_request))

    
    


    register_signal_handlers(app)
    


    register_form_handlers(app, app.bot, ADMIN_ID)

    

    set_bot(app.bot) 


    threading.Thread(target=scheduler_thread, daemon=True).start()
   
    print('running...')
    
    
    
    
   
    
    app.run_polling(poll_interval=1)
