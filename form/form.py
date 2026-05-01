"""
forms_db.py — Tables SQLite pour le système de formulaires dynamiques.

Tables :
  forms             → le formulaire complet (payload JSON du frontend)
  form_sessions     → une session par utilisateur/formulaire (état courant)
  form_responses    → chaque réponse individuelle
  form_submissions  → soumission complète (score, actions exécutées)

Intégration :
  from forms_db import init_forms_db
  init_forms_db()   # au démarrage
"""

import sqlite3
import json
from datetime import datetime

DB_PATH = "preinscriptions.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ════════════════════════════════════════════════════════════════════════════
# INIT TABLES
# ════════════════════════════════════════════════════════════════════════════
def migrate_forms_db():
    """Ajoute les colonnes manquantes sans casser les données existantes."""
    with _conn() as conn:
        # Ajouter field_label à form_responses
        try:
            conn.execute("ALTER TABLE form_responses ADD COLUMN field_label TEXT DEFAULT ''")
            conn.commit()
            print("[forms_db] Colonne field_label ajoutée.")
        except Exception:
            pass  # déjà présente

def init_forms_db():
    """Crée les 4 tables si elles n'existent pas. À appeler au démarrage."""
    migrate_forms_db()
    with _conn() as conn:
        conn.executescript("""
            -- Formulaire complet tel que défini dans le builder frontend
            CREATE TABLE IF NOT EXISTS forms (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,
                command       TEXT    NOT NULL UNIQUE,   -- ex: /quiz
                type          TEXT    NOT NULL,           -- quiz | inscription | sondage | journal | temoignage | custom
                trigger_type  TEXT    NOT NULL DEFAULT 'command',  -- command | start | scheduled | condition
                trigger_value TEXT,                       -- cron string ou condition JSON si trigger planifié/auto
                intro         TEXT    DEFAULT '',
                outro         TEXT    DEFAULT '',
                fields        TEXT    NOT NULL DEFAULT '[]',  -- JSON list des champs
                actions       TEXT    NOT NULL DEFAULT '[]',  -- JSON list des actions post-soumission
                conditions    TEXT    NOT NULL DEFAULT '[]',  -- JSON list des règles SI/ALORS
                quiz_config   TEXT    NOT NULL DEFAULT '{}',  -- scoring, pénalité, etc.
                options       TEXT    NOT NULL DEFAULT '{}',  -- reprise, progression, une_seule_reponse...
                actif         INTEGER NOT NULL DEFAULT 1,
                cree_le       DATETIME DEFAULT (datetime('now')),
                modifie_le    DATETIME DEFAULT (datetime('now'))
            );

            -- Session = une instance d'exécution d'un formulaire pour un user
            CREATE TABLE IF NOT EXISTS form_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                form_id       INTEGER NOT NULL REFERENCES forms(id),
                telegram_id   INTEGER NOT NULL,
                step_index    INTEGER NOT NULL DEFAULT 0,   -- index dans fields[]
                status        TEXT    NOT NULL DEFAULT 'in_progress',  -- in_progress | completed | abandoned
                score         INTEGER NOT NULL DEFAULT 0,
                started_at    DATETIME DEFAULT (datetime('now')),
                updated_at    DATETIME DEFAULT (datetime('now')),
                UNIQUE(form_id, telegram_id)               -- une session active par user/formulaire
            );

            -- Réponse individuelle à un champ
            CREATE TABLE IF NOT EXISTS form_responses (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    INTEGER NOT NULL REFERENCES form_sessions(id),
                form_id       INTEGER NOT NULL,
                telegram_id   INTEGER NOT NULL,
                field_id      INTEGER NOT NULL,    -- correspond à fields[].id dans le JSON
                field_type    TEXT    NOT NULL,    -- qcm | text | note5 | etc.
                value         TEXT,               -- réponse brute (texte ou JSON pour multi)
                is_correct    INTEGER,            -- NULL si pas quiz, 1/0 si quiz
                points        INTEGER DEFAULT 0,
                answered_at   DATETIME DEFAULT (datetime('now'))
            );

            -- Soumission complète (une par session terminée)
            CREATE TABLE IF NOT EXISTS form_submissions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL UNIQUE REFERENCES form_sessions(id),
                form_id         INTEGER NOT NULL,
                telegram_id     INTEGER NOT NULL,
                score_final     INTEGER DEFAULT 0,
                score_max       INTEGER DEFAULT 0,
                pct             INTEGER DEFAULT 0,
                actions_done    TEXT    DEFAULT '[]',   -- JSON list des actions exécutées
                submitted_at    DATETIME DEFAULT (datetime('now'))
            );

            -- Index utiles
            CREATE INDEX IF NOT EXISTS idx_sessions_user   ON form_sessions (telegram_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_form   ON form_sessions (form_id);
            CREATE INDEX IF NOT EXISTS idx_responses_sess  ON form_responses (session_id);
            CREATE INDEX IF NOT EXISTS idx_submissions_form ON form_submissions (form_id);
        """)
        conn.commit()
    print("[forms_db] Tables initialisées.")


# ════════════════════════════════════════════════════════════════════════════
# CRUD FORMULAIRES
# ════════════════════════════════════════════════════════════════════════════

def save_form(payload: dict) -> int:
    """
    Crée ou met à jour un formulaire depuis le payload JSON du frontend.
    Retourne l'id du formulaire.

    payload attendu (identique au publish() du frontend) :
    {
        name, command, type, trigger (str),
        intro, outro,
        fields: [...],
        actions?: [...],    # post-soumission
        conditions?: [...], # logique conditionnelle
        quiz_config?: {...},
        options?: {...}
    }
    """
    # Normaliser le trigger
    trigger_raw = payload.get("trigger", "Commande manuelle")
    trigger_map = {
        "Commande manuelle":       ("command",   None),
        "À l'inscription (/start)":("start",    None),
        "Planifié (date/heure)":   ("scheduled", payload.get("trigger_value")),
        "Automatique (condition)": ("condition", json.dumps(payload.get("conditions", []))),
    }
    trigger_type, trigger_value = trigger_map.get(trigger_raw, ("command", None))

    # Normaliser la commande (assurer le slash)
    command = payload.get("command", "")
    if not command.startswith("/"):
        command = "/" + command

    fields     = json.dumps(payload.get("fields", []),     ensure_ascii=False)
    actions    = json.dumps(payload.get("actions", []),    ensure_ascii=False)
    conditions = json.dumps(payload.get("conditions", []), ensure_ascii=False)
    quiz_cfg   = json.dumps(payload.get("quiz_config", {}),ensure_ascii=False)
    options    = json.dumps(payload.get("options", {}),    ensure_ascii=False)

    with _conn() as conn:
        # Upsert : si la commande existe déjà, on met à jour
        existing = conn.execute(
            "SELECT id FROM forms WHERE command = ?", (command,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE forms SET
                    name=?, type=?, trigger_type=?, trigger_value=?,
                    intro=?, outro=?,
                    fields=?, actions=?, conditions=?, quiz_config=?, options=?,
                    actif=1, modifie_le=datetime('now')
                WHERE command=?
            """, (
                payload.get("name", ""), payload.get("type", "custom"),
                trigger_type, trigger_value,
                payload.get("intro", ""), payload.get("outro", ""),
                fields, actions, conditions, quiz_cfg, options,
                command
            ))
            conn.commit()
            return existing["id"]
        else:
            cur = conn.execute("""
                INSERT INTO forms
                    (name, command, type, trigger_type, trigger_value,
                     intro, outro, fields, actions, conditions, quiz_config, options)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                payload.get("name", ""), command, payload.get("type", "custom"),
                trigger_type, trigger_value,
                payload.get("intro", ""), payload.get("outro", ""),
                fields, actions, conditions, quiz_cfg, options
            ))
            conn.commit()
            return cur.lastrowid


def get_form_by_command(command: str) -> dict | None:
    """Retourne un formulaire actif par sa commande Telegram (/quiz, /start…)."""
    if not command.startswith("/"):
        command = "/" + command
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM forms WHERE command=? AND actif=1", (command,)
        ).fetchone()
    if not row:
        return None
    return _deserialize_form(dict(row))


def get_form_by_id(form_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM forms WHERE id=?", (form_id,)).fetchone()
    return _deserialize_form(dict(row)) if row else None


def get_all_forms(actif_only: bool = True) -> list[dict]:
    with _conn() as conn:
        q = "SELECT * FROM forms" + (" WHERE actif=1" if actif_only else "") + " ORDER BY id DESC"
        rows = conn.execute(q).fetchall()
    return [_deserialize_form(dict(r)) for r in rows]


def toggle_form(form_id: int, actif: bool):
    with _conn() as conn:
        conn.execute("UPDATE forms SET actif=? WHERE id=?", (1 if actif else 0, form_id))
        conn.commit()


def _deserialize_form(row: dict) -> dict:
    """Désérialise les colonnes JSON d'un formulaire."""
    for col in ("fields", "actions", "conditions"):
        try:
            row[col] = json.loads(row[col]) if row[col] else []
        except Exception:
            row[col] = []
    for col in ("quiz_config", "options"):
        try:
            row[col] = json.loads(row[col]) if row[col] else {}
        except Exception:
            row[col] = {}
    return row


# ════════════════════════════════════════════════════════════════════════════
# SESSIONS
# ════════════════════════════════════════════════════════════════════════════

def get_or_create_session(form_id: int, telegram_id: int) -> dict:
    """
    Retourne la session en cours pour ce user/formulaire.
    Si aucune session n'existe, en crée une nouvelle.
    Si la session précédente est terminée (completed), en recrée une.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM form_sessions WHERE form_id=? AND telegram_id=?",
            (form_id, telegram_id)
        ).fetchone()

        if row:
            session = dict(row)
            # Si la session est terminée, on en recrée une fraîche
            if session["status"] == "completed":
                conn.execute(
                    "DELETE FROM form_sessions WHERE id=?", (session["id"],)
                )
                conn.commit()
                # On tombe dans le cas "pas de session" ci-dessous
            else:
                return session

        # Créer une nouvelle session
        cur = conn.execute(
            "INSERT INTO form_sessions (form_id, telegram_id, step_index, status, score) VALUES (?,?,0,'in_progress',0)",
            (form_id, telegram_id)
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "form_id": form_id,
            "telegram_id": telegram_id,
            "step_index": 0,
            "status": "in_progress",
            "score": 0,
        }


def advance_session(session_id: int, new_step: int, add_score: int = 0):
    """Avance la session à l'étape suivante et ajoute des points si quiz."""
    with _conn() as conn:
        conn.execute("""
            UPDATE form_sessions
            SET step_index=?, score=score+?, updated_at=datetime('now')
            WHERE id=?
        """, (new_step, add_score, session_id))
        conn.commit()


def complete_session(session_id: int):
    with _conn() as conn:
        conn.execute(
            "UPDATE form_sessions SET status='completed', updated_at=datetime('now') WHERE id=?",
            (session_id,)
        )
        conn.commit()


def abandon_session(session_id: int):
    with _conn() as conn:
        conn.execute(
            "UPDATE form_sessions SET status='abandoned', updated_at=datetime('now') WHERE id=?",
            (session_id,)
        )
        conn.commit()


def get_session(session_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM form_sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None

print('go')
# ════════════════════════════════════════════════════════════════════════════
# RÉPONSES
# ════════════════════════════════════════════════════════════════════════════

def save_response(
    session_id:  int,
    form_id:     int,
    telegram_id: int,
    field_id:    int,
    field_type:  str,
    value,
    field_label: str  = "",
    is_correct:  bool | None = None,
    points:      int  = 0,
):
    """
    Enregistre la réponse d'un utilisateur à un champ.
    value peut être :
      - Texte libre
      - Chemin local  : "/media/forms/abc.jpg"  (photo/video/audio/document)
      - Valeur spéciale : "__skip__" | "__info__"
    """
    val     = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
    correct = None if is_correct is None else (1 if is_correct else 0)
 
    with _conn() as conn:
        conn.execute("""
            INSERT INTO form_responses
                (session_id, form_id, telegram_id, field_id, field_type, field_label, value, is_correct, points)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (session_id, form_id, telegram_id, field_id, field_type, field_label or "", val, correct, points))
        conn.commit()
 


# ════════════════════════════════════════════════════════════════════════════
# SOUMISSIONS
# ════════════════════════════════════════════════════════════════════════════

def save_submission(session_id: int, form_id: int, telegram_id: int, actions_done: list) -> int:
    """Finalise une soumission. Calcule score_final depuis la session."""
    with _conn() as conn:
        session = conn.execute(
            "SELECT score FROM form_sessions WHERE id=?", (session_id,)
        ).fetchone()
        score_final = session["score"] if session else 0

        form = conn.execute(
            "SELECT quiz_config FROM forms WHERE id=?", (form_id,)
        ).fetchone()
        quiz_cfg = json.loads(form["quiz_config"]) if form and form["quiz_config"] else {}
        score_max = int(quiz_cfg.get("max", 0))
        pct = round(score_final / score_max * 100) if score_max > 0 else 0

        cur = conn.execute("""
            INSERT OR REPLACE INTO form_submissions
                (session_id, form_id, telegram_id, score_final, score_max, pct, actions_done)
            VALUES (?,?,?,?,?,?,?)
        """, (
            session_id, form_id, telegram_id,
            score_final, score_max, pct,
            json.dumps(actions_done, ensure_ascii=False)
        ))
        conn.commit()
        return cur.lastrowid


# ════════════════════════════════════════════════════════════════════════════
# STATS (pour l'API)
# ════════════════════════════════════════════════════════════════════════════

def get_form_stats(form_id: int) -> dict:
    """Retourne les stats d'un formulaire pour le dashboard."""
    with _conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as n FROM form_sessions WHERE form_id=?", (form_id,)
        ).fetchone()["n"]

        completed = conn.execute(
            "SELECT COUNT(*) as n FROM form_sessions WHERE form_id=? AND status='completed'", (form_id,)
        ).fetchone()["n"]

        avg_score = conn.execute(
            "SELECT AVG(score_final) as s FROM form_submissions WHERE form_id=?", (form_id,)
        ).fetchone()["s"]

    return {
        "form_id":      form_id,
        "total":        total,
        "completed":    completed,
        "completion_pct": round(completed / total * 100) if total else 0,
        "avg_score":    round(avg_score or 0),
    }


def get_form_responses(form_id: int, limit: int = 100) -> list[dict]:
    """Retourne les soumissions complètes d'un formulaire pour la vue Réponses."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT s.telegram_id, s.score_final, s.score_max, s.pct, s.submitted_at,
                   u.name
            FROM form_submissions s
            LEFT JOIN users u ON u.telegram_id = s.telegram_id
            WHERE s.form_id=?
            ORDER BY s.submitted_at DESC
            LIMIT ?
        """, (form_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_user_responses_for_form(form_id: int, telegram_id: int) -> list[dict]:
    """
    Retourne toutes les réponses détaillées d'un user pour un formulaire.
    Inclut field_label pour l'affichage dans la modal.
    """
    with _conn() as conn:
        session = conn.execute(
            "SELECT id FROM form_sessions WHERE form_id=? AND telegram_id=? ORDER BY id DESC LIMIT 1",
            (form_id, telegram_id)
        ).fetchone()
 
        if not session:
            return []
 
        rows = conn.execute(
            """SELECT field_id, field_type, field_label, value, is_correct, points, answered_at
               FROM form_responses
               WHERE session_id=?
               ORDER BY answered_at""",
            (session["id"],)
        ).fetchall()
 
    result = []
    for r in rows:
        row = dict(r)
        # Nettoyer les valeurs JSON si besoin
        val = row.get("value", "")
        try:
            parsed = json.loads(val)
            row["value"] = parsed if isinstance(parsed, str) else val
        except Exception:
            row["value"] = val
        result.append(row)
 
    return result