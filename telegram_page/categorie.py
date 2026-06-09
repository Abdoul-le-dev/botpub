# categories.py — v4 MySQL

from datetime import datetime, timedelta
from db import get_db


# ────────────────────────────────────────────────────────────────────────
# HELPERS PRIVÉS
# ────────────────────────────────────────────────────────────────────────

def _bulk_insert_members(conn, name_categorie: str, user_ids: list, added_by: str = "manual") -> int:
    """Insère en boucle (mysql-connector ne supporte pas executemany via le wrapper)."""
    now    = datetime.now().isoformat()
    added  = 0
    for uid in user_ids:
        conn.execute("""
            INSERT IGNORE INTO categories (id_user, name_categorie, created_at)
            VALUES (?, ?, ?)
        """, (uid, name_categorie, now))
        # ROW_COUNT() = 1 si inséré, 0 si ignoré
        n = conn.execute("SELECT ROW_COUNT() as n").fetchone()["n"]
        added += n
    return added


def _ensure_meta_exists(conn, name_categorie: str):
    conn.execute("""
        INSERT IGNORE INTO categories_meta (name_categorie) VALUES (?)
    """, (name_categorie,))


# ────────────────────────────────────────────────────────────────────────
# STATS GLOBALES
# ────────────────────────────────────────────────────────────────────────

async def get_categories_stats():
    with get_db() as conn:
        total_cats = conn.execute("SELECT COUNT(*) as n FROM categories_meta").fetchone()["n"]
        tagged     = conn.execute("SELECT COUNT(DISTINCT id_user) as n FROM categories").fetchone()["n"]
        total_tags = conn.execute("SELECT COUNT(*) as n FROM categories").fetchone()["n"]

    avg_tags = round(total_tags / tagged, 1) if tagged > 0 else 0
    return {
        "total_categories":    total_cats,
        "tagged_members":      tagged,
        "avg_tags_per_member": avg_tags,
    }


# ────────────────────────────────────────────────────────────────────────
# CRUD CATÉGORIES
# ────────────────────────────────────────────────────────────────────────

async def get_categories():
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()

    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                cm.id, cm.name_categorie, cm.color, cm.description, cm.created_at,
                COALESCE(COUNT(c.id), 0) AS member_count,
                COALESCE(SUM(CASE WHEN c.created_at >= ? THEN 1 ELSE 0 END), 0) AS new_this_month
            FROM categories_meta cm
            LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
            GROUP BY cm.id
            ORDER BY member_count DESC
        """, (month_ago,)).fetchall()

        categories = [dict(r) for r in rows]

        for cat in categories:
            rules = conn.execute("""
                SELECT id, trigger_type, trigger_value FROM category_rules
                WHERE name_categorie = ? AND is_active = 1
            """, (cat["name_categorie"],)).fetchall()
            cat["rules"] = [dict(r) for r in rules]

    return categories


async def get_category_by_name(name_categorie: str):
    with get_db() as conn:
        row = conn.execute("""
            SELECT cm.*, COUNT(c.id) AS member_count
            FROM categories_meta cm
            LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
            WHERE cm.name_categorie = ?
            GROUP BY cm.id
        """, (name_categorie,)).fetchone()

        if not row:
            return None

        category = dict(row)
        rules    = conn.execute("""
            SELECT * FROM category_rules
            WHERE name_categorie = ? AND is_active = 1
        """, (name_categorie,)).fetchall()
        category["rules"] = [dict(r) for r in rules]

    return category


async def create_category(payload: dict):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO categories_meta (name_categorie, color, description)
            VALUES (?, ?, ?)
        """, (
            payload["name_categorie"],
            payload.get("color", "#38bdf8"),
            payload.get("description", ""),
        ))

        if payload.get("rule"):
            rule = payload["rule"]
            conn.execute("""
                INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
                VALUES (?, ?, ?)
            """, (payload["name_categorie"], rule["trigger_type"], rule.get("trigger_value", "")))

        added = 0
        if payload.get("member_ids"):
            added = _bulk_insert_members(conn, payload["name_categorie"], payload["member_ids"])

    return {"status": "created", "name_categorie": payload["name_categorie"], "members_added": added}


async def update_category(name_categorie: str, payload: dict):
    fields, values = [], []
    if "new_name"    in payload: fields.append("name_categorie = ?"); values.append(payload["new_name"])
    if "color"       in payload: fields.append("color = ?");          values.append(payload["color"])
    if "description" in payload: fields.append("description = ?");    values.append(payload["description"])

    if not fields:
        return {"status": "nothing_to_update"}

    values.append(name_categorie)
    try:
        with get_db() as conn:
            conn.execute(
                f"UPDATE categories_meta SET {', '.join(fields)} WHERE name_categorie = ?",
                values,
            )
    except Exception:
        return {"status": "error", "detail": "Ce nom de catégorie existe déjà"}

    return {"status": "updated"}


# ────────────────────────────────────────────────────────────────────────
# MEMBRES
# ────────────────────────────────────────────────────────────────────────
async def get_category_members(name_categorie: str, filters: dict = None):
    f      = filters or {}
    limit  = int(f.get("limit", 50))
    offset = int(f.get("offset",  0))

    query  = """
        SELECT
            c.id, c.id_user AS telegram_id, c.created_at AS added_at,
            u.name, u.phone, u.email,
            MAX(m.created_at) AS last_activity
        FROM categories c
        LEFT JOIN users    u ON u.telegram_id = c.id_user
        LEFT JOIN messages m ON m.user_id     = c.id_user
        WHERE c.name_categorie = ?
    """
    params = [name_categorie]

    if f.get("search"):
        query  += " AND (u.name LIKE ? OR CAST(c.id_user AS CHAR) LIKE ?)"
        term    = f"%{f['search']}%"
        params += [term, term]

    if f.get("active_only"):
        query  += " AND m.created_at >= ?"
        params.append((datetime.now() - timedelta(days=7)).isoformat())

    if f.get("inactive_only"):
        query  += " AND (m.created_at < ? OR m.created_at IS NULL)"
        params.append((datetime.now() - timedelta(days=21)).isoformat())

    query += f" GROUP BY c.id, c.id_user, c.created_at, u.name, u.phone, u.email ORDER BY c.created_at DESC LIMIT {limit} OFFSET {offset}"

    with get_db() as conn:
        members = [dict(r) for r in conn.execute(query, params).fetchall()]
        total   = conn.execute(
            "SELECT COUNT(*) as n FROM categories WHERE name_categorie = ?", (name_categorie,)
        ).fetchone()["n"]

    return {"members": members, "total": total, "limit": limit, "offset": offset}

async def get_category_members_(name_categorie: str, filters: dict = None):
    f      = filters or {}
    limit  = int(f.get("limit", 50))
    offset = int(f.get("offset",  0))

    query  = """
        SELECT
            c.id, c.id_user AS telegram_id, c.created_at AS added_at,
            u.name, u.phone, u.email,
            MAX(m.created_at) AS last_activity
        FROM categories c
        LEFT JOIN users    u ON u.telegram_id = c.id_user
        LEFT JOIN messages m ON m.user_id     = c.id_user
        WHERE c.name_categorie = ?
    """
    params = [name_categorie]

    if f.get("search"):
        query  += " AND (u.name LIKE ? OR CAST(c.id_user AS CHAR) LIKE ?)"
        term    = f"%{f['search']}%"
        params += [term, term]

    if f.get("active_only"):
        query  += " AND m.created_at >= ?"
        params.append((datetime.now() - timedelta(days=7)).isoformat())

    if f.get("inactive_only"):
        query  += " AND (m.created_at < ? OR m.created_at IS NULL)"
        params.append((datetime.now() - timedelta(days=21)).isoformat())

    query += f" GROUP BY c.id ORDER BY c.created_at DESC LIMIT {limit} OFFSET {offset}"

    with get_db() as conn:
        members = [dict(r) for r in conn.execute(query, params).fetchall()]
        total   = conn.execute(
            "SELECT COUNT(*) as n FROM categories WHERE name_categorie = ?", (name_categorie,)
        ).fetchone()["n"]

    return {"members": members, "total": total, "limit": limit, "offset": offset}


async def add_members_to_category(name_categorie: str, user_ids: list, added_by: str = "manual"):
    with get_db() as conn:
        _ensure_meta_exists(conn, name_categorie)
        added = _bulk_insert_members(conn, name_categorie, user_ids, added_by)

    return {
        "status":          "ok",
        "added":           added,
        "ignored":         len(user_ids) - added,
        "total_submitted": len(user_ids),
    }


async def remove_member_from_category(name_categorie: str, telegram_id: int):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM categories WHERE name_categorie = ? AND id_user = ?",
            (name_categorie, telegram_id),
        )
    return {"status": "removed", "telegram_id": telegram_id}


async def move_members(payload: dict):
    source      = payload["source"]
    destination = payload["destination"]
    action      = payload.get("action", "copy")

    with get_db() as conn:
        if payload["user_ids"] == "all":
            ids = [r["id_user"] for r in conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?", (source,)
            ).fetchall()]
        else:
            ids = payload["user_ids"]

        _ensure_meta_exists(conn, destination)
        added = _bulk_insert_members(conn, destination, ids, added_by="move")

        if action == "move" and ids:
            placeholders = ",".join(["%s"] * len(ids))
            conn.execute(
                f"DELETE FROM categories WHERE name_categorie = ? AND id_user IN ({placeholders})",
                [source] + ids,
            )

    return {"status": "ok", "action": action, "count": len(ids), "added": added, "ignored": len(ids) - added}


async def merge_categories(target: str, sources: list):
    total_added = 0
    with get_db() as conn:
        for source in sources:
            ids = [r["id_user"] for r in conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?", (source,)
            ).fetchall()]

            if ids:
                total_added += _bulk_insert_members(conn, target, ids, added_by="merge")

            conn.execute("DELETE FROM categories WHERE name_categorie = ?",      (source,))
            conn.execute("DELETE FROM categories_meta WHERE name_categorie = ?", (source,))

    return {"status": "merged", "target": target, "sources_deleted": len(sources), "members_added": total_added}


async def import_members_csv(name_categorie: str, user_ids: list):
    return await add_members_to_category(name_categorie, user_ids, added_by="import")


# ────────────────────────────────────────────────────────────────────────
# RÈGLES
# ────────────────────────────────────────────────────────────────────────

async def get_category_rules(name_categorie: str):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM category_rules
            WHERE name_categorie = ? AND is_active = 1
            ORDER BY created_at ASC
        """, (name_categorie,)).fetchall()
    return [dict(r) for r in rows]


async def add_category_rule(name_categorie: str, rule: dict):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
            VALUES (?, ?, ?)
        """, (name_categorie, rule["trigger_type"], rule.get("trigger_value", "")))
        rule_id = conn.execute("SELECT LAST_INSERT_ID() as id").fetchone()["id"]
    return {"id": rule_id, "status": "created"}


async def delete_category_rule(rule_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM category_rules WHERE id = ?", (rule_id,))
    return {"status": "deleted", "rule_id": rule_id}


# ────────────────────────────────────────────────────────────────────────
# STATS
# ────────────────────────────────────────────────────────────────────────

async def get_category_stats(name_categorie: str):
    active_since = (datetime.now() - timedelta(days=7)).isoformat()

    with get_db() as conn:
        member_count = conn.execute(
            "SELECT COUNT(*) as n FROM categories WHERE name_categorie = ?", (name_categorie,)
        ).fetchone()["n"]

        active_7d = conn.execute("""
            SELECT COUNT(DISTINCT c.id_user) as n
            FROM categories c
            JOIN messages m ON m.user_id = c.id_user
            WHERE c.name_categorie = ? AND m.created_at >= ?
        """, (name_categorie, active_since)).fetchone()["n"]

        multi_cat = conn.execute("""
            SELECT COUNT(*) as n FROM (
                SELECT id_user FROM categories
                WHERE id_user IN (SELECT id_user FROM categories WHERE name_categorie = ?)
                GROUP BY id_user HAVING COUNT(DISTINCT name_categorie) > 1
            ) t
        """, (name_categorie,)).fetchone()["n"]

        last_bh = conn.execute("""
            SELECT started_at FROM broadcast_history
            WHERE category = ? ORDER BY started_at DESC LIMIT 1
        """, (name_categorie,)).fetchone()
        last_broadcast = last_bh["started_at"] if last_bh else None

        win_rate = None
        try:
            wr = conn.execute("""
                SELECT
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(
                        SUM(CASE WHEN tj.result_pips > 0 THEN 1 ELSE 0 END)
                        / COUNT(*) * 100, 1
                    ) END as n
                FROM trade_journal tj
                JOIN categories c ON c.id_user = tj.user_id
                WHERE c.name_categorie = ? AND tj.status = 'closed'
            """, (name_categorie,)).fetchone()
            win_rate = wr["n"] if wr else None
        except Exception:
            pass

    return {
        "name_categorie":   name_categorie,
        "member_count":     member_count,
        "active_7d":        active_7d,
        "multi_categories": multi_cat,
        "last_broadcast":   last_broadcast,
        "win_rate":         win_rate,
        "open_rate":        None,
    }


# ────────────────────────────────────────────────────────────────────────
# INTERSECTIONS
# ────────────────────────────────────────────────────────────────────────

async def get_category_intersections(name_categorie: str):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c2.name_categorie, cm.color, COUNT(*) AS shared_count
            FROM categories c1
            JOIN categories c2
                ON  c1.id_user = c2.id_user
                AND c2.name_categorie != c1.name_categorie
            LEFT JOIN categories_meta cm ON cm.name_categorie = c2.name_categorie
            WHERE c1.name_categorie = ?
            GROUP BY c2.name_categorie, cm.color
            ORDER BY shared_count DESC
            LIMIT 10
        """, (name_categorie,)).fetchall()
    return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────────
# PROFIL MEMBRE
# ────────────────────────────────────────────────────────────────────────

async def get_member_profile(telegram_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not row:
            return None

        user = dict(row)

        cats = conn.execute("""
            SELECT c.name_categorie, cm.color
            FROM categories c
            LEFT JOIN categories_meta cm ON cm.name_categorie = c.name_categorie
            WHERE c.id_user = ?
            ORDER BY c.created_at DESC
        """, (telegram_id,)).fetchall()
        user["categories"] = [dict(r) for r in cats]

        last = conn.execute("""
            SELECT created_at FROM messages
            WHERE user_id = ? ORDER BY created_at DESC LIMIT 1
        """, (telegram_id,)).fetchone()
        user["last_activity"] = last["created_at"] if last else None

        try:
            ts = conn.execute("""
                SELECT
                    COUNT(*) AS total_trades,
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(
                        SUM(CASE WHEN result_pips > 0 THEN 1 ELSE 0 END)
                        / COUNT(*) * 100, 1
                    ) END AS win_rate,
                    ROUND(AVG(result_percent), 2) AS avg_percent
                FROM trade_journal
                WHERE user_id = ? AND status = 'closed'
            """, (telegram_id,)).fetchone()
            user["trading_stats"] = dict(ts) if ts else None
        except Exception:
            user["trading_stats"] = None

    return user


async def get_member_categories(telegram_id: int):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.name_categorie, cm.color, c.created_at AS added_at
            FROM categories c
            LEFT JOIN categories_meta cm ON cm.name_categorie = c.name_categorie
            WHERE c.id_user = ?
            ORDER BY c.created_at DESC
        """, (telegram_id,)).fetchall()
    return [dict(r) for r in rows]