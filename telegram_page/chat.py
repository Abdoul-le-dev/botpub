# chat.py — Chat Direct backend
# Compatible SQLite · Même pattern que categories.py
# Couvre : conversations, messages, agent IA, upload médias, profil, abonnements

import sqlite3
import csv
import io
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH    = "preinscriptions.db"
MEDIA_DIR  = Path("media")

# Instance du bot Telegram — injectée depuis api.py via set_bot()
_bot = None

def set_bot(bot_instance):
    """Appelé depuis api.py pour injecter l'instance bot au démarrage."""
    global _bot
    _bot = bot_instance

# Import _send_one depuis broadcast_engine pour réutiliser la logique d'envoi
from telegram_page.broadcast_engine import _send_one

PLANS = {
    "mensuel":     30,
    "trimestriel": 90,
    "semestriel":  180,
    "annuel":      270,
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now().isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ════════════════════════════════════════════════════════════════════════
# MIGRATIONS
# ════════════════════════════════════════════════════════════════════════

def init_chat_tables():
    """
    Crée les nouvelles tables et migre messages.
    Idempotent — sans risque si appelée plusieurs fois.
    À appeler dans le lifespan FastAPI au démarrage.
    """
    conn = get_conn()
    try:
        # ── Nouvelles colonnes sur messages ──────────────────────────────
        migrations = [
            "ALTER TABLE messages ADD COLUMN direction      TEXT    DEFAULT 'inbound'",
            "ALTER TABLE messages ADD COLUMN answered_by    TEXT    DEFAULT NULL",
            "ALTER TABLE messages ADD COLUMN replied_to_id  INTEGER DEFAULT NULL",
            "ALTER TABLE messages ADD COLUMN message_type   TEXT    DEFAULT 'text'",
            "ALTER TABLE messages ADD COLUMN ia_enabled     INTEGER DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN read_at        TEXT    DEFAULT NULL",
            "ALTER TABLE messages ADD COLUMN delivered_at   TEXT    DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # colonne déjà présente

        # ── Table conversations ──────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id               INTEGER  PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER  NOT NULL UNIQUE,
                ia_enabled       INTEGER  DEFAULT 1,
                is_blocked       INTEGER  DEFAULT 0,
                unread_count     INTEGER  DEFAULT 0,
                last_message_id  INTEGER  DEFAULT NULL,
                last_activity    TEXT     DEFAULT NULL,
                pinned           INTEGER  DEFAULT 0,
                archived         INTEGER  DEFAULT 0,
                note_admin       TEXT     DEFAULT NULL,
                created_at       TEXT     DEFAULT (datetime('now')),
                updated_at       TEXT     DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user     ON conversations(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_activity ON conversations(last_activity DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_unread   ON conversations(unread_count DESC)")

        # ── Table subscriptions ──────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id            INTEGER  PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER  NOT NULL,
                plan          TEXT     NOT NULL,
                duration_days INTEGER  NOT NULL,
                started_at    TEXT     NOT NULL,
                expires_at    TEXT     NOT NULL,
                status        TEXT     DEFAULT 'active',
                note          TEXT     DEFAULT NULL,
                created_at    TEXT     DEFAULT (datetime('now')),
                updated_at    TEXT     DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user    ON subscriptions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_status  ON subscriptions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_expires ON subscriptions(expires_at)")

        # ── Index messages ───────────────────────────────────────────────
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_user    ON messages(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_type    ON messages(message_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_bcast   ON messages(broadcast_id)")

        # ── Trigger : mise à jour automatique de conversations ───────────
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_upsert_conv
            AFTER INSERT ON messages
            BEGIN
                INSERT INTO conversations (user_id, last_message_id, last_activity, unread_count, updated_at)
                VALUES (NEW.user_id, NEW.id, NEW.created_at, 1, NEW.created_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_message_id = NEW.id,
                    last_activity   = NEW.created_at,
                    unread_count    = CASE
                                        WHEN NEW.direction = 'inbound' THEN unread_count + 1
                                        ELSE unread_count
                                      END,
                    updated_at      = NEW.created_at;
            END
        """)

        conn.commit()
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# HELPERS PRIVÉS
# ════════════════════════════════════════════════════════════════════════

def _upsert_conversation(conn, user_id: int, new_message_id: int = None, increment_unread: bool = False):
    now      = _now()
    existing = conn.execute(
        "SELECT id FROM conversations WHERE user_id = ?", (user_id,)
    ).fetchone()

    if not existing:
        conn.execute("""
            INSERT INTO conversations (user_id, last_message_id, last_activity, unread_count, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, new_message_id, now, 1 if increment_unread else 0, now))
    else:
        unread_sql = "unread_count + 1" if increment_unread else "unread_count"
        if new_message_id:
            conn.execute(f"""
                UPDATE conversations
                SET last_message_id = ?,
                    last_activity   = ?,
                    unread_count    = {unread_sql},
                    updated_at      = ?
                WHERE user_id = ?
            """, (new_message_id, now, now, user_id))
        else:
            conn.execute(f"""
                UPDATE conversations
                SET last_activity = ?,
                    unread_count  = {unread_sql},
                    updated_at    = ?
                WHERE user_id = ?
            """, (now, now, user_id))


def _get_latest_expiry(conn, user_id: int):
    row = conn.execute("""
        SELECT MAX(expires_at) AS max_expiry
        FROM subscriptions
        WHERE user_id = ? AND status = 'active'
    """, (user_id,)).fetchone()

    if row and row["max_expiry"]:
        return _parse_dt(row["max_expiry"])
    return None


def _compute_subscription_dates(conn, user_id: int, duration_days: int) -> tuple:
    latest = _get_latest_expiry(conn, user_id)
    base   = latest if (latest and latest > datetime.now()) else datetime.now()
    started_at = base.isoformat()
    expires_at = (base + timedelta(days=duration_days)).isoformat()
    return started_at, expires_at


def _enrich_subscription(row: dict) -> dict:
    now = datetime.now()
    try:
        expires            = _parse_dt(row["expires_at"])
        delta              = (expires - now).days
        row["days_remaining"] = max(delta, 0)
        row["is_active"]      = row["status"] == "active" and delta > 0
    except Exception:
        row["days_remaining"] = 0
        row["is_active"]      = False
    return row


# ════════════════════════════════════════════════════════════════════════
# ABONNEMENTS
# ════════════════════════════════════════════════════════════════════════

async def create_subscription(payload: dict) -> dict:
    user_id = payload["user_id"]
    plan    = payload["plan"]

    if plan not in PLANS:
        return {"error": f"Plan invalide. Valeurs acceptées : {', '.join(PLANS.keys())}"}

    duration_days = PLANS[plan]

    conn = get_conn()
    try:
        started_at, expires_at = _compute_subscription_dates(conn, user_id, duration_days)

        cur = conn.execute("""
            INSERT INTO subscriptions (user_id, plan, duration_days, started_at, expires_at, note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, plan, duration_days, started_at, expires_at, payload.get("note")))

        sub_id = cur.lastrowid
        conn.commit()

        row = dict(conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (sub_id,)
        ).fetchone())
    finally:
        conn.close()

    return _enrich_subscription(row)


async def get_subscriptions(user_id: int) -> list:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM subscriptions
            WHERE user_id = ?
            ORDER BY expires_at DESC
        """, (user_id,)).fetchall()
    finally:
        conn.close()

    return [_enrich_subscription(dict(r)) for r in rows]


async def get_subscription_summary(user_id: int) -> dict:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM subscriptions
            WHERE user_id = ? AND status = 'active'
            ORDER BY expires_at DESC
        """, (user_id,)).fetchall()
    finally:
        conn.close()

    active = [_enrich_subscription(dict(r)) for r in rows]

    if not active:
        return {
            "has_active":     False,
            "plans_active":   [],
            "max_expiry":     None,
            "days_remaining": 0,
            "total_active":   0,
        }

    return {
        "has_active":     True,
        "plans_active":   [r["plan"] for r in active],
        "max_expiry":     max(r["expires_at"] for r in active),
        "days_remaining": max(r["days_remaining"] for r in active),
        "total_active":   len(active),
        "subscriptions":  active,
    }


async def cancel_subscription(sub_id: int) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM subscriptions WHERE id = ?", (sub_id,)
        ).fetchone()

        if not row:
            return {"error": "Abonnement introuvable"}

        conn.execute("""
            UPDATE subscriptions
            SET status = 'cancelled', updated_at = ?
            WHERE id = ?
        """, (_now(), sub_id))
        conn.commit()
    finally:
        conn.close()

    return {"status": "cancelled", "id": sub_id}


async def expire_subscriptions() -> dict:
    conn = get_conn()
    try:
        cur = conn.execute("""
            UPDATE subscriptions
            SET status = 'expired', updated_at = ?
            WHERE status = 'active' AND expires_at < ?
        """, (_now(), _now()))
        count = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "expired_count": count}


async def get_subscriptions_stats() -> dict:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*)                                                        AS total,
                COUNT(CASE WHEN status = 'active'    THEN 1 END)               AS active,
                COUNT(CASE WHEN status = 'expired'   THEN 1 END)               AS expired,
                COUNT(CASE WHEN status = 'cancelled' THEN 1 END)               AS cancelled,
                COUNT(DISTINCT CASE WHEN status = 'active' THEN user_id END)   AS members_with_active,
                COUNT(CASE WHEN status = 'active' AND expires_at <= ?
                           THEN 1 END)                                          AS expiring_in_7_days
            FROM subscriptions
        """, ((datetime.now() + timedelta(days=7)).isoformat(),)).fetchone()
    finally:
        conn.close()

    return dict(row)


# ════════════════════════════════════════════════════════════════════════
# CONVERSATIONS — LISTE & ÉTAT
# ════════════════════════════════════════════════════════════════════════

async def get_conversations(filters: dict = None) -> dict:
    f      = filters or {}
    tab    = f.get("tab",    "all")
    search = f.get("search", "").strip()
    limit  = int(f.get("limit",  50))
    offset = int(f.get("offset",  0))

    conn = get_conn()
    try:
        where_clauses = ["1=1"]
        params        = []

        if tab == "unread":
            where_clauses.append("c.unread_count > 0")
        elif tab == "ia":
            where_clauses.append("c.ia_enabled = 1 AND c.is_blocked = 0")
        elif tab == "blocked":
            where_clauses.append("c.is_blocked = 1")

        if search:
            where_clauses.append("u.name LIKE ?")
            term    = f"%{search}%"
            params += [term]

        where_sql = " AND ".join(where_clauses)

        rows = conn.execute(f"""
            SELECT
                c.id,
                c.user_id,
                c.ia_enabled,
                c.is_blocked,
                c.unread_count,
                c.last_activity,
                c.pinned,
                c.note_admin,
                u.name,
                m.message_text  AS last_message,
                m.direction     AS last_direction,
                m.answered_by   AS last_answered_by,
                m.message_type  AS last_type,
                m.created_at    AS last_sent_at,
                GROUP_CONCAT(cat.name_categorie) AS categories_raw
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            LEFT JOIN messages m     ON m.id      = c.last_message_id
            LEFT JOIN categories cat ON cat.id_user = c.user_id
            WHERE {where_sql}
            GROUP BY c.id
            ORDER BY c.pinned DESC, c.last_activity DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()

        total = conn.execute(f"""
            SELECT COUNT(DISTINCT c.id)
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            WHERE {where_sql}
        """, params).fetchone()[0]

        conversations = []
        for r in rows:
            d   = dict(r)
            raw = d.pop("categories_raw", "") or ""
            d["categories"] = [c.strip() for c in raw.split(",") if c.strip()]
            conversations.append(d)

    finally:
        conn.close()

    return {
        "conversations": conversations,
        "total":         total,
        "limit":         limit,
        "offset":        offset,
    }


async def get_conversation(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                c.*,
                u.name,
                GROUP_CONCAT(cat.name_categorie) AS categories_raw
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            LEFT JOIN categories cat ON cat.id_user = c.user_id
            WHERE c.user_id = ?
            GROUP BY c.id
        """, (user_id,)).fetchone()

        if not row:
            return None

        d   = dict(row)
        raw = d.pop("categories_raw", "") or ""
        d["categories"] = [c.strip() for c in raw.split(",") if c.strip()]
    finally:
        conn.close()

    return d


async def get_conversation_stats() -> dict:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*)                                                       AS total_conversations,
                SUM(unread_count)                                              AS total_unread,
                COUNT(CASE WHEN ia_enabled = 1 AND is_blocked = 0 THEN 1 END) AS ia_active_count,
                COUNT(CASE WHEN is_blocked = 1 THEN 1 END)                    AS blocked_count,
                COUNT(CASE WHEN last_activity >= ? THEN 1 END)                AS active_today
            FROM conversations
        """, ((datetime.now() - timedelta(hours=24)).isoformat(),)).fetchone()
    finally:
        conn.close()

    return dict(row)


async def set_ia_enabled(user_id: int, enabled: bool) -> dict:
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE conversations
            SET ia_enabled = ?, updated_at = ?
            WHERE user_id = ?
        """, (1 if enabled else 0, _now(), user_id))
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "ia_enabled": enabled}


async def set_conversation_blocked(user_id: int, blocked: bool) -> dict:
    conn = get_conn()
    try:
        _upsert_conversation(conn, user_id)
        conn.execute("""
            UPDATE conversations
            SET is_blocked = ?, updated_at = ?
            WHERE user_id = ?
        """, (1 if blocked else 0, _now(), user_id))
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "is_blocked": blocked}


async def mark_as_read(user_id: int) -> dict:
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE conversations
            SET unread_count = 0, updated_at = ?
            WHERE user_id = ?
        """, (_now(), user_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "user_id": user_id}


async def pin_conversation(user_id: int, pinned: bool) -> dict:
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE conversations
            SET pinned = ?, updated_at = ?
            WHERE user_id = ?
        """, (1 if pinned else 0, _now(), user_id))
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "pinned": pinned}


async def set_admin_note(user_id: int, note: str) -> dict:
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE conversations
            SET note_admin = ?, updated_at = ?
            WHERE user_id = ?
        """, (note, _now(), user_id))
        conn.commit()
    finally:
        conn.close()
    return {"user_id": user_id, "note_admin": note}


async def search_conversations(query: str) -> list:
    conn = get_conn()
    term = f"%{query}%"
    try:
        rows = conn.execute("""
            SELECT DISTINCT
                c.user_id,
                u.name,
                c.last_activity,
                c.unread_count,
                c.ia_enabled,
                c.is_blocked
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            LEFT JOIN messages m ON m.user_id = c.user_id
            WHERE u.name LIKE ?
               OR m.message_text LIKE ?
            ORDER BY c.last_activity DESC
            LIMIT 20
        """, (term, term)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════
# MESSAGES
# ════════════════════════════════════════════════════════════════════════

async def get_messages(user_id: int, options: dict = None) -> dict:
    o         = options or {}
    limit     = int(o.get("limit", 50))
    before_id = o.get("before_id")
    after_id  = o.get("after_id")

    conn   = get_conn()
    params = [user_id]
    extra  = ""

    if before_id:
        extra += " AND m.id < ?"
        params.append(before_id)
    if after_id:
        extra += " AND m.id > ?"
        params.append(after_id)

    try:
        rows = conn.execute(f"""
            SELECT
                m.id,
                m.user_id,
                m.message_id      AS telegram_message_id,
                m.message_text,
                m.direction,
                m.answered_by,
                m.message_type,
                m.media_url,
                m.status,
                m.broadcast_id,
                m.replied_to_id,
                m.ia_enabled,
                m.read_at,
                m.delivered_at,
                m.created_at,
                r.message_text    AS replied_to_text,
                r.direction       AS replied_to_direction,
                bh.tag            AS broadcast_tag
            FROM messages m
            LEFT JOIN messages r          ON r.id  = m.replied_to_id
            LEFT JOIN broadcast_history bh ON bh.id = m.broadcast_id
            WHERE m.user_id = ? {extra}
            ORDER BY m.id DESC
            LIMIT ?
        """, params + [limit]).fetchall()

        messages = list(reversed([dict(r) for r in rows]))
    finally:
        conn.close()

    return {"messages": messages, "count": len(messages)}


# ── Mapping mime_type → type simplifié (pour déduire depuis l'URL si besoin) ──
_EXT_TO_TYPE = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image", ".webp": "image",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video", ".webm": "video",
    ".pdf": "pdf",
    ".doc": "word",  ".docx": "word",
    ".xls": "excel", ".xlsx": "excel",
    ".ppt": "powerpoint", ".pptx": "powerpoint",
    ".txt": "text",
    ".zip": "archive", ".rar": "archive",
}


def _type_from_path(path: str) -> str:
    """Déduit le type simplifié depuis l'extension du fichier."""
    ext = Path(path).suffix.lower()
    return _EXT_TO_TYPE.get(ext, "document")


async def send_message(payload: dict) -> dict:
    """
    Enregistre un message sortant admin et l'envoie via le bot Python.
    payload: { user_id, message_text?, message_type, media_url?, replied_to_id? }

    Retourne toujours un dict avec :
      - le message enregistré en base si l'envoi a réussi
      - { "error": "...", "send_failed": True } si l'envoi Telegram a échoué
        (le message N'EST PAS enregistré en base en cas d'échec)
    """
    user_id       = payload["user_id"]
    message_text  = payload.get("message_text", "") or ""
    media_url     = payload.get("media_url")
    replied_to_id = payload.get("replied_to_id")

    # ── Déduire le message_type réel depuis l'extension si un fichier est joint ──
    # Le frontend peut envoyer "text" par défaut même quand il y a un fichier.
    # if media_url:

    #     message_type = payload.get("message_type")
    # else:
    #     message_type = "text"

    if not _bot:
        return {"error": "Bot non initialisé", "send_failed": True}

    # ── Construire les paramètres d'envoi Telegram ──────────────────────
    fmt      = "text"
    tg_media = None
    message_type = payload.get("message_type")

    print(media_url)

    if media_url:
        file_path = Path(media_url.lstrip("/"))

        if not file_path.exists():
            return {
                "error": f"Fichier introuvable sur le serveur : {media_url}",
                "send_failed": True,
            }

        tg_media = open(file_path, "rb")   # fermé dans le finally ci-dessous

        if message_type == "image":
            fmt = "image+text" if message_text else "image"
        elif message_type == "video":
            fmt = "video+text" if message_text else "video"
        else:
            # pdf, word, excel, powerpoint, archive, text → send_document
            # Telegram exige un caption non-vide OU pas de caption du tout.
            # On passe le texte tel quel ; s'il est vide on ne le passe pas.
            fmt = "document"

    # ── Envoi Telegram ──────────────────────────────────────────────────
    try:
        await _send_one(
            bot       = _bot,
            user_id   = user_id,
            fmt       = fmt,
            text      = message_text,          # chaîne vide acceptée pour les documents
            media_url = tg_media,              # file object ou None
        )
    except Exception as e:
        error_msg = str(e)
        print(f"⚠️ Échec envoi Telegram uid={user_id} : {error_msg}")
        return {
            "error":       f"Échec envoi Telegram : {error_msg}",
            "send_failed": True,
        }
    finally:
        if tg_media:
            tg_media.close()

    # ── Enregistrement en base UNIQUEMENT si l'envoi a réussi ───────────
    conn = get_conn()
    try:
        conv        = conn.execute(
            "SELECT ia_enabled FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        ia_snapshot = conv["ia_enabled"] if conv else 0

        cur = conn.execute("""
            INSERT INTO messages
                (user_id, message_text, direction, answered_by, message_type,
                 media_url, replied_to_id, ia_enabled, status, created_at)
            VALUES (?, ?, 'outbound', 'admin', ?, ?, ?, ?, 'sent', ?)
        """, (user_id, message_text, message_type,
              media_url, replied_to_id, ia_snapshot, _now()))

        message_id = cur.lastrowid
        _upsert_conversation(conn, user_id, new_message_id=message_id, increment_unread=False)
        conn.commit()

        message = dict(conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone())
    finally:
        conn.close()

    return message


async def receive_message(payload: dict) -> dict:
    user_id      = payload["user_id"]
    message_text = payload.get("message_text", "")
    message_type = payload.get("message_type", "text")
    media_url    = payload.get("media_url")
    tg_msg_id    = payload.get("message_id")

    conn = get_conn()
    try:
        conv        = conn.execute(
            "SELECT ia_enabled, is_blocked FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
        ia_snapshot = conv["ia_enabled"] if conv else 1
        is_blocked  = conv["is_blocked"] if conv else 0

        cur = conn.execute("""
            INSERT INTO messages
                (user_id, message_id, message_text, direction, message_type,
                 media_url, ia_enabled, status, created_at)
            VALUES (?, ?, ?, 'inbound', ?, ?, ?, 'received', ?)
        """, (user_id, tg_msg_id, message_text, message_type,
              media_url, ia_snapshot, _now()))

        message_id = cur.lastrowid
        _upsert_conversation(conn, user_id, new_message_id=message_id, increment_unread=True)
        conn.commit()

        message = dict(conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone())
    finally:
        conn.close()

    if ia_snapshot and not is_blocked:
        await trigger_ia_response(user_id, message_id)

    return message


async def receive_ia_message(payload: dict) -> dict:
    user_id      = payload["user_id"]
    message_text = payload.get("message_text", "")
    message_type = payload.get("message_type", "text")

    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO messages
                (user_id, message_text, direction, answered_by, message_type,
                 ia_enabled, status, created_at)
            VALUES (?, ?, 'outbound', 'ia', ?, 1, 'sent', ?)
        """, (user_id, message_text, message_type, _now()))

        message_id = cur.lastrowid
        _upsert_conversation(conn, user_id, new_message_id=message_id, increment_unread=False)
        conn.commit()

        message = dict(conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone())
    finally:
        conn.close()

    return message


async def update_message_status(message_id: int, status: str, timestamp: str = None) -> dict:
    ts   = timestamp or _now()
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE messages
            SET status       = ?,
                delivered_at = CASE WHEN ? = 'delivered' THEN ? ELSE delivered_at END,
                read_at      = CASE WHEN ? = 'read'      THEN ? ELSE read_at      END
            WHERE id = ?
        """, (status, status, ts, status, ts, message_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "message_id": message_id, "new_status": status}


async def delete_message(message_id: int, user_id: int) -> dict:
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE messages
            SET status = 'deleted', message_text = NULL
            WHERE id = ? AND user_id = ?
        """, (message_id, user_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "deleted", "message_id": message_id}


async def get_conversation_timeline(user_id: int) -> list:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                m.id,
                m.message_text,
                m.direction,
                m.answered_by,
                m.message_type,
                m.media_url,
                m.replied_to_id,
                m.broadcast_id,
                m.status,
                m.read_at,
                m.delivered_at,
                m.created_at,
                DATE(m.created_at) AS date_group,
                bh.tag             AS broadcast_tag,
                bh.total           AS broadcast_total,
                bh.sent            AS broadcast_sent
            FROM messages m
            LEFT JOIN broadcast_history bh ON bh.id = m.broadcast_id
            WHERE m.user_id = ? AND m.status != 'deleted'
            ORDER BY m.created_at ASC
        """, (user_id,)).fetchall()
    finally:
        conn.close()

    groups = {}
    for r in rows:
        d    = dict(r)
        date = d.pop("date_group")
        groups.setdefault(date, []).append(d)

    return [{"date": date, "messages": msgs} for date, msgs in groups.items()]


# ════════════════════════════════════════════════════════════════════════
# AGENT IA
# ════════════════════════════════════════════════════════════════════════

async def trigger_ia_response(user_id: int, incoming_message_id: int) -> None:
    conn = get_conn()
    try:
        context_rows = conn.execute("""
            SELECT message_text, direction, answered_by, created_at
            FROM messages
            WHERE user_id = ? AND status != 'deleted'
            ORDER BY id DESC LIMIT 10
        """, (user_id,)).fetchall()
        context = list(reversed([dict(r) for r in context_rows]))
    finally:
        conn.close()

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{BOT_URL}/ia/respond",
                json={
                    "user_id":             user_id,
                    "incoming_message_id": incoming_message_id,
                    "context":             context,
                },
                headers={"X-Bot-Secret": BOT_SECRET}
            )
    except Exception:
        pass


async def get_ia_stats(user_id: int) -> dict:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*)                                        AS total_ia_messages,
                COUNT(CASE WHEN read_at IS NOT NULL THEN 1 END) AS read_count,
                MIN(created_at)                                 AS first_ia_message,
                MAX(created_at)                                 AS last_ia_message
            FROM messages
            WHERE user_id    = ?
              AND answered_by = 'ia'
              AND direction   = 'outbound'
        """, (user_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


# ════════════════════════════════════════════════════════════════════════
# UPLOAD MÉDIAS
# ════════════════════════════════════════════════════════════════════════

ALLOWED_MEDIA = {
    "image/jpeg":                                                    ("image",    ".jpg",  10),
    "image/png":                                                     ("image",    ".png",  10),
    "image/gif":                                                     ("image",    ".gif",  10),
    "image/webp":                                                    ("image",    ".webp", 10),
    "video/mp4":                                                     ("video",    ".mp4",  50),
    "video/quicktime":                                               ("video",    ".mov",  50),
    "video/x-msvideo":                                               ("video",    ".avi",  50),
    "video/x-matroska":                                              ("video",    ".mkv",  50),
    "video/webm":                                                    ("video",    ".webm", 50),
    "application/pdf":                                               ("pdf",      ".pdf",  20),
    "application/msword":                                            ("word",     ".doc",  20),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                                                                     ("word",     ".docx", 20),
    "application/vnd.ms-excel":                                      ("excel",    ".xls",  20),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                                                                     ("excel",    ".xlsx", 20),
    "application/vnd.ms-powerpoint":                                 ("powerpoint", ".ppt", 20),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":
                                                                     ("powerpoint", ".pptx", 20),
    "text/plain":                                                    ("text",     ".txt",   5),
    "application/zip":                                               ("archive",  ".zip",  50),
    "application/x-rar-compressed":                                  ("archive",  ".rar",  50),
}


def _make_image_thumbnail(src: Path, stem: str, ext: str) -> str | None:
    try:
        from PIL import Image
        thumb_name = f"{stem}_thumb{ext}"
        thumb_path = MEDIA_DIR / thumb_name
        img = Image.open(src)
        img.thumbnail((300, 300))
        img.save(thumb_path)
        return f"/media/{thumb_name}"
    except Exception:
        return None


def _make_video_thumbnail(src: Path, stem: str) -> str | None:
    try:
        import subprocess
        thumb_name = f"{stem}_thumb.jpg"
        thumb_path = MEDIA_DIR / thumb_name
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(src),
                "-ss", "00:00:01",
                "-vframes", "1",
                "-vf", "scale=300:-1",
                str(thumb_path),
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0 and thumb_path.exists():
            return f"/media/{thumb_name}"
    except Exception:
        pass
    return None


async def upload_media(file_bytes: bytes, filename: str, mime_type: str, user_id: int) -> dict:
    if mime_type not in ALLOWED_MEDIA:
        return {"error": f"Type de fichier non autorisé ({mime_type})"}

    ftype, ext, max_mb = ALLOWED_MEDIA[mime_type]
    max_bytes          = max_mb * 1024 * 1024

    if len(file_bytes) > max_bytes:
        return {"error": f"Fichier trop volumineux (max {max_mb} MB pour ce type)"}

    original_ext = Path(filename).suffix.lower()
    final_ext    = original_ext if original_ext else ext

    stem  = str(uuid.uuid4())
    fname = f"{stem}{final_ext}"

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    dest  = MEDIA_DIR / fname
    dest.write_bytes(file_bytes)

    file_url  = f"/media/{fname}"
    thumb_url = None

    if ftype == "image":
        thumb_url = _make_image_thumbnail(dest, stem, final_ext)
    elif ftype == "video":
        thumb_url = _make_video_thumbnail(dest, stem)

    return {
        "filename":   fname,
        "url":        file_url,
        "thumbnail":  thumb_url,
        "mime_type":  mime_type,
        "type":       ftype,
        "size_bytes": len(file_bytes),
        "size_mb":    round(len(file_bytes) / 1024 / 1024, 2),
    }


# ════════════════════════════════════════════════════════════════════════
# PROFIL MEMBRE
# ════════════════════════════════════════════════════════════════════════

async def get_chat_profile(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                u.telegram_id,
                u.name,
                u.created_at                AS registered_at,
                c.ia_enabled,
                c.is_blocked,
                c.unread_count,
                c.note_admin,
                c.last_activity,
                COUNT(DISTINCT m.id)        AS total_messages,
                SUM(CASE WHEN m.direction = 'inbound'  THEN 1 ELSE 0 END) AS messages_received,
                SUM(CASE WHEN m.direction = 'outbound' THEN 1 ELSE 0 END) AS messages_sent,
                GROUP_CONCAT(DISTINCT cat.name_categorie) AS categories_raw
            FROM users u
            LEFT JOIN conversations c   ON c.user_id   = u.telegram_id
            LEFT JOIN messages m        ON m.user_id   = u.telegram_id AND m.status != 'deleted'
            LEFT JOIN categories cat    ON cat.id_user  = u.telegram_id
            WHERE u.telegram_id = ?
            GROUP BY u.telegram_id
        """, (user_id,)).fetchone()

        if not row:
            return None

        profile     = dict(row)
        raw         = profile.pop("categories_raw", "") or ""
        profile["categories"] = [c.strip() for c in raw.split(",") if c.strip()]

        try:
            ts = conn.execute("""
                SELECT
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN result_percent > 0 THEN 1 ELSE 0 END) AS wins,
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(
                        CAST(SUM(CASE WHEN result_percent > 0 THEN 1 ELSE 0 END) AS REAL)
                        / COUNT(*) * 100, 1
                    ) END AS win_rate,
                    ROUND(AVG(result_percent), 2) AS avg_result_percent
                FROM trade_journal
                WHERE user_id = ? AND status = 'closed'
            """, (user_id,)).fetchone()
            profile["trading"] = dict(ts) if ts else None
        except Exception:
            profile["trading"] = None

    finally:
        conn.close()

    profile["subscription"]       = await get_subscription_summary(user_id)
    profile["broadcasts_received"] = await get_received_broadcasts(user_id, limit=5)

    return profile


async def get_received_broadcasts(user_id: int, limit: int = 5) -> list:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                bh.id,
                bh.tag,
                bh.message,
                bh.format,
                bh.started_at,
                m.status,
                m.read_at
            FROM broadcast_history bh
            JOIN messages m ON m.broadcast_id = bh.id AND m.user_id = ?
            ORDER BY bh.started_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════
# EXPORTS
# ════════════════════════════════════════════════════════════════════════

async def export_conversation(user_id: int, fmt: str = "json") -> dict:
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT m.id, m.created_at, m.direction, m.answered_by,
                   m.message_type, m.message_text, m.status
            FROM messages m
            WHERE m.user_id = ? AND m.status != 'deleted'
            ORDER BY m.created_at ASC
        """, (user_id,)).fetchall()
        messages = [dict(r) for r in rows]
    finally:
        conn.close()

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=messages[0].keys() if messages else [])
        writer.writeheader()
        writer.writerows(messages)
        return {"content": output.getvalue(), "content_type": "text/csv",
                "filename": f"conv_{user_id}.csv"}

    elif fmt == "txt":
        lines = []
        for msg in messages:
            who  = msg["answered_by"] or msg["direction"]
            time = msg["created_at"][:16]
            lines.append(f"[{time}] {who.upper()}: {msg['message_text'] or ''}")
        return {"content": "\n".join(lines), "content_type": "text/plain",
                "filename": f"conv_{user_id}.txt"}

    else:
        import json
        return {"content": json.dumps(messages, ensure_ascii=False, indent=2),
                "content_type": "application/json",
                "filename": f"conv_{user_id}.json"}