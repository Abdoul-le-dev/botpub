import sqlite3
from datetime import datetime
import asyncio

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

    cursor.execute(''' SELECT 1 FROM users WHERE telegram_id = ? AND email IS NOT NULL AND phone IS NOT NULL''', (user_id,))

    result =cursor.fetchone() is not None
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
    INSERT OR IGNORE INTO participants (nom, email, token,mail_envoyer, token_utilise )
    VALUES (?, ?, ?, 0,0)
    """, (nom, email, token))

    conn.commit()

def update_mail_status(email):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE participants
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
    UPDATE participants
    SET token_utilise = 1
    WHERE token = ?
    """, (token,))

    conn.commit()
    conn.close()

def get_token_exists(token):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT 1 FROM participants
    WHERE token = ? AND token_utilise = 0
    """, (token,))

    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def mail_token_utilise(email: str) -> bool:
    
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT token_utilise FROM participants WHERE email = ?", (email,))
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

def add_exam_user(id_user: int, email: str, exam_id: str):
    """
    Ajoute un utilisateur dans la table exam_user
    avant le début de son examen.
    """
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO exam_user (id_user, email, exam_id)
    VALUES (?, ?, ?)
    """, (id_user, email, exam_id))

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
    SELECT email,exam_id, note_one, time_one, note_two, time_two, qr_code, created_at
    FROM exam_user
    WHERE id_user = ?
    """, (id_user,))

    result = cursor.fetchone()
    conn.close()

    if result:
        email,exam_id, note_one, time_one, note_two, time_two, qr_code, created_at = result
        moyenne = None
        if note_one is not None and note_two is not None:
            moyenne = (note_one + note_two) / 2

        return {
            "id_user": id_user,
            "email": email,
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
