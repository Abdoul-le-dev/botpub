"""
trading_journal.py — Backend complet du Journal de Trading.

Couvre :
  - Signaux (publication, clôture, commentaires de suivi, ticker live)
  - Journaux membres (participations, résultats, comportements)
  - Performances membres (capital, courbe, théorique vs réel)
  - Classement
  - Bilan IA (génération Claude + envoi broadcast)
  - Paires & Pip (CRUD + calculateur de lot)
  - Formulaires & Collecte (mapping champ→stat, données reçues)

Intégration avec l'existant :
  - Même DB SQLite : preinscriptions.db
  - Réutilise broadcast_engine pour les envois
  - Réutilise form_engine pour la collecte post-clôture
  - Réutilise set_bot() / _bot de chat.py

Schéma IA attendu (retourné par les fonctions IA) :
  Voir SCHEMA IA en bas de fichier.
"""

import sqlite3
import json
import uuid
import asyncio
import httpx
import math

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Config ─────────────────────────────────────────────────────────────────────

DB_PATH   = "preinscriptions.db"
ADMIN_ID  = 571718066
MEDIA_DIR = Path("media")

# Instance bot injectée depuis api.py
_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS BASE
# ══════════════════════════════════════════════════════════════════════════════

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now().isoformat()


def _pips(entry: float, exit_: float, direction: str, decimals: int = 5) -> float:
    """Calcule les pips selon la direction et les décimales de la paire."""
    multiplier = 10 ** (decimals - 1)
    diff = (exit_ - entry) if direction == "long" else (entry - exit_)
    return round(diff * multiplier, 1)


def _percent(entry: float, exit_: float, direction: str) -> float:
    if entry == 0:
        return 0.0
    diff = (exit_ - entry) if direction == "long" else (entry - exit_)
    return round(diff / entry * 100, 2)


# ══════════════════════════════════════════════════════════════════════════════
# INITIALISATION TABLES
# ══════════════════════════════════════════════════════════════════════════════
def reset_problem_tables():
    conn = get_conn()
    try:
        tables = [
            "signals",
            "trade_journal",
            "signal_participations",
            "followup_comments"
        ]

        for t in tables:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
            print(f"[DROP] {t}")

        conn.commit()
    finally:
        conn.close()

def init_trading_tables():

    
    
    conn = get_conn()

    def ensure_table(table_name, create_sql, columns):
        # 1. créer table si absente
        conn.execute(create_sql)

        # 2. récupérer colonnes existantes
        existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table_name})")]

        # 3. ajouter colonnes manquantes
        for col_name, col_type in columns.items():
            if col_name not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
                    print(f"[MIGRATION] {table_name}.{col_name} ajouté")
                except Exception as e:
                    print(f"[MIGRATION ERROR] {table_name}.{col_name} -> {e}")

    try:
        # ─────────────────────────────────────────────
        # SIGNALS
        # ─────────────────────────────────────────────
        ensure_table(
            "signals",
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
            """,
            {
                "pair": "TEXT",
                "direction": "TEXT",
                "timeframe": "TEXT DEFAULT 'H4'",
                "entry_price": "REAL",
                "tp1": "REAL",
                "tp2": "REAL",
                "sl": "REAL",
                "note": "TEXT",
                "screenshot_url": "TEXT",
                "category": "TEXT DEFAULT 'clients_actifs'",
                "status": "TEXT DEFAULT 'open'",
                "close_price": "REAL",
                "close_result": "TEXT",
                "close_screenshot": "TEXT",
                "result_pips": "REAL",
                "result_percent": "REAL",
                "published_at": "TEXT",
                "closed_at": "TEXT",
                "lot_suggested": "REAL",
                "broadcast_id": "INTEGER"
            }
        )

        # ─────────────────────────────────────────────
        # SIGNAL PARTICIPATIONS
        # ─────────────────────────────────────────────
        ensure_table(
            "signal_participations",
            """
            CREATE TABLE IF NOT EXISTS signal_participations (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
            """,
            {
                "signal_id": "INTEGER",
                "user_id": "INTEGER",
                "response": "TEXT",
                "responded_at": "TEXT"
            }
        )

        # ─────────────────────────────────────────────
        # TRADE JOURNAL
        # ─────────────────────────────────────────────
        ensure_table(
            "trade_journal",
            """
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
            """,
            {
                "signal_id": "INTEGER",
                "user_id": "INTEGER",
                "participated": "INTEGER DEFAULT 1",
                "entry_price": "REAL",
                "exit_price": "REAL",
                "result_pips": "REAL",
                "result_percent": "REAL",
                "gain_usd": "REAL",
                "lot_used": "REAL",
                "behavior": "TEXT",
                "screenshot_url": "TEXT",
                "capital_before": "REAL",
                "capital_after": "REAL",
                "submitted_at": "TEXT",
                "status": "TEXT"
            }
        )

        # ─────────────────────────────────────────────
        # FOLLOWUP COMMENTS
        # ─────────────────────────────────────────────
        ensure_table(
            "followup_comments",
            """
            CREATE TABLE IF NOT EXISTS followup_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
            """,
            {
                "signal_id": "INTEGER",
                "type": "TEXT",
                "message": "TEXT",
                "screenshot_url": "TEXT",
                "broadcast_id": "INTEGER",
                "sent_at": "TEXT"
            }
        )

        # ─────────────────────────────────────────────
        # MEMBER CAPITAL
        # ─────────────────────────────────────────────
        ensure_table(
            "member_capital",
            """
            CREATE TABLE IF NOT EXISTS member_capital (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
            """,
            {
                "user_id": "INTEGER",
                "capital": "REAL",
                "type": "TEXT DEFAULT 'gains'",
                "declared_at": "TEXT",
                "source": "TEXT DEFAULT 'form'"
            }
        )

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_pub ON signals(published_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_status ON signals(status)
        """)

        conn.commit()
        print("[DB] Vérification + migration terminée OK")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT
        )
    """)

        cols = [r[1] for r in conn.execute("PRAGMA table_info(forms)")]

        def add(col, typ):
            if col not in cols:
                conn.execute(f"ALTER TABLE forms ADD COLUMN {col} {typ}")

        add("name", "TEXT")
        add("command", "TEXT")
        add("type", "TEXT DEFAULT 'custom'")
        add("is_active", "INTEGER DEFAULT 1")

        # ❌ PAS de DEFAULT dynamique ici
        add("created_at", "TEXT")

        # remplir manuellement après
        conn.execute("""
            UPDATE forms
            SET created_at = datetime('now')
            WHERE created_at IS NULL
        """)

    finally:
        conn.close()
# SECTION 1 — SIGNAUX
# ══════════════════════════════════════════════════════════════════════════════

async def publish_signal(payload: dict) -> dict:
    """
    Publie un nouveau signal de trading.

    payload: {
        pair, direction, timeframe?, entry_price, tp1?, tp2?, sl?,
        note?, screenshot_url?, category?, lot_suggested?
    }
    Déclenche le broadcast automatiquement via broadcast_engine.
    Retourne le signal créé + broadcast_id.
    """
    conn = get_conn()
    print(payload)
    try:
        cur = conn.execute("""
            INSERT INTO signals
                (pair, direction, timeframe, entry_price, tp1, tp2, sl,
                 note, screenshot_url, category, lot_suggested, status, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """, (
            payload["pair"],
            payload["direction"],
            payload.get("timeframe", "H4"),
            float(payload["entry_price"]),
            float(payload["tp1"])   if payload.get("tp1") else None,
            float(payload["tp2"])   if payload.get("tp2") else None,
            float(payload["sl"])    if payload.get("sl")  else None,
            payload.get("note"),
            payload.get("screenshot_url"),
            payload.get("category", "clients_actifs"),
            payload.get("lot_suggested"),
            _now(),
        ))
        signal_id = cur.lastrowid
        conn.commit()
        signal = dict(conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone())
    finally:
        conn.close()

    # Broadcast Telegram si bot disponible
    print("1")
    if _bot:
        print("2")
        try:
            from telegram_page.broadcast_engine import broadcast_engine
            direction_emoji = "📈" if payload["direction"] == "long" else "📉"
            direction_label = "LONG" if payload["direction"] == "long" else "SHORT"

            message_lines = [
                f"📊 *Signal de Trading*",
                f"",
                f"🔷 Paire : *{payload['pair']}*",
                f"{direction_emoji} Direction : *{direction_label}*",
                f"🎯 Entrée : *{payload['entry_price']}*",
            ]
            if payload.get("tp1"):
                message_lines.append(f"✅ TP1 : *{payload['tp1']}*")
            if payload.get("tp2"):
                message_lines.append(f"✅ TP2 : *{payload['tp2']}*")
            if payload.get("sl"):
                message_lines.append(f"❌ SL : *{payload['sl']}*")
            if payload.get("note"):
                message_lines.append(f"\n_{payload['note']}_")

            message = "\n".join(message_lines)
            print(message)

            broadcast_payload = {
                "message":   message,
                "format":    "text",
                "category":  payload.get("category", "clients_actifs"),
                "tag":       f"signal_{signal_id}_{payload['pair'].replace('/', '')}",
                "delay":     0.05,
            }
            if payload.get("screenshot_url"):
                broadcast_payload["format"]    = "image+text"
                broadcast_payload["media_url"] = payload["screenshot_url"]

                print("1")
                print(payload)

            report = await broadcast_engine(_bot, broadcast_payload)
            broadcast_id = None  # broadcast_engine ne retourne pas l'id DB direct

            # Mise à jour du broadcast_id si disponible
            conn2 = get_conn()
            try:
                bh = conn2.execute(
                    "SELECT id FROM broadcast_history ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if bh:
                    broadcast_id = bh["id"]
                    conn2.execute(
                        "UPDATE signals SET broadcast_id = ? WHERE id = ?",
                        (broadcast_id, signal_id)
                    )
                    conn2.commit()
                    signal["broadcast_id"] = broadcast_id
            finally:
                conn2.close()

        except Exception as e:
            signal["broadcast_warning"] = str(e)
    else:
        print('not bot...')
    signal["id"] = signal_id
    return signal


async def get_signals(filters: dict = None) -> dict:
    """
    Liste des signaux avec stats de participation en temps réel.

    filters: {
        status?: 'open'|'closed'|'cancelled'|'all',
        pair?, limit?, offset?,
        date_from?, date_to?
    }
    """
    f      = filters or {}
    status = f.get("status", "all")
    limit  = int(f.get("limit", 20))
    offset = int(f.get("offset", 0))

    conn        = get_conn()
    where       = ["1=1"]
    params      = []

    if status != "all":
        where.append("s.status = ?")
        params.append(status)
    if f.get("pair"):
        where.append("s.pair = ?")
        params.append(f["pair"])
    if f.get("date_from"):
        where.append("s.published_at >= ?")
        params.append(f["date_from"])
    if f.get("date_to"):
        where.append("s.published_at <= ?")
        params.append(f["date_to"])

    where_sql = " AND ".join(where)

    try:
        rows = conn.execute(f"""
            SELECT
                s.*,
                COUNT(DISTINCT sp.user_id)                                            AS total_participants,
                COUNT(DISTINCT CASE WHEN sp.response = 'in'  THEN sp.user_id END)     AS count_in,
                COUNT(DISTINCT CASE WHEN sp.response = 'out' THEN sp.user_id END)     AS count_out,
                COUNT(DISTINCT CASE WHEN tj.id IS NOT NULL    THEN tj.user_id END)     AS journals_submitted,
                COUNT(DISTINCT fc.id)                                                  AS followup_count
            FROM signals s
            LEFT JOIN signal_participations sp ON sp.signal_id = s.id
            LEFT JOIN trade_journal tj         ON tj.signal_id = s.id
            LEFT JOIN followup_comments fc     ON fc.signal_id = s.id
            WHERE {where_sql}
            GROUP BY s.id
            ORDER BY s.published_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) FROM signals s WHERE {where_sql}", params
        ).fetchone()[0]

        signals = []
        for r in rows:
            d = dict(r)
            # Calcul R:R
            if d.get("entry_price") and d.get("tp1") and d.get("sl"):
                tp_dist = abs(d["tp1"] - d["entry_price"])
                sl_dist = abs(d["entry_price"] - d["sl"])
                d["rr_ratio"] = round(tp_dist / sl_dist, 2) if sl_dist > 0 else None
            else:
                d["rr_ratio"] = None

            # Distance pips temps réel (placeholder — à remplacer par prix live)
            d["pips_to_tp1"] = None
            d["pips_to_sl"]  = None
            signals.append(d)

    finally:
        conn.close()

    return {"signals": signals, "total": total, "limit": limit, "offset": offset}


async def get_signal_detail(signal_id: int) -> dict | None:
    """Détail complet d'un signal avec participations, comportements et commentaires."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not row:
            return None
        signal = dict(row)

        # Participations
        parts = conn.execute("""
            SELECT sp.user_id, sp.response, sp.responded_at, u.name
            FROM signal_participations sp
            LEFT JOIN users u ON u.telegram_id = sp.user_id
            WHERE sp.signal_id = ?
            ORDER BY sp.responded_at DESC
        """, (signal_id,)).fetchall()
        signal["participations"] = [dict(p) for p in parts]

        # Comportements clôturés
        behaviors = conn.execute("""
            SELECT
                behavior,
                COUNT(*) AS count,
                ROUND(AVG(result_percent), 2) AS avg_pct,
                ROUND(AVG(result_pips), 1)    AS avg_pips
            FROM trade_journal
            WHERE signal_id = ? AND participated = 1
            GROUP BY behavior
        """, (signal_id,)).fetchall()
        signal["behaviors"] = [dict(b) for b in behaviors]

        # Commentaires de suivi
        followups = conn.execute("""
            SELECT * FROM followup_comments
            WHERE signal_id = ?
            ORDER BY sent_at DESC
        """, (signal_id,)).fetchall()
        signal["followup_comments"] = [dict(f) for f in followups]

        # Stats globales journal
        stats = conn.execute("""
            SELECT
                COUNT(*)                                                            AS total_journals,
                ROUND(AVG(result_percent), 2)                                       AS avg_result_percent,
                ROUND(AVG(result_pips), 1)                                          AS avg_pips,
                COUNT(CASE WHEN result_percent > 0 THEN 1 END)                      AS wins,
                COUNT(CASE WHEN result_percent < 0 THEN 1 END)                      AS losses,
                COUNT(CASE WHEN behavior = 'disciplined' THEN 1 END)                AS disciplined,
                COUNT(CASE WHEN behavior = 'early_exit'  THEN 1 END)                AS early_exits,
                COUNT(CASE WHEN behavior = 'sl_skip'     THEN 1 END)                AS sl_skips
            FROM trade_journal
            WHERE signal_id = ? AND participated = 1
        """, (signal_id,)).fetchone()
        signal["journal_stats"] = dict(stats) if stats else {}

    finally:
        conn.close()

    return signal


async def close_signal(signal_id: int, payload: dict) -> dict:
    """
    Clôture un signal et enregistre le résultat admin.

    payload: {
        close_price, close_result: 'tp'|'sl'|'partial'|'cancelled',
        close_screenshot?, form_id?, send_form_to: 'participated'|'all'
    }
    Déclenche l'envoi du formulaire de collecte via form_engine.
    """
    conn = get_conn()
    try:
        signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not signal:
            return {"error": "Signal introuvable"}

        signal = dict(signal)
        close_price = float(payload["close_price"])

        result_pips    = _pips(signal["entry_price"], close_price, signal["direction"],
                                _get_pair_decimals(signal["pair"]))
        result_percent = _percent(signal["entry_price"], close_price, signal["direction"])

        conn.execute("""
            UPDATE signals
            SET status = 'closed',
                close_price     = ?,
                close_result    = ?,
                close_screenshot = ?,
                result_pips     = ?,
                result_percent  = ?,
                closed_at       = ?
            WHERE id = ?
        """, (
            close_price,
            payload["close_result"],
            payload.get("close_screenshot"),
            result_pips,
            result_percent,
            _now(),
            signal_id,
        ))
        conn.commit()
        updated = dict(conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone())
    finally:
        conn.close()

    # Envoi formulaire de collecte post-clôture
    if payload.get("form_id") and _bot:
        try:
            from form.form_engine import broadcast_form

            target = payload.get("send_form_to", "participated")
            if target == "participated":
                conn2 = get_conn()
                try:
                    rows = conn2.execute("""
                        SELECT user_id FROM signal_participations
                        WHERE signal_id = ? AND response = 'in'
                    """, (signal_id,)).fetchall()
                    user_ids = [r["user_id"] for r in rows]
                finally:
                    conn2.close()
            else:
                # Tous les destinataires du signal original
                user_ids = await _get_signal_recipients(signal_id)

            await broadcast_form(
                bot      = _bot,
                form_id  = payload["form_id"],
                user_ids = user_ids,
                admin_id = ADMIN_ID,
            )
            updated["form_sent_to"] = len(user_ids)
        except Exception as e:
            updated["form_warning"] = str(e)

    return updated


async def record_participation(signal_id: int, user_id: int, response: str) -> dict:
    """
    Enregistre la participation d'un membre (bouton Telegram).
    response: 'in' | 'out'
    Appelé par le bot Python via webhook.
    """
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO signal_participations (signal_id, user_id, response, responded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(signal_id, user_id) DO UPDATE SET
                response     = excluded.response,
                responded_at = excluded.responded_at
        """, (signal_id, user_id, response, _now()))
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "signal_id": signal_id, "user_id": user_id, "response": response}


async def send_followup_comment(signal_id: int, payload: dict) -> dict:
    """
    Envoie un commentaire de suivi sur un trade ouvert.

    payload: {
        type: 'update'|'invalidation'|'secure'|'encourage',
        message,
        screenshot_url?
    }
    Envoi ciblé uniquement aux membres 'in' (Je suis dedans).
    """
    conn = get_conn()
    try:
        # Récupérer le signal pour construire le message
        signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not signal:
            return {"error": "Signal introuvable"}
        signal = dict(signal)

        # Membres "Je suis dedans"
        user_ids_rows = conn.execute("""
            SELECT user_id FROM signal_participations
            WHERE signal_id = ? AND response = 'in'
        """, (signal_id,)).fetchall()
        user_ids = [r["user_id"] for r in user_ids_rows]

        # Enregistrement du commentaire
        type_emojis = {
            "update":      "🔔",
            "invalidation": "⚠️",
            "secure":      "🔒",
            "encourage":   "💪",
        }
        emoji = type_emojis.get(payload["type"], "📌")

        cur = conn.execute("""
            INSERT INTO followup_comments
                (signal_id, type, message, screenshot_url, sent_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            signal_id,
            payload["type"],
            payload["message"],
            payload.get("screenshot_url"),
            _now(),
        ))
        comment_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # Broadcast aux membres "in"
    broadcast_id = None
    if user_ids and _bot:
        try:
            from telegram_page.broadcast_engine import broadcast_engine
            full_message = (
                f"{emoji} *{payload['type'].replace('_', ' ').title()} — "
                f"{signal['pair']}*\n\n{payload['message']}"
            )
            bc_payload = {
                "message":  full_message,
                "format":   "text",
                "user_ids": user_ids,
                "tag":      f"followup_{comment_id}_{signal_id}",
                "delay":    0.05,
            }
            if payload.get("screenshot_url"):
                bc_payload["format"]    = "image+text"
                bc_payload["media_url"] = payload["screenshot_url"]

            await broadcast_engine(_bot, bc_payload)

            # Récupérer le broadcast_id
            conn3 = get_conn()
            try:
                bh = conn3.execute(
                    "SELECT id FROM broadcast_history ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if bh:
                    broadcast_id = bh["id"]
                    conn3.execute(
                        "UPDATE followup_comments SET broadcast_id = ? WHERE id = ?",
                        (broadcast_id, comment_id)
                    )
                    conn3.commit()
            finally:
                conn3.close()

        except Exception as e:
            return {"error": str(e), "comment_id": comment_id}

    return {
        "comment_id":   comment_id,
        "signal_id":    signal_id,
        "type":         payload["type"],
        "sent_to":      len(user_ids),
        "broadcast_id": broadcast_id,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — JOURNAL MEMBRES
# ══════════════════════════════════════════════════════════════════════════════

async def submit_trade_result(signal_id: int, user_id: int, payload: dict) -> dict:
    """
    Enregistre le résultat réel d'un membre pour un signal.
    Appelé par le form_engine après réception du formulaire de clôture.

    payload: {
        entry_price?, exit_price?, lot_used?,
        screenshot_url?, capital_before?, capital_after?,
        behavior?: 'disciplined'|'early_exit'|'sl_skip'|'passive'
    }
    """
    conn = get_conn()
    try:
        signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not signal:
            return {"error": "Signal introuvable"}
        signal = dict(signal)

        entry   = float(payload.get("entry_price")  or signal["entry_price"])
        exit_p  = float(payload["exit_price"]) if payload.get("exit_price") else None
        decimals = _get_pair_decimals(signal["pair"])

        pips    = _pips(entry, exit_p, signal["direction"], decimals) if exit_p else None
        pct     = _percent(entry, exit_p, signal["direction"])        if exit_p else None

        # Calcul gain USD
        lot     = float(payload.get("lot_used") or 0)
        pair_pv = _get_pip_value(signal["pair"])
        gain_usd = round(pips * lot * pair_pv, 2) if (pips is not None and lot > 0) else None

        conn.execute("""
            INSERT INTO trade_journal
                (signal_id, user_id, participated, entry_price, exit_price,
                 result_pips, result_percent, gain_usd, lot_used,
                 behavior, screenshot_url, capital_before, capital_after,
                 submitted_at, status)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed')
            ON CONFLICT(signal_id, user_id) DO UPDATE SET
                exit_price    = excluded.exit_price,
                result_pips   = excluded.result_pips,
                result_percent= excluded.result_percent,
                gain_usd      = excluded.gain_usd,
                lot_used      = excluded.lot_used,
                behavior      = excluded.behavior,
                screenshot_url= excluded.screenshot_url,
                capital_before= excluded.capital_before,
                capital_after = excluded.capital_after,
                submitted_at  = excluded.submitted_at
        """, (
            signal_id, user_id, entry, exit_p,
            pips, pct, gain_usd, lot,
            payload.get("behavior"),
            payload.get("screenshot_url"),
            payload.get("capital_before"),
            payload.get("capital_after"),
            _now(),
        ))

        # Mise à jour capital membre si fourni
        if payload.get("capital_after"):
            conn.execute("""
                INSERT INTO member_capital (user_id, capital, type, declared_at, source)
                VALUES (?, ?, 'gains', ?, 'trade_result')
            """, (user_id, float(payload["capital_after"]), _now()))

        conn.commit()
        result = dict(conn.execute("""
            SELECT * FROM trade_journal WHERE signal_id = ? AND user_id = ?
        """, (signal_id, user_id)).fetchone())
    finally:
        conn.close()

    return result


async def get_history(filters: dict = None) -> dict:
    """
    Historique croisé membres × signaux.

    filters: {
        member_id?, signal_id?, pair?,
        status?: 'took'|'skip'|'all',
        limit?, offset?,
        date_from?, date_to?
    }
    Retourne les lignes pour le tableau de la Vue Historique.
    """
    f      = filters or {}
    limit  = int(f.get("limit",  50))
    offset = int(f.get("offset",  0))
    status = f.get("status", "all")

    conn   = get_conn()
    where  = ["s.status = 'closed'"]
    params = []

    if status == "took":
        where.append("tj.participated = 1")
    elif status == "skip":
        where.append("(tj.participated = 0 OR tj.user_id IS NULL)")

    if f.get("member_id"):
        where.append("(sp.user_id = ? OR tj.user_id = ?)")
        params += [f["member_id"], f["member_id"]]
    if f.get("signal_id"):
        where.append("s.id = ?")
        params.append(f["signal_id"])
    if f.get("pair"):
        where.append("s.pair = ?")
        params.append(f["pair"])
    if f.get("search"):
        where.append("(u.name LIKE ? OR s.pair LIKE ?)")
        term = f"%{f['search']}%"
        params += [term, term]
    if f.get("date_from"):
        where.append("s.published_at >= ?")
        params.append(f["date_from"])

    where_sql = " AND ".join(where)

    try:
        rows = conn.execute(f"""
            SELECT
                u.telegram_id       AS member_id,
                u.name              AS member_name,
                s.id                AS signal_id,
                s.pair,
                s.direction,
                s.close_result      AS signal_result,
                s.result_pips       AS admin_pips,
                s.result_percent    AS admin_pct,
                sp.response         AS participation,
                tj.entry_price,
                tj.exit_price,
                tj.result_pips,
                tj.result_percent,
                tj.gain_usd,
                tj.capital_after,
                tj.behavior,
                tj.screenshot_url   AS capture_url,
                tj.submitted_at,
                COALESCE(tj.participated, 0) AS took_trade
            FROM signals s
            JOIN signal_participations sp ON sp.signal_id = s.id
            JOIN users u                  ON u.telegram_id = sp.user_id
            LEFT JOIN trade_journal tj    ON tj.signal_id = s.id
                                         AND tj.user_id   = sp.user_id
            WHERE {where_sql}
            ORDER BY s.published_at DESC, u.name ASC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        total = conn.execute(f"""
            SELECT COUNT(*)
            FROM signals s
            JOIN signal_participations sp ON sp.signal_id = s.id
            JOIN users u                  ON u.telegram_id = sp.user_id
            LEFT JOIN trade_journal tj    ON tj.signal_id = s.id
                                         AND tj.user_id   = sp.user_id
            WHERE {where_sql}
        """, params).fetchone()[0]

        history = [dict(r) for r in rows]
    finally:
        conn.close()

    return {"history": history, "total": total, "limit": limit, "offset": offset}


async def get_crossed_performance(filters: dict = None) -> dict:
    """
    Données pour le graphique de performance croisée (Vue Historique).

    Retourne :
      - admin_curve   : [{date, cumulative_pct}]   ligne admin (théorique)
      - members_curve : [{date, cumulative_pct}]   ligne membres (moyenne réelle)
      - capital_curve : [{date, avg_capital}]      capital moyen membres
    filters: { period: 'day'|'week'|'month', pair?, member_id? }
    """
    f      = filters or {}
    period = f.get("period", "day")
    pair   = f.get("pair")
    member = f.get("member_id")

    date_format = {
        "day":   "%Y-%m-%d",
        "week":  "%Y-W%W",
        "month": "%Y-%m",
    }.get(period, "%Y-%m-%d")

    conn   = get_conn()
    params_admin   = []
    params_members = []
    where_admin    = ["s.status = 'closed'"]
    where_members  = ["s.status = 'closed'", "tj.participated = 1"]

    if pair:
        where_admin.append("s.pair = ?");   params_admin.append(pair)
        where_members.append("s.pair = ?"); params_members.append(pair)
    if member:
        where_members.append("tj.user_id = ?"); params_members.append(member)

    try:
        # Courbe admin
        admin_rows = conn.execute(f"""
            SELECT
                strftime('{date_format}', s.closed_at) AS period,
                SUM(s.result_percent)                   AS total_pct,
                COUNT(*)                                AS trades
            FROM signals s
            WHERE {' AND '.join(where_admin)}
            GROUP BY period
            ORDER BY period ASC
        """, params_admin).fetchall()

        # Courbe membres
        members_rows = conn.execute(f"""
            SELECT
                strftime('{date_format}', s.closed_at) AS period,
                AVG(tj.result_percent)                  AS avg_pct,
                COUNT(*)                                AS journals
            FROM trade_journal tj
            JOIN signals s ON s.id = tj.signal_id
            WHERE {' AND '.join(where_members)}
            GROUP BY period
            ORDER BY period ASC
        """, params_members).fetchall()

        # Capital moyen membres (depuis member_capital)
        cap_rows = conn.execute(f"""
            SELECT
                strftime('{date_format}', mc.declared_at) AS period,
                AVG(mc.capital)                            AS avg_capital
            FROM member_capital mc
            GROUP BY period
            ORDER BY period ASC
        """).fetchall()

        # Cumulatif
        admin_curve   = []
        cumul_admin   = 0.0
        for r in admin_rows:
            cumul_admin += (r["total_pct"] or 0)
            admin_curve.append({
                "period": r["period"],
                "cumulative_pct": round(cumul_admin, 2),
                "trades": r["trades"],
            })

        members_curve = []
        cumul_members = 0.0
        for r in members_rows:
            cumul_members += (r["avg_pct"] or 0)
            members_curve.append({
                "period": r["period"],
                "cumulative_pct": round(cumul_members, 2),
                "journals": r["journals"],
            })

        capital_curve = [dict(r) for r in cap_rows]

    finally:
        conn.close()

    return {
        "admin_curve":    admin_curve,
        "members_curve":  members_curve,
        "capital_curve":  capital_curve,
        "period":         period,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PERFORMANCES MEMBRES
# ══════════════════════════════════════════════════════════════════════════════

async def get_member_performance(user_id: int) -> dict | None:
    """
    Profil de performance complet d'un membre pour le drawer Performances.

    Retourne :
      - stats globales (trades, win_rate, engagement, perf_totale)
      - capital_initial, capital_actuel, evolution_pct
      - capital_21j : liste de 21 points [{date, capital, type: 'up'|'down'|'flat'}]
      - capital_theorique : si le membre avait suivi tous les TP
      - manque_a_gagner : écart théorique - réel
      - performance_curve : [{signal_id, pair, result_pct, behavior, date}]
      - comportements détaillés
      - derniers trades
    """
    conn = get_conn()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
        if not user:
            return None
        user = dict(user)

        # Stats globales trading
        stats = conn.execute("""
            SELECT
                COUNT(*)                                                                AS total_trades,
                COUNT(CASE WHEN result_percent > 0 THEN 1 END)                         AS wins,
                COUNT(CASE WHEN result_percent < 0 THEN 1 END)                         AS losses,
                CASE WHEN COUNT(*) = 0 THEN NULL
                ELSE ROUND(
                    CAST(COUNT(CASE WHEN result_percent > 0 THEN 1 END) AS REAL)
                    / COUNT(*) * 100, 1
                ) END                                                                   AS win_rate,
                ROUND(SUM(result_percent), 2)                                           AS perf_totale,
                ROUND(AVG(result_percent), 2)                                           AS avg_result,
                ROUND(SUM(gain_usd), 2)                                                 AS total_gain_usd
            FROM trade_journal
            WHERE user_id = ? AND participated = 1 AND status = 'closed'
        """, (user_id,)).fetchone()
        stats = dict(stats) if stats else {}

        # Taux d'engagement (signaux répondus / signaux reçus)
        engagement = conn.execute("""
            SELECT
                COUNT(DISTINCT sp.signal_id)                            AS signals_received,
                COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL
                               THEN sp.signal_id END)                   AS signals_answered,
                COUNT(DISTINCT CASE WHEN sp.response = 'in'
                               THEN sp.signal_id END)                   AS signals_taken
            FROM signal_participations sp
            WHERE sp.user_id = ?
        """, (user_id,)).fetchone()
        if engagement and engagement["signals_received"] > 0:
            stats["engagement_rate"] = round(
                engagement["signals_answered"] / engagement["signals_received"] * 100, 1
            )
            stats["signals_taken"]    = engagement["signals_taken"]
            stats["signals_received"] = engagement["signals_received"]
        else:
            stats["engagement_rate"] = 0

        # Capital : initial (première déclaration), actuel (dernière)
        cap_initial = conn.execute("""
            SELECT capital FROM member_capital
            WHERE user_id = ? AND type = 'initial'
            ORDER BY declared_at ASC LIMIT 1
        """, (user_id,)).fetchone()

        cap_actuel = conn.execute("""
            SELECT capital, declared_at FROM member_capital
            WHERE user_id = ?
            ORDER BY declared_at DESC LIMIT 1
        """, (user_id,)).fetchone()

        capital_initial = float(cap_initial["capital"]) if cap_initial else None
        capital_actuel  = float(cap_actuel["capital"])  if cap_actuel  else None

        evolution_pct = None
        if capital_initial and capital_actuel and capital_initial > 0:
            evolution_pct = round((capital_actuel - capital_initial) / capital_initial * 100, 2)

        # Capital sur 21 jours (1 point par jour)
        twenty_one_days_ago = (datetime.now() - timedelta(days=21)).isoformat()
        cap_rows = conn.execute("""
            SELECT
                DATE(declared_at)        AS day,
                capital,
                type
            FROM member_capital
            WHERE user_id = ? AND declared_at >= ?
            ORDER BY declared_at ASC
        """, (user_id, twenty_one_days_ago)).fetchall()

        capital_21j = []
        prev_cap = None
        for r in cap_rows:
            cap = float(r["capital"])
            if prev_cap is None:
                direction = "flat"
            elif cap > prev_cap:
                direction = "up"
            elif cap < prev_cap:
                direction = "down"
            else:
                direction = "flat"
            capital_21j.append({
                "date":    r["day"],
                "capital": cap,
                "type":    direction,
            })
            prev_cap = cap

        # Capital théorique (si le membre avait suivi tous les TP1 admin)
        theo_row = conn.execute("""
            SELECT
                ROUND(SUM(CASE WHEN s.close_result = 'tp' THEN s.result_percent ELSE 0 END), 2) AS sum_admin_wins,
                ROUND(SUM(CASE WHEN s.close_result = 'sl' THEN s.result_percent ELSE 0 END), 2) AS sum_admin_losses
            FROM signals s
            JOIN signal_participations sp ON sp.signal_id = s.id
            WHERE sp.user_id = ? AND sp.response = 'in' AND s.status = 'closed'
        """, (user_id,)).fetchone()

        capital_theorique = None
        manque_a_gagner   = None
        if capital_initial and theo_row:
            cumul_theo = (theo_row["sum_admin_wins"] or 0) + (theo_row["sum_admin_losses"] or 0)
            capital_theorique = round(capital_initial * (1 + cumul_theo / 100), 2)
            if capital_actuel:
                manque_a_gagner = round(capital_theorique - capital_actuel, 2)

        # Courbe de performance (1 point par trade)
        perf_curve = conn.execute("""
            SELECT
                tj.signal_id,
                s.pair,
                s.direction,
                tj.result_percent,
                tj.result_pips,
                tj.behavior,
                tj.gain_usd,
                s.close_result,
                s.closed_at     AS trade_date
            FROM trade_journal tj
            JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND tj.participated = 1 AND tj.status = 'closed'
            ORDER BY s.closed_at ASC
        """, (user_id,)).fetchall()

        performance_curve = []
        cumul = 0.0
        for r in perf_curve:
            cumul += (r["result_percent"] or 0)
            performance_curve.append({
                "signal_id":     r["signal_id"],
                "pair":          r["pair"],
                "direction":     r["direction"],
                "result_pct":    r["result_percent"],
                "result_pips":   r["result_pips"],
                "gain_usd":      r["gain_usd"],
                "behavior":      r["behavior"],
                "close_result":  r["close_result"],
                "cumulative_pct": round(cumul, 2),
                "date":          r["trade_date"],
            })

        # Comportements détaillés
        behaviors = conn.execute("""
            SELECT
                behavior,
                COUNT(*)                        AS count,
                ROUND(AVG(result_percent), 2)   AS avg_pct
            FROM trade_journal
            WHERE user_id = ? AND participated = 1
            GROUP BY behavior
        """, (user_id,)).fetchall()

        # Respect des lots recommandés
        lot_respect = conn.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN tj.lot_used <= (s.lot_suggested * 1.1)
                            AND tj.lot_used > 0 THEN 1 END) AS respected
            FROM trade_journal tj
            JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND s.lot_suggested IS NOT NULL AND tj.lot_used IS NOT NULL
        """, (user_id,)).fetchone()

        lot_respect_rate = None
        if lot_respect and lot_respect["total"] > 0:
            lot_respect_rate = round(lot_respect["respected"] / lot_respect["total"] * 100, 1)

        # Vérifier suivi capital activé
        suivi_capital = conn.execute("""
            SELECT COUNT(*) FROM member_capital
            WHERE user_id = ? AND source = 'form'
            AND declared_at >= ?
        """, (user_id, (datetime.now() - timedelta(days=10)).isoformat())).fetchone()[0]

    finally:
        conn.close()

    return {
        "user_id":            user_id,
        "name":               user.get("name"),
        "stats":              stats,
        "capital_initial":    capital_initial,
        "capital_actuel":     capital_actuel,
        "evolution_pct":      evolution_pct,
        "capital_21j":        capital_21j,
        "capital_theorique":  capital_theorique,
        "manque_a_gagner":    manque_a_gagner,
        "performance_curve":  performance_curve,
        "behaviors":          [dict(b) for b in behaviors],
        "lot_respect_rate":   lot_respect_rate,
        "suivi_capital_actif": suivi_capital > 0,
    }


async def get_performances_list(filters: dict = None) -> dict:
    """
    Liste des membres pour la Vue Performances.

    filters: { search?, sort_by?: 'win_rate'|'discipline'|'engagement'|'capital', limit?, offset? }
    """
    f       = filters or {}
    limit   = int(f.get("limit",  50))
    offset  = int(f.get("offset",  0))
    sort_by = f.get("sort_by", "win_rate")
    search  = f.get("search", "")

    order_map = {
        "win_rate":    "win_rate DESC",
        "discipline":  "disciplined_count DESC",
        "engagement":  "engagement_rate DESC",
        "capital":     "capital_actuel DESC",
        "perf":        "perf_totale DESC",
    }
    order_sql = order_map.get(sort_by, "win_rate DESC")

    conn = get_conn()
    try:
        where = ["1=1"]
        params = []
        if search:
            where.append("u.name LIKE ?")
            params.append(f"%{search}%")

        where_sql = " AND ".join(where)

        rows = conn.execute(f"""
            SELECT
                u.telegram_id                                                               AS user_id,
                u.name,
                COUNT(DISTINCT CASE WHEN tj.participated = 1 THEN tj.signal_id END)        AS total_trades,
                COUNT(DISTINCT CASE WHEN tj.participated = 1
                               AND tj.result_percent > 0 THEN tj.signal_id END)            AS wins,
                CASE WHEN COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END) = 0
                     THEN NULL
                     ELSE ROUND(
                        CAST(COUNT(DISTINCT CASE WHEN tj.participated=1
                                              AND tj.result_percent > 0
                                              THEN tj.signal_id END) AS REAL)
                        / COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END) * 100, 1
                     ) END                                                                  AS win_rate,
                ROUND(SUM(CASE WHEN tj.participated=1 THEN tj.result_percent ELSE 0 END), 2) AS perf_totale,
                COUNT(DISTINCT CASE WHEN tj.behavior = 'disciplined' THEN tj.signal_id END) AS disciplined_count,
                COUNT(DISTINCT sp.signal_id)                                                 AS signals_received,
                CASE WHEN COUNT(DISTINCT sp.signal_id) = 0 THEN 0
                     ELSE ROUND(
                        CAST(COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL
                                             THEN sp.signal_id END) AS REAL)
                        / COUNT(DISTINCT sp.signal_id) * 100, 1
                     ) END                                                                   AS engagement_rate,
                mc_last.capital                                                              AS capital_actuel,
                mc_last.capital_delta_pct                                                    AS capital_evolution_pct,
                CASE WHEN mc_last.capital IS NULL THEN 'no_tracking' ELSE 'active' END      AS suivi_status
            FROM users u
            LEFT JOIN trade_journal tj          ON tj.user_id   = u.telegram_id
            LEFT JOIN signal_participations sp  ON sp.user_id   = u.telegram_id
            LEFT JOIN (
                SELECT
                    mc1.user_id,
                    mc1.capital,
                    CASE WHEN mc_init.capital > 0
                         THEN ROUND((mc1.capital - mc_init.capital) / mc_init.capital * 100, 2)
                         ELSE NULL END AS capital_delta_pct
                FROM member_capital mc1
                LEFT JOIN (
                    SELECT user_id, capital FROM member_capital
                    WHERE type = 'initial'
                ) mc_init ON mc_init.user_id = mc1.user_id
                WHERE mc1.declared_at = (
                    SELECT MAX(declared_at) FROM member_capital WHERE user_id = mc1.user_id
                )
            ) mc_last ON mc_last.user_id = u.telegram_id
            WHERE {where_sql}
            AND EXISTS (SELECT 1 FROM signal_participations WHERE user_id = u.telegram_id)
            GROUP BY u.telegram_id
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        total = conn.execute(f"""
            SELECT COUNT(DISTINCT u.telegram_id)
            FROM users u
            WHERE {where_sql}
            AND EXISTS (SELECT 1 FROM signal_participations WHERE user_id = u.telegram_id)
        """, params).fetchone()[0]

        members = [dict(r) for r in rows]
    finally:
        conn.close()

    return {"members": members, "total": total, "limit": limit, "offset": offset}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CLASSEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def get_leaderboard(filters: dict = None) -> dict:
    """
    Classement des membres par performance.
    Minimum 3 trades journalisés pour figurer.

    filters: {
        period?: 'week'|'month'|'all',
        min_trades?: int, limit?, offset?
    }
    """
    f          = filters or {}
    period     = f.get("period", "all")
    min_trades = int(f.get("min_trades", 3))
    limit      = int(f.get("limit", 50))
    offset     = int(f.get("offset", 0))

    date_filter = ""
    params = []
    if period == "week":
        date_filter = "AND s.closed_at >= ?"
        params.append((datetime.now() - timedelta(days=7)).isoformat())
    elif period == "month":
        date_filter = "AND s.closed_at >= ?"
        params.append((datetime.now() - timedelta(days=30)).isoformat())

    conn = get_conn()
    try:
        rows = conn.execute(f"""
            SELECT
                u.telegram_id   AS user_id,
                u.name,
                COUNT(DISTINCT CASE WHEN tj.participated=1 THEN tj.signal_id END) AS total_trades,
                ROUND(
                    CAST(COUNT(DISTINCT CASE WHEN tj.participated=1
                                          AND tj.result_percent > 0
                                          THEN tj.signal_id END) AS REAL)
                    / NULLIF(COUNT(DISTINCT CASE WHEN tj.participated=1
                                             THEN tj.signal_id END), 0) * 100, 1
                )                                                                  AS win_rate,
                ROUND(SUM(CASE WHEN tj.participated=1 THEN tj.result_percent ELSE 0 END), 2) AS perf_totale,
                CASE WHEN COUNT(DISTINCT sp.signal_id) = 0 THEN 0
                ELSE ROUND(
                    CAST(COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL THEN sp.signal_id END) AS REAL)
                    / COUNT(DISTINCT sp.signal_id) * 100, 1
                ) END                                                              AS engagement_rate,
                mc_last.capital                                                    AS capital_actuel,
                CASE WHEN mc_last.capital IS NULL THEN 1 ELSE 0 END               AS suivi_off
            FROM users u
            JOIN trade_journal tj         ON tj.user_id = u.telegram_id {date_filter}
            LEFT JOIN signals s           ON s.id = tj.signal_id
            LEFT JOIN signal_participations sp ON sp.user_id = u.telegram_id
            LEFT JOIN (
                SELECT user_id, capital FROM member_capital
                WHERE declared_at = (
                    SELECT MAX(declared_at) FROM member_capital mc2
                    WHERE mc2.user_id = member_capital.user_id
                )
            ) mc_last ON mc_last.user_id = u.telegram_id
            WHERE tj.participated = 1 AND tj.status = 'closed'
            GROUP BY u.telegram_id
            HAVING total_trades >= ?
            ORDER BY perf_totale DESC
            LIMIT ? OFFSET ?
        """, params + [min_trades, limit, offset]).fetchall()

        leaderboard = []
        for rank, r in enumerate(rows, start=1 + offset):
            d          = dict(r)
            d["rank"]  = rank
            leaderboard.append(d)

        total = conn.execute(f"""
            SELECT COUNT(DISTINCT tj.user_id)
            FROM trade_journal tj
            LEFT JOIN signals s ON s.id = tj.signal_id
            WHERE tj.participated = 1 AND tj.status = 'closed' {date_filter}
            GROUP BY tj.user_id
            HAVING COUNT(DISTINCT tj.signal_id) >= ?
        """, params + [min_trades]).fetchone()
        total_count = total[0] if total else 0

    finally:
        conn.close()

    return {
        "leaderboard": leaderboard,
        "total":       total_count,
        "period":      period,
        "min_trades":  min_trades,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — PAIRES & PIP
# ══════════════════════════════════════════════════════════════════════════════

async def get_pairs(active_only: bool = False) -> list:
    """Liste de toutes les paires avec leurs paramètres."""
    conn = get_conn()
    try:
        where = "WHERE is_active = 1" if active_only else ""
        rows  = conn.execute(f"""
            SELECT * FROM trading_pairs {where} ORDER BY category, symbol
        """).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


async def create_pair(payload: dict) -> dict:
    """
    Ajoute une nouvelle paire.
    payload: { symbol, category, pip_value, decimals, binance_symbol?, note? }
    """
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO trading_pairs
                (symbol, category, pip_value, decimals, binance_symbol, note, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (
            payload["symbol"].upper(),
            payload.get("category", "forex"),
            float(payload["pip_value"]),
            int(payload.get("decimals", 5)),
            payload.get("binance_symbol"),
            payload.get("note"),
        ))
        pair_id = cur.lastrowid
        conn.commit()
        pair = dict(conn.execute("SELECT * FROM trading_pairs WHERE id = ?", (pair_id,)).fetchone())
    finally:
        conn.close()
    return pair


async def update_pair(pair_id: int, payload: dict) -> dict:
    """Met à jour les paramètres d'une paire."""
    fields, values = [], []
    for col in ("symbol", "category", "pip_value", "decimals", "binance_symbol", "is_active", "note"):
        if col in payload:
            fields.append(f"{col} = ?")
            values.append(payload[col])
    if not fields:
        return {"status": "nothing_to_update"}
    fields.append("updated_at = ?")
    values.append(_now())
    values.append(pair_id)

    conn = get_conn()
    try:
        conn.execute(f"UPDATE trading_pairs SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        pair = dict(conn.execute("SELECT * FROM trading_pairs WHERE id = ?", (pair_id,)).fetchone())
    finally:
        conn.close()
    return pair


async def delete_pair(pair_id: int) -> dict:
    """Désactive une paire (soft delete)."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE trading_pairs SET is_active = 0, updated_at = ? WHERE id = ?",
            (_now(), pair_id)
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "deactivated", "id": pair_id}


def calculate_lot(
    capital: float,
    risk_pct: float,
    sl_pips: float,
    pair_symbol: str,
    tp1_pips: float = 0,
) -> dict:
    """
    Calculateur de lot.

    Formule : lot = (capital × risk%) / (sl_pips × pip_value)

    Retourne : {
        risk_usd, lot_suggested, max_loss,
        gain_tp1, rr_ratio, pip_value_used
    }
    """
    pip_value = _get_pip_value(pair_symbol)
    risk_usd  = round(capital * risk_pct / 100, 2)
    lot       = round(risk_usd / (sl_pips * pip_value), 4) if sl_pips > 0 else 0
    gain_tp1  = round(lot * tp1_pips * pip_value, 2) if tp1_pips > 0 else 0
    rr_ratio  = round(tp1_pips / sl_pips, 2) if (sl_pips > 0 and tp1_pips > 0) else None

    return {
        "risk_usd":       risk_usd,
        "lot_suggested":  lot,
        "max_loss":       -risk_usd,
        "gain_tp1":       gain_tp1,
        "rr_ratio":       rr_ratio,
        "pip_value_used": pip_value,
    }


async def get_suggested_lot_for_signal(
    signal_id: int,
    risk_pct: float = 2.0,
) -> dict:
    """
    Calcule le lot suggéré pour tous les membres actifs d'un signal,
    basé sur leur capital individuel.

    Retourne :
      - avg_lot       : lot moyen (pour le message groupé)
      - per_member    : [{user_id, name, capital, lot}] (pour messages personnalisés)
      - avg_capital   : capital moyen des membres
    """
    conn = get_conn()
    try:
        signal = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not signal:
            return {"error": "Signal introuvable"}
        signal = dict(signal)

        # Destinataires du signal
        cat_members = conn.execute("""
            SELECT DISTINCT u.telegram_id AS user_id, u.name,
                   mc.capital
            FROM categories c
            JOIN users u ON u.telegram_id = c.id_user
            LEFT JOIN (
                SELECT user_id, capital FROM member_capital
                WHERE declared_at = (
                    SELECT MAX(declared_at) FROM member_capital mc2
                    WHERE mc2.user_id = member_capital.user_id
                )
            ) mc ON mc.user_id = u.telegram_id
            WHERE c.name_categorie = ?
        """, (signal["category"],)).fetchall()

    finally:
        conn.close()

    if not signal.get("sl") or not signal.get("entry_price"):
        return {"error": "Signal sans SL — calcul impossible"}

    decimals = _get_pair_decimals(signal["pair"])
    sl_pips  = abs(_pips(signal["entry_price"], signal["sl"],
                         signal["direction"], decimals))

    per_member = []
    for m in cat_members:
        capital = float(m["capital"]) if m["capital"] else 1000.0  # fallback 1000$
        result  = calculate_lot(capital, risk_pct, sl_pips, signal["pair"])
        per_member.append({
            "user_id": m["user_id"],
            "name":    m["name"],
            "capital": capital,
            "lot":     result["lot_suggested"],
        })

    avg_capital = round(sum(m["capital"] for m in per_member) / len(per_member), 2) if per_member else 0
    avg_lot     = round(sum(m["lot"] for m in per_member) / len(per_member), 4)     if per_member else 0

    return {
        "signal_id":   signal_id,
        "pair":        signal["pair"],
        "sl_pips":     sl_pips,
        "risk_pct":    risk_pct,
        "avg_capital": avg_capital,
        "avg_lot":     avg_lot,
        "per_member":  per_member,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — FORMULAIRES & COLLECTE
# ══════════════════════════════════════════════════════════════════════════════

async def get_form_stats() -> dict:
    """Stats globales des formulaires de collecte trading."""
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(DISTINCT f.id)                                          AS total_forms,
                COUNT(DISTINCT fr.id)                                         AS total_responses,
                COUNT(DISTINCT fr.telegram_id)                                AS unique_respondents,
                COUNT(DISTINCT CASE WHEN f.type = 'system' THEN f.id END)     AS system_forms,
                COUNT(DISTINCT CASE WHEN f.type = 'custom' THEN f.id END)     AS custom_forms
            FROM forms f
            LEFT JOIN form_responses fr ON fr.form_id = f.id
        """).fetchone()
        stats = dict(row)

        # Taux de complétion (sessions complètes / sessions créées)
        completion = conn.execute("""
            SELECT
                COUNT(*)                                        AS total_sessions,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed_sessions
            FROM form_sessions
        """).fetchone()
        if completion and completion["total_sessions"] > 0:
            stats["completion_rate"] = round(
                completion["completed_sessions"] / completion["total_sessions"] * 100, 1
            )
        else:
            stats["completion_rate"] = 0

    finally:
        conn.close()
    return stats




async def get_forms_list() -> list:
    """
    Liste tous les formulaires avec statistiques de collecte.
    Indique le type (system | custom) et les stats associées.
    """
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                f.id,
                f.name,
                f.command,
                f.type,
                f.is_active,
                f.created_at,
                COUNT(DISTINCT fr.telegram_id) AS respondents,
                COUNT(DISTINCT fr.id)          AS total_responses,
                MAX(fr.created_at)             AS last_response_at
            FROM forms f
            LEFT JOIN form_responses fr ON fr.form_id = f.id
            GROUP BY f.id
            ORDER BY f.type DESC, f.created_at DESC
        """).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


async def get_form_field_mapping(form_id: int) -> dict:
    """
    Retourne le mapping champ→statistique d'un formulaire.

    Structure retournée :
    {
      form_id, form_name,
      fields: [{
        field_id, field_label, field_type,
        maps_to_stat, aggregation, data_type,
        sample_values: [dernières valeurs reçues]
      }]
    }
    """
    conn = get_conn()
    try:
        form = conn.execute("SELECT * FROM forms WHERE id = ?", (form_id,)).fetchone()
        if not form:
            return {"error": "Formulaire introuvable"}
        form = dict(form)

        fields = json.loads(form.get("fields", "[]"))

        # Pour chaque champ, récupérer des exemples de valeurs reçues
        enriched = []
        for field in fields:
            fid = field.get("id")
            samples = []
            if fid:
                sample_rows = conn.execute("""
                    SELECT value, created_at FROM form_responses
                    WHERE form_id = ? AND field_id = ?
                    ORDER BY created_at DESC LIMIT 5
                """, (form_id, fid)).fetchall()
                samples = [{"value": r["value"], "at": r["created_at"]} for r in sample_rows]

            enriched.append({
                "field_id":     fid,
                "field_label":  field.get("label"),
                "field_type":   field.get("type"),
                "maps_to_stat": field.get("maps_to_stat"),
                "aggregation":  field.get("aggregation", "last"),
                "data_type":    field.get("data_type", "text"),
                "required":     field.get("required", True),
                "sample_values": samples,
            })

    finally:
        conn.close()

    return {
        "form_id":   form_id,
        "form_name": form.get("name"),
        "form_type": form.get("type"),
        "fields":    enriched,
    }


async def update_form_field_mapping(form_id: int, payload: dict) -> dict:
    """
    Met à jour le mapping champ→statistique pour un formulaire.

    payload: {
        fields: [{
            field_id, maps_to_stat, aggregation, data_type
        }]
    }
    """
    conn = get_conn()
    try:
        form = conn.execute("SELECT fields FROM forms WHERE id = ?", (form_id,)).fetchone()
        if not form:
            return {"error": "Formulaire introuvable"}

        fields = json.loads(form["fields"] or "[]")
        updates = {str(f["field_id"]): f for f in payload.get("fields", [])}

        for field in fields:
            fid = str(field.get("id", ""))
            if fid in updates:
                upd = updates[fid]
                if "maps_to_stat" in upd: field["maps_to_stat"] = upd["maps_to_stat"]
                if "aggregation"  in upd: field["aggregation"]  = upd["aggregation"]
                if "data_type"    in upd: field["data_type"]     = upd["data_type"]

        conn.execute(
            "UPDATE forms SET fields = ? WHERE id = ?",
            (json.dumps(fields, ensure_ascii=False), form_id)
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "updated", "form_id": form_id}


async def get_collected_data_summary() -> list:
    """
    Tableau récapitulatif : formulaire → données collectées → stats produites.

    Retourne : [{
        form_id, form_name, form_type,
        fields_collected: [field_label],
        stats_produced:   ['win_rate_reel', 'capital_evolution', ...],
        total_responses, last_response_at
    }]
    """
    conn = get_conn()
    try:
        forms = conn.execute("""
            SELECT f.id, f.name, f.type, f.fields,
                   COUNT(DISTINCT fr.telegram_id) AS total_responses,
                   MAX(fr.created_at)             AS last_response_at
            FROM forms f
            LEFT JOIN form_responses fr ON fr.form_id = f.id
            GROUP BY f.id
        """).fetchall()

        summary = []
        for f in forms:
            form_dict = dict(f)
            try:
                fields = json.loads(form_dict.get("fields") or "[]")
            except Exception:
                fields = []

            fields_collected = [fld.get("label") for fld in fields if fld.get("label")]
            stats_produced   = list({
                fld.get("maps_to_stat") for fld in fields
                if fld.get("maps_to_stat")
            })

            summary.append({
                "form_id":          form_dict["id"],
                "form_name":        form_dict["name"],
                "form_type":        form_dict["type"],
                "fields_collected": fields_collected,
                "stats_produced":   stats_produced,
                "total_responses":  form_dict["total_responses"],
                "last_response_at": form_dict["last_response_at"],
            })
    finally:
        conn.close()

    return summary


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — BILAN IA
# ══════════════════════════════════════════════════════════════════════════════

"""
SCHÉMA IA — Données retournées pour les bilans IA

L'objet passé à Claude pour générer un bilan personnalisé contient :

{
  "member": {
    "user_id": int,
    "name": str,
    "week_label": str,       // ex: "Semaine du 14 au 20 avril 2026"
  },
  "performance": {
    "total_trades":    int,
    "wins":            int,
    "losses":          int,
    "win_rate":        float,   // ex: 71.4
    "perf_totale":     float,   // ex: +12.3 (%)
    "avg_result":      float,   // ex: +1.8 (% moyen par trade)
    "best_trade": {"pair": str, "result_pct": float, "date": str},
    "worst_trade": {"pair": str, "result_pct": float, "date": str},
  },
  "behavior": {
    "disciplined_pct":  float,  // % trades disciplinés
    "early_exit_pct":   float,
    "sl_skip_pct":      float,
    "lot_respect_rate": float,
    "early_exit_cost":  float,  // perte liée aux sorties anticipées (%)
  },
  "capital": {
    "initial":    float | None,
    "actuel":     float | None,
    "theorique":  float | None,
    "manque":     float | None,
    "evolution_pct": float | None,
  },
  "engagement": {
    "rate":            float,   // % signaux répondus
    "signals_taken":   int,
    "signals_received": int,
  },
  "comparison": {
    "admin_win_rate":  float,
    "admin_perf":      float,
    "diff_win_rate":   float,   // membre - admin
    "diff_perf":       float,
  },
  "admin_config": {
    "include_perf":       bool,
    "include_behavior":   bool,
    "include_recommendations": bool,
    "include_comparison": bool,
  }
}

Claude retourne un message Telegram formaté (Markdown Telegram) :
{
  "user_id": int,
  "message": str   // Le bilan formaté pour ce membre
}
"""

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL   = "claude-sonnet-4-20250514"


async def _build_member_bilan_context(
    user_id:       int,
    week_start:    str,
    week_end:      str,
    week_label:    str,
    admin_config:  dict,
) -> dict:
    """
    Construit le contexte complet d'un membre pour la génération IA.
    Retourne le schéma IA documenté ci-dessus.
    """
    conn = get_conn()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
        if not user:
            return None
        name = dict(user).get("name", "l'ami")

        # Performance sur la semaine
        perf = conn.execute("""
            SELECT
                COUNT(*)                                                                AS total_trades,
                COUNT(CASE WHEN tj.result_percent > 0 THEN 1 END)                      AS wins,
                COUNT(CASE WHEN tj.result_percent < 0 THEN 1 END)                      AS losses,
                CASE WHEN COUNT(*) = 0 THEN NULL
                ELSE ROUND(CAST(COUNT(CASE WHEN tj.result_percent > 0 THEN 1 END) AS REAL)
                     / COUNT(*) * 100, 1) END                                          AS win_rate,
                ROUND(SUM(tj.result_percent), 2)                                        AS perf_totale,
                ROUND(AVG(tj.result_percent), 2)                                        AS avg_result
            FROM trade_journal tj
            JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND tj.participated = 1
              AND s.closed_at >= ? AND s.closed_at <= ?
        """, (user_id, week_start, week_end)).fetchone()
        perf = dict(perf) if perf else {}

        # Best / Worst trade de la semaine
        best = conn.execute("""
            SELECT s.pair, tj.result_percent, s.closed_at
            FROM trade_journal tj JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND s.closed_at >= ? AND s.closed_at <= ?
            ORDER BY tj.result_percent DESC LIMIT 1
        """, (user_id, week_start, week_end)).fetchone()

        worst = conn.execute("""
            SELECT s.pair, tj.result_percent, s.closed_at
            FROM trade_journal tj JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND s.closed_at >= ? AND s.closed_at <= ?
            ORDER BY tj.result_percent ASC LIMIT 1
        """, (user_id, week_start, week_end)).fetchone()

        # Comportements de la semaine
        beh = conn.execute("""
            SELECT
                COUNT(*)                                                    AS total,
                COUNT(CASE WHEN behavior = 'disciplined' THEN 1 END)        AS disciplined,
                COUNT(CASE WHEN behavior = 'early_exit'  THEN 1 END)        AS early_exit,
                COUNT(CASE WHEN behavior = 'sl_skip'     THEN 1 END)        AS sl_skip
            FROM trade_journal tj
            JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND tj.participated = 1
              AND s.closed_at >= ? AND s.closed_at <= ?
        """, (user_id, week_start, week_end)).fetchone()
        beh = dict(beh) if beh else {}
        total_beh = beh.get("total") or 1

        # Coût des sorties anticipées (différence résultat réel vs TP admin)
        early_cost = conn.execute("""
            SELECT ROUND(SUM(s.result_percent - tj.result_percent), 2) AS cost
            FROM trade_journal tj
            JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND tj.behavior = 'early_exit'
              AND s.closed_at >= ? AND s.closed_at <= ?
        """, (user_id, week_start, week_end)).fetchone()

        # Capital
        cap_actuel = conn.execute("""
            SELECT capital FROM member_capital WHERE user_id = ?
            ORDER BY declared_at DESC LIMIT 1
        """, (user_id,)).fetchone()

        cap_initial = conn.execute("""
            SELECT capital FROM member_capital WHERE user_id = ? AND type = 'initial'
            ORDER BY declared_at ASC LIMIT 1
        """, (user_id,)).fetchone()

        capital_actuel  = float(cap_actuel["capital"])  if cap_actuel  else None
        capital_initial = float(cap_initial["capital"]) if cap_initial else None
        evo_pct = None
        if capital_initial and capital_actuel:
            evo_pct = round((capital_actuel - capital_initial) / capital_initial * 100, 2)

        # Capital théorique sur la semaine
        theo = conn.execute("""
            SELECT ROUND(SUM(s.result_percent), 2) AS admin_sum
            FROM signals s
            JOIN signal_participations sp ON sp.signal_id = s.id
            WHERE sp.user_id = ? AND sp.response = 'in'
              AND s.closed_at >= ? AND s.closed_at <= ?
              AND s.status = 'closed'
        """, (user_id, week_start, week_end)).fetchone()

        cap_theo   = None
        manque     = None
        if capital_initial and theo and theo["admin_sum"]:
            cap_theo = round(capital_initial * (1 + theo["admin_sum"] / 100), 2)
            if capital_actuel:
                manque = round(cap_theo - capital_actuel, 2)

        # Engagement de la semaine
        eng = conn.execute("""
            SELECT
                COUNT(DISTINCT sp.signal_id) AS received,
                COUNT(DISTINCT CASE WHEN sp.response IS NOT NULL THEN sp.signal_id END) AS answered,
                COUNT(DISTINCT CASE WHEN sp.response = 'in' THEN sp.signal_id END) AS taken
            FROM signal_participations sp
            JOIN signals s ON s.id = sp.signal_id
            WHERE sp.user_id = ? AND s.published_at >= ? AND s.published_at <= ?
        """, (user_id, week_start, week_end)).fetchone()
        eng = dict(eng) if eng else {}
        eng_rate = 0
        if eng.get("received") and eng["received"] > 0:
            eng_rate = round(eng.get("answered", 0) / eng["received"] * 100, 1)

        # Stats admin sur la période (pour comparaison)
        admin_perf = conn.execute("""
            SELECT
                ROUND(AVG(CASE WHEN close_result = 'tp' THEN 1.0 ELSE 0.0 END) * 100, 1) AS win_rate,
                ROUND(SUM(result_percent), 2) AS perf_totale
            FROM signals
            WHERE status = 'closed' AND closed_at >= ? AND closed_at <= ?
        """, (week_start, week_end)).fetchone()
        admin_perf = dict(admin_perf) if admin_perf else {}

        # Lot respect rate
        lot_r = conn.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN tj.lot_used <= (s.lot_suggested * 1.1) AND tj.lot_used > 0 THEN 1 END) AS respected
            FROM trade_journal tj
            JOIN signals s ON s.id = tj.signal_id
            WHERE tj.user_id = ? AND s.lot_suggested IS NOT NULL
              AND s.closed_at >= ? AND s.closed_at <= ?
        """, (user_id, week_start, week_end)).fetchone()
        lot_respect = None
        if lot_r and lot_r["total"] > 0:
            lot_respect = round(lot_r["respected"] / lot_r["total"] * 100, 1)

    finally:
        conn.close()

    return {
        "member": {
            "user_id":    user_id,
            "name":       name,
            "week_label": week_label,
        },
        "performance": {
            "total_trades": perf.get("total_trades", 0),
            "wins":         perf.get("wins", 0),
            "losses":       perf.get("losses", 0),
            "win_rate":     perf.get("win_rate"),
            "perf_totale":  perf.get("perf_totale"),
            "avg_result":   perf.get("avg_result"),
            "best_trade":   dict(best)  if best  else None,
            "worst_trade":  dict(worst) if worst else None,
        },
        "behavior": {
            "disciplined_pct":  round(beh.get("disciplined", 0) / total_beh * 100, 1),
            "early_exit_pct":   round(beh.get("early_exit",  0) / total_beh * 100, 1),
            "sl_skip_pct":      round(beh.get("sl_skip",     0) / total_beh * 100, 1),
            "lot_respect_rate": lot_respect,
            "early_exit_cost":  float(early_cost["cost"]) if early_cost and early_cost["cost"] else 0,
        },
        "capital": {
            "initial":       capital_initial,
            "actuel":        capital_actuel,
            "theorique":     cap_theo,
            "manque":        manque,
            "evolution_pct": evo_pct,
        },
        "engagement": {
            "rate":             eng_rate,
            "signals_taken":    eng.get("taken", 0),
            "signals_received": eng.get("received", 0),
        },
        "comparison": {
            "admin_win_rate":  admin_perf.get("win_rate"),
            "admin_perf":      admin_perf.get("perf_totale"),
            "diff_win_rate":   round((perf.get("win_rate") or 0) - (admin_perf.get("win_rate") or 0), 1),
            "diff_perf":       round((perf.get("perf_totale") or 0) - (admin_perf.get("perf_totale") or 0), 2),
        },
        "admin_config": admin_config,
    }


async def generate_member_bilan(context: dict) -> str:
    """
    Appelle Claude pour générer le bilan personnalisé d'un membre.
    Retourne le message Telegram Markdown.

    Le prompt est construit dynamiquement à partir du contexte IA.
    """
    member  = context["member"]
    perf    = context["performance"]
    beh     = context["behavior"]
    cap     = context["capital"]
    eng     = context["engagement"]
    comp    = context["comparison"]
    config  = context["admin_config"]

    # Construction du prompt
    sections = []
    sections.append(
        f"Tu es un coach de trading bienveillant et direct. "
        f"Génère le bilan hebdomadaire de {member['name']} pour {member['week_label']}. "
        f"Réponds en français. Format : Telegram Markdown (gras avec *texte*, italique avec _texte_). "
        f"Commence directement par le bilan, sans introduction générique. "
        f"Sois concis (max 200 mots), personnalisé et actionnable."
    )

    sections.append(f"\n\n### DONNÉES DISPONIBLES ###\n")

    if config.get("include_perf", True) and perf.get("total_trades", 0) > 0:
        sections.append(
            f"PERFORMANCE : {perf['total_trades']} trades · "
            f"Win rate : {perf.get('win_rate') or 'N/A'}% · "
            f"Perf totale : {perf.get('perf_totale') or 0}% · "
            f"Moy/trade : {perf.get('avg_result') or 0}%"
        )
        if perf.get("best_trade"):
            bt = perf["best_trade"]
            sections.append(f"Meilleur trade : {bt.get('pair')} {bt.get('result_percent') or bt.get('result_pct', '')}%")

    if config.get("include_behavior", True):
        sections.append(
            f"COMPORTEMENT : Discipliné {beh['disciplined_pct']}% · "
            f"Sortie anticipée {beh['early_exit_pct']}% · "
            f"Ignore SL {beh['sl_skip_pct']}%"
        )
        if beh.get("early_exit_cost") and beh["early_exit_cost"] > 0:
            sections.append(f"Coût sorties anticipées : -{beh['early_exit_cost']}%")
        if beh.get("lot_respect_rate") is not None:
            sections.append(f"Respect des lots : {beh['lot_respect_rate']}%")

    if cap.get("actuel"):
        sections.append(
            f"CAPITAL : {cap['actuel']}$ "
            f"({'↑' if (cap.get('evolution_pct') or 0) >= 0 else '↓'} "
            f"{abs(cap.get('evolution_pct') or 0)}%)"
        )
        if cap.get("manque") and cap["manque"] > 0:
            sections.append(f"Manque à gagner (sorties anticipées) : -{cap['manque']}$")

    sections.append(
        f"ENGAGEMENT : {eng['rate']}% des signaux traités "
        f"({eng['signals_taken']}/{eng['signals_received']} pris)"
    )

    if config.get("include_comparison", False) and comp.get("admin_win_rate"):
        sections.append(
            f"COMPARAISON ADMIN : admin {comp['admin_win_rate']}% win · "
            f"toi {perf.get('win_rate') or 'N/A'}% (écart {comp['diff_win_rate']:+}%)"
        )

    if config.get("include_recommendations", True):
        sections.append(
            "\n### CONSIGNE ###\n"
            "Inclus 1-2 recommandations concrètes et personnalisées basées sur ces données. "
            "Si sorties anticipées > 20% : insiste sur la patience au TP. "
            "Si sl_skip > 10% : insiste sur la discipline SL. "
            "Si win_rate > 70% : félicite chaleureusement. "
            "Si 0 trade : encourage à journaliser la semaine prochaine."
        )

    prompt = "\n".join(sections)

    # Appel API Anthropic
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                ANTHROPIC_API_URL,
                headers={"Content-Type": "application/json"},
                json={
                    "model":      ANTHROPIC_MODEL,
                    "max_tokens": 500,
                    "messages":   [{"role": "user", "content": prompt}],
                },
            )
            data    = resp.json()
            message = data["content"][0]["text"].strip()
    except Exception as e:
        # Fallback message si Claude indisponible
        message = (
            f"📊 *Bilan {member['week_label']}*\n\n"
            f"💪 {perf.get('total_trades', 0)} trades · "
            f"{perf.get('win_rate') or 'N/A'}% win rate\n"
            f"📈 Perf : {perf.get('perf_totale') or 0}%\n\n"
            f"_Bilan détaillé temporairement indisponible._"
        )

    return message


async def generate_weekly_bilans(payload: dict) -> dict:
    """
    Génère et (optionnellement) envoie les bilans hebdomadaires IA.

    payload: {
        week_start:  '2026-04-14T00:00:00',
        week_end:    '2026-04-20T23:59:59',
        week_label:  'Semaine du 14 au 20 avril 2026',
        target:      'journalised' | 'all' | 'clients_actifs',
        send:        bool,   // envoyer ou juste prévisualiser
        admin_config: {
            include_perf:          bool,
            include_behavior:      bool,
            include_recommendations: bool,
            include_comparison:    bool,
        }
    }

    Retourne : {
        total:        int,
        generated:    int,
        sent:         int,
        errors:       int,
        preview:      str,    // exemple du premier bilan généré
        preview_user: str,    // nom du membre de prévisualisation
        bilan_id:     int,    // id en base ai_bilans
    }
    """
    week_start   = payload["week_start"]
    week_end     = payload["week_end"]
    week_label   = payload.get("week_label", "Cette semaine")
    target       = payload.get("target", "journalised")
    send         = payload.get("send", False)
    admin_config = payload.get("admin_config", {
        "include_perf":            True,
        "include_behavior":        True,
        "include_recommendations": True,
        "include_comparison":      False,
    })

    # Récupérer la liste des membres cibles
    conn = get_conn()
    try:
        if target == "journalised":
            rows = conn.execute("""
                SELECT DISTINCT tj.user_id
                FROM trade_journal tj
                JOIN signals s ON s.id = tj.signal_id
                WHERE s.closed_at >= ? AND s.closed_at <= ?
                  AND tj.participated = 1
            """, (week_start, week_end)).fetchall()
        elif target == "all":
            rows = conn.execute(
                "SELECT telegram_id AS user_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
        else:
            rows = conn.execute("""
                SELECT DISTINCT id_user AS user_id FROM categories
                WHERE name_categorie = ?
            """, (target,)).fetchall()

        user_ids = [r["user_id"] for r in rows]

        # Enregistrement du bilan en base
        cur = conn.execute("""
            INSERT INTO ai_bilans
                (week_label, week_start, week_end, target, generated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (week_label, week_start, week_end, target, _now()))
        bilan_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    total      = len(user_ids)
    generated  = 0
    sent       = 0
    errors     = 0
    preview    = None
    preview_user = None

    # Génération en batch (limité pour ne pas surcharger l'API)
    for idx, user_id in enumerate(user_ids):
        try:
            context = await _build_member_bilan_context(
                user_id, week_start, week_end, week_label, admin_config
            )
            if not context:
                errors += 1
                continue

            message = await generate_member_bilan(context)
            generated += 1

            # Premier bilan → aperçu
            if idx == 0:
                preview      = message
                preview_user = context["member"]["name"]

            # Envoi Telegram si demandé
            if send and _bot:
                try:
                    await _bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                    sent += 1
                    await asyncio.sleep(0.1)  # throttle
                except Exception:
                    pass

        except Exception as e:
            errors += 1

    # Mise à jour stats en base
    conn2 = get_conn()
    try:
        conn2.execute(
            "UPDATE ai_bilans SET total_sent = ? WHERE id = ?",
            (sent if send else 0, bilan_id)
        )
        conn2.commit()
    finally:
        conn2.close()

    return {
        "bilan_id":    bilan_id,
        "total":       total,
        "generated":   generated,
        "sent":        sent if send else 0,
        "errors":      errors,
        "preview":     preview,
        "preview_user": preview_user,
        "week_label":  week_label,
    }


async def get_bilan_history() -> list:
    """Historique des bilans IA envoyés."""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM ai_bilans
            ORDER BY generated_at DESC
            LIMIT 20
        """).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — STATS DASHBOARD (Vue Signaux — header stats)
# ══════════════════════════════════════════════════════════════════════════════

async def get_dashboard_stats(period: str = "month") -> dict:
    """
    Stats globales pour les 5 cartes du header de la Vue Signaux.

    period: 'week' | 'month' | 'all'
    Retourne : {
        trades_published, win_rate_admin, engagement_rate,
        journals_collected, open_trades_count,
        avg_member_capital, weekly_performance: [{day, count, win: bool}]
    }
    """
    date_from = {
        "week":  (datetime.now() - timedelta(days=7)).isoformat(),
        "month": (datetime.now() - timedelta(days=30)).isoformat(),
        "all":   "2000-01-01",
    }.get(period, (datetime.now() - timedelta(days=30)).isoformat())

    conn = get_conn()
    try:
        # Signaux publiés
        trades_pub = conn.execute("""
            SELECT COUNT(*) FROM signals WHERE published_at >= ?
        """, (date_from,)).fetchone()[0]

        # Win rate admin
        wr = conn.execute("""
            SELECT
                CASE WHEN COUNT(*) = 0 THEN NULL
                ELSE ROUND(
                    CAST(COUNT(CASE WHEN close_result = 'tp' THEN 1 END) AS REAL)
                    / COUNT(*) * 100, 1
                ) END AS win_rate
            FROM signals
            WHERE status = 'closed' AND closed_at >= ?
        """, (date_from,)).fetchone()
        win_rate_admin = wr["win_rate"] if wr else None

        # Taux engagement (membres qui répondent "in" / total signaux)
        eng = conn.execute("""
            SELECT
                COUNT(DISTINCT sp.signal_id) AS signals,
                COUNT(DISTINCT CASE WHEN sp.response = 'in' THEN sp.user_id END) AS users_in
            FROM signal_participations sp
            JOIN signals s ON s.id = sp.signal_id
            WHERE s.published_at >= ?
        """, (date_from,)).fetchone()
        engagement_rate = None
        if eng and eng["signals"] > 0 and eng["users_in"] > 0:
            # destinataires de la période
            dest = conn.execute("""
                SELECT COUNT(DISTINCT u.telegram_id) FROM users u
            """).fetchone()[0]
            if dest > 0:
                engagement_rate = round(eng["users_in"] / dest * 100, 1)

        # Formulaires collectés (réponses journal)
        journals = conn.execute("""
            SELECT COUNT(*) FROM trade_journal
            WHERE submitted_at >= ?
        """, (date_from,)).fetchone()[0]

        # Trades ouverts
        open_trades = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE status = 'open'"
        ).fetchone()[0]

        # Capital moyen membres
        avg_cap = conn.execute("""
            SELECT ROUND(AVG(last_cap), 2) FROM (
                SELECT user_id, MAX(capital) AS last_cap
                FROM member_capital
                GROUP BY user_id
            )
        """).fetchone()[0]

        # Performance hebdomadaire (7 derniers jours, 1 bar / jour)
        weekly = conn.execute("""
            SELECT
                DATE(closed_at) AS day,
                COUNT(*)        AS trades,
                COUNT(CASE WHEN close_result = 'tp' THEN 1 END) AS wins,
                COUNT(CASE WHEN close_result = 'sl' THEN 1 END) AS losses
            FROM signals
            WHERE status = 'closed'
              AND closed_at >= ?
            GROUP BY day
            ORDER BY day ASC
        """, ((datetime.now() - timedelta(days=7)).isoformat(),)).fetchall()
        weekly_perf = [dict(r) for r in weekly]

    finally:
        conn.close()

    return {
        "trades_published":    trades_pub,
        "win_rate_admin":      win_rate_admin,
        "engagement_rate":     engagement_rate,
        "journals_collected":  journals,
        "open_trades_count":   open_trades,
        "avg_member_capital":  avg_cap,
        "weekly_performance":  weekly_perf,
        "period":              period,
    }


async def declare_member_capital(user_id: int, payload: dict) -> dict:
    """
    Enregistre une déclaration de capital membre.
    Appelé par le form_engine après réception du formulaire "Capital membres".

    payload: {
        capital,
        type: 'gains'|'withdrawal'|'loss'|'initial',
        source?: 'form'|'manual'|'trade_result'
    }
    """
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO member_capital
                (user_id, capital, type, declared_at, source)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            float(payload["capital"]),
            payload.get("type", "gains"),
            _now(),
            payload.get("source", "form"),
        ))
        cap_id = cur.lastrowid
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM member_capital WHERE id = ?", (cap_id,)
        ).fetchone())
    finally:
        conn.close()
    return row


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════════════════

def _get_pip_value(symbol: str) -> float:
    """Retourne la valeur pip d'une paire depuis la base (fallback 10$)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT pip_value FROM trading_pairs WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
    finally:
        conn.close()
    return float(row["pip_value"]) if row else 10.0


def _get_pair_decimals(symbol: str) -> int:
    """Retourne le nombre de décimales d'une paire (pour calcul pips)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT decimals FROM trading_pairs WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
    finally:
        conn.close()
    return int(row["decimals"]) if row else 5


async def _get_signal_recipients(signal_id: int) -> list:
    """Retourne les user_ids destinataires d'un signal (via catégorie)."""
    conn = get_conn()
    try:
        signal = conn.execute("SELECT category FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not signal:
            return []
        rows = conn.execute("""
            SELECT DISTINCT id_user FROM categories WHERE name_categorie = ?
        """, (signal["category"],)).fetchall()
    finally:
        conn.close()
    return [r["id_user"] for r in rows]