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

    # Charger tous les IDs
    cursor.execute("SELECT id FROM users")
    all_user_ids = [row[0] for row in cursor.fetchall()]

    # 1. Tirage aléatoire (max 10)
    random_ids = []
    random_pool = [uid for uid in all_user_ids if uid >= 6000]
    while len(random_ids) < 10 and random_pool:
        uid = random.choice(random_pool)
        if uid not in random_ids:
            random_ids.append(uid)

    # 2. Top 8 utilisateurs actifs
    used_ids = set(random_ids)
    cursor.execute(f'''
        SELECT user_id, COUNT(*) as total
        FROM messages
        WHERE user_id NOT IN ({','.join(['?'] * len(used_ids))})
        GROUP BY user_id
        ORDER BY total DESC
        LIMIT 15
    ''', tuple(used_ids))
    top_ids = []
    for row in cursor.fetchall():
        uid = row[0]
        if uid in all_user_ids and uid not in used_ids:
            top_ids.append(uid)
            used_ids.add(uid)
        if len(top_ids) == 8:
            break

    # 3. Récupération des infos
    ids_final = random_ids + top_ids
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
            print(f"⚠️ ID {uid} absent de la table users.")

    # 4. Ajout des 2 personnes fixes
    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")
    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")

    # 5. Générer PDF
    filename = 'gagnants_juin_2025.pdf'
    generate_pdf(filename, lignes)

    # 6. Envoi des gagnants à l'admin
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="🎉 M Fiacre KPANOU, voici les 20 gagnants du concours de Juin 2025 :"
    )

    for i, ligne in enumerate(lignes, start=1):
        await context.bot.send_message(chat_id=update.effective_user.id, text=f"{i}. {ligne}")

    # 7. Envoi du PDF
    with open(filename, 'rb') as doc:
        await context.bot.send_document(chat_id=update.effective_user.id, document=doc)
