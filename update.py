import sqlite3
from database.database import init_db

def alter_users_table():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Vérifie si les colonnes existent déjà
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'why' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN why TEXT")

    if 'what' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN what TEXT")

      

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
alter_users_table()
create_user_default_table()

create_video_table()