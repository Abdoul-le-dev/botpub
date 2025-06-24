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

    # Récupérer tous les utilisateurs valides
    cursor.execute("SELECT id, name, country, telegram_id FROM users")
    all_users = cursor.fetchall()
    user_map = {u[0]: u for u in all_users}
    all_user_ids = set(user_map.keys())

    # 1. Tirage au hasard 10 utilisateurs (id >= 6000)
    eligible_random_ids = [uid for uid in all_user_ids if uid >= 6000]
    random.shuffle(eligible_random_ids)
    ids_random = eligible_random_ids[:10]

    # 2. Top 8 utilisateurs par nombre de messages (décroissant)
    cursor.execute('''
        SELECT user_id, COUNT(*) as total
        FROM messages
        GROUP BY user_id
        ORDER BY total DESC
        LIMIT 8
    ''')
    top_8_raw = cursor.fetchall()
    # Extrait uniquement les ids top 8
    top_8_ids = [uid for uid, _ in top_8_raw]

    # 3. Filtrer top 8 pour exclure ceux déjà dans ids_random
    top_8_filtered = [uid for uid in top_8_ids if uid not in ids_random and uid in all_user_ids]

    # 4. Combiner les listes et assurer unicité
    gagnants_set = set(ids_random)
    gagnants_set.update(top_8_filtered)

    # 5. Vérifier si on a au moins 18 gagnants valides, sinon compléter aléatoirement
    if len(gagnants_set) < 18:
        remaining_needed = 18 - len(gagnants_set)
        # candidats restants exclus déjà pris
        candidats_restants = list(all_user_ids - gagnants_set)
        random.shuffle(candidats_restants)
        gagnants_set.update(candidats_restants[:remaining_needed])

    gagnants_list = list(gagnants_set)

    # 6. Ajouter 2 gagnants fixes
    lignes = []
    for uid in gagnants_list:
        user = user_map.get(uid)
        if user:
            lignes.append(f"Nom : {user[1]} | Prénom : - | Pays : {user[2]} | ID Telegram : {user[3]}")

    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")
    lignes.append("Nom : Rico | Prénom : Gabin | Pays : Afrique du Sud | ID Telegram : 1234")

    # 7. Générer PDF
    filename = 'gagnants_juin_2025.pdf'
    generate_pdf(filename, lignes)

    # 8. Envoyer message d’intro
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="🎉 M Fiacre KPANOU, voici la liste des 20 gagnants du concours de Juin 2025 :"
    )

    # 9. Envoyer les messages un par un
    for i, ligne in enumerate(lignes, start=1):
        await context.bot.send_message(chat_id=update.effective_user.id, text=f"{i}. {ligne}")

    # 10. Envoyer PDF
    with open(filename, 'rb') as doc:
        await context.bot.send_document(chat_id=update.effective_user.id, document=doc)

    conn.close()
