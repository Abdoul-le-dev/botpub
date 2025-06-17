import sqlite3

conn = sqlite3.connect("preinscriptions.db")
cursor = conn.cursor()

# On ajoute la colonne telegram_id (si elle n’existe pas déjà)
try:
    cursor.execute("ALTER TABLE users ADD COLUMN telegram_id INTEGER")
    print("✅ Colonne 'telegram_id' ajoutée.")
except Exception as e:
    print("⚠️ Erreur ou colonne déjà existante :", e)

conn.commit()
conn.close()