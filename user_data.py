import sqlite3
from telegram import Update
from telegram.ext import ContextTypes


from constance import ASK_IDS
ADMIN_ID = 571718066  # à remplacer par ton ID réel

from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler



async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return

    try:
        telegram_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Fournis un ID Telegram valide après la commande.")
        return

    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    # --- 1. Infos utilisateur ---
    cursor.execute('''
        SELECT name, phone, country, email, motivation, level, created_at
        FROM users
        WHERE telegram_id = ?
    ''', (telegram_id,))
    user = cursor.fetchone()

    if not user:
        await update.message.reply_text("❌ Utilisateur non trouvé.")
        return

    msg_user = f"👤 *Infos de l’utilisateur ({telegram_id}) :*\n"
    msg_user += f"• Nom : {user[0]}\n• Téléphone : {user[1]}\n• Pays : {user[2]}\n"
    msg_user += f"• Email : {user[3] or '—'}\n• Motivation : {user[4] or '—'}\n"
    msg_user += f"• Niveau : {user[5] or '—'}\n• Inscrit le : {user[6]}"

    await update.message.reply_text(msg_user, parse_mode="Markdown")

    # --- 2. Catégories ---
    cursor.execute("SELECT name_categorie FROM categories WHERE id_user = ?", (telegram_id,))
    cats = [row[0] for row in cursor.fetchall()]
    msg_cat = "📂 *Catégorie(s) :*\n" + ("\n".join(f"• {c}" for c in cats) if cats else "Aucune catégorie trouvée.")
    await update.message.reply_text(msg_cat, parse_mode="Markdown")

    # --- 3. Derniers messages ---
    cursor.execute('''
        SELECT message_text, answer, message_type, created_at
        FROM messages
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 5
    ''', (telegram_id,))
    msgs = cursor.fetchall()

    if msgs:
        msg_msg = "💬 *5 derniers messages :*"
        for i, m in enumerate(msgs, 1):
            msg_msg += f"\n\n{i}. ✉️ {m[0]}\n↪️ {m[1] or '—'}\n📂 {m[2]} | 🕒 {m[3]}"
    else:
        msg_msg = "💬 Aucun message enregistré pour cet utilisateur."

    await update.message.reply_text(msg_msg, parse_mode="Markdown")

    conn.close()



async def start_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if  update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Accès réservé à l’administrateur.")
        return

    try:
        telegram_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Fournis un ID Telegram valide après la commande.")
        return


    user_id_to_delete = int(telegram_id )

    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Vérifier si user existe
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id_to_delete,))
    user = cursor.fetchone()
    if not user:
        await update.message.reply_text("⚠️ Aucun utilisateur trouvé avec cet ID.")
        conn.close()
        return ConversationHandler.END

    # Supprimer dans les tables liées
    cursor.execute("DELETE FROM exam_user WHERE id_user = ?", (user_id_to_delete,))
    cursor.execute("DELETE FROM categories WHERE id_user = ?", (user_id_to_delete,))
    cursor.execute("DELETE FROM messages WHERE user_id = ?", (user_id_to_delete,))
    cursor.execute("DELETE FROM users WHERE telegram_id = ?", (user_id_to_delete,))
    cursor.execute("DELETE FROM categories WHERE id_user = ?", (user_id_to_delete,))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ L'utilisateur {user_id_to_delete} et ses données ont été supprimés.")
    return ConversationHandler.END

