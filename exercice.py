from telegram.ext import Updater, CommandHandler, MessageHandler, filters, ConversationHandler
import os
from telegram import Update
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler

import time
ADMIN_ID =571718066 
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def build_answer_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("✅ VRAI", callback_data="VRAI"),
            InlineKeyboardButton("❌ FAUX", callback_data="FAUX")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

sessions = {}
# États de la conversation
from constance import QUESTION, ANSWER, EXPLANATION, CATEGORIE, NOM_CATEGORIE, WAITING_ANSWER,CHOISIR_CATEGORIE 
from telegram import ReplyKeyboardRemove
from database.database import add_exercice,get_user_args,get_questions, save_user_answer, save_daily_result, delete_args
from database.database import get_category_questions_report,get_final_score,create_args,update_arg, add_categorie_exercice, verifier_et_valider_categorie,check_if_user,get_categories,verify_categorie, user_has_categorie

async def start_add_exercice(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    if  update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return
    await update.message.reply_text("📚 Envoie la **question** de l'exercice :")
    return QUESTION

async def get_question(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    Context.user_data['question'] = update.message.text
    await update.message.reply_text("✏️ Envoie la **réponse correcte** :")
    return ANSWER

async def get_answer(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    Context.user_data['answer'] = update.message.text
    await update.message.reply_text("💡 Envoie **l'explication** :")
    return EXPLANATION

async def get_explanation(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    Context.user_data['explanation'] = update.message.text
    await update.message.reply_text("📂 Envoie l'**ID de la catégorie** :")
    return CATEGORIE

async def get_categorie(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    try:
        categorie_id = int(update.message.text)
        add_exercice(
            Context.user_data['question'],
            Context.user_data['answer'],
            Context.user_data['explanation'],
            categorie_id
        )
        await update.message.reply_text("✅ Exercice ajouté avec succès.")
    except ValueError:
       await update.message.reply_text("❌ ID de catégorie invalide. Annulé.")
    return ConversationHandler.END

async def cancel(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Ajout annulé.")
    return ConversationHandler.END

async def start_add_categorie(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    if  update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return
    await update.message.reply_text("📂 Envoie le nom de la nouvelle catégorie :")
    return NOM_CATEGORIE

async  def get_nom_categorie(update: Update, Context: ContextTypes.DEFAULT_TYPE):
   
    nom_categorie = update.message.text
    categorie_id = add_categorie_exercice(nom_categorie, admin_verify=False)
    await update.message.reply_text(
        f"✅ Catégorie '{nom_categorie}' ajoutée avec succès.\n📌 ID : {categorie_id}"
    )
    return ConversationHandler.END

async def cmd_verify_categorie(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admin_ids = [571718066, 8195437212]  # Liste des user_id admins à adapter

    if user_id not in admin_ids:
        await update.message.reply_text("⛔ Tu n'as pas la permission pour faire ça.")
        return

    if len(Context.args) != 1:
        await update.message.reply_text("⚠️ Usage : /verify_categorie <id_categorie>")
        return

    try:
        categorie_id = int(Context.args[0])
    except ValueError:
        await update.message.reply_text("❌ L'ID doit être un nombre.")
        return

    success, message = verifier_et_valider_categorie(categorie_id)
    await update.message.reply_text(message)

def build_result_message(answers, total_time,user_id, categorie_name):
    """
    answers = liste de tuples : (question_id, user_answer, correct_answer, explanation)
    """
    correct_count = 0
    msg = f"__**🏁 Exercice terminé !**__\n\n"
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

    # Variable à remplacer par le calcul réel
   
   # note_sur_100 = get_final_score(user_id,)

    # Résumé des notes
    msg += (
        f"__**📊 Résultat**__\n"
        f"📝 Note sur cet exercice : `{correct_count} / {len(answers)}`\n"
        f"🏆 Note totale (sur 100) : `📝🕵️‍♂️/100`\n\n"
        
    )
    promo=""
    # Commentaire selon score
    if correct_count == len(answers):
        msg += "🎉 `Parfait ! Tu as tout réussi.`"
    elif correct_count >= len(answers) * 0.7:
        msg += "💪 `Très bon travail, continue comme ça !`"
    elif correct_count >= len(answers) * 0.4:
        msg += "🙂 `Pas mal, mais tu peux encore progresser.`"
    else:
        msg += "📚 `Courage, révise un peu et ça ira mieux la prochaine fois.`"

    # Invitation à recommencer si note < 5
    if correct_count < 5:
        msg += "\n\n🔄 `Ta note est inférieure à 5/10, tu peux retenter l'exercice clique juste sur \n` /jeRecommence"
        return msg, correct_count,promo
    if correct_count >= 6 and user_has_categorie(user_id,'leseminaire') == None:  
        promo =  (
    "🎉 *Bravo !* 🎉\n"
    "Tu fais partie de ceux qui ont décroché *6/10 ou plus* à notre test 📝💪\n"
    "C’est déjà un bon pas, mais le meilleur reste à venir… 🚀\n\n"
    "🔥 Tu as fait tes preuves, et tu *mérites d’être avec nous* 💪🔥\n"
    "Pour toi : *une offre exclusive de -50%*, valable *uniquement ce soir* ⏳✨\n\n"
    "🚀 Ne laisse pas passer ta chance…\n"
    "👉 Lien de paiement : https://me.fedapay.com/pJUafYc0\n\n"
    "*Paiement crypto USDT TRC20*\n"
    "Adresse : `TUxRmHjGo9uJDiYGTEKCG6GxXsYcfcpCgu`"
)
        

    update_arg(user_id,categorie_name)
    return msg, correct_count, promo


async def start_exercice(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    


    if check_if_user(user_id):
        args = get_user_args(user_id)
        print(args)
        if create_args(user_id,args, 0) == 'already':
                await update.message.reply_text(
                    "Tu as déja traiter tes exercices"
                )
                return ConversationHandler.END
        if verify_categorie(args) != None :
            #user_has_categorie(user_id,'leseminaire'):
             print(args)
        else:
            await update.message.reply_text("⚠️⚠️⚠️")
            return ConversationHandler.END     
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
        f"__**🚀 Exercice `{args}` lancé !**__\n\n"
        "🎯 __*Le principe est simple*__ :\n"
        "📝 `C’est un QCM de 10 questions, 1 point par bonne réponse.`\n\n"
        "✅ `Si l’énoncé est correct → appuyez sur Vrai.`\n"
        "❌ `Si l’énoncé est incorrect → appuyez sur Faux.`\n\n"
        "💡 `Pas besoin de stresser, c’est très simple et fun.`\n"
        "🔥 `Donnez le meilleur de vous-même et voyons votre score !`\n\n"
        f"❓ __*Question 1 :*__\n\n`{questions[0][1]}`",
        parse_mode='Markdown',
        reply_markup=build_answer_keyboard()
        )


        sessions[user_id]['question_start_time'] = time.time()
        return WAITING_ANSWER 
    else:
        await update.message.reply_text("⚠️ Tu n'as pas d'exercice en cours. Veuillez d'abord vous inscrire.")
        return ConversationHandler.END   
async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user_answer = query.data.lower()

    if user_id not in sessions:
        await query.message.reply_text("⚠️ Tu n'as pas d'exercice en cours.")
        return ConversationHandler.END

    session = sessions[user_id]

    # Si toutes les questions sont déjà traitées
    if session['index'] >= len(session['questions']):
        await query.message.reply_text("✅ Exercice déjà terminé.")
        return ConversationHandler.END

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
        msg, note, promo = build_result_message(session['answers'], total_time, user_id, session['categorie_nom'])
        save_daily_result(user_id, session['categorie_id'], session['start_time'], end_time, note)
        del sessions[user_id]
        await query.message.reply_text(msg, parse_mode="Markdown")
        if promo:
            await query.message.reply_text(promo, parse_mode="Markdown")
        return ConversationHandler.END

    # Sinon envoyer la prochaine
    next_q = session['questions'][session['index']]
    await query.message.reply_text(
        f"__**❓ Question {session['index'] + 1} sur {len(session['questions'])} 📚**__\n\n"
        f"`{next_q[1]}`\n\n",
        parse_mode="Markdown",
        reply_markup=build_answer_keyboard()
    )

    session['question_start_time'] = time.time()
    return WAITING_ANSWER

async  def receive_answers(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    #user_id = update.effective_user.id
    #user_answer = update.message.text.strip()
    print('now')
    query = update.callback_query
    await query.answer()  # obligatoire pour que le clic fonctionne

    user_id = query.from_user.id
    user_answer = query.data.lower() 

    if user_id not in sessions:
        await  update.message.reply_text("⚠️ Tu n'as pas d'exercice en cours.")
        return ConversationHandler.END

    session = sessions[user_id]
    
    q = session['questions'][session['index']]
    q_id, q_text, q_answer, q_explanation = q

    q_start = session.get('question_start_time', time.time())
    q_end = time.time()

    # Sauvegarder la réponse en base
    save_user_answer(user_id, session['categorie_id'], q_id, user_answer, q_start, q_end)

    # Stocker pour le résumé final
    session['answers'].append((q_id, user_answer, q_answer, q_explanation))

    session['index'] += 1
    if session['index'] >= len(session['questions']):
        # Fin exercice
        end_time = time.time()
        total_time = end_time - session['start_time']

        msg, note,promo = build_result_message(session['answers'], total_time,user_id,session['categorie_nom'])
        save_daily_result(user_id, session['categorie_id'], session['start_time'], end_time, note)
        
        del sessions[user_id]
        await query.message.reply_text(msg, parse_mode="Markdown")
        if promo != "":
            await query.message.reply_text(promo, parse_mode="Markdown")
        

        return ConversationHandler.END

    # Question suivante
    next_q = session['questions'][session['index']]
    #await query.message.reply_text(f"Question {session['index'] + 1}:\n{next_q[1]}",reply_markup=build_answer_keyboard())
    await query.message.reply_text(
    f"__**❓ Question {session['index'] + 1} sur 10 📚**__\n\n"
    f"`{next_q[1]}`\n\n",
    parse_mode="Markdown",
    reply_markup=build_answer_keyboard()
    )


    session['question_start_time'] = time.time()
    return WAITING_ANSWER

# Étape 1 : Commande /rapport → demander catégorie
async def start_rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if  update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return
    await update.message.reply_text("📂 Entrez l'ID de la catégorie pour le rapport :")
    return CHOISIR_CATEGORIE

# Étape 2 : Réception de la catégorie et génération du rapport
async def recevoir_categorie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        categorie_id = int(update.message.text.strip())
        rapport = get_category_questions_report(categorie_id)
        await update.message.reply_text(rapport)
    except ValueError:
        await update.message.reply_text("❌ Catégorie invalide. Entrez un nombre.")
    return ConversationHandler.END