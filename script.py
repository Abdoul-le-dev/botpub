import os
from telegram import Update
import  threading

from database.database import get_user_categories

from database.database import add_new_user
from ai_agent import set_bot, log_unhandled_message
from database.database import save_user_default,delete_all_exercices

from database.database import get_file_id
from database.database import save_file_id
from validation_handler import register_validation_handler

from form.form import init_forms_db
from form.form_engine import register_form_handlers, setup_background_worker

USER,PWD = range(2) 

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler

from telegram.error import BadRequest

from dotenv import load_dotenv
from telegram.ext import filters


import asyncio


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

CATEGORIE = "USER_PUB_1_NON_ACHAT"

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
        
    try:
        from telegram_page.categorie import add_members_to_category
        await add_members_to_category(CATEGORIE, [user_id])
        print(f"[validation] catégorie ajoutée")
    except Exception as e:
        print(f"[validation] categorie error: {e}")
        
    await Context.bot.send_message(
    chat_id=user_id,
    text=(
        "Bonjour l'ami 👋\n\n"
        "Je suis <b>Fiacre KPANOU</b>, j'échange directement avec toi via mon assistant bot.\n\n"
        "J'ai remarqué que tu n'as pas encore profité de l'offre disponible sur la plateforme, mais ce n'est absolument pas grave. "
        "Je salue d'ailleurs ton initiative d'avoir rejoint mon canal 🙌\n\n"
        "D'autres opportunités arrivent très bientôt. "
        "J'organise régulièrement des <b>webinaires</b> où je t'initie pas à pas aux marchés financiers :\n\n"
        "📊 Comment aborder les marchés avec méthode\n"
        "🏆 Les résultats concrets de mes apprenants\n"
        "💡 Des success stories qui vont t'inspirer et te donner envie de te lancer\n\n"
        "Clique ici pour t'enregistrer en avant-première : /Je_MEnregistre_en_avant_premiere_pour_la_prochaine_masterclass\n\n"
        "Reste connecté et bien branché 🔥\n"
        "Je t'enverrai toutes les informations importantes directement via mon assistant.\n\n"
        "Merci l'ami 🤝"
    ),
    parse_mode="HTML")

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
     
async def cancel(update: Update, Context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Annulé.")
    return ConversationHandler.END


if __name__ == '__main__':

    
    init_forms_db()

    
    app = Application.builder().token(token).read_timeout(30).write_timeout(30).build()

    app.post_init = setup_background_worker()

    

    
    
    app.add_handler(ChatJoinRequestHandler(approve_join_request))

    register_validation_handler(app)  

    register_form_handlers(app, app.bot, ADMIN_ID)

    
    


    register_signal_handlers(app)
    


   

    app.add_handler(MessageHandler(filters.TEXT, log_unhandled_message))

    set_bot(app.bot) 

   
    print('running...')
    
    
    
    
   
    
    app.run_polling(poll_interval=1)
