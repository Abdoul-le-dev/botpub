from telegram import Update
from constance import LEVEL_WELCOME, WHY_WELCOME, NUMERO_WHATSAPP_WELCOME, MAIL_WELCOME, NOM_WELCOME

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler

import asyncio

from mail_fonction import envoyer_email

from database.database import save_user

from database.database import add_categorie
async def get_level_welcome(update: Update, Context: ContextTypes.DEFAULT_TYPE):
    
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    await query.message.reply_text(
    "📊 J’ai besoin de connaître ton niveau actuel en trading.\n\n"
    "Dis-moi où tu te situes aujourd’hui :\n\n"
    "1️⃣ DÉBUTANT – JE DÉCOUVRE À PEINE LE TRADING\n\n"
    "2️⃣ INTERMÉDIAIRE – J’AI DES BASES, MAIS JE NE SUIS PAS ENCORE RENTABLE\n\n"
    "3️⃣ AVANCÉ – JE SUIS DÉJÀ RENTABLE ET JE CHERCHE À ALLER PLUS LOIN\n\n"

    "✍️ Réponds maintenant par **1**, **2** ou **3**.\n\n"
    "Peu importe où tu démarres… c’est la suite qui compte 🔥",
    parse_mode='Markdown'
)


    return LEVEL_WELCOME

async def get_why_welcome(update: Update, Context: ContextTypes.DEFAULT_TYPE):
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
        return LEVEL_WELCOME
    
    Context.user_data["level"] = niveau_map[response]

    await update.message.reply_text(
               
                "🔥 Dis-moi pourquoi t'intèresse tu au trading ? :\n\n"
                "1️⃣ POUR EN FAIRE UNE SOURCE DE REVENU PRINCIPALE\n\n"
                "2️⃣ POUR EN FAIRE UNE SOURCE DE REVENU SECONDAIRE\n\n"
                "3️⃣ POUR ATTEINDRE UNE LIBERTE FINANCIERE COMPLETE\n\n"
                "🚨 ATTENTION 🚨\n"
                "✍️ Réponds maintenant par **1**, **2** ou **3**.\n"
                
                ,parse_mode='Markdown'
                
            )
    return WHY_WELCOME

async def get_numero_whatsapp_welcome(update: Update, Context: ContextTypes.DEFAULT_TYPE):

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
        return WHY_WELCOME

    Context.user_data["why"] = why_map[response]

    await update.message.reply_text(
    "`📞 Quel est ton numéro whatsapp ?`\n\n"
    "`🌍 Voici le mien : +22997203304`",
    parse_mode="Markdown")


    return NUMERO_WHATSAPP_WELCOME

async def get_mail_welcome(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text:
        if update.effective_chat:
            await Context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d’envoyer ton numéro."
            )
        return NUMERO_WHATSAPP_WELCOME
    
    Context.user_data["phone"] = update.message.text

    await update.message.reply_text(
                            "📧 `Pour traiter tes demandes en priorité à l'avenir, redonne-nous ton adresse mail, vu que tu fais maintenant partie de la famille.`\n\n"
                            "➡️ `Envoie uniquement ton mail, par exemple : fiacrekpanou@gmail.com`",
                            parse_mode='Markdown'
                        )
    
    return MAIL_WELCOME 

async def get_name_welcome(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    if not update.message or not update.message.text or "@" not in update.message.text:
        await update.message.reply_text("❌ Merci d’envoyer une adresse email valide.")
        return MAIL_WELCOME 
    # Enregistre l'email de l'utilisateur
    Context.user_data["email"] = update.message.text

    await update.message.reply_text(
    "📌 Nous y sommes presque !\n"
    "Indique-moi simplement ton *nom complet* pour recevoir ton mail définitif "
    "de confirmation à la *Grande Conférence* 🎉\n\n"
    "✍️ Renvoie-moi uniquement ton *nom complet*.\n"
    "👉 Exemple : *Fiacre KPANOU*",
    parse_mode="Markdown"
    )

    return NOM_WELCOME

async def last_step_welcome(update: Update, Context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    Context.user_data["name"] = update.message.text

    async def safe_task(coro):
            """Exécute une tâche async sans bloquer et log les erreurs."""
            try:
                await coro
            except Exception as e:
                print(f"[ERREUR TÂCHE] {e}")

    if Context.user_data.get("name"):
        asyncio.create_task(safe_task(save_user(
                name=Context.user_data.get("name"),
                phone=Context.user_data.get("phone"),
                telegram_id=user.id,
                contexte_user="Grande Conference", 
                email=Context.user_data.get("email")  ,       
                level=Context.user_data.get("level"),
                why=Context.user_data.get("why")
                
        )))

    prenom = Context.user_data.get("name")  # à remplacer dynamiquement

    subject = "📌 Confirmation et informations pour la Grande Conférence"

    mail = Context.user_data.get("email") 

    msg = (
        f"Bonjour {prenom},\n\n"
        "🎉 Merci pour votre inscription à la Grande Conférence !\n\n"
        "🗓 Date : 2 octobre à partir de 20h\n"
        "🔗 Le lien de la conférence vous sera envoyé via :\n"
        "- Mon canal Telegram (vous y êtes déjà)\n"
        "- Par mail\n"
        "- Par WhatsApp\n"
        "- Directement via l'assistant bot si vous le souhaitez\n\n"
        "Nous avons hâte de vous retrouver pour cet événement exceptionnel !\n\n"
        "Cordialement,\n"
        "🤖 Assistant Bot du coach Fiacre (@FIACRE_D_KPANOU_ASSISTANCE_bot)"
    )

    # ça doit retourner 1
    get =  await envoyer_email(subjet=subject,msge=msg,mail=mail)

    async def safe_task(coro):
            """Exécute une tâche async sans bloquer et log les erreurs."""
            try:
                await coro
            except Exception as e:
                print(f"[ERREUR TÂCHE] {e}")


    asyncio.create_task(safe_task(add_categorie(user.id, "Grande_CONFERENCE_FIN_PROCESS_BOT")))

    if get ==1 :
        await update.message.reply_text(
        "📧 Ton inscription est confirmée !\n\n"
        "Je viens de t’envoyer un mail définitif confirmant ta place à la *Grande Conférence*.\n\n"
        "Merci et à très bientôt ! 🎉",
        parse_mode="Markdown"
        )
        return ConversationHandler.END
    else :

        await update.message.reply_text(
            f"{prenom}, 🎉 ton inscription à la *Grande Conférence* est confirmée !\n\n"
            "🗓 Date : 2 octobre à partir de 20h\n"
            "🔗 Le lien de la conférence te sera envoyé via :\n"
            "- Mon canal Telegram (vous y êtes déjà)\n"
            "- Par WhatsApp\n"
            "- Directement via l'assistant bot si tu le souhaites\n\n"
            "📧 Je n’ai pas pu t’envoyer le mail de confirmation cette fois-ci, "
            "mais ne t’inquiète pas : tout est bien enregistré.\n\n"
            "Nous avons hâte de te retrouver pour cet événement exceptionnel !\n\n"
            "🤖 Assistant Bot du coach Fiacre (@FIACRE_D_KPANOU_ASSISTANCE_bot)",
            parse_mode="Markdown"
        )



        return ConversationHandler.END




