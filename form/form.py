"""
form/form.py — v4 MySQL
Tables et CRUD pour le système de formulaires dynamiques.
"""

import json
from datetime import datetime

from db import get_db   # ← pool MySQL


# ════════════════════════════════════════════════════════════════════════════
# INIT / MIGRATION
# ════════════════════════════════════════════════════════════════════════════

def migrate_forms_db():
    """Ajoute les colonnes manquantes sans casser les données."""
    try:
        with get_db() as conn:
            conn.execute("ALTER TABLE form_responses ADD COLUMN field_label TEXT DEFAULT ''")
        print("[forms_db] Colonne field_label ajoutée.")
    except Exception:
        pass  # déjà présente


def init_forms_db():
    """Schéma déjà créé par schema_mysql.sql — on run juste la migration."""
    migrate_forms_db()
    print("[forms_db] Tables initialisées.")


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _deserialize_form(row: dict) -> dict:
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
# CRUD FORMULAIRES
# ════════════════════════════════════════════════════════════════════════════

def save_form(payload: dict) -> int:
    trigger_raw = payload.get("trigger", "Commande manuelle")
    trigger_map = {
        "Commande manuelle":        ("command",   None),
        "À l'inscription (/start)": ("start",     None),
        "Planifié (date/heure)":    ("scheduled", payload.get("trigger_value")),
        "Automatique (condition)":  ("condition", json.dumps(payload.get("conditions", []))),
    }
    trigger_type, trigger_value = trigger_map.get(trigger_raw, ("command", None))

    command = payload.get("command", "")
    if not command.startswith("/"):
        command = "/" + command

    fields     = json.dumps(payload.get("fields",     []),  ensure_ascii=False)
    actions    = json.dumps(payload.get("actions",    []),  ensure_ascii=False)
    conditions = json.dumps(payload.get("conditions", []),  ensure_ascii=False)
    quiz_cfg   = json.dumps(payload.get("quiz_config",{}),  ensure_ascii=False)
    options    = json.dumps(payload.get("options",    {}),  ensure_ascii=False)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM forms WHERE command = ?", (command,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE forms SET
                    name=?, type=?, trigger_type=?, trigger_value=?,
                    intro=?, outro=?,
                    fields=?, actions=?, conditions=?, quiz_config=?, options=?,
                    actif=1, modifie_le=NOW()
                WHERE command=?
            """, (
                payload.get("name", ""), payload.get("type", "custom"),
                trigger_type, trigger_value,
                payload.get("intro", ""), payload.get("outro", ""),
                fields, actions, conditions, quiz_cfg, options, command
            ))
            return existing["id"]
        else:
            conn.execute("""
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
            return conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]


def get_form_by_command(command: str) -> dict | None:
    if not command.startswith("/"):
        command = "/" + command
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM forms WHERE command=? AND actif=1", (command,)
        ).fetchone()
    return _deserialize_form(dict(row)) if row else None


def get_form_by_id(form_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM forms WHERE id=?", (form_id,)).fetchone()
    return _deserialize_form(dict(row)) if row else None


def get_all_forms(actif_only: bool = True) -> list[dict]:
    with get_db() as conn:
        q    = "SELECT * FROM forms" + (" WHERE actif=1" if actif_only else "") + " ORDER BY id DESC"
        rows = conn.execute(q).fetchall()
    return [_deserialize_form(dict(r)) for r in rows]


def toggle_form(form_id: int, actif: bool):
    with get_db() as conn:
        conn.execute("UPDATE forms SET actif=? WHERE id=?", (1 if actif else 0, form_id))


# ════════════════════════════════════════════════════════════════════════════
# SESSIONS
# ════════════════════════════════════════════════════════════════════════════

def get_or_create_session(form_id: int, telegram_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM form_sessions WHERE form_id=? AND telegram_id=?",
            (form_id, telegram_id)
        ).fetchone()

        if row:
            session = dict(row)
            if session["status"] == "completed":
                conn.execute("DELETE FROM form_sessions WHERE id=?", (session["id"],))
            else:
                return session

        conn.execute("""
            INSERT INTO form_sessions (form_id, telegram_id, step_index, status, score)
            VALUES (?,?,0,'in_progress',0)
        """, (form_id, telegram_id))
        new_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]

    return {"id": new_id, "form_id": form_id, "telegram_id": telegram_id,
            "step_index": 0, "status": "in_progress", "score": 0}


def advance_session(session_id: int, new_step: int, add_score: int = 0):
    with get_db() as conn:
        conn.execute("""
            UPDATE form_sessions
            SET step_index=?, score=score+?, updated_at=NOW()
            WHERE id=?
        """, (new_step, add_score, session_id))


def complete_session(session_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE form_sessions SET status='completed', updated_at=NOW() WHERE id=?",
            (session_id,)
        )


def abandon_session(session_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE form_sessions SET status='abandoned', updated_at=NOW() WHERE id=?",
            (session_id,)
        )


def get_session(session_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM form_sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


# ════════════════════════════════════════════════════════════════════════════
# RÉPONSES
# ════════════════════════════════════════════════════════════════════════════

def save_response(session_id, form_id, telegram_id, field_id, field_type,
                  value, field_label="", is_correct=None, points=0):
    val     = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
    correct = None if is_correct is None else (1 if is_correct else 0)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO form_responses
                (session_id, form_id, telegram_id, field_id, field_type,
                 field_label, value, is_correct, points)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (session_id, form_id, telegram_id, field_id, field_type,
              field_label or "", val, correct, points))


# ════════════════════════════════════════════════════════════════════════════
# SOUMISSIONS
# ════════════════════════════════════════════════════════════════════════════

def save_submission(session_id: int, form_id: int, telegram_id: int, actions_done: list) -> int:
    with get_db() as conn:
        session = conn.execute(
            "SELECT score FROM form_sessions WHERE id=?", (session_id,)
        ).fetchone()
        score_final = session["score"] if session else 0

        form = conn.execute(
            "SELECT quiz_config FROM forms WHERE id=?", (form_id,)
        ).fetchone()
        quiz_cfg  = json.loads(form["quiz_config"]) if form and form["quiz_config"] else {}
        score_max = int(quiz_cfg.get("max", 0))
        pct       = round(score_final / score_max * 100) if score_max > 0 else 0

        conn.execute("""
            INSERT INTO form_submissions
                (session_id, form_id, telegram_id, score_final, score_max, pct, actions_done)
            VALUES (?,?,?,?,?,?,?)
            ON DUPLICATE KEY UPDATE
                score_final  = VALUES(score_final),
                score_max    = VALUES(score_max),
                pct          = VALUES(pct),
                actions_done = VALUES(actions_done)
        """, (session_id, form_id, telegram_id, score_final, score_max, pct,
              json.dumps(actions_done, ensure_ascii=False)))
        return conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]


# ════════════════════════════════════════════════════════════════════════════
# STATS
# ════════════════════════════════════════════════════════════════════════════

def get_form_stats(form_id: int) -> dict:
    with get_db() as conn:
        total     = conn.execute("SELECT COUNT(*) as n FROM form_sessions WHERE form_id=?", (form_id,)).fetchone()["n"]
        completed = conn.execute("SELECT COUNT(*) as n FROM form_sessions WHERE form_id=? AND status='completed'", (form_id,)).fetchone()["n"]
        avg_score = conn.execute("SELECT AVG(score_final) as s FROM form_submissions WHERE form_id=?", (form_id,)).fetchone()["s"]
    return {
        "form_id": form_id, "total": total, "completed": completed,
        "completion_pct": round(completed / total * 100) if total else 0,
        "avg_score": round(avg_score or 0),
    }


def get_form_responses(form_id: int, limit: int = 100) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.telegram_id, s.score_final, s.score_max, s.pct, s.submitted_at, u.name
            FROM form_submissions s
            LEFT JOIN users u ON u.telegram_id = s.telegram_id
            WHERE s.form_id=? ORDER BY s.submitted_at DESC LIMIT ?
        """, (form_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_user_responses_for_form(form_id: int, telegram_id: int) -> list[dict]:
    with get_db() as conn:
        session = conn.execute(
            "SELECT id FROM form_sessions WHERE form_id=? AND telegram_id=? ORDER BY id DESC LIMIT 1",
            (form_id, telegram_id)
        ).fetchone()
        if not session:
            return []
        rows = conn.execute("""
            SELECT field_id, field_type, field_label, value, is_correct, points, answered_at
            FROM form_responses WHERE session_id=? ORDER BY answered_at
        """, (session["id"],)).fetchall()

    result = []
    for r in rows:
        row = dict(r)
        val = row.get("value", "")
        try:
            parsed    = json.loads(val)
            row["value"] = parsed if isinstance(parsed, str) else val
        except Exception:
            row["value"] = val
        result.append(row)
    return result