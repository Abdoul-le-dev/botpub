import sqlite3
from database.database import init_db



def create_tables_exercices():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Table categorie_exercice
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categorie_exercice (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            admin_verify BOOLEAN DEFAULT 0
        )
    ''')

    # Table exercice
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exercice (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            explanation TEXT,
            categorie_id INTEGER NOT NULL,
            FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id)
                ON DELETE CASCADE
        )
    ''')

    # Table resultat_student_question
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS resultat_student_question (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_user INTEGER NOT NULL,
            categorie_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            answer TEXT,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            second_time BOOLEAN DEFAULT 0,         
            FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id)
                ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES exercice(id)
                ON DELETE CASCADE
        )
    ''')

    # Table resultat_student_day
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS resultat_student_day (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_user INTEGER NOT NULL,
            categorie_id INTEGER NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            note REAL,
            second_time BOOLEAN DEFAULT 0,      
            FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id)
                ON DELETE CASCADE
                  
        )
    ''')

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS args (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_user INTEGER NOT NULL,
    args TEXT NOT NULL,
    use_it INTEGER NOT NULL CHECK(use_it IN (0, 1))
    )
    """)

    print('Tables créées ou déjà existantes.')
    conn.commit()
    conn.close()

def create_column():
    try:
        conn = sqlite3.connect('preinscriptions.db')
        cursor = conn.cursor()

        # Suppression des tables
        try:
            cursor.execute("DROP TABLE IF EXISTS resultat_student_question")
            cursor.execute("DROP TABLE IF EXISTS resultat_student_day")
            print('✅ Tables supprimées.')
        except sqlite3.OperationalError as e:
            print(f"⚠️ Erreur lors de la suppression des tables : {e}")

        conn.commit()

    except sqlite3.Error as e:
        print(f"❌ Erreur de connexion à la base : {e}")


    
# Exemple d'utilisation
if __name__ == "__main__":
    create_tables_exercices()

