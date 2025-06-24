import os
from telegram import Update
from database.database import init_db
from database.database import save_user
from database.database import user_exists
from database.database import save_message
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ChatJoinRequestHandler,CallbackQueryHandler, Application, CommandHandler, MessageHandler, filters, ContextTypes, PollAnswerHandler,ConversationHandler
import sqlite3
import pandas as pd
import random
import string
from telegram import Update
from telegram.ext import ContextTypes

from telegram.error import BadRequest

from dotenv import load_dotenv

load_dotenv()

token = os.getenv("token")

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
