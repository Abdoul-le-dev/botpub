import sqlite3
from database.database import init_db

def alter_users_table():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Vérifie si les colonnes existent déjà
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'email' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")

    if 'motivation' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN motivation TEXT")

    if 'level' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN level TEXT")    

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

create_categories_table()
alter_users_table()