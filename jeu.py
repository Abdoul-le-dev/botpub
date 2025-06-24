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

    # Charger tous les utilisateurs valides
    cursor.execute("SELECT id, name, country, telegram_id FROM users")
    all_users = cursor.fetchall()
    all_user_ids = {u[0] for u in all_users}
    user_data = {u[0]: u for u in all_users}

    # --- 1. Tirage aléatoire (10 IDs avec id >= 6000) ---
    eligible_random_ids = [uid for uid in all_user_ids if uid >= 6000]
    random.shuffle(eligible_random_ids)
    ids_random = eligible_random_ids[:10]

    # --- 2. Top 8 utilisateurs (par message) puis tri croissant ---
    cursor.execute('''
        SELECT user_id, COUNT(*) as total
        FROM messages
        GROUP BY user_id
        ORDER BY total DESC
        LIMIT 20
    ''')
    top_users_raw = cursor.fetchall()

    top_users_valid = [(uid, total) for uid, total in top_users_raw if uid in all_user_ids and uid not in ids_random]
    top_users_sorted = sorted(top_users_valid[:8], key=lambda x: x[1])  # tri croissant

    top_ids = [uid for uid, _ in top_users_sorted]

    # --- 3. Fusionner les gagnants ---
    final_ids = ids_random + top_ids

    # --- 4. Génération de la liste formatée ---
    lignes = []
    for uid in final_ids:
        user = user_data.get(uid)
        if user:
            lignes.append(f"Nom : {user[1]} | Prénom : - | Pays : {user[2]} | ID Telegram : {user[3]}")

    # --- 5. Ajout des deux gagnants fixes ---
    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")
    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")

    # --- 6. Générer PDF ---
    filename = 'gagnants_juin_2025.pdf'
    generate_pdf(filename, lignes)

    # --- 7. Envoi à l’admin ---
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="🎉 M Fiacre KPANOU, voici les 20 gagnants du concours de Juin 2025 :"
    )

    for i, ligne in enumerate(lignes, start=1):
        await context.bot.send_message(chat_id=update.effective_user.id, text=f"{i}. {ligne}")

    with open(filename, 'rb') as doc:
        await context.bot.send_document(chat_id=update.effective_user.id, document=doc)
