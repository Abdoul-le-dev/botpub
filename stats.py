import os
from telegram import Update

from telegram import Update
from telegram.ext import ContextTypes

from telegram.error import BadRequest

import sqlite3

async def last_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Total utilisateurs
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    # Total messages
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]

    # 2 derniers messages
    cursor.execute('''
        SELECT user_id, message_text, answer, message_type, created_at
        FROM messages
        ORDER BY created_at DESC
        LIMIT 2
    ''')
    last = cursor.fetchall()
    conn.close()

    msg = f"📊 *Statistiques :*\n👤 Utilisateurs : {total_users}\n💬 Messages : {total_messages}\n\n🕓 *Derniers messages :*\n"
    for m in last:
        msg += f"\n- 🆔 {m[0]} |\n- message : {m[1]} |\n- 📎 {m[2]} |\n- 🕒 {m[4]}\n- ✏️ {m[3]}"

    await update.message.reply_text(msg, parse_mode="Markdown")
