from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ConversationHandler

from database.database import liste_categories
from telegram.error import BadRequest
from telegram import InputFile

import sqlite3
import asyncio

WHO,CHOOSE_FORMAT,GET_MEDIA, GET_TEXT = range(4)
CHOOSE_TYPES =range(1)
ADMIN_ID = 571718066 #571718066

def limit_text(text, max_length=4096):
    """
    Coupe le texte à max_length caractères si nécessaire et ajoute "…".
    """
    if len(text) > max_length:
        return text[:max_length-1] + "…"
    return text

async def handle_who(update, context):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Désolé, cette commande est réservée à l’administrateur.")
        return ConversationHandler.END

    await update.message.reply_text(
        f"Choice : \n 1-Challenge \n 2- All",
        reply_markup=ReplyKeyboardRemove()
    )

    # Pour les autres formats : texte seul, image seule, vidéo seule
   
    return WHO

async def choose_format(update, context):


    choix = update.message.text[0]
    #user = update.effective_user.first_name or "toi"
    if choix not in {'1','2'}:
        await update.message.reply_text(f"❌ , ton choix n'est pas valide. Merci de choisir parmi les options.")
        return CHOOSE_FORMAT
    context.user_data["who"] = choix

   
 
    await update.message.reply_text(
        "📤 Choisis le format de ton message à diffuser, en envoyant simplement le **chiffre correspondant** :\n\n"
        "1 - Texte\n"
        "2 - Image + texte\n"
        "3 - Vidéo + texte\n"
        "4 - Image\n"
        "5 - Vidéo\n\n"
        "NB: Veuillez a ce que le texte ne sois pas trop long (max 4096 caractères)",
        
    )
    return CHOOSE_FORMAT



async def handle_format_choice(update, context):
    choix = update.message.text[0]
    #user = update.effective_user.first_name or "toi"
    if choix not in {'1','2','3','4','5'}:
        await update.message.reply_text(f"❌ , ton choix n'est pas valide. Merci de choisir parmi les options.")
        return CHOOSE_FORMAT
    context.user_data["format"] = choix

    if choix in {'2', '3'}:  # Image + texte ou Vidéo + texte
        type_media = "image" if choix == "2" else "vidéo"
        await update.message.reply_text(
            f"📁 , envoie maintenant ton fichier {type_media}.",
            reply_markup=ReplyKeyboardRemove()
        )
        return GET_MEDIA
    
    

    # Pour les autres formats : texte seul, image seule, vidéo seule

    await update.message.reply_text(
        f"✏️ , envoie maintenant ton contenu (texte, image ou vidéo selon ton choix).",
        reply_markup=ReplyKeyboardRemove()
    )
    return GET_TEXT
   
   


async def handle_format_choices(update, context):
    choix = update.message.text[0]
    user = update.effective_user.first_name or "toi"
    if choix not in {'1','2'}:
        await update.message.reply_text(f"❌ , ton choix n'est pas valide. Merci de choisir parmi les options.")
        return CHOOSE_FORMAT
    context.user_data["format"] = choix

    if choix in {'2', '3'}:  # Image + texte ou Vidéo + texte
        type_media = "image" if choix == "2" else "vidéo"
        await update.message.reply_text(
            f"📁 , envoie maintenant ton fichier {type_media}.",
            reply_markup=ReplyKeyboardRemove()
        )
        return GET_MEDIA

    # Pour les autres formats : texte seul, image seule, vidéo seule
    await update.message.reply_text(
        f"✏️ , envoie maintenant ton contenu (texte, image ou vidéo selon ton choix).",
        reply_markup=ReplyKeyboardRemove()
    )
    return GET_TEXT

async def get_media(update, context):
    choix = context.user_data["format"]
    user = update.effective_user.first_name or "toi"

    if choix == "2":  # Image + texte
        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
            context.user_data["media_file_id"] = photo_file.file_id
            await update.message.reply_text(f"👍 , image reçue avec succès.")
        else:
            await update.message.reply_text(f"❌ , ce n'est pas une image valide. Merci d'envoyer une image.")
            return GET_MEDIA

    elif choix == "3":  # Vidéo + texte

     if update.message.video:
        try:
            # Essaye d'obtenir le fichier depuis Telegram
            video_file = await update.message.video.get_file()
            context.user_data["media_file_id"] = video_file.file_id
            await update.message.reply_text("👍 Vidéo reçue avec succès depuis Telegram.")
            
            # Optionnel : tu peux aussi la télécharger localement
            # await video_file.download_to_drive(custom_path="video.mp4")

        except BadRequest as e:
            # Si le fichier est trop gros
            if "File is too big" in str(e):
                await update.message.reply_text(
                    "⚠️ La vidéo est trop volumineuse pour être téléchargée via Telegram.\n"
                    " Sauvegarder localement..."
                )
                try:
                    # Téléchargement local manuel (si possible)
                    file_path = f"video_.mp4"

                    context.user_data["media_file_id"] = file_path
                    await update.message.video.get_file()  # Peut échouer à nouveau
                    await update.message.video.download_to_drive(custom_path=file_path)
                    await update.message.reply_text(f"✅ Vidéo enregistrée localement ")
                except Exception as ex:
                    await update.message.reply_text(
                        f"❌ Impossible de télécharger la vidéo localement : {ex}"
                    )
                    return GET_MEDIA
            else:
                # Autres erreurs Telegram
                await update.message.reply_text(f"❌ Erreur : {e}")
                return GET_MEDIA

    else:
        await update.message.reply_text("❌ Ce n'est pas une vidéo valide. Merci d'envoyer une vidéo.")
        return GET_MEDIA
    
        if update.message.video:
            video_file = await update.message.video.get_file()
            context.user_data["media_file_id"] = video_file.file_id
            await update.message.reply_text(f"👍 , vidéo reçue avec succès.")
        else:
            await update.message.reply_text(f"❌ , ce n'est pas une vidéo valide. Merci d'envoyer une vidéo.")
            return GET_MEDIA

    await update.message.reply_text(f"✏️ , envoie maintenant le texte associé à ce fichier.")
    return GET_TEXT

async def get_text(update, context):
    user = update.effective_user.first_name or "toi"
    texte = update.message.text
    context.user_data["text_content"] = texte
    context_user_data = context.user_data.copy()  # Copie des données utilisateur pour la diffusion
    await update.message.reply_text(f"✅ , ton message est prêt à être diffusé ! ")
    
    asyncio.create_task(broadcast_messages(context.bot, update.effective_user.id, context.user_data))
    #await broadcast_messages(context.bot, update.effective_user.id, context_user_data)
    return ConversationHandler.END




async def broadcast_messages(bot, admin_id, context_user_data):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    categorie = 'challenge10000usd'  # Valeur par défaut


    whos = context_user_data.get("who")
    if whos == "2": 

        cursor.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
    elif whos == "1":   
    #cursor.execute("SELECT id_user FROM categories WHERE id_user IS NOT NULL")
        cursor.execute("SELECT id_user FROM categories WHERE name_categorie =?", (categorie,))
    else:
        await bot.send_message(admin_id, "❌ Error contact l'administrateur technique.")
        return    
    rows = cursor.fetchall()
    conn.close()

    user_ids = [row[0] for row in rows]
    total = len(user_ids)
    sent = 0
    await bot.send_message(admin_id,
        f"📤 total: {total}")

    est = round(total * 0.1 / 60, 2)

    if total == 0:
        await bot.send_message(admin_id, "❌ Aucun utilisateur à contacter.")
        return
    

    format_choisi = context_user_data.get("format")

    
    texte = context_user_data.get("text_content", "")
    media_file_id = context_user_data.get("media_file_id")
 

    # Vérification préalable : si média requis mais absent => stop
    if format_choisi in {"2", "3", "4", "5"} and not media_file_id:
        await bot.send_message(admin_id,
            "❌ Échec de l'envoi : le fichier média demandé est manquant. Diffusion annulée.")
        return

    await bot.send_message(admin_id,
        f"📤 Envoi du message à {total} utilisateurs en cours...\n⏳ Estimé : {est} min")

    
    for idx, user_id in enumerate(user_ids, start=1):
        try:
            if format_choisi == "1":  # Texte seul
                await bot.send_message(chat_id=user_id, text=texte)

            elif format_choisi == "2":  # Image + texte
                await bot.send_photo(chat_id=user_id, photo=media_file_id, caption=texte)

            elif format_choisi == "3":  # Vidéo + texte

                with open(media_file_id, "rb") as video_file:
                    await bot.send_video(chat_id=user_id, video=InputFile(video_file))

                #await bot.send_video(chat_id=user_id, video=media_file_id, caption=texte)

            elif format_choisi == "4":  # Image seule
                await bot.send_photo(chat_id=user_id, photo=media_file_id)

            elif format_choisi == "5":  # Vidéo seule
                await bot.send_video(chat_id=user_id, video=media_file_id)

            else:
                await bot.send_message(chat_id=user_id, text=texte)

            sent += 1
            print(f"sent: {sent} messages")

        except Exception as e:
            print(f"Erreur en envoyant à {user_id}: {e}")
            pass

        # Suivi en 1/3, 2/3, fin
        if idx == total // 3:
            await bot.send_message(admin_id, "✅ 1/3 du message envoyé")
        elif idx == (2 * total) // 3:
            await bot.send_message(admin_id, "✅ 2/3 du message envoyé")
        elif idx == total:
            await bot.send_message(admin_id, f"✅ Message terminé — envoyé à {sent} utilisateurs")

        await asyncio.sleep(0.1)


async def user_list_in_categorie(update, context):
    
    lists = liste_categories()
    indice =0
    msg  = ""
    for list in lists:
        
        indice +=1
        msg  += str(indice) + "-"+list[1] + "\n"
    
    msg = limit_text(msg, 4000)  # Limite le message à 4000 caractères pour éviter les erreurs Telegram
    await update.message.reply_text(msg
        
    )

    await update.message.reply_text('Revois le choix de la catégorie (en envoyant simplement le **chiffre correspondant** ) :\n\n'
        
    )
    return CHOOSE_TYPES

async def user_list_in_categories(update, context):

    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    categorie = 'challenge10000usd'  # Valeur par défaut
    #cursor.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
    #cursor.execute("SELECT id_user FROM categories WHERE id_user IS NOT NULL")
    cursor.execute("SELECT id_user FROM categories WHERE name_categorie =?", (categorie,))
    rows = cursor.fetchall()
    conn.close()

    user_ids = [row[0] for row in rows]
    total = len(user_ids)

    await update.message.reply_text(f"📤 total: {total} dans la catégorie {categorie}")
    return ConversationHandler.END
async def user_list_in_categorie(update, context):

    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    categorie = 'Grande_CONFERENCE_FIN_PROCESS_BOT'  # Valeur par défaut
    #cursor.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
    #cursor.execute("SELECT id_user FROM categories WHERE id_user IS NOT NULL")
    cursor.execute("SELECT id_user FROM categories WHERE name_categorie =?", (categorie,))
    rows = cursor.fetchall()
    conn.close()

    user_ids = [row[0] for row in rows]
    total = len(user_ids)

    await update.message.reply_text(f"📤 total: {total} dans la catégorie {categorie}")
    return ConversationHandler.END