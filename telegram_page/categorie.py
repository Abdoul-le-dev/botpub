
from datetime import datetime, timedelta

from database.database import get_conn
# ────────────────────────────────────────────────────────────────────────
# HELPERS PRIVÉS
# ────────────────────────────────────────────────────────────────────────

def _bulk_insert_members(cursor, name_categorie: str, user_ids: list, added_by: str = "manual") -> int:
    """
    INSERT IGNORE pour ignorer les doublons silencieusement.
    Retourne le nombre de lignes réellement insérées.
    """
    if not user_ids:
        return 0

    now = datetime.now().isoformat()
    values = [(uid, name_categorie, now) for uid in user_ids]

    cursor.executemany("""
        INSERT IGNORE INTO categories (id_user, name_categorie, created_at)
        VALUES (%s, %s, %s)
    """, values)

    return cursor.rowcount


def _ensure_meta_exists(cursor, name_categorie: str):
    """
    Crée une entrée dans categories_meta si elle n'existe pas encore.
    Utile quand on ajoute des membres à une catégorie sans meta explicite.
    """
    cursor.execute("""
        INSERT IGNORE INTO categories_meta (name_categorie)
        VALUES (%s)
    """, (name_categorie,))


# ────────────────────────────────────────────────────────────────────────
# STATS GLOBALES
# ────────────────────────────────────────────────────────────────────────

async def get_categories_stats():
    """
    Stats globales pour la topbar / stats bar :
    - total catégories
    - membres tagués (distincts)
    - tags / membre moyen
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS total_categories FROM categories_meta")
    total_cats = cursor.fetchone()["total_categories"]

    cursor.execute("SELECT COUNT(DISTINCT id_user) AS tagged FROM categories")
    tagged = cursor.fetchone()["tagged"]

    cursor.execute("SELECT COUNT(*) AS total_tags FROM categories")
    total_tags = cursor.fetchone()["total_tags"]

    avg_tags = round(total_tags / tagged, 1) if tagged > 0 else 0

    cursor.close()
    conn.close()

    return {
        "total_categories":     total_cats,
        "tagged_members":       tagged,
        "avg_tags_per_member":  avg_tags
    }


# ────────────────────────────────────────────────────────────────────────
# CRUD CATÉGORIES
# ────────────────────────────────────────────────────────────────────────

async def get_categories():
    """
    Retourne toutes les catégories avec :
    - meta (couleur, description)
    - nombre de membres
    - variation ce mois (nouveaux membres dans les 30 derniers jours)
    - règles actives
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    month_ago = (datetime.now() - timedelta(days=30)).isoformat()

    cursor.execute("""
        SELECT
            cm.id,
            cm.name_categorie,
            cm.color,
            cm.description,
            cm.created_at,
            COUNT(c.id)                                         AS member_count,
            SUM(CASE WHEN c.created_at >= %s THEN 1 ELSE 0 END) AS new_this_month
        FROM categories_meta cm
        LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
        GROUP BY cm.id
        ORDER BY member_count DESC
    """, (month_ago,))

    categories = cursor.fetchall()

    # Ajouter les règles actives pour chaque catégorie
    for cat in categories:
        cursor.execute("""
            SELECT id, trigger_type, trigger_value
            FROM category_rules
            WHERE name_categorie = %s AND is_active = TRUE
        """, (cat["name_categorie"],))
        cat["rules"] = cursor.fetchall()

    cursor.close()
    conn.close()
    return categories


async def get_category_by_name(name_categorie: str):
    """
    Retourne une catégorie complète :
    - meta + règles + stats
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            cm.*,
            COUNT(c.id) AS member_count
        FROM categories_meta cm
        LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
        WHERE cm.name_categorie = %s
        GROUP BY cm.id
    """, (name_categorie,))

    category = cursor.fetchone()
    if not category:
        cursor.close()
        conn.close()
        return None

    # Règles
    cursor.execute("""
        SELECT * FROM category_rules
        WHERE name_categorie = %s AND is_active = TRUE
    """, (name_categorie,))
    category["rules"] = cursor.fetchall()

    cursor.close()
    conn.close()
    return category


async def create_category(payload: dict):
    """
    Crée une catégorie complète.
    payload: {
        name_categorie, color?, description?,
        rule?: { trigger_type, trigger_value },
        member_ids?: [123, 456, ...]
    }
    """
    conn = get_conn()
    cursor = conn.cursor()

    # Créer la meta
    cursor.execute("""
        INSERT INTO categories_meta (name_categorie, color, description)
        VALUES (%s, %s, %s)
    """, (
        payload["name_categorie"],
        payload.get("color", "#38bdf8"),
        payload.get("description", "")
    ))

    # Règle auto si fournie
    if payload.get("rule"):
        rule = payload["rule"]
        cursor.execute("""
            INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
            VALUES (%s, %s, %s)
        """, (
            payload["name_categorie"],
            rule["trigger_type"],
            rule.get("trigger_value", "")
        ))

    # IDs immédiats si fournis
    added = 0
    if payload.get("member_ids"):
        added = _bulk_insert_members(
            cursor,
            payload["name_categorie"],
            payload["member_ids"],
            added_by="manual"
        )

    conn.commit()
    cursor.close()
    conn.close()

    return {
        "status":         "created",
        "name_categorie": payload["name_categorie"],
        "members_added":  added
    }


async def update_category(name_categorie: str, payload: dict):
    """
    Modifie nom, couleur ou description d'une catégorie.
    payload: { name_categorie?, color?, description? }
    Si name_categorie change → ON UPDATE CASCADE met à jour
    categories et category_rules automatiquement.
    """
    conn = get_conn()
    cursor = conn.cursor()

    fields = []
    values = []

    if "new_name" in payload:
        fields.append("name_categorie = %s")
        values.append(payload["new_name"])
    if "color" in payload:
        fields.append("color = %s")
        values.append(payload["color"])
    if "description" in payload:
        fields.append("description = %s")
        values.append(payload["description"])

    if not fields:
        cursor.close()
        conn.close()
        return {"status": "nothing_to_update"}

    values.append(name_categorie)
    cursor.execute(
        f"UPDATE categories_meta SET {', '.join(fields)} WHERE name_categorie = %s",
        values
    )

    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "updated"}


# ────────────────────────────────────────────────────────────────────────
# MEMBRES D'UNE CATÉGORIE
# ────────────────────────────────────────────────────────────────────────

async def get_category_members(name_categorie: str, filters: dict = None):
    """
    Retourne les membres d'une catégorie avec leurs infos users.
    filters: { search?, active_only?, inactive_only?, limit?, offset? }
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT
            c.id,
            c.id_user       AS telegram_id,
            c.created_at    AS added_at,
            u.name,
            u.phone,
            u.email,
            u.level,
            MAX(m.created_at) AS last_activity
        FROM categories c
        LEFT JOIN users    u ON u.telegram_id = c.id_user
        LEFT JOIN messages m ON m.user_id     = c.id_user
        WHERE c.name_categorie = %s
    """
    params = [name_categorie]

    if filters:
        if filters.get("search"):
            query += " AND (u.name LIKE %s OR CAST(c.id_user AS CHAR) LIKE %s)"
            term = f"%{filters['search']}%"
            params += [term, term]

        if filters.get("active_only"):
            active_since = (datetime.now() - timedelta(days=7)).isoformat()
            query += " AND m.created_at >= %s"
            params.append(active_since)

        if filters.get("inactive_only"):
            inactive_since = (datetime.now() - timedelta(days=21)).isoformat()
            query += " AND (m.created_at < %s OR m.created_at IS NULL)"
            params.append(inactive_since)

    query += " GROUP BY c.id ORDER BY c.created_at DESC"

    limit  = int(filters.get("limit",  50)) if filters else 50
    offset = int(filters.get("offset",  0)) if filters else 0
    query += f" LIMIT {limit} OFFSET {offset}"

    cursor.execute(query, params)
    members = cursor.fetchall()

    # Total pour pagination
    cursor.execute(
        "SELECT COUNT(*) AS total FROM categories WHERE name_categorie = %s",
        (name_categorie,)
    )
    total = cursor.fetchone()["total"]

    cursor.close()
    conn.close()

    return {"members": members, "total": total, "limit": limit, "offset": offset}


async def add_members_to_category(name_categorie: str, user_ids: list, added_by: str = "manual"):
    """
    Ajoute une liste de telegram_ids à une catégorie.
    Crée la meta si elle n'existe pas encore.
    Retourne : ajoutés / ignorés (doublons).
    """
    conn = get_conn()
    cursor = conn.cursor()

    _ensure_meta_exists(cursor, name_categorie)
    added = _bulk_insert_members(cursor, name_categorie, user_ids, added_by)

    conn.commit()
    cursor.close()
    conn.close()

    return {
        "status":           "ok",
        "added":            added,
        "ignored":          len(user_ids) - added,
        "total_submitted":  len(user_ids)
    }


async def remove_member_from_category(name_categorie: str, telegram_id: int):
    """Retire un membre d'une catégorie sans le supprimer."""
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM categories
        WHERE name_categorie = %s AND id_user = %s
    """, (name_categorie, telegram_id))

    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "removed", "telegram_id": telegram_id}


async def move_members(payload: dict):
    """
    Déplace ou copie des membres entre catégories.
    payload: {
        source:      str,          -- name_categorie source
        destination: str,          -- name_categorie destination
        user_ids:    list | 'all', -- IDs concernés ou 'all'
        action:      'move' | 'copy'
    }
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    source      = payload["source"]
    destination = payload["destination"]
    action      = payload.get("action", "copy")

    # Résoudre 'all'
    if payload["user_ids"] == "all":
        cursor.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s",
            (source,)
        )
        ids = [r["id_user"] for r in cursor.fetchall()]
    else:
        ids = payload["user_ids"]

    # S'assurer que la destination a une meta
    _ensure_meta_exists(cursor, destination)

    # Insérer dans la destination
    added = _bulk_insert_members(cursor, destination, ids, added_by="move")

    # Si déplacement : retirer de la source
    if action == "move" and ids:
        placeholders = ",".join(["%s"] * len(ids))
        cursor.execute(
            f"DELETE FROM categories WHERE name_categorie = %s AND id_user IN ({placeholders})",
            [source] + ids
        )

    conn.commit()
    cursor.close()
    conn.close()

    return {
        "status":      "ok",
        "action":      action,
        "count":       len(ids),
        "added":       added,
        "ignored":     len(ids) - added
    }


async def merge_categories(target: str, sources: list):
    """
    Fusionne plusieurs catégories dans target.
    Les catégories sources sont supprimées après fusion.
    payload: { target: str, sources: [str, str, ...] }
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    total_added = 0

    for source in sources:
        # Récupérer tous les membres de la source
        cursor.execute(
            "SELECT id_user FROM categories WHERE name_categorie = %s",
            (source,)
        )
        ids = [r["id_user"] for r in cursor.fetchall()]

        if ids:
            added = _bulk_insert_members(cursor, target, ids, added_by="merge")
            total_added += added

        # Supprimer la source (members + meta + rules en CASCADE)
        cursor.execute(
            "DELETE FROM categories WHERE name_categorie = %s",
            (source,)
        )
        cursor.execute(
            "DELETE FROM categories_meta WHERE name_categorie = %s",
            (source,)
        )

    conn.commit()
    cursor.close()
    conn.close()

    return {
        "status":          "merged",
        "target":          target,
        "sources_deleted": len(sources),
        "members_added":   total_added
    }


async def import_members_csv(name_categorie: str, user_ids: list):
    """
    Import bulk depuis un CSV (le parsing CSV est fait côté route FastAPI).
    user_ids = liste d'integers déjà parsés.
    """
    return await add_members_to_category(name_categorie, user_ids, added_by="import")


# ────────────────────────────────────────────────────────────────────────
# RÈGLES D'ATTRIBUTION
# ────────────────────────────────────────────────────────────────────────

async def get_category_rules(name_categorie: str):
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM category_rules
        WHERE name_categorie = %s AND is_active = TRUE
        ORDER BY created_at ASC
    """, (name_categorie,))

    rules = cursor.fetchall()
    cursor.close()
    conn.close()
    return rules


async def add_category_rule(name_categorie: str, rule: dict):
    """
    rule: {
        trigger_type:  'link' | 'inactivity' | 'survey' |
                       'subscription' | 'trade_perf' | 'keyword' | 'no_open'
        trigger_value: str  (ex: 'forex-pro', '21', 'intéressé', '3')
    }
    """
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO category_rules (name_categorie, trigger_type, trigger_value)
        VALUES (%s, %s, %s)
    """, (
        name_categorie,
        rule["trigger_type"],
        rule.get("trigger_value", "")
    ))

    rule_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()
    return {"id": rule_id, "status": "created"}


async def delete_category_rule(rule_id: int):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM category_rules WHERE id = %s", (rule_id,))

    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "deleted", "rule_id": rule_id}


# ────────────────────────────────────────────────────────────────────────
# STATS D'UNE CATÉGORIE (colonne droite)
# ────────────────────────────────────────────────────────────────────────

async def get_category_stats(name_categorie: str):
    """
    Stats complètes pour la colonne droite du front :
    - member_count
    - active_7d         (depuis messages)
    - multi_categories  (membres présents dans plusieurs catégories)
    - last_broadcast    (depuis broadcast_history)
    - win_rate          (depuis trade_journal — None si pas encore de données)
    - open_rate         (None — non disponible sur Telegram)
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    # Total membres
    cursor.execute(
        "SELECT COUNT(*) AS total FROM categories WHERE name_categorie = %s",
        (name_categorie,)
    )
    member_count = cursor.fetchone()["total"]

    # Actifs 7j — membres qui ont envoyé un message dans les 7 derniers jours
    active_since = (datetime.now() - timedelta(days=7)).isoformat()
    cursor.execute("""
        SELECT COUNT(DISTINCT c.id_user) AS active_7d
        FROM categories c
        JOIN messages m ON m.user_id = c.id_user
        WHERE c.name_categorie = %s
          AND m.created_at >= %s
    """, (name_categorie, active_since))
    active_7d = cursor.fetchone()["active_7d"]

    # Multi-catégories — membres présents dans au moins 2 catégories
    cursor.execute("""
        SELECT COUNT(*) AS multi_cat
        FROM (
            SELECT id_user
            FROM categories
            WHERE id_user IN (
                SELECT id_user FROM categories WHERE name_categorie = %s
            )
            GROUP BY id_user
            HAVING COUNT(DISTINCT name_categorie) > 1
        ) AS sub
    """, (name_categorie,))
    multi_cat = cursor.fetchone()["multi_cat"]

    # Dernière campagne depuis broadcast_history
    cursor.execute("""
        SELECT started_at
        FROM broadcast_history
        WHERE category = %s
        ORDER BY started_at DESC
        LIMIT 1
    """, (name_categorie,))
    last_bh = cursor.fetchone()
    last_broadcast = last_bh["started_at"] if last_bh else None

    # Win rate moyen des membres de cette catégorie (si table trade_journal existe)
    win_rate = None
    try:
        cursor.execute("""
            SELECT
                ROUND(
                    SUM(CASE WHEN tj.result_pips > 0 THEN 1 ELSE 0 END)
                    / NULLIF(COUNT(*), 0) * 100, 1
                ) AS win_rate
            FROM trade_journal tj
            JOIN categories c ON c.id_user = tj.user_id
            WHERE c.name_categorie = %s
              AND tj.status = 'closed'
        """, (name_categorie,))
        wr = cursor.fetchone()
        win_rate = wr["win_rate"] if wr else None
    except Exception:
        pass  # table trade_journal pas encore peuplée

    cursor.close()
    conn.close()

    return {
        "name_categorie":  name_categorie,
        "member_count":    member_count,
        "active_7d":       active_7d,
        "multi_categories": multi_cat,
        "last_broadcast":  last_broadcast,
        "win_rate":        win_rate,
        "open_rate":       None  # non disponible sur Telegram
    }


# ────────────────────────────────────────────────────────────────────────
# INTERSECTIONS (colonne droite — "présents aussi dans")
# ────────────────────────────────────────────────────────────────────────

async def get_category_intersections(name_categorie: str):
    """
    Retourne les autres catégories qui partagent des membres
    avec name_categorie, triées par nombre de membres communs.
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            c2.name_categorie,
            cm.color,
            COUNT(*) AS shared_count
        FROM categories c1
        JOIN categories c2
            ON  c1.id_user         = c2.id_user
            AND c2.name_categorie != c1.name_categorie
        LEFT JOIN categories_meta cm
            ON cm.name_categorie = c2.name_categorie
        WHERE c1.name_categorie = %s
        GROUP BY c2.name_categorie
        ORDER BY shared_count DESC
        LIMIT 10
    """, (name_categorie,))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


# ────────────────────────────────────────────────────────────────────────
# PROFIL MEMBRE (drawer)
# ────────────────────────────────────────────────────────────────────────

async def get_member_profile(telegram_id: int):
    """
    Retourne toutes les infos d'un membre pour le drawer :
    - infos users
    - catégories actives
    - dernière activité
    - stats trading si disponibles
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    # Infos de base
    cursor.execute(
        "SELECT * FROM users WHERE telegram_id = %s",
        (telegram_id,)
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return None

    # Catégories actives
    cursor.execute("""
        SELECT c.name_categorie, cm.color
        FROM categories c
        LEFT JOIN categories_meta cm ON cm.name_categorie = c.name_categorie
        WHERE c.id_user = %s
        ORDER BY c.created_at DESC
    """, (telegram_id,))
    user["categories"] = cursor.fetchall()

    # Dernière activité (dernier message entrant)
    cursor.execute("""
        SELECT created_at AS last_activity
        FROM messages
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 1
    """, (telegram_id,))
    last = cursor.fetchone()
    user["last_activity"] = last["last_activity"] if last else None

    # Stats trading si disponibles
    try:
        cursor.execute("""
            SELECT
                COUNT(*)                                                    AS total_trades,
                ROUND(
                    SUM(CASE WHEN result_pips > 0 THEN 1 ELSE 0 END)
                    / NULLIF(COUNT(*), 0) * 100, 1
                )                                                           AS win_rate,
                ROUND(AVG(result_percent), 2)                               AS avg_percent
            FROM trade_journal
            WHERE user_id = %s AND status = 'closed'
        """, (telegram_id,))
        user["trading_stats"] = cursor.fetchone()
    except Exception:
        user["trading_stats"] = None

    cursor.close()
    conn.close()
    return user


async def get_member_categories(telegram_id: int):
    """Retourne uniquement les catégories actives d'un membre."""
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT c.name_categorie, cm.color, c.created_at AS added_at
        FROM categories c
        LEFT JOIN categories_meta cm ON cm.name_categorie = c.name_categorie
        WHERE c.id_user = %s
        ORDER BY c.created_at DESC
    """, (telegram_id,))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows