import sqlite3
from datetime import datetime

def init_db():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            country TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()




def save_user(name, phone, country=None, telegram_id=None):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Création de la table si elle n’existe pas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            country TEXT,
            created_at TEXT NOT NULL
        )
    ''')

    # Date et heure actuelles
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Insertion des données
    cursor.execute('''
        INSERT INTO users (name, phone, country, created_at,telegram_id)
        VALUES (?, ?, ?, ?,?)
    ''', (name, phone, country, now,telegram_id))

    conn.commit()
    conn.close()

def user_exists(telegram_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()

    conn.close()
    return result is not None