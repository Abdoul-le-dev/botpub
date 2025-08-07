import os
from telegram import Update
from constance import CATEGORIE, NOMBRE_QUESTIONS, QUESTION, NB_CHOIX, CHOIX, REPONSE_SUIVANTE

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
import sqlite3
# Temporaire : stockage en mémoire
user_qcm_data = {}

async def start_qcm_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🗂️ Donne un nom à ta catégorie d'exercices (ex: Day 1)")
    return CATEGORIE

async def set_categorie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["categorie"] = update.message.text
    await update.message.reply_text("📝 Combien de questions veux-tu ajouter ?")
    return NOMBRE_QUESTIONS

async def set_nb_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nb_questions"] = int(update.message.text)
    context.user_data["questions"] = []
    context.user_data["current_q"] = 1
    await update.message.reply_text("❓ Question 1 : envoie le texte de la question.")
    return QUESTION
async def set_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["current_question_text"] = update.message.text
    await update.message.reply_text("✍️ Combien de suggestions pour cette question ?")
    return NB_CHOIX

async def set_nb_choix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nb_choix"] = int(update.message.text)
    context.user_data["current_choix"] = []
    context.user_data["choix_count"] = 1
    await update.message.reply_text(f"🔠 Suggestion A : Envoie le texte.")
    return CHOIX

async def add_choix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choix_text = update.message.text
    current_count = context.user_data["choix_count"]
    lettre = chr(64 + current_count)  # A, B, C...

    context.user_data["last_choix"] = {"text": choix_text}

    await update.message.reply_text(f"✅ Est-ce la bonne réponse ? (oui / non)")
    return REPONSE_SUIVANTE

async def validate_choix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.lower()
    is_correct = answer == "oui"
    context.user_data["last_choix"]["is_correct"] = is_correct

    if not is_correct:
        await update.message.reply_text("🧐 Pourquoi ce n'est pas la bonne réponse ?")
        return validate_bad_reason

    # Ajout immédiat si c’est une bonne réponse
    context.user_data["current_choix"].append(context.user_data["last_choix"])
    return continue_choices(update, context)

async def validate_bad_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text
    context.user_data["last_choix"]["reason"] = reason
    context.user_data["current_choix"].append(context.user_data["last_choix"])
    return await continue_choices(update, context)

async def continue_choices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["choix_count"] += 1
    if context.user_data["choix_count"] <= context.user_data["nb_choix"]:
        lettre = chr(64 + context.user_data["choix_count"])
        await update.message.reply_text(f"🔠 Suggestion {lettre} : Envoie le texte.")
        return CHOIX
    else:
        # Enregistre la question et ses choix
        context.user_data["questions"].append({
            "question": context.user_data["current_question_text"],
            "choix": context.user_data["current_choix"]
        })

        if context.user_data["current_q"] < context.user_data["nb_questions"]:
            context.user_data["current_q"] += 1
            await update.message.reply_text(
                f"❓ Question {context.user_data['current_q']} : envoie le texte."
            )
            return QUESTION
        else:
            # Fin de la création
            await update.message.reply_text("✅ Création terminée ! Merci 🙌")
            # Ici, tu peux sauvegarder `context.user_data` dans ta DB
            return ConversationHandler.END

# Handler d'annulation
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Création annulée.")
    return ConversationHandler.END

# Déclaration du handler
