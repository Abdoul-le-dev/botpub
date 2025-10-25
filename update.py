import sqlite3
from database.database import init_db

def alter_users_table():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Vérifie si les colonnes existent déjà
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'expectations' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN expectations TEXT")   

    if 'discover' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN discover TEXT")       



      

    conn.commit()
    conn.close()
    print("✅ Modifications terminées avec succès.")

def create_messages_table():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message_id INTEGER,
            message_text TEXT NOT NULL,       
            answer TEXT,
            message_type TEXT,
            created_at TEXT NOT NULL
        )
    ''')

    print('Table messages créée ou déjà existante.')
    conn.commit()
    conn.close()

#alter_users_table()
#create_messages_table()
def create_categories_table():
    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_user INTEGER NOT NULL,
            name_categorie TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()

def create_video_table():
    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_name TEXT NOT NULL,
            file_id TEXT,       
            created_at TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()    
def create_user_default_table():
    conn = sqlite3.connect("preinscriptions.db")
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usersdefault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,  
            created_at TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()        


#create_categories_table()
#alter_users_table()
#create_user_default_table()

#create_video_table()
def mail():
    # === 1. Connexion à la base SQLite (ou création si elle n'existe pas) ===
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # === 2. Création de la table si elle n'existe pas déjà ===
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        email TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        mail_envoyer BOOLEAN DEFAULT 0,
        token_utilise BOOLEAN DEFAULT 0
    )
    """)

    conn.commit()

def args_link():
    # === 1. Connexion à la base SQLite (ou création si elle n'existe pas) ===
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # === 2. Création de la table si elle n'existe pas déjà ===
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS args_link (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        token_utilise BOOLEAN DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()

def mail_table():
    # Création de la table mail
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mail_valide (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT UNIQUE NOT NULL,
            psw TEXT NOT NULL,
            nbre_mail_envoyer_jrs INTEGER       
        )
    """)

    conn.commit()
    conn.close()
#mail_table()

def exam_table():
    """Crée la table des examens (structure des épreuves)."""
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS exam (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_name TEXT NOT NULL,           
        id_part_one INTEGER NOT NULL,
        id_part_two INTEGER NOT NULL
    )
    """)

    conn.commit()
    conn.close()


def exam_user():
    """Crée la table des utilisateurs ayant passé l’examen."""
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE exam_user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id TEXT NOT NULL,           
        id_user INTEGER NOT NULL,
        email TEXT NOT NULL,
        user_name TEXT NOT NULL,
        last_name TEXT NOT NULL,                      
        note_one INTEGER DEFAULT 0,
        time_one TEXT DEFAULT NULL,
        note_two INTEGER DEFAULT 0,
        time_two TEXT DEFAULT NULL,
        qr_code TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """)

    conn.commit()
    conn.close()

#exam_table()
#exam_user()    

import sqlite3

def recreate_exam_user_table():
    """Supprime et recrée la table exam_user."""
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Supprimer la table si elle existe
    cursor.execute("DROP TABLE IF EXISTS exam_user")

    # Recréer la table
    cursor.execute("""
    CREATE TABLE exam_user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id TEXT NOT NULL,           
        id_user INTEGER NOT NULL,
        email TEXT NOT NULL,
        user_name TEXT NOT NULL,
        last_name TEXT NOT NULL,                      
        note_one INTEGER DEFAULT 0,
        time_one TEXT DEFAULT NULL,
        note_two INTEGER DEFAULT 0,
        time_two TEXT DEFAULT NULL,
        qr_code TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """)

    conn.commit()
    conn.close()
    print("✅ Table 'exam_user' supprimée et recréée avec succès.")

#recreate_exam_user_table()    

def mails():
    # === 1. Connexion à la base SQLite (ou création si elle n'existe pas) ===
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()


    """Supprime et recrée la table  participants_2nd."""
    # Supprimer la table si elle existe
    cursor.execute("DROP TABLE IF EXISTS  participants_2nd")

    # === 2. Création de la table si elle n'existe pas déjà ===
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS participants_2nd (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        email TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        mail_envoyer BOOLEAN DEFAULT 0,
        token_utilise BOOLEAN DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )
    """)

    conn.commit()

mails()    