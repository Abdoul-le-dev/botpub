
# ============================================================
# telegram_page/ia_config_tables.py
# ============================================================

import sqlite3

DB = "preinscriptions.db"

def init_ia_config_tables():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ia_prompts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            description   TEXT,
            content       TEXT    NOT NULL DEFAULT '',
            return_format TEXT    NOT NULL DEFAULT 'text'
                          CHECK(return_format IN ('text','json','list','markdown')),
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ia_functions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            description TEXT,
            code        TEXT    NOT NULL DEFAULT '',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now'))
        );
    """)
    print('ook')
    conn.commit()
    conn.close()


# ============================================================
# telegram_page/routes_ia_config.py
# ============================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3

router = APIRouter(prefix="/ia-config", tags=["ia-config"])
DB     = "preinscriptions.db"

def get_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c

# ── Schemas ──────────────────────────────────────────────────

class PromptIn(BaseModel):
    name:          str
    description:   Optional[str] = ""
    content:       Optional[str] = ""
    return_format: Optional[str] = "text"
    is_active:     Optional[int] = 1

class PromptPatch(BaseModel):
    name:          Optional[str] = None
    description:   Optional[str] = None
    content:       Optional[str] = None
    return_format: Optional[str] = None
    is_active:     Optional[int] = None

class FunctionIn(BaseModel):
    name:        str
    description: Optional[str] = ""
    code:        Optional[str] = ""
    is_active:   Optional[int] = 1

class FunctionPatch(BaseModel):
    name:        Optional[str] = None
    description: Optional[str] = None
    code:        Optional[str] = None
    is_active:   Optional[int] = None

# ── PROMPTS ──────────────────────────────────────────────────

@router.get("/prompts")
def list_prompts():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ia_prompts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/prompts", status_code=201)
def create_prompt(p: PromptIn):
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO ia_prompts (name, description, content, return_format, is_active)
            VALUES (?,?,?,?,?)
        """, (p.name, p.description, p.content, p.return_format, p.is_active))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ia_prompts WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()

@router.patch("/prompts/{pid}")
def update_prompt(pid: int, p: PromptPatch):
    conn = get_conn()
    try:
        fields = {k: v for k, v in p.dict().items() if v is not None}
        if not fields:
            raise HTTPException(400, "Aucun champ à modifier")
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE ia_prompts SET {sets} WHERE id=?",
            (*fields.values(), pid)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ia_prompts WHERE id=?", (pid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Prompt introuvable")
        return dict(row)
    finally:
        conn.close()

@router.delete("/prompts/{pid}")
def delete_prompt(pid: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM ia_prompts WHERE id=?", (pid,))
        conn.commit()
        return {"deleted": True}
    finally:
        conn.close()

# ── FUNCTIONS ────────────────────────────────────────────────

@router.get("/functions")
def list_functions():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ia_functions ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/functions", status_code=201)
def create_function(f: FunctionIn):
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO ia_functions (name, description, code, is_active)
            VALUES (?,?,?,?)
        """, (f.name, f.description, f.code, f.is_active))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ia_functions WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()

@router.patch("/functions/{fid}")
def update_function(fid: int, f: FunctionPatch):
    conn = get_conn()
    try:
        fields = {k: v for k, v in f.dict().items() if v is not None}
        if not fields:
            raise HTTPException(400, "Aucun champ à modifier")
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE ia_functions SET {sets} WHERE id=?",
            (*fields.values(), fid)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ia_functions WHERE id=?", (fid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Fonction introuvable")
        return dict(row)
    finally:
        conn.close()

@router.delete("/functions/{fid}")
def delete_function(fid: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM ia_functions WHERE id=?", (fid,))
        conn.commit()
        return {"deleted": True}
    finally:
        conn.close()

# ── EXPORT .py ───────────────────────────────────────────────

@router.get("/export/functions")
def export_functions():
    from fastapi.responses import PlainTextResponse
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ia_functions WHERE is_active=1 ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "# ============================================================",
        "# Fonctions IA — exporté depuis TradingBot IA Config",
        f"# {__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "# ============================================================\n",
    ]
    for r in rows:
        r = dict(r)
        lines.append(f"# {r['name']} — {r['description'] or ''}")
        lines.append(r['code'])
        lines.append("")

    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": "attachment; filename=ia_functions.py"}
    )

@router.get("/export/prompts")
def export_prompts():
    from fastapi.responses import PlainTextResponse
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ia_prompts WHERE is_active=1 ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "# ============================================================",
        "# Prompts IA — exporté depuis TradingBot IA Config",
        f"# {__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "# ============================================================\n",
    ]
    for r in rows:
        r = dict(r)
        lines.append(f"### {r['name']}")
        lines.append(f"# Description : {r['description'] or '—'}")
        lines.append(f"# Format retour : {r['return_format']}")
        lines.append(f"PROMPT_{r['name'].upper().replace(' ','_')} = \"\"\"{r['content']}\"\"\"")
        lines.append("")

    return PlainTextResponse(
        "\n".join(lines),
        headers={"Content-Disposition": "attachment; filename=ia_prompts.py"}
    )