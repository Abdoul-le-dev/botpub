import sqlite3
from database.database import init_db


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

create_messages_table()