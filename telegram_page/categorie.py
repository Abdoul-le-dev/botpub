# categories.py — compatible SQLite
# Différences vs MySQL :
#   - conn.row_factory = sqlite3.Row  au lieu de cursor(dictionary=True)
#   - dict(row) pour convertir une Row en dict mutable
#   - ? au lieu de %s pour les paramètres
#   - INSERT OR IGNORE au lieu de INSERT IGNORE
#   - NULLIF() → remplacé par CASE WHEN pour compatibilité SQLite

import sqlite3
from datetime import datetime, timedelta

DB_PATH = "preinscriptions.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # accès par nom de colonne : row["name"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ────────────────────────────────────────────────────────────────────────
# HELPERS PRIVÉS
# ────────────────────────────────────────────────────────────────────────

def _bulk_insert_members(conn, name_categorie: str, user_ids: list, added_by: str = "manual") -> int:
    now    = datetime.now().isoformat()
    values = [(uid, name_categorie, now) for uid in user_ids]
    cur    = conn.executemany("""
        INSERT OR IGNORE INTO categories (id_user, name_categorie, created_at)
        VALUES (?, ?, ?)
    """, values)
    return cur.rowcount


def _ensure_meta_exists(conn, name_categorie: str):
    conn.execute("""
        INSERT OR IGNORE INTO categories_meta (name_categorie)
        VALUES (?)
    """, (name_categorie,))


# ────────────────────────────────────────────────────────────────────────
# STATS GLOBALES
# ────────────────────────────────────────────────────────────────────────

async def get_categories_stats():
    conn = get_conn()
    try:
        total_cats = conn.execute("SELECT COUNT(*) FROM categories_meta").fetchone()[0]
        tagged     = conn.execute("SELECT COUNT(DISTINCT id_user) FROM categories").fetchone()[0]
        total_tags = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        avg_tags   = round(total_tags / tagged, 1) if tagged > 0 else 0
    finally:
        conn.close()

    return {
        "total_categories":    total_cats,
        "tagged_members":      tagged,
        "avg_tags_per_member": avg_tags
    }


# ────────────────────────────────────────────────────────────────────────
# CRUD CATÉGORIES
# ────────────────────────────────────────────────────────────────────────

async def get_categories():
    conn      = get_conn()
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = conn.execute("""
            SELECT
                cm.id,
                cm.name_categorie,
                cm.color,
                cm.description,
                cm.created_at,
                COUNT(c.id)                                         AS member_count,
                SUM(CASE WHEN c.created_at >= ? THEN 1 ELSE 0 END) AS new_this_month
            FROM categories_meta cm
            LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
            GROUP BY cm.id
            ORDER BY member_count DESC
        """, (month_ago,)).fetchall()

        categories = [dict(r) for r in rows]

        for cat in categories:
            rules = conn.execute("""
                SELECT id, trigger_type, trigger_value
                FROM category_rules
                WHERE name_categorie = ? AND is_active = 1
            """, (cat["name_categorie"],)).fetchall()
            cat["rules"] = [dict(r) for r in rules]

    finally:
        conn.close()

    return categories


async def get_category_by_name(name_categorie: str):
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT
                cm.*,
                COUNT(c.id) AS member_count
            FROM categories_meta cm
            LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
            WHERE cm.name_categorie = ?
            GROUP BY cm.id
        """, (name_categorie,)).fetchone()

        if not row:
            return None

        category = dict(row)

        rules = conn.execute("""
            SELECT * FROM category_rules
            WHERE name_categorie = ? AND is_active = 1
        """, (name_categorie,)).fetchall()
        category["rules"] = [dict(r) for r in rules]

    finally:
        conn.close()

    return category


async def create_category(payload: dict):
    conn  = get_conn()
    added = 0
    try:
        conn.execute("""
            INSERT INTO categories_meta (name_categorie, color, description)
            VALUES (?, ?, ?)
        """, (
            payload["name_categorie"],
            payload.get("color", "#38bdf8"),
            payload.get("description", "")
        ))

        if payload.get("rule"):
            rule = payload["rule"]
            conn.execute("""
                INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
                VALUES (?, ?, ?)
            """, (
                payload["name_categorie"],
                rule["trigger_type"],
                rule.get("trigger_value", "")
            ))

        if payload.get("member_ids"):
            added = _bulk_insert_members(conn, payload["name_categorie"], payload["member_ids"])

        conn.commit()
    finally:
        conn.close()

    return {"status": "created", "name_categorie": payload["name_categorie"], "members_added": added}


async def update_category(name_categorie: str, payload: dict):
    fields, values = [], []

    if "new_name"    in payload: fields.append("name_categorie = ?"); values.append(payload["new_name"])
    if "color"       in payload: fields.append("color = ?");          values.append(payload["color"])
    if "description" in payload: fields.append("description = ?");    values.append(payload["description"])

    if not fields:
        return {"status": "nothing_to_update"}

    values.append(name_categorie)
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE categories_meta SET {', '.join(fields)} WHERE name_categorie = ?",
            values
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "updated"}


# ────────────────────────────────────────────────────────────────────────
# MEMBRES D'UNE CATÉGORIE
# ────────────────────────────────────────────────────────────────────────

async def get_category_members(name_categorie: str, filters: dict = None):
    conn   = get_conn()
    f      = filters or {}
    limit  = int(f.get("limit",  50))
    offset = int(f.get("offset",  0))

    query  = """
        SELECT
            c.id,
            c.id_user         AS telegram_id,
            c.created_at      AS added_at,
            u.name,
            u.phone,
            u.email,
            MAX(m.created_at) AS last_activity
        FROM categories c
        LEFT JOIN users    u ON u.telegram_id = c.id_user
        LEFT JOIN messages m ON m.user_id     = c.id_user
        WHERE c.name_categorie = ?
    """
    params = [name_categorie]

    if f.get("search"):
        query  += " AND (u.name LIKE ? OR CAST(c.id_user AS TEXT) LIKE ?)"
        term    = f"%{f['search']}%"
        params += [term, term]

    if f.get("active_only"):
        query  += " AND m.created_at >= ?"
        params.append((datetime.now() - timedelta(days=7)).isoformat())

    if f.get("inactive_only"):
        query  += " AND (m.created_at < ? OR m.created_at IS NULL)"
        params.append((datetime.now() - timedelta(days=21)).isoformat())

    query += f" GROUP BY c.id ORDER BY c.created_at DESC LIMIT {limit} OFFSET {offset}"

    try:
        members = [dict(r) for r in conn.execute(query, params).fetchall()]
        total   = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE name_categorie = ?",
            (name_categorie,)
        ).fetchone()[0]
    finally:
        conn.close()

    return {"members": members, "total": total, "limit": limit, "offset": offset}


async def add_members_to_category(name_categorie: str, user_ids: list, added_by: str = "manual"):
    conn = get_conn()
    try:
        _ensure_meta_exists(conn, name_categorie)
        added = _bulk_insert_members(conn, name_categorie, user_ids, added_by)
        conn.commit()
    finally:
        conn.close()

    return {
        "status":          "ok",
        "added":           added,
        "ignored":         len(user_ids) - added,
        "total_submitted": len(user_ids)
    }


async def remove_member_from_category(name_categorie: str, telegram_id: int):
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM categories WHERE name_categorie = ? AND id_user = ?",
            (name_categorie, telegram_id)
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "removed", "telegram_id": telegram_id}


async def move_members(payload: dict):
    conn        = get_conn()
    source      = payload["source"]
    destination = payload["destination"]
    action      = payload.get("action", "copy")

    try:
        if payload["user_ids"] == "all":
            ids = [r[0] for r in conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?", (source,)
            ).fetchall()]
        else:
            ids = payload["user_ids"]

        _ensure_meta_exists(conn, destination)
        added = _bulk_insert_members(conn, destination, ids, added_by="move")

        if action == "move" and ids:
            placeholders = ",".join(["?"] * len(ids))
            conn.execute(
                f"DELETE FROM categories WHERE name_categorie = ? AND id_user IN ({placeholders})",
                [source] + ids
            )

        conn.commit()
    finally:
        conn.close()

    return {
        "status":  "ok",
        "action":  action,
        "count":   len(ids),
        "added":   added,
        "ignored": len(ids) - added
    }


async def merge_categories(target: str, sources: list):
    conn        = get_conn()
    total_added = 0
    try:
        for source in sources:
            ids = [r[0] for r in conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?", (source,)
            ).fetchall()]

            if ids:
                total_added += _bulk_insert_members(conn, target, ids, added_by="merge")

            conn.execute("DELETE FROM categories WHERE name_categorie = ?",     (source,))
            conn.execute("DELETE FROM categories_meta WHERE name_categorie = ?", (source,))

        conn.commit()
    finally:
        conn.close()

    return {"status": "merged", "target": target, "sources_deleted": len(sources), "members_added": total_added}


async def import_members_csv(name_categorie: str, user_ids: list):
    return await add_members_to_category(name_categorie, user_ids, added_by="import")


# ────────────────────────────────────────────────────────────────────────
# RÈGLES D'ATTRIBUTION
# ────────────────────────────────────────────────────────────────────────

async def get_category_rules(name_categorie: str):
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM category_rules
            WHERE name_categorie = ? AND is_active = 1
            ORDER BY created_at ASC
        """, (name_categorie,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


async def add_category_rule(name_categorie: str, rule: dict):
    conn = get_conn()
    try:
        cur     = conn.execute("""
            INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
            VALUES (?, ?, ?)
        """, (name_categorie, rule["trigger_type"], rule.get("trigger_value", "")))
        rule_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"id": rule_id, "status": "created"}


async def delete_category_rule(rule_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM category_rules WHERE id = ?", (rule_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "deleted", "rule_id": rule_id}


# ────────────────────────────────────────────────────────────────────────
# STATS D'UNE CATÉGORIE
# ────────────────────────────────────────────────────────────────────────

async def get_category_stats(name_categorie: str):
    conn         = get_conn()
    active_since = (datetime.now() - timedelta(days=7)).isoformat()

    try:
        member_count = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE name_categorie = ?",
            (name_categorie,)
        ).fetchone()[0]

        active_7d = conn.execute("""
            SELECT COUNT(DISTINCT c.id_user)
            FROM categories c
            JOIN messages m ON m.user_id = c.id_user
            WHERE c.name_categorie = ? AND m.created_at >= ?
        """, (name_categorie, active_since)).fetchone()[0]

        multi_cat = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT id_user FROM categories
                WHERE id_user IN (
                    SELECT id_user FROM categories WHERE name_categorie = ?
                )
                GROUP BY id_user
                HAVING COUNT(DISTINCT name_categorie) > 1
            )
        """, (name_categorie,)).fetchone()[0]

        last_bh = conn.execute("""
            SELECT started_at FROM broadcast_history
            WHERE category = ? ORDER BY started_at DESC LIMIT 1
        """, (name_categorie,)).fetchone()
        last_broadcast = last_bh[0] if last_bh else None

        win_rate = None
        try:
            wr = conn.execute("""
                SELECT
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(
                        CAST(SUM(CASE WHEN tj.result_pips > 0 THEN 1 ELSE 0 END) AS REAL)
                        / COUNT(*) * 100, 1
                    ) END
                FROM trade_journal tj
                JOIN categories c ON c.id_user = tj.user_id
                WHERE c.name_categorie = ? AND tj.status = 'closed'
            """, (name_categorie,)).fetchone()
            win_rate = wr[0] if wr else None
        except Exception:
            pass

    finally:
        conn.close()

    return {
        "name_categorie":   name_categorie,
        "member_count":     member_count,
        "active_7d":        active_7d,
        "multi_categories": multi_cat,
        "last_broadcast":   last_broadcast,
        "win_rate":         win_rate,
        "open_rate":        None
    }


# ────────────────────────────────────────────────────────────────────────
# INTERSECTIONS
# ────────────────────────────────────────────────────────────────────────

async def get_category_intersections(name_categorie: str):
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                c2.name_categorie,
                cm.color,
                COUNT(*) AS shared_count
            FROM categories c1
            JOIN categories c2
                ON  c1.id_user         = c2.id_user
                AND c2.name_categorie != c1.name_categorie
            LEFT JOIN categories_meta cm ON cm.name_categorie = c2.name_categorie
            WHERE c1.name_categorie = ?
            GROUP BY c2.name_categorie
            ORDER BY shared_count DESC
            LIMIT 10
        """, (name_categorie,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────────
# PROFIL MEMBRE (drawer)
# ────────────────────────────────────────────────────────────────────────

async def get_member_profile(telegram_id: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()

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
        user["last_activity"] = last[0] if last else None

        try:
            ts = conn.execute("""
                SELECT
                    COUNT(*) AS total_trades,
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(
                        CAST(SUM(CASE WHEN result_pips > 0 THEN 1 ELSE 0 END) AS REAL)
                        / COUNT(*) * 100, 1
                    ) END AS win_rate,
                    ROUND(AVG(result_percent), 2) AS avg_percent
                FROM trade_journal
                WHERE user_id = ? AND status = 'closed'
            """, (telegram_id,)).fetchone()
            user["trading_stats"] = dict(ts) if ts else None
        except Exception:
            user["trading_stats"] = None

    finally:
        conn.close()

    return user


async def get_member_categories(telegram_id: int):
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT c.name_categorie, cm.color, c.created_at AS added_at
            FROM categories c
            LEFT JOIN categories_meta cm ON cm.name_categorie = c.name_categorie
            WHERE c.id_user = ?
            ORDER BY c.created_at DESC
        """, (telegram_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]