import asyncio
import logging
from datetime import datetime
from typing import Dict, Any
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters
from mail_fonction import envoyer_email
from database.database import save_user,user_has_categorie,add_categorie
from constance import LEVEL_WELCOME, WHY_WELCOME, NUMERO_WHATSAPP_WELCOME, MAIL_WELCOME, NOM_WELCOME
# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pool de tâches pour limiter la concurrence
TASK_SEMAPHORE = asyncio.Semaphore(50)  # Maximum 50 tâches simultanées
BACKGROUND_TASKS = set()  # Pour éviter la garbage collection des tâches

# Statistiques de monitoring
STATS = {
    "tasks_in_queue": 0,
    "tasks_completed": 0,
    "tasks_failed": 0,
    "max_queue_size": 0
}

async def execute_background_task(coro, task_name: str = "unknown"):
    """
    Exécute une tâche en arrière-plan avec gestion d'erreur et limitation de concurrence
    """
    # Incrémenter le compteur de file d'attente
    STATS["tasks_in_queue"] += 1
    STATS["max_queue_size"] = max(STATS["max_queue_size"], STATS["tasks_in_queue"])
    
    logger.info(f"⏳ Tâche {task_name} ajoutée à la file (position: {STATS['tasks_in_queue']})")
    
    async with TASK_SEMAPHORE:
        try:
            # La tâche commence maintenant (sort de la file d'attente)
            STATS["tasks_in_queue"] -= 1
            logger.info(f"🚀 Démarrage de la tâche {task_name} ({TASK_SEMAPHORE._value} slots libres)")
            
            await coro
            
            STATS["tasks_completed"] += 1
            logger.info(f"✅ Tâche {task_name} terminée avec succès (Total: {STATS['tasks_completed']})")
            
        except Exception as e:
            STATS["tasks_failed"] += 1
            logger.error(f"❌ Erreur dans la tâche {task_name}: {e} (Échecs: {STATS['tasks_failed']})")
            # Envoyer notification d'erreur en arrière-plan
            asyncio.create_task(
                envoyer_email(
                    subjet=f'Erreur Bot - {task_name}',
                    msge=f"Erreur: {str(e)}\nTâche: {task_name}\nTimestamp: {datetime.now()}\n"
                         f"Statistiques: {STATS}",
                    mail='abdoulledev@gmail.com'
                )
            )

def create_background_task(coro, task_name: str = "background_task"):
    """
    Crée une tâche en arrière-plan et l'ajoute au set de suivi
    """
    task = asyncio.create_task(execute_background_task(coro, task_name))
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task

async def get_level_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Vérification en arrière-plan pour ne pas bloquer l'interface
    if user_has_categorie(user_id, "Grande_CONFERENCE_FIN_PROCESS_BOT"):
        return ConversationHandler.END
    
    await query.message.reply_text(
        "📊 J'ai besoin de connaître ton niveau actuel en trading.\n\n"
        "Dis-moi où tu te situes aujourd'hui :\n\n"
        "1️⃣ DÉBUTANT – JE DÉCOUVRE À PEINE LE TRADING\n\n"
        "2️⃣ INTERMÉDIAIRE – J'AI DES BASES, MAIS JE NE SUIS PAS ENCORE RENTABLE\n\n"
        "3️⃣ AVANCÉ – JE SUIS DÉJÀ RENTABLE ET JE CHERCHE À ALLER PLUS LOIN\n\n"
        "✍️ Réponds maintenant par **1**, **2** ou **3**.\n\n"
        "Peu importe où tu démarres… c'est la suite qui compte 🔥",
        parse_mode='Markdown'
    )
    
    return LEVEL_WELCOME

async def get_why_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    context.user_data["level"] = niveau_map[response]
    
    await update.message.reply_text(
        "🔥 Dis-moi pourquoi t'intèresse tu au trading ? :\n\n"
        "1️⃣ POUR EN FAIRE UNE SOURCE DE REVENU PRINCIPALE\n\n"
        "2️⃣ POUR EN FAIRE UNE SOURCE DE REVENU SECONDAIRE\n\n"
        "3️⃣ POUR ATTEINDRE UNE LIBERTE FINANCIERE COMPLETE\n\n"
        "🚨 ATTENTION 🚨\n"
        "✍️ Réponds maintenant par **1**, **2** ou **3**.\n",
        parse_mode='Markdown'
    )
    return WHY_WELCOME

async def get_numero_whatsapp_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    context.user_data["why"] = why_map[response]
    
    await update.message.reply_text(
        "`📞 Quel est ton numéro whatsapp ?`\n\n"
        "`🌍 Envoie sous le format internationnal, ex: +229 01 97 20 31 88`",
        parse_mode="Markdown"
    )
    
    return NUMERO_WHATSAPP_WELCOME

async def get_mail_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Merci d'envoyer ton numéro."
            )
        return NUMERO_WHATSAPP_WELCOME
    
    context.user_data["phone"] = update.message.text
    
    await update.message.reply_text(
        "📧 `Pour traiter tes demandes en priorité à l'avenir, redonne-nous ton adresse mail, vu que tu fais maintenant partie de la famille.`\n\n"
        "➡️ `Envoie uniquement ton mail, par exemple : fiacrekpanou@gmail.com`",
        parse_mode='Markdown'
    )
    
    return MAIL_WELCOME

async def get_name_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or "@" not in update.message.text:
        await update.message.reply_text("❌ Merci d'envoyer une adresse email valide.")
        return MAIL_WELCOME
    
    context.user_data["email"] = update.message.text
    
    await update.message.reply_text(
        "📌 Nous y sommes presque !\n"
        "Indique-moi simplement ton *nom complet* pour recevoir ton mail définitif "
        "de confirmation à la *Grande Conférence* 🎉\n\n"
        "✍️ Renvoie-moi uniquement ton *nom complet*.\n"
        "👉 Exemple : *Fiacre KPANOU*",
        parse_mode="Markdown"
    )
    
    return NOM_WELCOME

async def last_step_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["name"] = update.message.text
    prenom = context.user_data.get("name")
    
    # Réponse immédiate à l'utilisateur
    await update.message.reply_text(
        "⏳ Inscription en cours de traitement...\n"
        "Tu recevras ta confirmation dans quelques instants ! 🎉",
        parse_mode="Markdown"
    )
    
    # Traitement en arrière-plan
    user_data = {
        "name": context.user_data.get("name"),
        "phone": context.user_data.get("phone"),
        "telegram_id": user.id,
        "contexte_user": "Grande Conference",
        "email": context.user_data.get("email"),
        "level": context.user_data.get("level"),
        "why": context.user_data.get("why"),
        "chat_id": update.effective_chat.id
    }
    
    # Lancer le processus complet en arrière-plan
    create_background_task(
        process_user_registration(user_data, context.bot),
        f"registration_user_{user.id}"
    )
    
    return ConversationHandler.END

async def process_user_registration(user_data: Dict[str, Any], bot):
    """
    Traite l'inscription utilisateur complète en arrière-plan
    """
    try:
        # 1. Sauvegarder l'utilisateur
        await save_user(
            name=user_data["name"],
            phone=user_data["phone"],
            telegram_id=user_data["telegram_id"],
            contexte_user=user_data["contexte_user"],
            email=user_data["email"],
            level=user_data["level"],
            why=user_data["why"]
        )
        logger.info(f"✅ Utilisateur {user_data['telegram_id']} sauvegardé")
        
        # 2. Envoyer l'email en parallèle
        email_task = asyncio.create_task(send_confirmation_email(user_data))
        
        # 3. Ajouter la catégorie en parallèle
        category_task = asyncio.create_task(
            add_categorie(user_data["telegram_id"], "Grande_CONFERENCE_FIN_PROCESS_BOT")
        )
        
        # Attendre les deux tâches
        email_success, category_success = await asyncio.gather(
            email_task, 
            category_task,
            return_exceptions=True
        )
        
        # 4. Envoyer le message de confirmation final
        await send_final_confirmation(
            bot, 
            user_data["chat_id"], 
            user_data["name"], 
            email_success
        )
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de l'inscription de {user_data.get('telegram_id')}: {e}")
        # Envoyer un message d'erreur à l'utilisateur
        try:
            await bot.send_message(
                chat_id=user_data["chat_id"],
                text="❌ Une erreur s'est produite lors de votre inscription. "
                     "Veuillez contacter le support.",
                parse_mode="Markdown"
            )
        except:
            pass

async def send_confirmation_email(user_data: Dict[str, Any]) -> bool:
    """
    Envoie l'email de confirmation
    """
    try:
        subject = "📌 Confirmation et informations pour la Grande Conférence"
        msg = (
            f"Bonjour {user_data['name']},\n\n"
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
        
        success, error = await envoyer_email(
            subjet=subject,
            msge=msg,
            mail=user_data["email"]
        )
        
        return success == 1
        
    except Exception as e:
        logger.error(f"Erreur envoi email pour {user_data['email']}: {e}")
        return False

async def send_final_confirmation(bot, chat_id: int, name: str, email_success: bool):
    """
    Envoie le message de confirmation final à l'utilisateur
    """
    try:
        if email_success:
            message = (
                "📧 Ton inscription est confirmée !\n\n"
                "Je viens de t'envoyer un mail définitif confirmant ta place à la *Grande Conférence*.\n\n"
                "Merci et à très bientôt ! 🎉"
            )
        else:
            message = (
                f"{name}, 🎉 ton inscription à la *Grande Conférence* est confirmée !\n\n"
                "🗓 Date : 2 octobre à partir de 20h heure de cotonou\n"
                "🔗 Le lien de la conférence te sera envoyé via :\n"
                "- Mon canal Telegram (vous y êtes déjà)\n"
                "- Par WhatsApp\n"
                "- Directement via l'assistant bot si tu le souhaites\n\n"
                "📧 Je n'ai pas pu t'envoyer le mail de confirmation "
                "mais ne t'inquiète pas : tout est bien enregistré.\n\n"
                "Nous avons hâte de te retrouver pour cet événement exceptionnel !\n\n"
            )
        
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Erreur envoi confirmation finale: {e}")

# Fonction pour monitorer et alerter sur la charge
async def monitor_system_load():
    """
    Monitore la charge du système et envoie des alertes si nécessaire
    """
    while True:
        await asyncio.sleep(30)  # Vérifier toutes les 30 secondes
        
        active_tasks = len(BACKGROUND_TASKS)
        queue_size = STATS["tasks_in_queue"]
        
        # Alerte si la file d'attente devient trop importante
        if queue_size > 100:
            logger.warning(f"🔥 CHARGE ÉLEVÉE: {queue_size} tâches en attente")
            
            # Envoyer une alerte par email
            asyncio.create_task(
                envoyer_email(
                    subjet='🚨 ALERTE - Charge élevée du bot',
                    msge=f"File d'attente: {queue_size} tâches\n"
                         f"Tâches actives: {active_tasks}\n"
                         f"Slots libres: {TASK_SEMAPHORE._value}/50\n"
                         f"Statistiques: {STATS}",
                    mail='abdoulledev@gmail.com'
                )
            )
        
        # Log des statistiques normales
        if queue_size > 0:
            logger.info(f"📊 File d'attente: {queue_size}, Tâches actives: {active_tasks}")

# Fonction pour adapter dynamiquement la limite
async def adaptive_semaphore_management():
    """
    Gère dynamiquement la limite du semaphore selon la charge
    """
    global TASK_SEMAPHORE
    
    while True:
        await asyncio.sleep(60)  # Vérifier toutes les minutes
        
        queue_size = STATS["tasks_in_queue"]
        current_limit = 50  # Limite actuelle fixe
        
        # Si la file d'attente est très importante, on pourrait augmenter temporairement
        # ATTENTION: Cela consomme plus de ressources serveur
        if queue_size > 200:
            logger.warning("⚡ Considérer l'augmentation de la limite du semaphore")
            # Optionnel: Créer un nouveau semaphore avec une limite plus élevée
            TASK_SEMAPHORE = asyncio.Semaphore(100)  # Augmenter temporairement

# ConversationHandler optimisé
conv_handler_welcome = ConversationHandler(
    entry_points=[CallbackQueryHandler(get_level_welcome, pattern='^(enregistre)$')],
    states={
        LEVEL_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_why_welcome)],
        WHY_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_numero_whatsapp_welcome)],
        NUMERO_WHATSAPP_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mail_welcome)],
        MAIL_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name_welcome)],
        NOM_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, last_step_welcome)],
    },
    fallbacks=[],
    per_chat=True,
    per_user=True,
    conversation_timeout=300,  # 5 minutes timeout
)

# Fonction pour nettoyer les tâches terminées (optionnel, pour éviter l'accumulation)
async def cleanup_finished_tasks():
    """
    Nettoie périodiquement les tâches terminées
    """
    while True:
        await asyncio.sleep(300)  # Toutes les 5 minutes
        finished_tasks = [task for task in BACKGROUND_TASKS if task.done()]
        for task in finished_tasks:
            BACKGROUND_TASKS.discard(task)
        if finished_tasks:
            logger.info(f"🧹 Nettoyé {len(finished_tasks)} tâches terminées")

# Démarrer les services de monitoring (à appeler au démarrage du bot)
async def start_monitoring_services():
    """
    Démarre tous les services de monitoring en arrière-plan
    """
    asyncio.create_task(cleanup_finished_tasks())
    asyncio.create_task(monitor_system_load())
    asyncio.create_task(adaptive_semaphore_management())
    logger.info("🚀 Services de monitoring démarrés")









     