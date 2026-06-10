# chat.py — v5 MySQL async

import csv
import io
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from db import get_db

MEDIA_DIR = Path(__file__).parent.parent / "media"

_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

from telegram_page.broadcast_engine import _send_one

PLANS = {
    "mensuel":     30,
    "trimestriel": 90,
    "semestriel":  180,
    "annuel":      270,
}


def _now() -> str:
    return datetime.now().isoformat()

def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ════════════════════════════════════════════════════════════════════════
# HELPERS PRIVÉS
# ════════════════════════════════════════════════════════════════════════

async def _upsert_conversation(cur, user_id: int, new_message_id: int = None, increment_unread: bool = False):
    now = _now()
    await cur.execute("SELECT id FROM conversations WHERE user_id = %s", (user_id,))
    existing = await cur.fetchone()

    if not existing:
        await cur.execute("""
            INSERT INTO conversations (user_id, last_message_id, last_activity, unread_count, updated_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, new_message_id, now, 1 if increment_unread else 0, now))
    else:
        unread_expr = "unread_count + 1" if increment_unread else "unread_count"
        if new_message_id:
            await cur.execute(f"""
                UPDATE conversations
                SET last_message_id = %s,
                    last_activity   = %s,
                    unread_count    = {unread_expr},
                    updated_at      = %s
                WHERE user_id = %s
            """, (new_message_id, now, now, user_id))
        else:
            await cur.execute(f"""
                UPDATE conversations
                SET last_activity = %s,
                    unread_count  = {unread_expr},
                    updated_at    = %s
                WHERE user_id = %s
            """, (now, now, user_id))


async def _get_latest_expiry(cur, user_id: int):
    await cur.execute("""
        SELECT MAX(expires_at) AS max_expiry
        FROM subscriptions
        WHERE user_id = %s AND status = 'active'
    """, (user_id,))
    row = await cur.fetchone()
    if row and row["max_expiry"]:
        return _parse_dt(str(row["max_expiry"]))
    return None


async def _compute_subscription_dates(cur, user_id: int, duration_days: int) -> tuple:
    latest     = await _get_latest_expiry(cur, user_id)
    base       = latest if (latest and latest > datetime.now()) else datetime.now()
    started_at = base.isoformat()
    expires_at = (base + timedelta(days=duration_days)).isoformat()
    return started_at, expires_at


def _enrich_subscription(row: dict) -> dict:
    now = datetime.now()
    try:
        expires               = _parse_dt(str(row["expires_at"]))
        delta                 = (expires - now).days
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

    async with get_db() as cur:
        started_at, expires_at = await _compute_subscription_dates(cur, user_id, duration_days)
        await cur.execute("""
            INSERT INTO subscriptions (user_id, plan, duration_days, started_at, expires_at, note)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, plan, duration_days, started_at, expires_at, payload.get("note")))
        sub_id = cur.lastrowid

        await cur.execute("SELECT * FROM subscriptions WHERE id = %s", (sub_id,))
        row = dict(await cur.fetchone())

    return _enrich_subscription(row)


async def get_subscriptions(user_id: int) -> list:
    async with get_db() as cur:
        await cur.execute("""
            SELECT * FROM subscriptions WHERE user_id = %s ORDER BY expires_at DESC
        """, (user_id,))
        rows = await cur.fetchall()
    return [_enrich_subscription(dict(r)) for r in rows]


async def get_subscription_summary(user_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            SELECT * FROM subscriptions
            WHERE user_id = %s AND status = 'active'
            ORDER BY expires_at DESC
        """, (user_id,))
        rows = await cur.fetchall()

    active = [_enrich_subscription(dict(r)) for r in rows]
    if not active:
        return {"has_active": False, "plans_active": [], "max_expiry": None,
                "days_remaining": 0, "total_active": 0}

    return {
        "has_active":     True,
        "plans_active":   [r["plan"] for r in active],
        "max_expiry":     max(str(r["expires_at"]) for r in active),
        "days_remaining": max(r["days_remaining"] for r in active),
        "total_active":   len(active),
        "subscriptions":  active,
    }


async def cancel_subscription(sub_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("SELECT id FROM subscriptions WHERE id = %s", (sub_id,))
        if not await cur.fetchone():
            return {"error": "Abonnement introuvable"}
        await cur.execute("""
            UPDATE subscriptions SET status = 'cancelled', updated_at = NOW() WHERE id = %s
        """, (sub_id,))
    return {"status": "cancelled", "id": sub_id}


async def expire_subscriptions() -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE subscriptions SET status = 'expired', updated_at = NOW()
            WHERE status = 'active' AND expires_at < NOW()
        """)
        count = cur.rowcount
    return {"status": "ok", "expired_count": count}


async def get_subscriptions_stats() -> dict:
    async with get_db() as cur:
        await cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN status = 'active'    THEN 1 END) AS active,
                COUNT(CASE WHEN status = 'expired'   THEN 1 END) AS expired,
                COUNT(CASE WHEN status = 'cancelled' THEN 1 END) AS cancelled,
                COUNT(DISTINCT CASE WHEN status = 'active' THEN user_id END) AS members_with_active,
                COUNT(CASE WHEN status = 'active'
                           AND expires_at <= %s THEN 1 END) AS expiring_in_7_days
            FROM subscriptions
        """, ((datetime.now() + timedelta(days=7)).isoformat(),))
        row = await cur.fetchone()
    return dict(row)


# ════════════════════════════════════════════════════════════════════════
# CONVERSATIONS
# ════════════════════════════════════════════════════════════════════════

async def get_conversations(filters: dict = None) -> dict:
    f      = filters or {}
    tab    = f.get("tab",    "all")
    search = f.get("search", "").strip()
    limit  = int(f.get("limit",  50))
    offset = int(f.get("offset",  0))

    where_clauses = ["1=1"]
    params        = []

    if tab == "unread":
        where_clauses.append("c.unread_count > 0")
    elif tab == "ia":
        where_clauses.append("c.ia_enabled = 1 AND c.is_blocked = 0")
    elif tab == "blocked":
        where_clauses.append("c.is_blocked = 1")
    elif tab == "requires_admin":
        where_clauses.append("""
            c.user_id IN (
                SELECT DISTINCT user_id FROM messages WHERE requires_admin = 1
            )
        """)

    if search:
        where_clauses.append("u.name LIKE %s")
        params.append(f"%{search}%")

    where_sql = " AND ".join(where_clauses)

    async with get_db() as cur:
        await cur.execute(f"""
            SELECT
                c.id, c.user_id, c.ia_enabled, c.is_blocked, c.unread_count,
                c.last_activity, c.pinned, c.note_admin,
                u.name,
                m.message_text  AS last_message,
                m.direction     AS last_direction,
                m.answered_by   AS last_answered_by,
                m.message_type  AS last_type,
                m.created_at    AS last_sent_at,
                GROUP_CONCAT(cat.name_categorie) AS categories_raw
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            LEFT JOIN messages m     ON m.id       = c.last_message_id
            LEFT JOIN categories cat ON cat.id_user = c.user_id
            WHERE {where_sql}
            GROUP BY c.id
            ORDER BY c.pinned DESC, c.last_activity DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = await cur.fetchall()

        await cur.execute(f"""
            SELECT COUNT(DISTINCT c.id) as n
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            WHERE {where_sql}
        """, params)
        total = (await cur.fetchone())["n"]

    conversations = []
    for r in rows:
        d   = dict(r)
        raw = d.pop("categories_raw", "") or ""
        d["categories"] = [c.strip() for c in raw.split(",") if c.strip()]
        conversations.append(d)

    return {"conversations": conversations, "total": total, "limit": limit, "offset": offset}


async def get_conversation(user_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT c.*, u.name,
                GROUP_CONCAT(cat.name_categorie) AS categories_raw
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            LEFT JOIN categories cat ON cat.id_user = c.user_id
            WHERE c.user_id = %s
            GROUP BY c.id
        """, (user_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d   = dict(row)
        raw = d.pop("categories_raw", "") or ""
        d["categories"] = [c.strip() for c in raw.split(",") if c.strip()]
    return d


async def get_conversation_stats() -> dict:
    async with get_db() as cur:
        await cur.execute("""
            SELECT
                COUNT(*) AS total_conversations,
                SUM(unread_count) AS total_unread,
                COUNT(CASE WHEN ia_enabled = 1 AND is_blocked = 0 THEN 1 END) AS ia_active_count,
                COUNT(CASE WHEN is_blocked = 1 THEN 1 END) AS blocked_count,
                COUNT(CASE WHEN last_activity >= %s THEN 1 END) AS active_today,
                COUNT(CASE WHEN user_id IN (
                    SELECT DISTINCT user_id FROM messages WHERE requires_admin = 1
                ) THEN 1 END) AS requires_admin_count
            FROM conversations
        """, ((datetime.now() - timedelta(hours=24)).isoformat(),))
        row = await cur.fetchone()
    return dict(row)


async def set_ia_enabled(user_id: int, enabled: bool) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE conversations SET ia_enabled = %s, updated_at = NOW() WHERE user_id = %s
        """, (1 if enabled else 0, user_id))
    return {"user_id": user_id, "ia_enabled": enabled}


async def set_conversation_blocked(user_id: int, blocked: bool) -> dict:
    async with get_db() as cur:
        await _upsert_conversation(cur, user_id)
        await cur.execute("""
            UPDATE conversations SET is_blocked = %s, updated_at = NOW() WHERE user_id = %s
        """, (1 if blocked else 0, user_id))
    return {"user_id": user_id, "is_blocked": blocked}


async def mark_as_read(user_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE conversations SET unread_count = 0, updated_at = NOW() WHERE user_id = %s
        """, (user_id,))
    return {"status": "ok", "user_id": user_id}


async def pin_conversation(user_id: int, pinned: bool) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE conversations SET pinned = %s, updated_at = NOW() WHERE user_id = %s
        """, (1 if pinned else 0, user_id))
    return {"user_id": user_id, "pinned": pinned}


async def set_admin_note(user_id: int, note: str) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE conversations SET note_admin = %s, updated_at = NOW() WHERE user_id = %s
        """, (note, user_id))
    return {"user_id": user_id, "note_admin": note}


async def search_conversations(query: str) -> list:
    term = f"%{query}%"
    async with get_db() as cur:
        await cur.execute("""
            SELECT DISTINCT c.user_id, u.name, c.last_activity,
                c.unread_count, c.ia_enabled, c.is_blocked
            FROM conversations c
            JOIN users u ON u.telegram_id = c.user_id
            LEFT JOIN messages m ON m.user_id = c.user_id
            WHERE u.name LIKE %s OR m.message_text LIKE %s
            ORDER BY c.last_activity DESC
            LIMIT 20
        """, (term, term))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════
# MESSAGES
# ════════════════════════════════════════════════════════════════════════

async def get_messages(user_id: int, options: dict = None) -> dict:
    o         = options or {}
    limit     = int(o.get("limit", 50))
    before_id = o.get("before_id")
    after_id  = o.get("after_id")

    params = [user_id]
    extra  = ""
    if before_id:
        extra += " AND m.id < %s"
        params.append(before_id)
    if after_id:
        extra += " AND m.id > %s"
        params.append(after_id)

    async with get_db() as cur:
        await cur.execute(f"""
            SELECT
                m.id, m.user_id,
                m.message_id      AS telegram_message_id,
                m.message_text, m.direction, m.answered_by,
                m.message_type, m.media_url, m.status,
                m.broadcast_id, m.replied_to_id, m.ia_enabled,
                m.read_at, m.delivered_at, m.created_at,
                r.message_text    AS replied_to_text,
                r.direction       AS replied_to_direction,
                bh.tag            AS broadcast_tag
            FROM messages m
            LEFT JOIN messages r           ON r.id  = m.replied_to_id
            LEFT JOIN broadcast_history bh ON bh.id = m.broadcast_id
            WHERE m.user_id = %s {extra}
            ORDER BY m.id DESC
            LIMIT %s
        """, params + [limit])
        rows = await cur.fetchall()

    messages = list(reversed([dict(r) for r in rows]))
    return {"messages": messages, "count": len(messages)}


_EXT_TO_TYPE = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image", ".webp": "image",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video", ".webm": "video",
    ".pdf": "pdf", ".doc": "word", ".docx": "word",
    ".xls": "excel", ".xlsx": "excel",
    ".ppt": "powerpoint", ".pptx": "powerpoint",
    ".txt": "text", ".zip": "archive", ".rar": "archive",
}


async def send_message(payload: dict) -> dict:
    user_id       = payload["user_id"]
    message_text  = payload.get("message_text", "") or ""
    media_url     = payload.get("media_url")
    replied_to_id = payload.get("replied_to_id")

    if not _bot:
        return {"error": "Bot non initialisé", "send_failed": True}

    fmt          = "text"
    tg_media     = None
    message_type = payload.get("message_type")

    if media_url:
        file_path = Path(media_url.lstrip("/"))
        if not file_path.exists():
            return {"error": f"Fichier introuvable sur le serveur : {media_url}", "send_failed": True}
        tg_media = open(file_path, "rb")
        if message_type == "image":
            fmt = "image+text" if message_text else "image"
        elif message_type == "video":
            fmt = "video+text" if message_text else "video"
        else:
            fmt = "document"

    try:
        if fmt == "text":
            await _bot.send_message(chat_id=user_id, text=message_text)
        elif fmt == "image+text":
            await _bot.send_message(chat_id=user_id, text=message_text)
            await _bot.send_photo(chat_id=user_id, photo=media_url.lstrip("/"))
        elif fmt == "image":
            await _bot.send_photo(chat_id=user_id, photo=media_url.lstrip("/"))
        elif fmt == "video":
            await _bot.send_video(chat_id=user_id, video=media_url.lstrip("/"))
        elif fmt == "video+text":
            if message_text:
                await _bot.send_message(chat_id=user_id, text=message_text)
            await _bot.send_video(chat_id=user_id, video=media_url.lstrip("/"))
        elif fmt == "document":
            if message_text:
                await _bot.send_message(chat_id=user_id, text=message_text)
            await _bot.send_document(chat_id=user_id, document=media_url.lstrip("/"))
    except Exception as e:
        return {"error": f"Échec envoi Telegram : {e}", "send_failed": True}
    finally:
        if tg_media:
            tg_media.close()

    async with get_db() as cur:
        await cur.execute(
            "SELECT ia_enabled FROM conversations WHERE user_id = %s", (user_id,)
        )
        conv        = await cur.fetchone()
        ia_snapshot = conv["ia_enabled"] if conv else 0

        await cur.execute("""
            INSERT INTO messages
                (user_id, message_text, direction, answered_by, message_type,
                 media_url, replied_to_id, ia_enabled, status, created_at)
            VALUES (%s, %s, 'outbound', 'admin', %s, %s, %s, %s, 'sent', NOW())
        """, (user_id, message_text, message_type, media_url, replied_to_id, ia_snapshot))

        message_id = cur.lastrowid
        await _upsert_conversation(cur, user_id, new_message_id=message_id, increment_unread=False)

        await cur.execute("SELECT * FROM messages WHERE id = %s", (message_id,))
        message = dict(await cur.fetchone())

    return message


async def receive_message(payload: dict) -> dict:
    user_id      = payload["user_id"]
    message_text = payload.get("message_text", "")
    message_type = payload.get("message_type", "text")
    media_url    = payload.get("media_url")
    tg_msg_id    = payload.get("message_id")

    async with get_db() as cur:
        await cur.execute(
            "SELECT ia_enabled, is_blocked FROM conversations WHERE user_id = %s", (user_id,)
        )
        conv        = await cur.fetchone()
        ia_snapshot = conv["ia_enabled"] if conv else 1
        is_blocked  = conv["is_blocked"] if conv else 0

        await cur.execute("""
            INSERT INTO messages
                (user_id, message_id, message_text, direction, message_type,
                 media_url, ia_enabled, status, created_at)
            VALUES (%s, %s, %s, 'inbound', %s, %s, %s, 'received', NOW())
        """, (user_id, tg_msg_id, message_text, message_type, media_url, ia_snapshot))

        message_id = cur.lastrowid
        await _upsert_conversation(cur, user_id, new_message_id=message_id, increment_unread=True)

        await cur.execute("SELECT * FROM messages WHERE id = %s", (message_id,))
        message = dict(await cur.fetchone())

    if ia_snapshot and not is_blocked:
        await trigger_ia_response(user_id, message_id)

    return message


async def receive_ia_message(payload: dict) -> dict:
    user_id      = payload["user_id"]
    message_text = payload.get("message_text", "")
    message_type = payload.get("message_type", "text")

    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO messages
                (user_id, message_text, direction, answered_by, message_type,
                 ia_enabled, status, created_at)
            VALUES (%s, %s, 'outbound', 'ia', %s, 1, 'sent', NOW())
        """, (user_id, message_text, message_type))

        message_id = cur.lastrowid
        await _upsert_conversation(cur, user_id, new_message_id=message_id, increment_unread=False)

        await cur.execute("SELECT * FROM messages WHERE id = %s", (message_id,))
        message = dict(await cur.fetchone())

    return message


async def update_message_status(message_id: int, status: str, timestamp: str = None) -> dict:
    ts = timestamp or _now()
    async with get_db() as cur:
        await cur.execute("""
            UPDATE messages
            SET status       = %s,
                delivered_at = CASE WHEN %s = 'delivered' THEN %s ELSE delivered_at END,
                read_at      = CASE WHEN %s = 'read'      THEN %s ELSE read_at      END
            WHERE id = %s
        """, (status, status, ts, status, ts, message_id))
    return {"status": "ok", "message_id": message_id, "new_status": status}


async def delete_message(message_id: int, user_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            UPDATE messages SET status = 'deleted', message_text = NULL
            WHERE id = %s AND user_id = %s
        """, (message_id, user_id))
    return {"status": "deleted", "message_id": message_id}


async def get_conversation_timeline(user_id: int) -> list:
    async with get_db() as cur:
        await cur.execute("""
            SELECT
                m.id, m.message_text, m.direction, m.answered_by,
                m.message_type, m.media_url, m.replied_to_id, m.broadcast_id,
                m.status, m.read_at, m.delivered_at, m.created_at,
                DATE(m.created_at) AS date_group,
                bh.tag AS broadcast_tag, bh.total AS broadcast_total, bh.sent AS broadcast_sent
            FROM messages m
            LEFT JOIN broadcast_history bh ON bh.id = m.broadcast_id
            WHERE m.user_id = %s AND m.status != 'deleted'
            ORDER BY m.created_at ASC
        """, (user_id,))
        rows = await cur.fetchall()

    groups = {}
    for r in rows:
        d    = dict(r)
        date = d.pop("date_group")
        groups.setdefault(str(date), []).append(d)

    return [{"date": date, "messages": msgs} for date, msgs in groups.items()]


# ════════════════════════════════════════════════════════════════════════
# AGENT IA
# ════════════════════════════════════════════════════════════════════════

async def trigger_ia_response(user_id: int, incoming_message_id: int) -> None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT message_text, direction, answered_by, created_at
            FROM messages
            WHERE user_id = %s AND status != 'deleted'
            ORDER BY id DESC LIMIT 10
        """, (user_id,))
        context_rows = await cur.fetchall()
    context = list(reversed([dict(r) for r in context_rows]))

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{BOT_URL}/ia/respond",
                json={"user_id": user_id, "incoming_message_id": incoming_message_id, "context": context},
                headers={"X-Bot-Secret": BOT_SECRET}
            )
    except Exception:
        pass


async def get_ia_stats(user_id: int) -> dict:
    async with get_db() as cur:
        await cur.execute("""
            SELECT
                COUNT(*) AS total_ia_messages,
                COUNT(CASE WHEN read_at IS NOT NULL THEN 1 END) AS read_count,
                MIN(created_at) AS first_ia_message,
                MAX(created_at) AS last_ia_message
            FROM messages
            WHERE user_id = %s AND answered_by = 'ia' AND direction = 'outbound'
        """, (user_id,))
        row = await cur.fetchone()
    return dict(row)


# ════════════════════════════════════════════════════════════════════════
# UPLOAD MÉDIAS
# ════════════════════════════════════════════════════════════════════════

ALLOWED_MEDIA = {
    "image/jpeg": ("image", ".jpg", 10), "image/png": ("image", ".png", 10),
    "image/gif":  ("image", ".gif", 10), "image/webp": ("image", ".webp", 10),
    "video/mp4":  ("video", ".mp4", 50), "video/quicktime": ("video", ".mov", 50),
    "video/x-msvideo": ("video", ".avi", 50), "video/x-matroska": ("video", ".mkv", 50),
    "video/webm": ("video", ".webm", 50),
    "application/pdf": ("pdf", ".pdf", 20),
    "application/msword": ("word", ".doc", 20),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("word", ".docx", 20),
    "application/vnd.ms-excel": ("excel", ".xls", 20),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("excel", ".xlsx", 20),
    "application/vnd.ms-powerpoint": ("powerpoint", ".ppt", 20),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("powerpoint", ".pptx", 20),
    "text/plain": ("text", ".txt", 5),
    "application/zip": ("archive", ".zip", 50),
    "application/x-rar-compressed": ("archive", ".rar", 50),
}


def _make_image_thumbnail(src: Path, stem: str, ext: str) -> str | None:
    try:
        from PIL import Image
        thumb_name = f"{stem}_thumb{ext}"
        thumb_path = MEDIA_DIR / thumb_name
        img = Image.open(src); img.thumbnail((300, 300)); img.save(thumb_path)
        return f"/media/{thumb_name}"
    except Exception:
        return None


def _make_video_thumbnail(src: Path, stem: str) -> str | None:
    try:
        import subprocess
        thumb_name = f"{stem}_thumb.jpg"
        thumb_path = MEDIA_DIR / thumb_name
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ss", "00:00:01",
             "-vframes", "1", "-vf", "scale=300:-1", str(thumb_path)],
            capture_output=True, timeout=15,
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
    if len(file_bytes) > max_mb * 1024 * 1024:
        return {"error": f"Fichier trop volumineux (max {max_mb} MB pour ce type)"}
    final_ext = Path(filename).suffix.lower() or ext
    stem  = str(uuid.uuid4())
    fname = f"{stem}{final_ext}"
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    dest  = MEDIA_DIR / fname
    dest.write_bytes(file_bytes)
    file_url  = f"/media/{fname}"
    thumb_url = (_make_image_thumbnail(dest, stem, final_ext) if ftype == "image"
                 else _make_video_thumbnail(dest, stem) if ftype == "video" else None)
    return {"filename": fname, "url": file_url, "thumbnail": thumb_url,
            "mime_type": mime_type, "type": ftype,
            "size_bytes": len(file_bytes), "size_mb": round(len(file_bytes) / 1024 / 1024, 2)}


# ════════════════════════════════════════════════════════════════════════
# PROFIL MEMBRE
# ════════════════════════════════════════════════════════════════════════

async def get_chat_profile(user_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("""
            SELECT
                u.telegram_id, u.name, u.created_at AS registered_at,
                c.ia_enabled, c.is_blocked, c.unread_count, c.note_admin, c.last_activity,
                COUNT(DISTINCT m.id) AS total_messages,
                SUM(CASE WHEN m.direction = 'inbound'  THEN 1 ELSE 0 END) AS messages_received,
                SUM(CASE WHEN m.direction = 'outbound' THEN 1 ELSE 0 END) AS messages_sent,
                GROUP_CONCAT(DISTINCT cat.name_categorie) AS categories_raw
            FROM users u
            LEFT JOIN conversations c   ON c.user_id  = u.telegram_id
            LEFT JOIN messages m        ON m.user_id  = u.telegram_id AND m.status != 'deleted'
            LEFT JOIN categories cat    ON cat.id_user = u.telegram_id
            WHERE u.telegram_id = %s
            GROUP BY u.telegram_id
        """, (user_id,))
        row = await cur.fetchone()

        if not row:
            return None

        profile = dict(row)
        raw     = profile.pop("categories_raw", "") or ""
        profile["categories"] = [c.strip() for c in raw.split(",") if c.strip()]

        try:
            await cur.execute("""
                SELECT
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN result_percent > 0 THEN 1 ELSE 0 END) AS wins,
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(SUM(CASE WHEN result_percent > 0 THEN 1 ELSE 0 END)
                               / COUNT(*) * 100, 1) END AS win_rate,
                    ROUND(AVG(result_percent), 2) AS avg_result_percent
                FROM trade_journal
                WHERE user_id = %s AND status = 'closed'
            """, (user_id,))
            ts = await cur.fetchone()
            profile["trading"] = dict(ts) if ts else None
        except Exception:
            profile["trading"] = None

    profile["subscription"]        = await get_subscription_summary(user_id)
    profile["broadcasts_received"] = await get_received_broadcasts(user_id, limit=5)
    return profile


async def get_received_broadcasts(user_id: int, limit: int = 5) -> list:
    async with get_db() as cur:
        await cur.execute("""
            SELECT bh.id, bh.tag, bh.message, bh.format, bh.started_at, m.status, m.read_at
            FROM broadcast_history bh
            JOIN messages m ON m.broadcast_id = bh.id AND m.user_id = %s
            ORDER BY bh.started_at DESC LIMIT %s
        """, (user_id, limit))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════
# EXPORTS
# ════════════════════════════════════════════════════════════════════════

async def export_conversation(user_id: int, fmt: str = "json") -> dict:
    async with get_db() as cur:
        await cur.execute("""
            SELECT m.id, m.created_at, m.direction, m.answered_by,
                   m.message_type, m.message_text, m.status
            FROM messages m
            WHERE m.user_id = %s AND m.status != 'deleted'
            ORDER BY m.created_at ASC
        """, (user_id,))
        rows = await cur.fetchall()
    messages = [dict(r) for r in rows]

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=messages[0].keys() if messages else [])
        writer.writeheader(); writer.writerows(messages)
        return {"content": output.getvalue(), "content_type": "text/csv",
                "filename": f"conv_{user_id}.csv"}
    elif fmt == "txt":
        lines = []
        for msg in messages:
            who  = msg["answered_by"] or msg["direction"]
            time = str(msg["created_at"])[:16]
            lines.append(f"[{time}] {who.upper()}: {msg['message_text'] or ''}")
        return {"content": "\n".join(lines), "content_type": "text/plain",
                "filename": f"conv_{user_id}.txt"}
    else:
        import json
        return {"content": json.dumps(messages, ensure_ascii=False, indent=2),
                "content_type": "application/json", "filename": f"conv_{user_id}.json"}


async def mark_requires_admin(message_id: int, value: int) -> dict:
    async with get_db() as cur:
        await cur.execute(
            "UPDATE messages SET requires_admin = %s WHERE id = %s", (value, message_id)
        )
    return {"status": "ok", "message_id": message_id, "requires_admin": value}


async def mark_testimonial(message_id: int, value: int) -> dict:
    async with get_db() as cur:
        await cur.execute(
            "UPDATE messages SET is_testimonial = %s WHERE id = %s", (value, message_id)
        )
    return {"status": "ok", "message_id": message_id, "is_testimonial": value}