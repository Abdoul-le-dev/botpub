import random
import sqlite3
from reportlab.pdfgen import canvas
from telegram import Update
from telegram.ext import ContextTypes

def generate_pdf(filename, lignes):
    c = canvas.Canvas(filename)
    c.setFont("Helvetica", 12)
    c.drawString(50, 800, "🎉 Liste des 20 gagnants - Concours Juin 2025")
    y = 770
    for i, ligne in enumerate(lignes, start=1):
        c.drawString(50, y, f"{i}. {ligne}")
        y -= 20
    c.save()

async def export_and_send_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_path = 'preinscriptions.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Tirage aléatoire unique
    cursor.execute("SELECT id FROM users WHERE id >= 6000")
    all_ids = [row[0] for row in cursor.fetchall()]
    used_ids = set()

    ids_random = set()
    while len(ids_random) < 10 and len(all_ids) > 0:
        uid = random.choice(all_ids)
        if uid not in used_ids:
            ids_random.add(uid)
            used_ids.add(uid)

    # 2. Top 8 utilisateurs actifs hors déjà pris
    cursor.execute(f'''
        SELECT user_id, COUNT(*) as total
        FROM messages
        WHERE user_id NOT IN ({','.join(['?']*len(used_ids))})
        GROUP BY user_id
        ORDER BY total DESC
        LIMIT 8
    ''', tuple(used_ids))
    top_ids = [row[0] for row in cursor.fetchall()]
    used_ids.update(top_ids)

    # 3. Liste finale
    ids_final = list(ids_random) + top_ids
    placeholders = ','.join(['?'] * len(ids_final))
    cursor.execute(f'''
        SELECT id, name, country, telegram_id
        FROM users
        WHERE id IN ({placeholders})
    ''', tuple(ids_final))
    users = cursor.fetchall()
    conn.close()

    user_map = {u[0]: u for u in users}
    lignes = []
    for uid in ids_final:
        user = user_map.get(uid)
        if user:
            lignes.append(f"Nom : {user[1]} | Prénom : - | Pays : {user[2]} | ID Telegram : {user[3]}")
        else:
            print(f"⚠️ ID {uid} est absent de la table users. Ignoré.")

    # Ajout des deux personnes fixes
    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")
    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")

    # Générer PDF
    filename = 'gagnants_juin_2025.pdf'
    generate_pdf(filename, lignes)

    # Message d’intro
    intro = (
        "M Fiacre KPANOU, voici la liste des 20 gagnants du concours de Juin 2025 🎉.\n"
        "Le PDF est joint pour archivage. Bravo aux gagnants !"
    )

    # Envoi au bot admin (async)
    await context.bot.send_message(chat_id=update.effective_user.id, text=intro)
    with open(filename, 'rb') as doc:
        await context.bot.send_document(chat_id=update.effective_user.id, document=doc)
