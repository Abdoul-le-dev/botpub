# categorie.py — v5 MySQL async (aiomysql)

from datetime import datetime, timedelta
from db import get_db


# ────────────────────────────────────────────────────────────────────────
# HELPERS PRIVÉS
# ────────────────────────────────────────────────────────────────────────

async def _bulk_insert_members(cur, name_categorie: str, user_ids: list, added_by: str = "manual") -> int:
    """Insère en boucle et compte les lignes réellement insérées."""
    now   = datetime.now().isoformat()
    added = 0
    for uid in user_ids:
        await cur.execute("""
            INSERT IGNORE INTO categories (id_user, name_categorie, created_at)
            VALUES (%s, %s, %s)
        """, (uid, name_categorie, now))
        added += cur.rowcount  # 1 si inséré, 0 si ignoré (INSERT IGNORE)
    return added


async def _ensure_meta_exists(cur, name_categorie: str):
    await cur.execute("""
        INSERT IGNORE INTO categories_meta (name_categorie) VALUES (%s)
    """, (name_categorie,))


# ────────────────────────────────────────────────────────────────────────
# STATS GLOBALES
# ────────────────────────────────────────────────────────────────────────

async def get_categories_stats():
    async with get_db() as cur:
        await cur.execute("SELECT COUNT(*) as n FROM categories_meta")
        total_cats = (await cur.fetchone())["n"]

        await cur.execute("SELECT COUNT(DISTINCT id_user) as n FROM categories")
        tagged = (await cur.fetchone())["n"]

        await cur.execute("SELECT COUNT(*) as n FROM categories")
        total_tags = (await cur.fetchone())["n"]

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

    async with get_db() as cur:
        await cur.execute("""
            SELECT
                cm.id, cm.name_categorie, cm.color, cm.description, cm.created_at,
                COALESCE(COUNT(c.id), 0) AS member_count,
                COALESCE(SUM(CASE WHEN c.created_at >= %s THEN 1 ELSE 0 END), 0) AS new_this_month
            FROM categories_meta cm
            LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
            GROUP BY cm.id
            ORDER BY member_count DESC
        """, (month_ago,))
        categories = [dict(r) for r in await cur.fetchall()]

        for cat in categories:
            await cur.execute("""
                SELECT id, trigger_type, trigger_value FROM category_rules
                WHERE name_categorie = %s AND is_active = 1
            """, (cat["name_categorie"],))
            cat["rules"] = [dict(r) for r in await cur.fetchall()]

    return categories


async def get_category_by_name(name_categorie: str):
    async with get_db() as cur:
        await cur.execute("""
            SELECT cm.*, COUNT(c.id) AS member_count
            FROM categories_meta cm
            LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
            WHERE cm.name_categorie = %s
            GROUP BY cm.id
        """, (name_categorie,))
        row = await cur.fetchone()
        if not row:
            return None

        category = dict(row)

        await cur.execute("""
            SELECT * FROM category_rules
            WHERE name_categorie = %s AND is_active = 1
        """, (name_categorie,))
        category["rules"] = [dict(r) for r in await cur.fetchall()]

    return category


async def create_category(payload: dict):
    added = 0
    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO categories_meta (name_categorie, color, description)
            VALUES (%s, %s, %s)
        """, (
            payload["name_categorie"],
            payload.get("color", "#38bdf8"),
            payload.get("description", ""),
        ))

        if payload.get("rule"):
            rule = payload["rule"]
            await cur.execute("""
                INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
                VALUES (%s, %s, %s)
            """, (payload["name_categorie"], rule["trigger_type"], rule.get("trigger_value", "")))

        if payload.get("member_ids"):
            added = await _bulk_insert_members(cur, payload["name_categorie"], payload["member_ids"])

    return {"status": "created", "name_categorie": payload["name_categorie"], "members_added": added}


async def update_category(name_categorie: str, payload: dict):
    fields, values = [], []
    if "new_name"    in payload: fields.append("name_categorie = %s"); values.append(payload["new_name"])
    if "color"       in payload: fields.append("color = %s");          values.append(payload["color"])
    if "description" in payload: fields.append("description = %s");    values.append(payload["description"])

    if not fields:
        return {"status": "nothing_to_update"}

    values.append(name_categorie)
    try:
        async with get_db() as cur:
            await cur.execute(
                f"UPDATE categories_meta SET {', '.join(fields)} WHERE name_categorie = %s",
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
    offset = int(f.get("offset", 0))

    query  = """
        SELECT
            c.id, c.id_user AS telegram_id, c.created_at AS added_at,
            u.name, u.phone, u.email,
            MAX(m.created_at) AS last_activity
        FROM categories c
        LEFT JOIN users    u ON u.telegram_id = c.id_user
        LEFT JOIN messages m ON m.user_id     = c.id_user
        WHERE c.name_categorie = %s
    """
    params = [name_categorie]

    if f.get("search"):
        query  += " AND (u.name LIKE %s OR CAST(c.id_user AS CHAR) LIKE %s)"
        term    = f"%{f['search']}%"
        params += [term, term]

    if f.get("active_only"):
        query  += " AND m.created_at >= %s"
        params.append((datetime.now() - timedelta(days=7)).isoformat())

    if f.get("inactive_only"):
        query  += " AND (m.created_at < %s OR m.created_at IS NULL)"
        params.append((datetime.now() - timedelta(days=21)).isoformat())

    query += f" GROUP BY c.id ORDER BY c.created_at DESC LIMIT {limit} OFFSET {offset}"

    async with get_db() as cur:
        await cur.execute(query, params)
        members = [dict(r) for r in await cur.fetchall()]

        await cur.execute(
            "SELECT COUNT(*) as n FROM categories WHERE name_categorie = %s", (name_categorie,)
        )
        total = (await cur.fetchone())["n"]

    return {"members": members, "total": total, "limit": limit, "offset": offset}


async def add_members_to_category(name_categorie: str, user_ids: list, added_by: str = "manual"):
    async with get_db() as cur:
        await _ensure_meta_exists(cur, name_categorie)
        added = await _bulk_insert_members(cur, name_categorie, user_ids, added_by)

    return {
        "status":          "ok",
        "added":           added,
        "ignored":         len(user_ids) - added,
        "total_submitted": len(user_ids),
    }


async def remove_member_from_category(name_categorie: str, telegram_id: int):
    async with get_db() as cur:
        await cur.execute(
            "DELETE FROM categories WHERE name_categorie = %s AND id_user = %s",
            (name_categorie, telegram_id),
        )
    return {"status": "removed", "telegram_id": telegram_id}


async def move_members(payload: dict):
    source      = payload["source"]
    destination = payload["destination"]
    action      = payload.get("action", "copy")

    async with get_db() as cur:
        if payload["user_ids"] == "all":
            await cur.execute(
                "SELECT id_user FROM categories WHERE name_categorie = %s", (source,)
            )
            ids = [r["id_user"] for r in await cur.fetchall()]
        else:
            ids = payload["user_ids"]

        await _ensure_meta_exists(cur, destination)
        added = await _bulk_insert_members(cur, destination, ids, added_by="move")

        if action == "move" and ids:
            placeholders = ",".join(["%s"] * len(ids))
            await cur.execute(
                f"DELETE FROM categories WHERE name_categorie = %s AND id_user IN ({placeholders})",
                [source] + ids,
            )

    return {"status": "ok", "action": action, "count": len(ids), "added": added, "ignored": len(ids) - added}


async def merge_categories(target: str, sources: list):
    total_added = 0
    async with get_db() as cur:
        for source in sources:
            await cur.execute(
                "SELECT id_user FROM categories WHERE name_categorie = %s", (source,)
            )
            ids = [r["id_user"] for r in await cur.fetchall()]

            if ids:
                total_added += await _bulk_insert_members(cur, target, ids, added_by="merge")

            await cur.execute("DELETE FROM categories WHERE name_categorie = %s",      (source,))
            await cur.execute("DELETE FROM categories_meta WHERE name_categorie = %s", (source,))

    return {"status": "merged", "target": target, "sources_deleted": len(sources), "members_added": total_added}


async def import_members_csv(name_categorie: str, user_ids: list):
    return await add_members_to_category(name_categorie, user_ids, added_by="import")


# ────────────────────────────────────────────────────────────────────────
# RÈGLES
# ────────────────────────────────────────────────────────────────────────

async def get_category_rules(name_categorie: str):
    async with get_db() as cur:
        await cur.execute("""
            SELECT * FROM category_rules
            WHERE name_categorie = %s AND is_active = 1
            ORDER BY created_at ASC
        """, (name_categorie,))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def add_category_rule(name_categorie: str, rule: dict):
    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
            VALUES (%s, %s, %s)
        """, (name_categorie, rule["trigger_type"], rule.get("trigger_value", "")))
        rule_id = cur.lastrowid
    return {"id": rule_id, "status": "created"}


async def delete_category_rule(rule_id: int):
    async with get_db() as cur:
        await cur.execute("DELETE FROM category_rules WHERE id = %s", (rule_id,))
    return {"status": "deleted", "rule_id": rule_id}


# ────────────────────────────────────────────────────────────────────────
# STATS
# ────────────────────────────────────────────────────────────────────────

async def get_category_stats(name_categorie: str):
    active_since = (datetime.now() - timedelta(days=7)).isoformat()

    async with get_db() as cur:
        await cur.execute(
            "SELECT COUNT(*) as n FROM categories WHERE name_categorie = %s", (name_categorie,)
        )
        member_count = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT COUNT(DISTINCT c.id_user) as n
            FROM categories c
            JOIN messages m ON m.user_id = c.id_user
            WHERE c.name_categorie = %s AND m.created_at >= %s
        """, (name_categorie, active_since))
        active_7d = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT COUNT(*) as n FROM (
                SELECT id_user FROM categories
                WHERE id_user IN (SELECT id_user FROM categories WHERE name_categorie = %s)
                GROUP BY id_user HAVING COUNT(DISTINCT name_categorie) > 1
            ) t
        """, (name_categorie,))
        multi_cat = (await cur.fetchone())["n"]

        await cur.execute("""
            SELECT started_at FROM broadcast_history
            WHERE category = %s ORDER BY started_at DESC LIMIT 1
        """, (name_categorie,))
        last_bh        = await cur.fetchone()
        last_broadcast = last_bh["started_at"] if last_bh else None

        win_rate = None
        try:
            await cur.execute("""
                SELECT
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(
                        SUM(CASE WHEN tj.result_pips > 0 THEN 1 ELSE 0 END)
                        / COUNT(*) * 100, 1
                    ) END as n
                FROM trade_journal tj
                JOIN categories c ON c.id_user = tj.user_id
                WHERE c.name_categorie = %s AND tj.status = 'closed'
            """, (name_categorie,))
            wr       = await cur.fetchone()
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
    async with get_db() as cur:
        await cur.execute("""
            SELECT c2.name_categorie, cm.color, COUNT(*) AS shared_count
            FROM categories c1
            JOIN categories c2
                ON  c1.id_user = c2.id_user
                AND c2.name_categorie != c1.name_categorie
            LEFT JOIN categories_meta cm ON cm.name_categorie = c2.name_categorie
            WHERE c1.name_categorie = %s
            GROUP BY c2.name_categorie
            ORDER BY shared_count DESC
            LIMIT 10
        """, (name_categorie,))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────────
# PROFIL MEMBRE
# ────────────────────────────────────────────────────────────────────────

async def get_member_profile(telegram_id: int):
    async with get_db() as cur:
        await cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
        row = await cur.fetchone()
        if not row:
            return None

        user = dict(row)

        await cur.execute("""
            SELECT c.name_categorie, cm.color
            FROM categories c
            LEFT JOIN categories_meta cm ON cm.name_categorie = c.name_categorie
            WHERE c.id_user = %s
            ORDER BY c.created_at DESC
        """, (telegram_id,))
        user["categories"] = [dict(r) for r in await cur.fetchall()]

        await cur.execute("""
            SELECT created_at FROM messages
            WHERE user_id = %s ORDER BY created_at DESC LIMIT 1
        """, (telegram_id,))
        last                = await cur.fetchone()
        user["last_activity"] = last["created_at"] if last else None

        try:
            await cur.execute("""
                SELECT
                    COUNT(*) AS total_trades,
                    CASE WHEN COUNT(*) = 0 THEN NULL
                    ELSE ROUND(
                        SUM(CASE WHEN result_pips > 0 THEN 1 ELSE 0 END)
                        / COUNT(*) * 100, 1
                    ) END AS win_rate,
                    ROUND(AVG(result_percent), 2) AS avg_percent
                FROM trade_journal
                WHERE user_id = %s AND status = 'closed'
            """, (telegram_id,))
            ts                   = await cur.fetchone()
            user["trading_stats"] = dict(ts) if ts else None
        except Exception:
            user["trading_stats"] = None

    return user


async def get_member_categories(telegram_id: int):
    async with get_db() as cur:
        await cur.execute("""
            SELECT c.name_categorie, cm.color, c.created_at AS added_at
            FROM categories c
            LEFT JOIN categories_meta cm ON cm.name_categorie = c.name_categorie
            WHERE c.id_user = %s
            ORDER BY c.created_at DESC
        """, (telegram_id,))
        rows = await cur.fetchall()
    return [dict(r) for r in rows]