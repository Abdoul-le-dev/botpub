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




def save_user(name, phone, country=None, telegram_id=None,contexte_user=None,email=None, motivation=None, level=None,why=None, what=None, expectations=None, discovery=None):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Création de la table si elle n’existe pas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            country TEXT,
            telegram_id INTEGER,
            contexte_user TEXT, 
            email TEXT,
            motivation TEXT,
            level TEXT,
            why TEXT,   
            what TEXT,
            expectations TEXT,
            discovery TEXT,                  
            created_at TEXT NOT NULL
        )
    ''')

    # Date et heure actuelles
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Insertion des données
    cursor.execute('''
        INSERT INTO users (name, phone, country, created_at,telegram_id,contexte_user, email, motivation, level, why, what, expectations,  discover)
        VALUES (?, ?, ?, ?,?,?, ?, ?, ?, ?, ?,?,?)
    ''', (name, phone, country, now,telegram_id,contexte_user, email, motivation, level, why, what, expectations, discovery))

    conn.commit()
    conn.close()

def user_exists(telegram_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,))
    result = cursor.fetchone()

    conn.close()
    return result is not None

def save_message(user_id, message_id, message_text, answer = None, message_type ="text"):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO messages (user_id, message_id,message_text, answer, message_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, message_id, message_text,answer, message_type, now))
    conn.commit()
    conn.close()

def update_user_info(telegram_id, email=None,  expectations=None, discovery=None):
    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE users
        SET email = ?, expectations = ?, discover= ?
        WHERE telegram_id = ?
    ''', (email,  expectations,  discovery, telegram_id))

    conn.commit()
    conn.close() 

def add_categorie(id_user, name_categorie):
    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute('''
        INSERT INTO categories (id_user, name_categorie, created_at)
        VALUES (?, ?, ?)
    ''', (id_user, name_categorie, created_at))

    conn.commit()
    conn.close()       
def user_has_categorie(id_user, name): 
    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    cursor.execute('''
        SELECT 1 FROM categories 
        WHERE id_user = ? AND  name_categorie = ? 
        LIMIT 1
    ''', (id_user, name))
    
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def get_user_info(telegram_id):
    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, name, phone, country, email, motivation, level, created_at
        FROM users
        WHERE telegram_id = ?
    ''', (telegram_id,))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            "ID": row[0],
            "Nom": row[1],
            "Téléphone": row[2],
            "Pays": row[3],
            "Email": row[5],
            "Motivation": row[6],
            "Niveau": row[7],
            "Inscrit le": row[4]
        }
    else:
        return None

def get_file_id(video_name):
    conn = sqlite3.connect("preinscriptions.db")
    cur = conn.cursor()
    cur.execute("SELECT file_id FROM videos WHERE video_name=?", (video_name,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None 

def save_file_id(video_name, file_id):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect("preinscriptions.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO videos (video_name, file_id,created_at) VALUES (?, ?, ?)", (video_name, file_id,now))
    conn.commit()
    conn.close()   

def save_user_default(user_id):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect("preinscriptions.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO usersdefault (user_id,created_at) VALUES (?, ?)", (user_id,now))
    conn.commit()
    conn.close() 
    print("User default saved:", user_id)      
