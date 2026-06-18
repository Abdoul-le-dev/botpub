"""
form/form.py — v5 MySQL async
Tables et CRUD pour le système de formulaires dynamiques.
"""

import json
from datetime import datetime

from db import get_db


# ════════════════════════════════════════════════════════════════════════════
# INIT / MIGRATION
# ════════════════════════════════════════════════════════════════════════════

async def migrate_forms_db():
    try:
        async with get_db() as cur:
            await cur.execute("ALTER TABLE form_responses ADD COLUMN field_label TEXT DEFAULT ''")
        print("[forms_db] Colonne field_label ajoutée.")
    except Exception:
        pass


async def init_forms_db():
    await migrate_forms_db()
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

async def save_form(payload: dict) -> int:
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

    fields     = json.dumps(payload.get("fields",     []), ensure_ascii=False)
    actions    = json.dumps(payload.get("actions",    []), ensure_ascii=False)
    conditions = json.dumps(payload.get("conditions", []), ensure_ascii=False)
    quiz_cfg   = json.dumps(payload.get("quiz_config",{}), ensure_ascii=False)
    options    = json.dumps(payload.get("options",    {}), ensure_ascii=False)

    async with get_db() as cur:
        await cur.execute("SELECT id FROM forms WHERE command = %s", (command,))
        existing = await cur.fetchone()

        if existing:
            await cur.execute("""
                UPDATE forms SET
                    name=%s, type=%s, trigger_type=%s, trigger_value=%s,
                    intro=%s, outro=%s,
                    fields=%s, actions=%s, conditions=%s, quiz_config=%s, options=%s,
                    actif=1, modifie_le=NOW()
                WHERE command=%s
            """, (
                payload.get("name", ""), payload.get("type", "custom"),
                trigger_type, trigger_value,
                payload.get("intro", ""), payload.get("outro", ""),
                fields, actions, conditions, quiz_cfg, options, command
            ))
            return existing["id"]
        else:
            await cur.execute("""
                INSERT INTO forms
                    (name, command, type, trigger_type, trigger_value,
                     intro, outro, fields, actions, conditions, quiz_config, options)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                payload.get("name", ""), command, payload.get("type", "custom"),
                trigger_type, trigger_value,
                payload.get("intro", ""), payload.get("outro", ""),
                fields, actions, conditions, quiz_cfg, options
            ))
            return cur.lastrowid


async def get_form_by_command(command: str) -> dict | None:
    if not command.startswith("/"):
        command = "/" + command
    async with get_db() as cur:
        await cur.execute("SELECT * FROM forms WHERE command=%s AND actif=1", (command,))
        row = await cur.fetchone()
    return _deserialize_form(dict(row)) if row else None


async def get_form_by_id(form_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM forms WHERE id=%s", (form_id,))
        row = await cur.fetchone()
    return _deserialize_form(dict(row)) if row else None


async def get_all_forms(actif_only: bool = True) -> list[dict]:
    async with get_db() as cur:
        q = "SELECT * FROM forms" + (" WHERE actif=1" if actif_only else "") + " ORDER BY id DESC"
        await cur.execute(q)
        rows = await cur.fetchall()
    return [_deserialize_form(dict(r)) for r in rows]


async def toggle_form(form_id: int, actif: bool):
    async with get_db() as cur:
        await cur.execute("UPDATE forms SET actif=%s WHERE id=%s", (1 if actif else 0, form_id))


# ════════════════════════════════════════════════════════════════════════════
# SESSIONS
# ════════════════════════════════════════════════════════════════════════════

async def get_or_create_session(form_id: int, telegram_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute(
            "SELECT * FROM form_sessions WHERE form_id=%s AND telegram_id=%s",
            (form_id, telegram_id)
        )
        row = await cur.fetchone()

        if row:
            session = dict(row)
            if session["status"] == "completed":
                # Supprimer d'abord les submissions liées (FK constraint)
                await cur.execute(
                    "DELETE FROM form_submissions WHERE session_id=%s",
                    (session["id"],)
                )
                await cur.execute(
                    "DELETE FROM form_sessions WHERE id=%s",
                    (session["id"],)
                )
            else:
                return session

        await cur.execute("""
            INSERT INTO form_sessions (form_id, telegram_id, step_index, status, score)
            VALUES (%s,%s,0,'in_progress',0)
        """, (form_id, telegram_id))
        new_id = cur.lastrowid

    return {"id": new_id, "form_id": form_id, "telegram_id": telegram_id,
            "step_index": 0, "status": "in_progress", "score": 0}

async def advance_session(session_id: int, new_step: int, add_score: int = 0):
    async with get_db() as cur:
        await cur.execute("""
            UPDATE form_sessions
            SET step_index=%s, score=score+%s, updated_at=NOW()
            WHERE id=%s
        """, (new_step, add_score, session_id))


async def complete_session(session_id: int):
    async with get_db() as cur:
        await cur.execute(
            "UPDATE form_sessions SET status='completed', updated_at=NOW() WHERE id=%s",
            (session_id,)
        )


async def abandon_session(session_id: int):
    async with get_db() as cur:
        await cur.execute(
            "UPDATE form_sessions SET status='abandoned', updated_at=NOW() WHERE id=%s",
            (session_id,)
        )


async def get_session(session_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM form_sessions WHERE id=%s", (session_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


# ════════════════════════════════════════════════════════════════════════════
# RÉPONSES
# ════════════════════════════════════════════════════════════════════════════

async def save_response(session_id, form_id, telegram_id, field_id, field_type,
                        value, field_label="", is_correct=None, points=0):
    val     = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
    correct = None if is_correct is None else (1 if is_correct else 0)
    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO form_responses
                (session_id, form_id, telegram_id, field_id, field_type,
                 field_label, value, is_correct, points)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (session_id, form_id, telegram_id, field_id, field_type,
              field_label or "", val, correct, points))


# ════════════════════════════════════════════════════════════════════════════
# SOUMISSIONS
# ════════════════════════════════════════════════════════════════════════════

async def save_submission(session_id: int, form_id: int, telegram_id: int, actions_done: list) -> int:
    async with get_db() as cur:
        await cur.execute("SELECT score FROM form_sessions WHERE id=%s", (session_id,))
        session     = await cur.fetchone()
        score_final = session["score"] if session else 0

        await cur.execute("SELECT quiz_config FROM forms WHERE id=%s", (form_id,))
        form      = await cur.fetchone()
        quiz_cfg  = json.loads(form["quiz_config"]) if form and form["quiz_config"] else {}
        score_max = int(quiz_cfg.get("max", 0))
        pct       = round(score_final / score_max * 100) if score_max > 0 else 0

        await cur.execute("""
            INSERT INTO form_submissions
                (session_id, form_id, telegram_id, score_final, score_max, pct, actions_done)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                score_final  = VALUES(score_final),
                score_max    = VALUES(score_max),
                pct          = VALUES(pct),
                actions_done = VALUES(actions_done)
        """, (session_id, form_id, telegram_id, score_final, score_max, pct,
              json.dumps(actions_done, ensure_ascii=False)))
        return cur.lastrowid


# ════════════════════════════════════════════════════════════════════════════
# STATS
# ════════════════════════════════════════════════════════════════════════════

async def get_form_stats(form_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("SELECT COUNT(*) as n FROM form_sessions WHERE form_id=%s", (form_id,))
        total = (await cur.fetchone())["n"]

        await cur.execute(
            "SELECT COUNT(*) as n FROM form_sessions WHERE form_id=%s AND status='completed'", (form_id,)
        )
        completed = (await cur.fetchone())["n"]

        await cur.execute("SELECT AVG(score_final) as s FROM form_submissions WHERE form_id=%s", (form_id,))
        avg_score = (await cur.fetchone())["s"]

    return {
        "form_id": form_id, "total": total, "completed": completed,
        "completion_pct": round(completed / total * 100) if total else 0,
        "avg_score": round(avg_score or 0),
    }


async def get_form_responses(form_id: int, limit: int = 100) -> list[dict]:
    async with get_db() as cur:
        await cur.execute("""
            SELECT s.telegram_id, s.score_final, s.score_max, s.pct, s.submitted_at, u.name
            FROM form_submissions s
            LEFT JOIN users u ON u.telegram_id = s.telegram_id
            WHERE s.form_id=%s ORDER BY s.submitted_at DESC LIMIT %s
        """, (form_id, limit))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_user_responses_for_form(form_id: int, telegram_id: int) -> list[dict]:
    async with get_db() as cur:
        await cur.execute(
            "SELECT id FROM form_sessions WHERE form_id=%s AND telegram_id=%s ORDER BY id DESC LIMIT 1",
            (form_id, telegram_id)
        )
        session = await cur.fetchone()
        if not session:
            return []

        await cur.execute("""
            SELECT field_id, field_type, field_label, value, is_correct, points, answered_at
            FROM form_responses WHERE session_id=%s ORDER BY answered_at
        """, (session["id"],))
        rows = await cur.fetchall()

    result = []
    for r in rows:
        row = dict(r)
        val = row.get("value", "")
        try:
            parsed   = json.loads(val)
            row["value"] = parsed if isinstance(parsed, str) else val
        except Exception:
            row["value"] = val
        result.append(row)
    return result