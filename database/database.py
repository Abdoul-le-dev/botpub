import sqlite3
from datetime import datetime
import asyncio
import json

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



def init_broadcast_history():
    """
    Crée toutes les tables nécessaires si elles n'existent pas.
    Compatible SQLite — utilise PRAGMA table_info() au lieu de SHOW COLUMNS.
    """
    with sqlite3.connect("preinscriptions.db") as conn:
 
        # ── broadcast_history ────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tag         TEXT,
                category    TEXT,
                format      TEXT,
                message     TEXT,
                total       INTEGER,
                sent        INTEGER,
                errors      INTEGER,
                started_at  TEXT,
                finished_at TEXT
            )
        """)
 
        # ── categories_meta ──────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories_meta (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name_categorie  TEXT NOT NULL UNIQUE,
                color           TEXT DEFAULT '#38bdf8',
                description     TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
 
        # ── category_rules ───────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS category_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name_categorie  TEXT NOT NULL,
                trigger_type    TEXT NOT NULL,
                trigger_value   TEXT,
                is_active       INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (name_categorie)
                    REFERENCES categories_meta(name_categorie)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE
            )
        """)
 
        # ── signals ──────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pair            TEXT    NOT NULL,
                direction       TEXT    NOT NULL,
                entry_price     REAL    NOT NULL,
                stop_loss       REAL    NOT NULL,
                take_profit_1   REAL    NOT NULL,
                take_profit_2   REAL,
                take_profit_3   REAL,
                result_pips     REAL    DEFAULT NULL,
                result_percent  REAL    DEFAULT NULL,
                status          TEXT    DEFAULT 'active',
                message_id      INTEGER DEFAULT NULL,
                note            TEXT,
                created_at      TEXT    DEFAULT (datetime('now')),
                closed_at       TEXT    DEFAULT NULL
            )
        """)
 
        # ── trade_journal ────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_journal (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id       INTEGER NOT NULL,
                user_id         INTEGER NOT NULL,
                entry_price     REAL    NOT NULL,
                exit_price      REAL    DEFAULT NULL,
                lot_size        REAL    DEFAULT NULL,
                result_pips     REAL    DEFAULT NULL,
                result_percent  REAL    DEFAULT NULL,
                screenshot_url  TEXT    DEFAULT NULL,
                status          TEXT    DEFAULT 'open',
                note            TEXT,
                created_at      TEXT    DEFAULT (datetime('now')),
                closed_at       TEXT    DEFAULT NULL,
                FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
            )
        """)
 
        # ── trade_comments ───────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_comments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                comment     TEXT    NOT NULL,
                created_at  TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (trade_id) REFERENCES trade_journal(id) ON DELETE CASCADE
            )
        """)
 
        # ────────────────────────────────────────────────────────────────
        # Migration table messages
        # SQLite : PRAGMA table_info() au lieu de SHOW COLUMNS
        # ────────────────────────────────────────────────────────────────
        cursor = conn.execute("PRAGMA table_info(messages)")
        existing_columns = {row[1] for row in cursor.fetchall()}  # row[1] = nom de la colonne
 
        columns_to_add = {
            "broadcast_id":  "INTEGER DEFAULT NULL",
            "media_url":     "TEXT    DEFAULT NULL",
            "status":        "TEXT    DEFAULT 'received'",
            "error_message": "TEXT    DEFAULT NULL",
        }
 
        for col, definition in columns_to_add.items():
            if col not in existing_columns:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {definition}")
                print(f"[DB] ✅ Colonne ajoutée : messages.{col}")
            else:
                print(f"[DB] ⏭️  Colonne déjà présente : messages.{col}")
 
        # SQLite ne supporte pas DROP COLUMN avant la version 3.35
        # On vérifie la version avant d'essayer
        if "message_type" in existing_columns:
            import sqlite3 as _sq
            version = tuple(int(x) for x in _sq.sqlite_version.split("."))
            if version >= (3, 35, 0):
                conn.execute("ALTER TABLE messages DROP COLUMN message_type")
                print("[DB] ✅ Colonne supprimée : messages.message_type")
            else:
                print(f"[DB] ⚠️  SQLite {_sq.sqlite_version} — DROP COLUMN non supporté, message_type conservée")
        else:
            print("[DB] ⏭️  messages.message_type déjà absente")
 
        # Migrer les lignes existantes sans status
        if "status" not in existing_columns:
            conn.execute("UPDATE messages SET status = 'received' WHERE status IS NULL")
            print(f"[DB] ✅ Lignes migrées → status='received'")
 
        conn.commit()
        print("[DB] ✅ init_broadcast_history terminé")

        
def get_conn():
    return sqlite3.connect('preinscriptions.db')

conn = get_conn()

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

db_lock = asyncio.Lock()

async def save_message(user_id, message_id, message_text, answer=None, message_type="text"):
    async with db_lock:  # empêche l'accès concurrent
        conn = sqlite3.connect('preinscriptions.db', timeout=30)  # attend jusqu'à 30s si verrouillé
        conn.execute("PRAGMA journal_mode=WAL;")  # Active Write-Ahead Logging
        cursor = conn.cursor()
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO messages (user_id, message_id, message_text, answer, message_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, message_id, message_text, answer, message_type, now))
        
        conn.commit()
        conn.close()

async def get_data():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, message_id,message_text, answer, message_type, created_at 
            FROM messages 
            ORDER BY created_at DESC
            LIMIT 500 ''')
    
    rows = cursor.fetchall()

    print(rows)

    conversations = {}
    for row in rows:
        user_id, message_id, message_text, answer, message_type, created_at= row
    
        # Initialiser la conversation si pas encore faite
        if user_id not in conversations:

            user_data  = get_user_info(user_id)
            #print(user_data)
            if user_data :

                conversations[user_id] = {
                    "id": user_id,
                    "name": user_data["Nom"],
                    "userId": str(user_id),
                    "lastMessage": message_text,
                    "time": created_at.split(" ")[1][:5],
                    "unread": 0,
                    "messages": []
                }
            else :
                 conversations[user_id] = {
                    "id": user_id,
                    "name": f"User {user_id}",
                    "userId": str(user_id),
                    "lastMessage": message_text,
                    "time": created_at.split(" ")[1][:5],
                    "unread": 0,
                    "messages": []
                }

    
    # Convertir en liste triée ↓
    conversations_list = list(conversations.values())

    # JSON final
    return  json.dumps(conversations_list, indent=4, ensure_ascii=False)

      
async def get_data_users(id) :

    cursor = sqlite3.connect('preinscriptions.db').cursor()
    cursor.execute('''

        SELECT message_id, message_text,answer, message_type, created_at 
            FROM messages 
            WHERE user_id = ?
        ''', (id,))    
    
    rows = cursor.fetchall()

    print(rows)

    if not rows:
        return ['a']

    user_data = get_user_info(id)

    messages = []
    last_message_text = ""
    last_message_time = ""

    for row in rows:
        message_id, message_text, answer, message_type, created_at = row

        # Heure format HH:MM
        time = created_at.split(" ")[1][:5]

        text = message_text if message_type == "received" else answer

        messages.append({
            "id": message_id,
            "text": message_text,
            "type": message_type,  # "received" ou "sent"
            "time": time
        })

        last_message_text = text
        last_message_time = time

    conversations = [
        {
            "id": id,
            "name": user_data["Nom"],
            "userId": str(id),
            "lastMessage": last_message_text,
            "time": last_message_time,
            "unread": 0,
            "messages": messages
        }
    ]


    # Convertir en liste triée ↓
    #conversations_list = list(conversations.values())

    # JSON final
    return  json.dumps(conversations, indent=4, ensure_ascii=False)

    #return conversations

       
       

        


def save_messages(user_id, message_id, message_text, answer = None, message_type ="text"):
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

async def add_categorie(id_user, name_categorie):
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



def add_exercice(question, answer, explanation, categorie_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO exercice (question, answer, explanation, categorie_id)
        VALUES (?, ?, ?, ?)
    ''', (question, answer, explanation, categorie_id))

    conn.commit()
    conn.close()
    print("✅ Nouvel exercice ajouté avec succès.")



def add_categorie_exercice(nom, admin_verify=False):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO categorie_exercice (nom, admin_verify)
        VALUES (?, ?)
    ''', (nom, int(admin_verify)))

    conn.commit()
    categorie_id = cursor.lastrowid  # Récupérer l'ID de la nouvelle catégorie
    conn.close()

    return categorie_id
def add_exercice_with_limit(question, answer, explanation, categorie_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Vérifier combien de questions existent déjà dans cette catégorie
    cursor.execute('SELECT COUNT(*) FROM exercice WHERE categorie_id = ?', (categorie_id,))
    nb_questions = cursor.fetchone()[0]

    if nb_questions >= 10:
        conn.close()
        return False, "❌ Cette catégorie a déjà 10 questions maximum."

    # Insérer la nouvelle question
    cursor.execute('''
        INSERT INTO exercice (question, answer, explanation, categorie_id)
        VALUES (?, ?, ?, ?)
    ''', (question, answer, explanation, categorie_id))

    conn.commit()
    conn.close()
    return True, "✅ Exercice ajouté avec succès."

def verifier_et_valider_categorie(categorie_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Vérifier que la catégorie existe
    cursor.execute('SELECT nom, admin_verify FROM categorie_exercice WHERE id = ?', (categorie_id,))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return False, "❌ Catégorie introuvable."

    nom, admin_verify = result
    if admin_verify:
        conn.close()
        return False, f"⚠️ La catégorie '{nom}' est déjà validée."

    # Mettre à jour admin_verify à 1
    cursor.execute('UPDATE categorie_exercice SET admin_verify = 1 WHERE id = ?', (categorie_id,))
    conn.commit()
    conn.close()
    return True, f"✅ Catégorie '{nom}' validée avec succès."

def get_questions(categorie_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, question, answer, explanation 
        FROM exercice WHERE categorie_id = ? LIMIT 10
    ''', (categorie_id,))
    questions = cursor.fetchall()
    conn.close()
    return questions  # liste de tuples (id, question, answer, explanation)

def save_user_answer(user_id, categorie_id, question_id, user_answer, start_time, end_time, second_time=False):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO resultat_student_question
        (id_user, categorie_id, question_id, answer, time_start, time_end, second_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, categorie_id, question_id, user_answer, start_time, end_time, second_time))
    conn.commit()
    conn.close()
def save_daily_result(user_id, categorie_id, time_start, time_end, note, second_time=False):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO resultat_student_day
        (id_user, categorie_id, time_start, time_end, note, second_time)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, categorie_id, time_start, time_end, note, second_time))
    conn.commit()
    conn.close()



def verify_categorie(name):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute(
        'SELECT admin_verify FROM categorie_exercice WHERE nom = ?',
        (name,)
    )
    row = cursor.fetchone()
    conn.close()
    
    if row is None:
        # La catégorie n'existe pas
        return None
    elif row[0] == 1:
        # La catégorie existe et est vérifiée
        return True
    else:
        # La catégorie existe mais n'est pas vérifiée
        return "non_verify"


def create_args(id_user: int, args_value: str, use_it: bool):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    
    # Vérifier si le user avec cet args existe déjà
    cursor.execute(
        "SELECT 1 FROM args WHERE id_user = ? AND args = ? AND use_it = ?",
        (id_user, args_value,1)
    )
    exists = cursor.fetchone()
    
    if exists:
        print("⚠ Déjà existant : id_user et args correspondent")
        conn.close()
        return "already"
    
    # Insérer seulement si non existant
    cursor.execute(
        "SELECT 1 FROM args WHERE id_user = ? AND args = ? AND use_it = ?",
        (id_user, args_value,0)
    )
    exist = cursor.fetchone()
    if exist:
        print("⚠ Déjà existant : id_user et args correspondent")
        conn.close()
        return ""
    cursor.execute(
        "INSERT INTO args (id_user, args, use_it) VALUES (?, ?, ?)",
        (id_user, args_value, int(use_it))
    )
    conn.commit()
    conn.close()
    print(f"✔ Arg créé : id_user={id_user}, args='{args_value}', use_it={use_it}")
    return "created"


# Fonction pour supprimer un enregistrement par ID
def delete_args(id_user: int):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM args WHERE id_user = ?", (id_user,))
    conn.commit()
    print(f"🗑 Arg avec id={id_user} supprimé.")

def check_if_user(id_user: int):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM args WHERE id_user = ?", (id_user,))
    rows = cursor.fetchall()
    if rows:
        print(f"✅ L'utilisateur {id_user} existe dans args :")
        for row in rows:
            print(row)
        return True
    else:
        print(f"❌ Aucun enregistrement trouvé pour id_user={id_user}")
        return False

def get_user_args(id_user: int):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("SELECT args FROM args WHERE id_user = ? AND use_it = 0 ", (id_user,))
    rows = cursor.fetchone()
    # Formatage simple des résultats en liste de dictionnaires
    result = rows[0] if rows else None
    return result

def get_categories(args):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM categorie_exercice WHERE nom = ?", (args,))
    rows = cursor.fetchone()
    result = rows[0] if rows else None
    return result


def get_categories_exam(args):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM categorie_exercice WHERE id = ?", (args,))
    rows = cursor.fetchone()
    result = rows[0] if rows else None
    return result
   
def update_arg(id_user: int, args_value: str):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "UPDATE args SET use_it = 1 WHERE id_user = ? AND args = ?",
        (id_user, args_value)
    )
    conn.commit()
    updated_rows = cursor.rowcount
    conn.close()

    if updated_rows > 0:
        print(f"✔ Arg mis à jour : id_user={id_user}, args='{args_value}', use_it=1")
        return True
    else:
        print(f"⚠ Aucun enregistrement trouvé pour id_user={id_user} et args='{args_value}'")
        return False   

def get_final_score(user_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    # Récupérer toutes les catégories où l'étudiant a participé
    cursor.execute("""
        SELECT DISTINCT categorie_id
        FROM resultat_student_day
        WHERE id_user = ?
    """, (user_id,))
    categories = cursor.fetchall()

    total_score = 0.0

    for (categorie_id,) in categories:
        # Chercher en priorité second_time = 1
        cursor.execute("""
            SELECT note
            FROM resultat_student_day
            WHERE id_user = ? AND categorie_id = ? AND second_time = 1
            ORDER BY id DESC
            LIMIT 1
        """, (user_id, categorie_id))
        row = cursor.fetchone()

        if row is None:
            # Sinon, prendre la première ligne trouvée
            cursor.execute("""
                SELECT note
                FROM resultat_student_day
                WHERE id_user = ? AND categorie_id = ?
                ORDER BY id DESC
                LIMIT 1
            """, (user_id, categorie_id))
            row = cursor.fetchone()

        if row and row[0] is not None:
            try:
                total_score += float(row[0])  # conversion sécurisée
            except ValueError:
                pass  # si note est invalide, on ignore

    conn.close()
    return total_score


def get_category_questions_report(categorie_id):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            rsq.question_id,
            e.question,
            COUNT(*) AS total_reponses,
            SUM(
                CASE 
                    WHEN LOWER(TRIM(rsq.answer)) = LOWER(TRIM(e.answer)) 
                    THEN 1 ELSE 0 
                END
            ) AS bonnes_reponses,
            ROUND(
                (SUM(
                    CASE 
                        WHEN LOWER(TRIM(rsq.answer)) = LOWER(TRIM(e.answer)) 
                        THEN 1 ELSE 0 
                    END
                ) * 100.0) / COUNT(*), 
                2
            ) AS pourcentage
        FROM resultat_student_question rsq
        JOIN exercice e 
            ON rsq.question_id = e.id
        WHERE rsq.categorie_id = ? 
          AND rsq.second_time = 0
        GROUP BY rsq.question_id, e.question
        ORDER BY pourcentage DESC
    """, (categorie_id,))
    
    results = cursor.fetchall()
    if not results:
        return f"Aucune donnée pour la catégorie {categorie_id}."

    rapport = f"📊 Rapport Catégorie {categorie_id} (première tentative, Vrai/Faux) :\n"
    for q_id, question, total, bonnes, pct in results:
        rapport += f"Q{q_id} : {pct}% ({bonnes}/{total} bonnes réponses)\n"
    return rapport

def delete_user_data_from_db(user_id):
  
    cursor = conn.cursor()

    cursor.execute("DELETE FROM resultat_student_day WHERE id_user = ?", (user_id,))
    cursor.execute("DELETE FROM resultat_student_question WHERE id_user = ?", (user_id,))
    cursor.execute("DELETE FROM resultat_student_question WHERE id_user = ?", (user_id,))
    cursor.execute("DELETE FROM args WHERE id_user = ?", (user_id,))

    conn.commit()
    conn.close()

def delete_all_exercices():
    
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM exercice")
        conn.commit()
        print("Toutes les données de la table 'exercice' ont été supprimées.")
    except sqlite3.Error as e:
        print(f"Erreur lors de la suppression : {e}")
    finally:
        conn.close()  

def verify_name_phone_mail(user_id):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 1
        FROM users
        WHERE telegram_id = ?
          AND email IS NOT NULL AND TRIM(email) <> ''
          AND phone IS NOT NULL AND TRIM(phone) <> ''
    """, (user_id,))

    result = cursor.fetchone() is not None
    conn.close()
    return result

def get_mail_and_name(user_id):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(''' SELECT email, name FROM users WHERE telegram_id = ? ''', (user_id,))

    result =cursor.fetchone()
    conn.close()
    return result



def liste_categories():

    conn = get_conn()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, name_categorie FROM categories")
    rows = cursor.fetchall()

    conn.close()

    return rows

def mail_user(nom, email, token):

    conn = get_conn()
    cursor = conn.cursor()

    # 🔹 Insertion dans la base de données
    cursor.execute("""
    INSERT OR IGNORE INTO participants_2nd (nom, email, token,mail_envoyer, token_utilise )
    VALUES (?, ?, ?, 0,0)
    """, (nom, email, token))

    conn.commit()

def update_mail_status(email):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE participants_2nd
    SET mail_envoyer = 1
    WHERE email = ?
    """, (email,))

    conn.commit()
    conn.close()

def get_unsent_emails():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT nom, email, token FROM participants
    WHERE mail_envoyer = 0
    """)

    rows = cursor.fetchall()
    conn.close()
    return rows

def update_token_used(token):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE participants_2nd
    SET token_utilise = 1
    WHERE token = ?
    """, (token,))

    conn.commit()
    conn.close()

def get_token_exists(token):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT 1 FROM participants_2nd
    WHERE token = ? AND token_utilise = 0
    """, (token,))

    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def mail_token_utilise(email: str) -> bool:
    
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT token_utilise FROM participants_2nd WHERE email = ?", (email,))
    row = cursor.fetchone()

    conn.close()

    if row and row[0]:   # row existe et la colonne token_utilise vaut 1 (True)
        return True
    else:
        return False 


def update_mail_count(user, increment=1):
    """
    Incrémente le compteur de mails envoyés pour un utilisateur.
    
    :param user: l'adresse email ou nom de l'utilisateur
    :param increment: nombre à ajouter au compteur (par défaut 1)
    """
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    
    # Met à jour le compteur en l'incrémentant
    cursor.execute("""
        UPDATE mail_valide
        SET nbre_mail_envoyer_jrs = nbre_mail_envoyer_jrs + ?
        WHERE user = ?
    """, (increment, user))
    
    conn.commit()
    conn.close()

def reset_all_mail_counts():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE mail_valide
        SET nbre_mail_envoyer_jrs = 0
    """)
    
    conn.commit()
    conn.close()        

def add_new_user(user, psw):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO mail_valide (user, psw, nbre_mail_envoyer_jrs)
            VALUES (?, ?, 0)
        """, (user, psw))
        conn.commit()
        print(f"Utilisateur '{user}' ajouté avec succès.")
    except sqlite3.IntegrityError:
        print(f"L'utilisateur '{user}' existe déjà !")
    
    conn.close()    

def get_user_under_limit():
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    
    # Sélection d'un utilisateur dont le compteur est < 2500
    cursor.execute("""
        SELECT user, psw FROM mail_valide
        WHERE nbre_mail_envoyer_jrs < 3000
        ORDER BY nbre_mail_envoyer_jrs ASC
        LIMIT 1
    """)
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        user_email, user_psw = result
        return user_email, user_psw
    else:
        return None, None  # Aucun utilisateur disponible      


def get_user_categories(user_id):
    try:
        conn = sqlite3.connect('preinscriptions.db')  # Remplacez par votre nom de DB
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name_categorie, created_at 
            FROM categories 
            WHERE id_user = ?
            ORDER BY created_at DESC
        ''', (user_id,))
        
        categories = cursor.fetchall()
        conn.close()
        
        return categories
        
    except sqlite3.Error as e:
        print(f"Erreur SQLite: {e}")
        return []


def add_exam(exam_name: str, id_part_one: int, id_part_two: int):
    """
    Ajoute un nouvel examen dans la table exam.
    """
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO exam (exam_name, id_part_one, id_part_two)
    VALUES (?, ?, ?)
    """, (exam_name, id_part_one, id_part_two))

    conn.commit()
    conn.close()

    print(f"✅ Examen '{exam_name}' ajouté avec succès !")

def get_exam_parts(exam_id: int):
    """
    Récupère les identifiants des deux parties (id_part_one et id_part_two)
    pour un examen donné.
    """
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id_part_one, id_part_two
    FROM exam
    WHERE id = ?
    """, (exam_id,))

    result = cursor.fetchone()
    conn.close()

    if result:
        id_part_one, id_part_two = result
        return id_part_one, id_part_two
    else:
        
        return None   

def add_exam_user(id_user: int, email: str,user_name: str,last_name: str, exam_id: str): 
    """
    Ajoute un utilisateur dans la table exam_user
    avant le début de son examen.
    """
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO exam_user (id_user, email, user_name, last_name, exam_id)
    VALUES (?, ?, ?,?,?)
    """, (id_user, email, user_name, last_name, exam_id))

    conn.commit()
    conn.close()

    print(f"✅ Utilisateur {email} (ID: {id_user}) ajouté avec succès dans exam_user.")

def update_exam_user(id_user: int, note: int, time_spent: str,qr_code : str, part: int):
    """
    Met à jour la note et le temps de l'utilisateur pour une partie spécifique.
    
    part = 1 → met à jour note_one et time_one  
    part = 2 → met à jour note_two et time_two
    """
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    if part == 1:
        cursor.execute("""
        UPDATE exam_user
        SET note_one = ?, time_one = ?
        WHERE id_user = ?
        """, (note, time_spent, id_user))

    elif part == 2:
        cursor.execute("""
        UPDATE exam_user
        SET note_two = ?, time_two = ?
        WHERE id_user = ?
        """, (note, time_spent, id_user)) 
    
    elif part == 3:
        cursor.execute("""
        UPDATE exam_user
        SET qr_code = ?
        WHERE id_user = ?
        """, (qr_code, id_user))

    else:
        print("⚠️ Partie invalide : utilisez 1 ou 2 pour 'part'.")
        conn.close()
        return

    conn.commit()
    conn.close()

    print(f"✅ Données de la partie {part} mises à jour pour l’utilisateur {id_user}.")

def get_user_exam(id_user: int):
    """
    Récupère les informations d'examen d'un utilisateur spécifique
    dans la table exam_user.

    Retourne un dictionnaire contenant :
    - email
    - exam_id
    - note_one, time_one
    - note_two, time_two
    - qr_code
    - created_at
    - moyenne
    """
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    SELECT email,user_name, last_name, exam_id, note_one, time_one, note_two, time_two, qr_code, created_at
    FROM exam_user
    WHERE id_user = ?
    """, (id_user,))

    result = cursor.fetchone()
    conn.close()

    if result:
        email, user_name, last_name,exam_id, note_one, time_one, note_two, time_two, qr_code, created_at = result
        moyenne = None
        if note_one is not None and note_two is not None:
            moyenne = (note_one + note_two) / 2

        return {
            "id_user": id_user,
            "email": email,
            "user_name": user_name,
            "last_name": last_name,
            "C" : exam_id,
            "note_one": note_one,
            "time_one": time_one,
            "note_two": note_two,
            "time_two": time_two,
            "qr_code": qr_code,
            "created_at": created_at,
            "moyenne": moyenne
        }
    else:
        print(f"⚠️ Aucun enregistrement trouvé pour l’utilisateur ID {id_user}.")
        return None    


def get_user_exams(email):

    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    SELECT email,user_name, last_name, exam_id, note_one, time_one, note_two, time_two, qr_code, created_at
    FROM exam_user
    WHERE email = ?
    """, (email,))

    result = cursor.fetchone()
    conn.close()

    if result:
        email, user_name, last_name,exam_id, note_one, time_one, note_two, time_two, qr_code, created_at = result
        moyenne = None
        if note_one is not None and note_two is not None:
            moyenne = (note_one + note_two) / 2

        return {

            "email": email,
            "user_name": user_name,
            "last_name": last_name,
            "C" : exam_id,
            "note_one": note_one,
            "time_one": time_one,
            "note_two": note_two,
            "time_two": time_two,
            "qr_code": qr_code,
            "created_at": created_at,
            "moyenne": moyenne
        }
    else:
        print(f"⚠️ Aucun enregistrement trouvé pour l’utilisateur ID {id_user}.")
        return None    



def find_category_duplicates(categorie: str):
    """
    Retourne une liste de tuples (user_id, total, doublons)
    pour tous les user_id qui apparaissent plus d'une fois
    dans la catégorie donnée.
    """
    conn = get_conn()
    cursor = conn.cursor()

    rows = cursor.execute("""
        SELECT id_user, COUNT(*) AS total
        FROM categories
        WHERE name_categorie = ?
        GROUP BY id_user
        HAVING COUNT(*) > 1
        ORDER BY total DESC, id_user ASC
    """, (categorie,)).fetchall()

    conn.close()

    # Format: (user_id, total, doublons = total - 1)
    return [(r[0], r[1], r[1] - 1) for r in rows]


def delete_user_duplicates(user_id: int,
                           categorie: str = "second_challenge10000usd",
                           keep: str = "oldest") -> tuple[int, int | None]:
    """
    Supprime tous les doublons pour (user_id, categorie) et ne garde qu'une seule ligne.
    - keep="oldest": garde le plus ancien (created_at ASC, id ASC)
    - keep="newest": garde le plus récent (created_at DESC, id DESC)

    Retourne (deleted_count, kept_id).
    deleted_count = nombre de lignes supprimées
    kept_id = id de la ligne conservée (None si aucune ligne trouvée)
    """
    order_clause = "created_at ASC, id ASC" if keep == "oldest" else "created_at DESC, id DESC"

    conn = get_conn()
    try:
        cur = conn.cursor()
        # On choisit la ligne à garder
        cur.execute(
            f"""
            SELECT id
            FROM categories
            WHERE id_user = ? AND name_categorie = ?
            ORDER BY {order_clause}
            LIMIT 1
            """,
            (user_id, categorie)
        )
        row = cur.fetchone()
        if not row:
            # Aucun enregistrement pour ce couple (user_id, categorie)
            return (0, None)

        kept_id = row[0]

        # Supprimer tout le reste
        conn.execute("BEGIN")
        before = conn.total_changes
        cur.execute(
            """
            DELETE FROM categories
            WHERE id_user = ? AND name_categorie = ? AND id <> ?
            """,
            (user_id, categorie, kept_id)
        )
        conn.commit()
        deleted_count = conn.total_changes - before
        return (deleted_count, kept_id)
    except Exception as e:
        conn.rollback()
        print(f"[ERREUR] delete_user_duplicates: {e}")
        return (0, None)
    finally:
        conn.close()

async def get_categories_user():
    # On se connecte à la base SQLite
    conn = get_conn()
    cursor = conn.cursor()

    # DISTINCT récupère chaque catégorie une seule fois
    # COUNT compte combien de users sont dans chaque catégorie
    cursor.execute(""" SELECT name_categorie, COUNT(*) as total FROM categories GROUP BY name_categorie;""")

    rows = cursor.fetchall()
    conn.close()

    # On retourne une liste de dicts
    # [{"name": "clients", "total": 847}, {"name": "prospects", "total": 643}]
    return [{"name": row[0], "total": row[1]} for row in rows]


def get_broadcast_history():
    """
    Retourne les 50 dernières campagnes, de la plus récente à la plus ancienne.
    Le front utilisera ça pour remplir la vue Historique.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM broadcast_history
        ORDER BY id DESC
        LIMIT 50
        """).fetchall()

    return [dict(row) for row in rows]

async def drop_category(name_categorie: str):
    """
    Supprime une catégorie complètement :
    - categories_meta (+ category_rules en cascade)
    - Tous les membres dans categories qui ont ce name_categorie
    """
    conn =  get_conn()
    cursor = conn.cursor()
 
    # 1. Supprimer les membres associés dans categories
    cursor.execute(
        "DELETE FROM categories WHERE name_categorie = %s",
        (name_categorie,)
    )
    deleted_members = cursor.rowcount
 
    # 2. Supprimer la meta (supprime aussi les rules en CASCADE)
    cursor.execute(
        "DELETE FROM categories_meta WHERE name_categorie = %s",
        (name_categorie,)
    )
 
    conn.commit()
    cursor.close()
    conn.close()
 
    return {
        "status": "deleted",
        "name_categorie": name_categorie,
        "members_removed": deleted_members
    }


async def init_trading_tables():
    """
    Crée les 3 tables trading si elles n'existent pas.
    Appelé depuis init_db() au boot.
    """
    conn = get_conn()
    cursor = conn.cursor()
 
    # ── signals ─────────────────────────────────────────────────────────
    # Les signaux envoyés par l'admin à la communauté
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pair            VARCHAR(20)  NOT NULL,          -- EURUSD, GBPJPY...
            direction       VARCHAR(10)  NOT NULL,          -- buy | sell
            entry_price     DECIMAL(10,5) NOT NULL,
            stop_loss       DECIMAL(10,5) NOT NULL,
            take_profit_1   DECIMAL(10,5) NOT NULL,
            take_profit_2   DECIMAL(10,5),                  -- optionnel
            take_profit_3   DECIMAL(10,5),                  -- optionnel
            result_pips     DECIMAL(8,1)  DEFAULT NULL,     -- rempli à la clôture
            result_percent  DECIMAL(6,2)  DEFAULT NULL,     -- rempli à la clôture
            status          VARCHAR(20)   DEFAULT 'active', -- active | closed | cancelled
            message_id      BIGINT        DEFAULT NULL,     -- message Telegram lié
            note            TEXT,                           -- commentaire admin
            created_at      DATETIME      DEFAULT CURRENT_TIMESTAMP,
            closed_at       DATETIME      DEFAULT NULL
        )
    """)
 
    # ── trade_journal ────────────────────────────────────────────────────
    # Journal personnel de chaque membre pour chaque signal
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id       INTEGER       NOT NULL,
            user_id         BIGINT        NOT NULL,         -- telegram_id du membre
            entry_price     DECIMAL(10,5) NOT NULL,         -- prix réel du membre
            exit_price      DECIMAL(10,5) DEFAULT NULL,     -- rempli à la clôture
            lot_size        DECIMAL(6,2)  DEFAULT NULL,     -- taille de position
            result_pips     DECIMAL(8,1)  DEFAULT NULL,
            result_percent  DECIMAL(6,2)  DEFAULT NULL,
            screenshot_url  TEXT          DEFAULT NULL,     -- URL serveur externe
            status          VARCHAR(20)   DEFAULT 'open',   -- open | closed | cancelled
            note            TEXT,                           -- note personnelle du membre
            created_at      DATETIME      DEFAULT CURRENT_TIMESTAMP,
            closed_at       DATETIME      DEFAULT NULL,
            FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE CASCADE
        )
    """)
 
    # ── trade_comments ───────────────────────────────────────────────────
    # Commentaires des membres sur un trade journalisé
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id    INTEGER  NOT NULL,                  -- trade_journal.id
            user_id     BIGINT   NOT NULL,                  -- telegram_id
            comment     TEXT     NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trade_id) REFERENCES trade_journal(id) ON DELETE CASCADE
        )
    """)
 
    conn.commit()
    cursor.close()
    conn.close()
    print("[DB] ✅ Tables trading vérifiées (signals, trade_journal, trade_comments)")
 
 
# ────────────────────────────────────────────────────────────────────────
# SIGNALS
# ────────────────────────────────────────────────────────────────────────
 
async def create_signal(payload: dict):
    """
    payload: {
        pair, direction, entry_price, stop_loss,
        take_profit_1, take_profit_2?, take_profit_3?,
        note?, message_id?
    }
    """
    conn = get_conn()
    cursor = conn.cursor()
 
    cursor.execute("""
        INSERT INTO signals (
            pair, direction, entry_price, stop_loss,
            take_profit_1, take_profit_2, take_profit_3,
            note, message_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        payload["pair"],
        payload["direction"],
        payload["entry_price"],
        payload["stop_loss"],
        payload["take_profit_1"],
        payload.get("take_profit_2"),
        payload.get("take_profit_3"),
        payload.get("note"),
        payload.get("message_id")
    ))
 
    signal_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()
    return {"id": signal_id, "status": "created"}
 
 
async def get_signals(status: str = None, limit: int = 50, offset: int = 0):
    """
    Retourne les signaux avec le nombre de membres qui ont journalisé.
    status: 'active' | 'closed' | 'cancelled' | None (tous)
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
 
    query = """
        SELECT
            s.*,
            COUNT(tj.id)                        AS journal_count,
            AVG(tj.result_percent)              AS avg_member_result,
            SUM(CASE WHEN tj.status = 'closed'  AND tj.result_pips > 0 THEN 1 ELSE 0 END) AS winners,
            SUM(CASE WHEN tj.status = 'closed'  AND tj.result_pips < 0 THEN 1 ELSE 0 END) AS losers
        FROM signals s
        LEFT JOIN trade_journal tj ON tj.signal_id = s.id
    """
    params = []
    if status:
        query += " WHERE s.status = %s"
        params.append(status)
 
    query += " GROUP BY s.id ORDER BY s.created_at DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
 
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows
 
 
async def get_signal_by_id(signal_id: int):
    """Retourne un signal + tous les trades journalisés dessus."""
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
 
    cursor.execute("SELECT * FROM signals WHERE id = %s", (signal_id,))
    signal = cursor.fetchone()
 
    if not signal:
        cursor.close()
        conn.close()
        return None
 
    cursor.execute("""
        SELECT tj.*, u.name
        FROM trade_journal tj
        LEFT JOIN users u ON u.telegram_id = tj.user_id
        WHERE tj.signal_id = %s
        ORDER BY tj.created_at DESC
    """, (signal_id,))
    signal["journals"] = cursor.fetchall()
 
    cursor.close()
    conn.close()
    return signal
 
 
async def close_signal(signal_id: int, result_pips: float, result_percent: float):
    """Clôture un signal avec ses résultats finaux."""
    conn = get_conn()
    cursor = conn.cursor()
 
    cursor.execute("""
        UPDATE signals
        SET status         = 'closed',
            result_pips    = %s,
            result_percent = %s,
            closed_at      = %s
        WHERE id = %s
    """, (result_pips, result_percent, datetime.now(), signal_id))
 
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "closed", "signal_id": signal_id}
 
 
async def cancel_signal(signal_id: int):
    """Annule un signal."""
    conn = get_conn()
    cursor = conn.cursor()
 
    cursor.execute("""
        UPDATE signals SET status = 'cancelled' WHERE id = %s
    """, (signal_id,))
 
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "cancelled", "signal_id": signal_id}
 
 
# ────────────────────────────────────────────────────────────────────────
# TRADE JOURNAL
# ────────────────────────────────────────────────────────────────────────
 
async def create_trade_journal(payload: dict):
    """
    Un membre journalise sa prise de trade sur un signal.
    payload: {
        signal_id, user_id, entry_price,
        lot_size?, note?, screenshot_url?
    }
    """
    conn = get_conn()
    cursor = conn.cursor()
 
    cursor.execute("""
        INSERT INTO trade_journal (
            signal_id, user_id, entry_price,
            lot_size, note, screenshot_url
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        payload["signal_id"],
        payload["user_id"],
        payload["entry_price"],
        payload.get("lot_size"),
        payload.get("note"),
        payload.get("screenshot_url")
    ))
 
    trade_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()
    return {"id": trade_id, "status": "created"}
 
 
async def close_trade_journal(trade_id: int, payload: dict):
    """
    Clôture le trade d'un membre.
    payload: { exit_price, result_pips, result_percent, screenshot_url? }
    """
    conn = get_conn()
    cursor = conn.cursor()
 
    cursor.execute("""
        UPDATE trade_journal
        SET status         = 'closed',
            exit_price     = %s,
            result_pips    = %s,
            result_percent = %s,
            screenshot_url = COALESCE(%s, screenshot_url),
            closed_at      = %s
        WHERE id = %s
    """, (
        payload["exit_price"],
        payload["result_pips"],
        payload["result_percent"],
        payload.get("screenshot_url"),
        datetime.now(),
        trade_id
    ))
 
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "closed", "trade_id": trade_id}
 
 
async def get_member_journal(user_id: int, status: str = None, limit: int = 50, offset: int = 0):
    """
    Retourne tous les trades journalisés d'un membre.
    status: 'open' | 'closed' | 'cancelled' | None (tous)
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
 
    query = """
        SELECT
            tj.*,
            s.pair, s.direction,
            s.entry_price   AS signal_entry,
            s.stop_loss, s.take_profit_1, s.take_profit_2, s.take_profit_3
        FROM trade_journal tj
        JOIN signals s ON s.id = tj.signal_id
        WHERE tj.user_id = %s
    """
    params = [user_id]
 
    if status:
        query += " AND tj.status = %s"
        params.append(status)
 
    query += " ORDER BY tj.created_at DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
 
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows
 
 
async def get_member_stats(user_id: int):
    """
    Stats globales d'un membre pour le profil / leaderboard.
    Retourne : total trades, win rate, avg result %, best trade, worst trade
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
 
    cursor.execute("""
        SELECT
            COUNT(*)                                                        AS total_trades,
            SUM(CASE WHEN result_pips > 0 THEN 1 ELSE 0 END)               AS wins,
            SUM(CASE WHEN result_pips < 0 THEN 1 ELSE 0 END)               AS losses,
            ROUND(
                SUM(CASE WHEN result_pips > 0 THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0) * 100, 1
            )                                                               AS win_rate,
            ROUND(AVG(result_percent), 2)                                   AS avg_result_percent,
            ROUND(SUM(result_percent), 2)                                   AS total_result_percent,
            MAX(result_pips)                                                AS best_trade_pips,
            MIN(result_pips)                                                AS worst_trade_pips
        FROM trade_journal
        WHERE user_id = %s AND status = 'closed'
    """, (user_id,))
 
    stats = cursor.fetchone()
    cursor.close()
    conn.close()
    return stats
 
 
async def get_leaderboard(limit: int = 20):
    """
    Classement des membres par performance (win rate + résultat total).
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
 
    cursor.execute("""
        SELECT
            tj.user_id,
            u.name,
            COUNT(*)                                                        AS total_trades,
            ROUND(
                SUM(CASE WHEN tj.result_pips > 0 THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0) * 100, 1
            )                                                               AS win_rate,
            ROUND(SUM(tj.result_percent), 2)                               AS total_percent,
            ROUND(AVG(tj.result_percent), 2)                               AS avg_percent
        FROM trade_journal tj
        LEFT JOIN users u ON u.telegram_id = tj.user_id
        WHERE tj.status = 'closed'
        GROUP BY tj.user_id
        HAVING total_trades >= 3
        ORDER BY total_percent DESC
        LIMIT %s
    """, (limit,))
 
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows
 
 
# ────────────────────────────────────────────────────────────────────────
# COMMENTS
# ────────────────────────────────────────────────────────────────────────
 
async def add_comment(trade_id: int, user_id: int, comment: str):
    conn = get_conn()
    cursor = conn.cursor()
 
    cursor.execute("""
        INSERT INTO trade_comments (trade_id, user_id, comment)
        VALUES (%s, %s, %s)
    """, (trade_id, user_id, comment))
 
    comment_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()
    return {"id": comment_id, "status": "created"}
 
 
async def get_comments(trade_id: int):
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
 
    cursor.execute("""
        SELECT
            tc.*,
            u.name
        FROM trade_comments tc
        LEFT JOIN users u ON u.telegram_id = tc.user_id
        WHERE tc.trade_id = %s
        ORDER BY tc.created_at ASC
    """, (trade_id,))
 
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows
 
 
async def delete_comment(comment_id: int):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trade_comments WHERE id = %s", (comment_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "deleted"}