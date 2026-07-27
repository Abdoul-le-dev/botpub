import sqlite3
import asyncio
import json
from datetime import datetime
from db import get_db


# ════════════════════════════════════════════════════════════════════════════
# INIT
# ════════════════════════════════════════════════════════════════════════════

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                phone      TEXT NOT NULL,
                country    TEXT,
                created_at TEXT NOT NULL
            )
        ''')


def init_broadcast_history():
    with get_db() as conn:
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS categories_meta (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name_categorie TEXT NOT NULL UNIQUE,
                color          TEXT DEFAULT '#38bdf8',
                description    TEXT,
                created_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS category_rules (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name_categorie TEXT NOT NULL,
                trigger_type   TEXT NOT NULL,
                trigger_value  TEXT,
                is_active      INTEGER DEFAULT 1,
                created_at     TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (name_categorie)
                    REFERENCES categories_meta(name_categorie)
                    ON DELETE CASCADE ON UPDATE CASCADE
            )
        """)

        # Migration colonnes messages
        existing = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        for col, definition in {
            "broadcast_id":  "INTEGER DEFAULT NULL",
            "media_url":     "TEXT    DEFAULT NULL",
            "status":        "TEXT    DEFAULT 'received'",
            "error_message": "TEXT    DEFAULT NULL",
        }.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {definition}")
                print(f"[DB] ✅ Colonne ajoutée : messages.{col}")

        if "status" not in existing:
            conn.execute("UPDATE messages SET status = 'received' WHERE status IS NULL")

        print("[DB] ✅ init_broadcast_history terminé")


def migrate_categories_to_meta():
    with get_db() as conn:
        rows     = conn.execute("SELECT DISTINCT name_categorie FROM categories").fetchall()
        migrated = 0
        for row in rows:
            conn.execute(
                "INSERT OR IGNORE INTO categories_meta (name_categorie) VALUES (?)", (row[0],)
            )
            migrated += conn.execute("SELECT changes()").fetchone()[0]

    print(f"[DB] ✅ {migrated} catégorie(s) migrée(s)" if migrated else "[DB] ⏭️  categories_meta déjà à jour")


# ════════════════════════════════════════════════════════════════════════════
# USERS
# ════════════════════════════════════════════════════════════════════════════

def save_user(
    name,
    phone,
    country=None,
    telegram_id=None,
    contexte_user=None,
    email=None,
    motivation=None,
    level=None,
    why=None,
    what=None,
    expectations=None,
    discover=None
):
    with get_db() as conn:

        conn.execute("""
            INSERT INTO users
            (
                name,
                phone,
                country,
                created_at,
                telegram_id,
                contexte_user,
                email,
                motivation,
                level,
                why,
                what,
                expectations,
                discover
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            phone,
            country,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            telegram_id,
            contexte_user,
            email,
            motivation,
            level,
            why,
            what,
            expectations,
            discover
        ))

        conn.commit()

def user_exists(telegram_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone() is not None


def get_user_info(telegram_id):
    with get_db() as conn:
        row = conn.execute('''
            SELECT id, name, phone, country, email, motivation, level, created_at
            FROM users WHERE telegram_id = ?
        ''', (telegram_id,)).fetchone()

    if not row:
        return None
    return {
        "ID": row[0], "Nom": row[1], "Téléphone": row[2],
        "Pays": row[3], "Email": row[4], "Motivation": row[5],
        "Niveau": row[6], "Inscrit le": row[7],
    }


def update_user_info(telegram_id, email=None, expectations=None, discovery=None):
    with get_db() as conn:
        conn.execute('''
            UPDATE users SET email = ?, expectations = ?, discover = ?
            WHERE telegram_id = ?
        ''', (email, expectations, discovery, telegram_id))


def verify_name_phone_mail(user_id):
    with get_db() as conn:
        return conn.execute("""
            SELECT 1 FROM users
            WHERE telegram_id = ?
              AND email IS NOT NULL AND TRIM(email) <> ''
              AND phone IS NOT NULL AND TRIM(phone) <> ''
        """, (user_id,)).fetchone() is not None


def get_mail_and_name(user_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT email, name FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()


def save_user_default(user_id):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO usersdefault (user_id, created_at) VALUES (?, ?)",
            (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
    print("User default saved:", user_id)


# ════════════════════════════════════════════════════════════════════════════
# MESSAGES
# ════════════════════════════════════════════════════════════════════════════

db_lock = asyncio.Lock()


def save_message(user_id, message_id, message_text=None, answer=None,
                 message_type="text", media_url=None, direction="inbound", answered_by=None):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO messages
                (user_id, message_id, message_text, answer,
                 message_type, media_url, direction, answered_by, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?)
        """, (user_id, message_id, message_text, answer,
              message_type, media_url, direction, answered_by,
              datetime.now().isoformat()))


def save_messages(user_id, message_id, message_text, answer=None, message_type="text"):
    with get_db() as conn:
        conn.execute('''
            INSERT INTO messages (user_id, message_id, message_text, answer, message_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, message_id, message_text, answer, message_type,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


async def get_data():
    with get_db() as conn:
        rows = conn.execute('''
            SELECT user_id, message_id, message_text, answer, message_type, created_at
            FROM messages ORDER BY created_at DESC LIMIT 500
        ''').fetchall()

    conversations = {}
    for row in rows:
        user_id, message_id, message_text, answer, message_type, created_at = tuple(row)
        if user_id not in conversations:
            user_data = get_user_info(user_id)
            conversations[user_id] = {
                "id": user_id,
                "name": user_data["Nom"] if user_data else f"User {user_id}",
                "userId": str(user_id),
                "lastMessage": message_text,
                "time": created_at.split(" ")[1][:5] if " " in created_at else "",
                "unread": 0,
                "messages": [],
            }

    return json.dumps(list(conversations.values()), indent=4, ensure_ascii=False)


async def get_data_users(id):
    with get_db() as conn:
        rows = conn.execute('''
            SELECT message_id, message_text, answer, message_type, created_at
            FROM messages WHERE user_id = ?
        ''', (id,)).fetchall()

    if not rows:
        return ['a']

    user_data = get_user_info(id)
    messages  = []

    for row in rows:
        message_id, message_text, answer, message_type, created_at = tuple(row)
        time = created_at.split(" ")[1][:5] if " " in created_at else ""
        messages.append({
            "id":   message_id,
            "text": message_text,
            "type": message_type,
            "time": time,
        })

    conversations = [{
        "id":          id,
        "name":        user_data["Nom"] if user_data else f"User {id}",
        "userId":      str(id),
        "lastMessage": messages[-1]["text"] if messages else "",
        "time":        messages[-1]["time"] if messages else "",
        "unread":      0,
        "messages":    messages,
    }]

    return json.dumps(conversations, indent=4, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# CATÉGORIES
# ════════════════════════════════════════════════════════════════════════════

async def add_categorie(id_user, name_categorie):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM categories WHERE id_user = ? AND name_categorie = ? LIMIT 1",
            (id_user, name_categorie)
        ).fetchone()

        if existing:
            return {"status": "already_exists"}

        conn.execute(
            "INSERT INTO categories (id_user, name_categorie, created_at) VALUES (?, ?, ?)",
            (id_user, name_categorie, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        return {"status": "added"}


def user_has_categorie(id_user, name):
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM categories WHERE id_user = ? AND name_categorie = ? LIMIT 1",
            (id_user, name)
        ).fetchone() is not None


async def get_categories_user():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name_categorie, COUNT(*) as total FROM categories GROUP BY name_categorie"
        ).fetchall()
    return [{"name": row[0], "total": row[1]} for row in rows]


def liste_categories():
    with get_db() as conn:
        return conn.execute("SELECT id, name_categorie FROM categories").fetchall()


def get_user_categories(user_id):
    with get_db() as conn:
        return conn.execute('''
            SELECT id, name_categorie, created_at FROM categories
            WHERE id_user = ? ORDER BY created_at DESC
        ''', (user_id,)).fetchall()


def find_category_duplicates(categorie: str):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id_user, COUNT(*) AS total FROM categories
            WHERE name_categorie = ?
            GROUP BY id_user HAVING COUNT(*) > 1
            ORDER BY total DESC, id_user ASC
        """, (categorie,)).fetchall()
    return [(r[0], r[1], r[1] - 1) for r in rows]


def delete_user_duplicates(user_id: int, categorie: str = "second_challenge10000usd",
                            keep: str = "oldest") -> tuple[int, int | None]:
    order = "created_at ASC, id ASC" if keep == "oldest" else "created_at DESC, id DESC"
    try:
        with get_db() as conn:
            row = conn.execute(
                f"SELECT id FROM categories WHERE id_user = ? AND name_categorie = ? ORDER BY {order} LIMIT 1",
                (user_id, categorie)
            ).fetchone()
            if not row:
                return (0, None)
            kept_id = row[0]
            conn.execute(
                "DELETE FROM categories WHERE id_user = ? AND name_categorie = ? AND id <> ?",
                (user_id, categorie, kept_id)
            )
            return (conn.execute("SELECT changes()").fetchone()[0], kept_id)
    except Exception as e:
        print(f"[ERREUR] delete_user_duplicates: {e}")
        return (0, None)


async def drop_category(name_categorie: str):
    with get_db() as conn:
        conn.execute("DELETE FROM categories WHERE name_categorie = ?", (name_categorie,))
        deleted = conn.execute("SELECT changes()").fetchone()[0]
        conn.execute("DELETE FROM categories_meta WHERE name_categorie = ?", (name_categorie,))
    return {"status": "deleted", "name_categorie": name_categorie, "members_removed": deleted}


# ════════════════════════════════════════════════════════════════════════════
# BROADCAST
# ════════════════════════════════════════════════════════════════════════════

def get_broadcast_history():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM broadcast_history ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# FICHIERS / VIDÉOS
# ════════════════════════════════════════════════════════════════════════════

def get_file_id(video_name):
    with get_db() as conn:
        row = conn.execute("SELECT file_id FROM videos WHERE video_name=?", (video_name,)).fetchone()
    return row[0] if row else None


def save_file_id(video_name, file_id):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO videos (video_name, file_id, created_at) VALUES (?, ?, ?)",
            (video_name, file_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )


# ════════════════════════════════════════════════════════════════════════════
# EXERCICES
# ════════════════════════════════════════════════════════════════════════════

def add_exercice(question, answer, explanation, categorie_id):
    with get_db() as conn:
        conn.execute('''
            INSERT INTO exercice (question, answer, explanation, categorie_id)
            VALUES (?, ?, ?, ?)
        ''', (question, answer, explanation, categorie_id))
    print("✅ Nouvel exercice ajouté.")


def add_categorie_exercice(nom, admin_verify=False):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO categorie_exercice (nom, admin_verify) VALUES (?, ?)",
            (nom, int(admin_verify))
        )
        return cur.lastrowid


def add_exercice_with_limit(question, answer, explanation, categorie_id):
    with get_db() as conn:
        nb = conn.execute(
            "SELECT COUNT(*) FROM exercice WHERE categorie_id = ?", (categorie_id,)
        ).fetchone()[0]

        if nb >= 10:
            return False, "❌ Cette catégorie a déjà 10 questions maximum."

        conn.execute('''
            INSERT INTO exercice (question, answer, explanation, categorie_id)
            VALUES (?, ?, ?, ?)
        ''', (question, answer, explanation, categorie_id))
    return True, "✅ Exercice ajouté avec succès."


def verifier_et_valider_categorie(categorie_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT nom, admin_verify FROM categorie_exercice WHERE id = ?", (categorie_id,)
        ).fetchone()

        if not row:
            return False, "❌ Catégorie introuvable."
        if row[1]:
            return False, f"⚠️ La catégorie '{row[0]}' est déjà validée."

        conn.execute(
            "UPDATE categorie_exercice SET admin_verify = 1 WHERE id = ?", (categorie_id,)
        )
    return True, f"✅ Catégorie '{row[0]}' validée."


def get_questions(categorie_id):
    with get_db() as conn:
        return conn.execute('''
            SELECT id, question, answer, explanation
            FROM exercice WHERE categorie_id = ? LIMIT 10
        ''', (categorie_id,)).fetchall()


def verify_categorie(name):
    with get_db() as conn:
        row = conn.execute(
            "SELECT admin_verify FROM categorie_exercice WHERE nom = ?", (name,)
        ).fetchone()
    if row is None:   return None
    if row[0] == 1:   return True
    return "non_verify"


def get_categories(args):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM categorie_exercice WHERE nom = ?", (args,)
        ).fetchone()
    return row[0] if row else None


def get_categories_exam(args):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM categorie_exercice WHERE id = ?", (args,)
        ).fetchone()
    return row[0] if row else None


def get_category_questions_report(categorie_id):
    with get_db() as conn:
        results = conn.execute("""
            SELECT rsq.question_id, e.question,
                COUNT(*) AS total_reponses,
                SUM(CASE WHEN LOWER(TRIM(rsq.answer)) = LOWER(TRIM(e.answer)) THEN 1 ELSE 0 END) AS bonnes,
                ROUND(
                    SUM(CASE WHEN LOWER(TRIM(rsq.answer)) = LOWER(TRIM(e.answer)) THEN 1 ELSE 0 END)
                    * 100.0 / COUNT(*), 2
                ) AS pct
            FROM resultat_student_question rsq
            JOIN exercice e ON rsq.question_id = e.id
            WHERE rsq.categorie_id = ? AND rsq.second_time = 0
            GROUP BY rsq.question_id, e.question
            ORDER BY pct DESC
        """, (categorie_id,)).fetchall()

    if not results:
        return f"Aucune donnée pour la catégorie {categorie_id}."

    rapport = f"📊 Rapport Catégorie {categorie_id} :\n"
    for q_id, question, total, bonnes, pct in results:
        rapport += f"Q{q_id} : {pct}% ({bonnes}/{total})\n"
    return rapport


# ════════════════════════════════════════════════════════════════════════════
# RÉSULTATS ÉTUDIANTS
# ════════════════════════════════════════════════════════════════════════════

def save_user_answer(user_id, categorie_id, question_id, user_answer, start_time, end_time, second_time=False):
    with get_db() as conn:
        conn.execute('''
            INSERT INTO resultat_student_question
                (id_user, categorie_id, question_id, answer, time_start, time_end, second_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, categorie_id, question_id, user_answer, start_time, end_time, second_time))


def save_daily_result(user_id, categorie_id, time_start, time_end, note, second_time=False):
    with get_db() as conn:
        conn.execute('''
            INSERT INTO resultat_student_day
                (id_user, categorie_id, time_start, time_end, note, second_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, categorie_id, time_start, time_end, note, second_time))


def get_final_score(user_id):
    with get_db() as conn:
        categories = conn.execute("""
            SELECT DISTINCT categorie_id FROM resultat_student_day WHERE id_user = ?
        """, (user_id,)).fetchall()

        total_score = 0.0
        for (categorie_id,) in categories:
            row = conn.execute("""
                SELECT note FROM resultat_student_day
                WHERE id_user = ? AND categorie_id = ? AND second_time = 1
                ORDER BY id DESC LIMIT 1
            """, (user_id, categorie_id)).fetchone()

            if not row:
                row = conn.execute("""
                    SELECT note FROM resultat_student_day
                    WHERE id_user = ? AND categorie_id = ?
                    ORDER BY id DESC LIMIT 1
                """, (user_id, categorie_id)).fetchone()

            if row and row[0] is not None:
                try:
                    total_score += float(row[0])
                except ValueError:
                    pass

    return total_score


def delete_user_data_from_db(user_id):
    with get_db() as conn:
        conn.execute("DELETE FROM resultat_student_day WHERE id_user = ?",      (user_id,))
        conn.execute("DELETE FROM resultat_student_question WHERE id_user = ?", (user_id,))
        conn.execute("DELETE FROM args WHERE id_user = ?",                      (user_id,))


def delete_all_exercices():
    with get_db() as conn:
        conn.execute("DELETE FROM exercice")
    print("Toutes les données de 'exercice' supprimées.")


# ════════════════════════════════════════════════════════════════════════════
# ARGS
# ════════════════════════════════════════════════════════════════════════════

def create_args(id_user: int, args_value: str, use_it: bool):
    with get_db() as conn:
        if conn.execute(
            "SELECT 1 FROM args WHERE id_user = ? AND args = ? AND use_it = 1", (id_user, args_value)
        ).fetchone():
            return "already"

        if conn.execute(
            "SELECT 1 FROM args WHERE id_user = ? AND args = ? AND use_it = 0", (id_user, args_value)
        ).fetchone():
            return ""

        conn.execute(
            "INSERT INTO args (id_user, args, use_it) VALUES (?, ?, ?)",
            (id_user, args_value, int(use_it))
        )
    return "created"


def delete_args(id_user: int):
    with get_db() as conn:
        conn.execute("DELETE FROM args WHERE id_user = ?", (id_user,))
    print(f"🗑 Arg id_user={id_user} supprimé.")


def check_if_user(id_user: int):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM args WHERE id_user = ?", (id_user,)).fetchall()
    return bool(rows)


def get_user_args(id_user: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT args FROM args WHERE id_user = ? AND use_it = 0", (id_user,)
        ).fetchone()
    return row[0] if row else None


def update_arg(id_user: int, args_value: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE args SET use_it = 1 WHERE id_user = ? AND args = ?",
            (id_user, args_value)
        )
        updated = conn.execute("SELECT changes()").fetchone()[0]
    return updated > 0


# ════════════════════════════════════════════════════════════════════════════
# MAIL / PARTICIPANTS
# ════════════════════════════════════════════════════════════════════════════

def mail_user(nom, email, token):
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO participants_2nd (nom, email, token, mail_envoyer, token_utilise)
            VALUES (?, ?, ?, 0, 0)
        """, (nom, email, token))


def update_mail_status(email):
    with get_db() as conn:
        conn.execute(
            "UPDATE participants_2nd SET mail_envoyer = 1 WHERE email = ?", (email,)
        )


def get_unsent_emails():
    with get_db() as conn:
        return conn.execute(
            "SELECT nom, email, token FROM participants WHERE mail_envoyer = 0"
        ).fetchall()


def update_token_used(token):
    with get_db() as conn:
        conn.execute(
            "UPDATE participants_2nd SET token_utilise = 1 WHERE token = ?", (token,)
        )


def get_token_exists(token):
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM participants_2nd WHERE token = ? AND token_utilise = 0", (token,)
        ).fetchone() is not None


def mail_token_utilise(email: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT token_utilise FROM participants_2nd WHERE email = ?", (email,)
        ).fetchone()
    return bool(row and row[0])


def update_mail_count(user, increment=1):
    with get_db() as conn:
        conn.execute("""
            UPDATE mail_valide
            SET nbre_mail_envoyer_jrs = nbre_mail_envoyer_jrs + ?
            WHERE user = ?
        """, (increment, user))


def reset_all_mail_counts():
    with get_db() as conn:
        conn.execute("UPDATE mail_valide SET nbre_mail_envoyer_jrs = 0")


def add_new_user(user, psw):
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO mail_valide (user, psw, nbre_mail_envoyer_jrs) VALUES (?, ?, 0)",
                (user, psw)
            )
            print(f"Utilisateur '{user}' ajouté.")
        except Exception:
            print(f"L'utilisateur '{user}' existe déjà.")


def get_user_under_limit():
    with get_db() as conn:
        result = conn.execute("""
            SELECT user, psw FROM mail_valide
            WHERE nbre_mail_envoyer_jrs < 3000
            ORDER BY nbre_mail_envoyer_jrs ASC LIMIT 1
        """).fetchone()
    return (result[0], result[1]) if result else (None, None)


# ════════════════════════════════════════════════════════════════════════════
# EXAMEN
# ════════════════════════════════════════════════════════════════════════════

def add_exam(exam_name: str, id_part_one: int, id_part_two: int):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO exam (exam_name, id_part_one, id_part_two) VALUES (?, ?, ?)",
            (exam_name, id_part_one, id_part_two)
        )
    print(f"✅ Examen '{exam_name}' ajouté.")


def get_exam_parts(exam_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id_part_one, id_part_two FROM exam WHERE id = ?", (exam_id,)
        ).fetchone()
    return (row[0], row[1]) if row else None


def add_exam_user(id_user, email, user_name, last_name, exam_id):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO exam_user (id_user, email, user_name, last_name, exam_id) VALUES (?, ?, ?, ?, ?)",
            (id_user, email, user_name, last_name, exam_id)
        )
    print(f"✅ Utilisateur {email} ajouté dans exam_user.")


def update_exam_user(id_user: int, note: int, time_spent: str, qr_code: str, part: int):
    with get_db() as conn:
        if part == 1:
            conn.execute(
                "UPDATE exam_user SET note_one = ?, time_one = ? WHERE id_user = ?",
                (note, time_spent, id_user)
            )
        elif part == 2:
            conn.execute(
                "UPDATE exam_user SET note_two = ?, time_two = ? WHERE id_user = ?",
                (note, time_spent, id_user)
            )
        elif part == 3:
            conn.execute(
                "UPDATE exam_user SET qr_code = ? WHERE id_user = ?",
                (qr_code, id_user)
            )
    print(f"✅ Partie {part} mise à jour pour user {id_user}.")


def get_user_exam(id_user: int):
    with get_db() as conn:
        result = conn.execute("""
            SELECT email, user_name, last_name, exam_id,
                   note_one, time_one, note_two, time_two, qr_code, created_at
            FROM exam_user WHERE id_user = ?
        """, (id_user,)).fetchone()

    if not result:
        return None

    email, user_name, last_name, exam_id, note_one, time_one, note_two, time_two, qr_code, created_at = tuple(result)
    moyenne = (note_one + note_two) / 2 if note_one is not None and note_two is not None else None

    return {
        "id_user": id_user, "email": email, "user_name": user_name,
        "last_name": last_name, "exam_id": exam_id,
        "note_one": note_one, "time_one": time_one,
        "note_two": note_two, "time_two": time_two,
        "qr_code": qr_code, "created_at": created_at, "moyenne": moyenne,
    }