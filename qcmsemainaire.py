from telegram.ext import Updater, CommandHandler, MessageHandler, filters, ConversationHandler
import os
from telegram import Update
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
from exercice import get_question, get_answer
import time
ADMIN_ID =571718066 
import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import requests

# États de la conversation 
from constance import WAITING_ANSWER_EXAM,EMAIL_EXAM,QUESTION, ANSWER, EXPLANATION, CATEGORIE, NOM_CATEGORIE, WAITING_ANSWER,CHOISIR_CATEGORIE 
from telegram import ReplyKeyboardRemove
from database.database import update_exam_user,get_user_args,get_questions, save_user_answer, save_daily_result, delete_args
from database.database import  add_exam_user,get_exam_parts,get_user_exam, get_category_questions_report,get_final_score,create_args,update_arg, add_categorie_exercice, verifier_et_valider_categorie,check_if_user,get_categories,verify_categorie, user_has_categorie

url = "https://conference.fiacrekpanoudtrade.com/api/bot/telegram/assistant"




sessions = {}

def build_answer_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("✅ VRAI", callback_data="VRAI"),
            InlineKeyboardButton("❌ FAUX", callback_data="FAUX")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_exam_1():
    keyboard = [
        [
            InlineKeyboardButton("✅Je Commence La Première Epreuve", callback_data="premiere")
            #InlineKeyboardButton("❌ Non j'ai pas compris", callback_data="no")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_exam_2():
    keyboard = [
        [
            InlineKeyboardButton("✅Je Commence La second Epreuve", callback_data="Second")
            #InlineKeyboardButton("❌ Non j'ai pas compris", callback_data="no")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_exam(update: Update, context: ContextTypes.DEFAULT_TYPE):

    print('exam')

    user = update.effective_user
    user_id = user.id

    data_user = get_user_exam(user_id )

    if data_user:
        if data_user['note_two'] != 0:

            await update.message.reply_text(
                    "Tu as déja Passer ton examen"
                )
            return ConversationHandler.END
        else :
            await update.message.reply_text("⚠️ Tu n'as pas d'examen en cours.")
            return ConversationHandler.END     
   
    """
    Demande à l'utilisateur son e-mail pour vérification de l'offre
    Pack Trading Gagnant Objectif +5000 USD avant de poursuivre l'examen.
    """
    await update.message.reply_text(
    "__**📬 Vérification Email**__\n\n"
    "Avant de commencer l'examen du **Pack Trading Gagnant Objectif +5000 USD**, "
    "merci de saisir l'adresse e-mail que vous avez utilisée lors de votre inscription :",
    parse_mode='Markdown'
    )

    return EMAIL_EXAM



async def verification_email(update: Update, context: ContextTypes.DEFAULT_TYPE):

   
    email = update.message.text.strip()
        
        # Simple validation de base (tu peux la rendre plus complète)
    if "@" in email and "." in email:
            await update.message.reply_text(
                f"✅ Email : `{email}` reçu !\n"
                "Vérification....."
                , parse_mode='Markdown'
            )

    else:
            await update.message.reply_text(
                "⚠️ L'adresse e-mail semble invalide. Renvoyer une address email vailde:"
            )
            return EMAIL_EXAM

    user = update.effective_user
    user_id = user.id

    #email = update.message.text.strip()



    # Préparer les données à envoyer
    data = {
        "user_email": email,
        "user_id_telegram": user.id,
        "id_produit": 1
    }

    
    try:
        # Envoi de la requête POST avec le JSON
        response = requests.post(url, json=data)
        #print(response)

        # 1️⃣ Statut HTTP
        print("Status Code:", response.status_code)

        # 2️⃣ Contenu brut
        print("Response Text:", response.text)
        # Vérifier si la requête s'est bien passée (code 200)
        if response.status_code == 200:
            # Convertir la réponse JSON en dictionnaire Python
            result = response.json()

            # Exemple de traitement selon la réponse
            if result.get("success"):
                 
                await update.message.reply_text(
                        f"✅ {result['user']["first_name"]}, vous êtes bien inscrite !\n\n"
                        "Votre inscription a été vérifiée avec succès pour le **Pack Trading Gagnant Objectif +5000 USD**.\n"
                        "Merci pour votre engagement et bienvenue à l'examen !",
                        parse_mode='Markdown'
                    )
                
                add_exam_user(user_id, email, 2)
                
                time.sleep(10) 

                await update.message.reply_text(
                    f"__**🎓 Examen !**__\n\n"
                    "📘 __*Voici le principe*__ :\n"
                    "🧩 `L’examen comprend 2 épreuves sur 10. Vous disposez de 5 minutes par épreuve.`\n"
                    "🧮 `Chaque épreuve est notée sur 10, avec une moyenne de 8 points par épreuve.`\n\n"
                    "📝 `Il s’agit d’un QCM de 10 questions, 1 point par bonne réponse.`\n\n"
                    "✅ `Si l’énoncé est correct → appuyez sur Vrai.`\n"
                    "❌ `Si l’énoncé est incorrect → appuyez sur Faux.`\n\n"
                    "💡 `Restez calme, c’est simple et ludique.`\n"
                    "🔥 `Donnez le meilleur de vous-même et voyons votre score final !`\n\n"
                    "🎯 __*La réussite de cette épreuve est indispensable pour votre coaching avec M. Fiacre.*__",
                    parse_mode='Markdown',
                    reply_markup=build_exam_1())
                


            else:
                await update.message.reply_text(
                        "⚠️ Vérification échouée.\n"
                        "Veuillez vous assurer que vous êtes correctement inscrit et réessayer."
                    )
                
                return ConversationHandler.END
                
        else:
            await update.message.reply_text(
            "⚠️ Vérification échouée.\n"
            "Veuillez vous assurer que vous êtes correctement inscrit et réessayer."
                     )
            return ConversationHandler.END

    except requests.exceptions.RequestException as e:
        await update.message.reply_text(
            "⚠️ Vérification échouée.\n"
            "Veuillez vous assurer que vous êtes correctement inscrit et réessayer."
        )
        return ConversationHandler.END

    
 




async def start_exams(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    print('yes')
    user = update.effective_user
    user_id = user.id

    


    data_user = get_user_exam(user_id )
    print(data_user)

    if data_user:
        if data_user['note_one'] == 0:
            id_part_one, id_part_two = get_exam_parts(user.id)
            args = id_part_one
        elif data_user['note_two'] == 0:    
             id_part_one, id_part_two = get_exam_parts(user.id)
             args = id_part_two
        else :
            await update.message.reply_text(
                    "Tu as déja Passer ton examen"
                )
            return ConversationHandler.END    
    else :
        return ConversationHandler.END         


    #args => categorie name
    categorie = get_categories(args)
    questions = get_questions(categorie)


        # Init session
    sessions[user_id] = {
            'categorie_id': categorie,
            'categorie_nom': args,
            'questions': questions,
            'index': 0,
            'start_time': time.time(),
            'answers': [],
    }

    await update.message.reply_text(
        f"__**🎓 Examen lancé !**__\n\n"
        "📘 __*Voici le principe*__ :\n"
        f"❓ __*Question 1 :*__\n\n`{questions[0][1]}`",
        parse_mode='Markdown',
        reply_markup=build_answer_keyboard())



    sessions[user_id]['question_start_time'] = time.time()

    return WAITING_ANSWER_EXAM

    

async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Réponse reçue ! ⏳")  # Réponse immédiate à Telegram

    user_id = query.from_user.id
    user_answer = query.data.lower()

    # Si pas de session, on stoppe
    if user_id not in sessions:
        await query.message.reply_text("⚠️ Tu n'as pas d'examenen cours.")
        return ConversationHandler.END

    # Lancer le traitement en arrière-plan
    asyncio.create_task(process_answer(user_id, user_answer, query))
    return WAITING_ANSWER_EXAM


async def process_answer(user_id, user_answer, query):
    session = sessions[user_id]

    # Si toutes les questions sont déjà traitées
    if session['index'] >= len(session['questions']):
        await query.message.reply_text("✅ Examen déjà terminé.")
        return

    # Récupérer la question actuelle
    q_id, q_text, q_answer, q_explanation = session['questions'][session['index']]

    # Temps de réponse
    q_start = session.get('question_start_time', time.time())
    q_end = time.time()

    # Sauvegarde réponse
    save_user_answer(user_id, session['categorie_id'], q_id, user_answer, q_start, q_end)
    session['answers'].append((q_id, user_answer, q_answer, q_explanation))

    # Passer à la suivante
    session['index'] += 1

    # Si plus de questions => fin
    if session['index'] >= len(session['questions']):
        end_time = time.time()
        total_time = end_time - session['start_time']

        msg, note = build_result_message(session['answers'], total_time, user_id, session['categorie_nom'])

        data_user = get_user_exam(user_id )

        if data_user:
            if data_user['note_one'] == 0 and data_user['note_two'] == 0 :

                update_exam_user(user_id, note, total_time,"", 1 )
                
            elif data_user['note_two'] != 0 and data_user['note_two'] == 0 :    
                
                update_exam_user(user_id, note, total_time,"", 2 )
            else :
                return ConversationHandler.END      
        else : 
            return ConversationHandler.END   

        save_daily_result(user_id, session['categorie_id'], session['start_time'], end_time, note)
        del sessions[user_id]

        #await query.message.reply_text(msg, parse_mode="Markdown")

        data_user = get_user_exam(user_id )

        if data_user:
            if data_user['note_one'] != 0 and data_user['note_two'] == 0:

                await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=build_exam_2())
                
            elif data_user['note_two'] != 0: 

                 await query.message.reply_text(msg, parse_mode="Markdown")   
                
            else :
                return     
        else : 
            return

        
        

    # Sinon envoyer la prochaine question
    next_q = session['questions'][session['index']]
    await query.message.reply_text(
        f"__**❓ Question {session['index'] + 1} sur {len(session['questions'])} 📚**__\n\n"
        f"`{next_q[1]}`\n\n",
        parse_mode="Markdown",
        reply_markup=build_answer_keyboard()
    )

    session['question_start_time'] = time.time()
    


def build_result_message(answers, total_time,user_id, categorie_name):
    """
    answers = liste de tuples : (question_id, user_answer, correct_answer, explanation)
    """
    correct_count = 0
    msg = f"__**🏁 Terminé !**__\n\n"
    msg += f"⏱️ Temps total : `{int(total_time)} secondes`\n\n"

    for i, (q_id, user_a, correct_a, explanation) in enumerate(answers, 1):
        if user_a.lower() == correct_a.lower():
            msg += f"{i}. ✅ `Bonne réponse`\n"
            correct_count += 1
        else:
            msg += (
                f"{i}. ❌ `Mauvaise réponse`\n"
                f"   ➡️ Ta réponse : `{user_a}`\n"
                f"   ✅ Bonne réponse : `{correct_a}`\n"
                f"   📖 Explication : `{explanation}`\n"
            )
        msg += "\n"

    msg += (
        f"__**📊 Résultat**__\n"
        f"📝 Note sur cet examen : `{correct_count} / {len(answers)}`\n"
        
    )
   
    # Commentaire selon score
    if correct_count == len(answers):
        msg += "🎉 `Parfait ! Tu as tout réussi.`"
    elif correct_count >= len(answers) * 0.7:
        msg += "💪 `Très bon travail, continue comme ça !`"
    elif correct_count >= len(answers) * 0.4:
        msg += "🙂 `Pas mal, mais tu peux encore progresser.`"
    else:
        msg += "📚 `Courage, révise un peu et ça ira mieux la prochaine fois.`"
       

    update_arg(user_id,categorie_name)
    return msg, correct_count
