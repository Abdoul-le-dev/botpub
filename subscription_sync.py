"""
subscription_sync.py — Synchronisation quotidienne des catégories d'abonnement.

Règle centrale :
    Un abonnement est ACTIF si et seulement si  expires_at > NOW().
    Le champ `status` n'entre PAS dans la définition — seule la date compte.

Catégories gérées :

    CATEGORIE_ACTIFS    : au moins un abonnement avec expires_at > NOW()
    CATEGORIE_J7        : un abonnement expire dans exactement 7 jours
    CATEGORIE_J3        : un abonnement expire dans exactement 3 jours
    CATEGORIE_J1        : un abonnement expire dans exactement 1 jour
    CATEGORIE_EXPIRES   : AUCUN abonnement encore valide (tous ont expires_at <= NOW())
                          → retiré de toutes les autres catégories.
"""

from datetime import datetime
from db import get_db

CATEGORIE_ACTIFS  = "clients_actifs"
CATEGORIE_J7      = "clients_j7"
CATEGORIE_J3      = "clients_j3"
CATEGORIE_J1      = "clients_j1"
CATEGORIE_EXPIRES = "clients_expires"

ALL_CATEGORIES = [
    CATEGORIE_ACTIFS,
    CATEGORIE_J7,
    CATEGORIE_J3,
    CATEGORIE_J1,
    CATEGORIE_EXPIRES,
]


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

async def _ensure_meta_exists(cur, name_categorie: str):
    if not name_categorie or not name_categorie.strip():
        return
    await cur.execute(
        "SELECT 1 FROM categories_meta WHERE name_categorie = %s",
        (name_categorie,)
    )
    if not await cur.fetchone():
        await cur.execute(
            "INSERT INTO categories_meta (name_categorie) VALUES (%s)",
            (name_categorie,)
        )


async def _bulk_insert_members(cur, name_categorie: str, user_ids: list[int]) -> int:
    if not user_ids:
        return 0
    now   = datetime.now().isoformat()
    added = 0
    for uid in user_ids:
        await cur.execute(
            "SELECT 1 FROM categories WHERE name_categorie=%s AND id_user=%s",
            (name_categorie, uid)
        )
        if not await cur.fetchone():
            await cur.execute(
                "INSERT INTO categories (name_categorie, id_user, created_at) VALUES (%s, %s, %s)",
                (name_categorie, uid, now)
            )
            added += 1
    return added


async def _remove_from_categories(cur, categories: list[str], user_ids: list[int]) -> int:
    """Retire les user_ids des catégories listées."""
    if not user_ids or not categories:
        return 0

    cat_placeholders  = ",".join(["%s"] * len(categories))
    user_placeholders = ",".join(["%s"] * len(user_ids))

    await cur.execute(
        f"DELETE FROM categories "
        f"WHERE name_categorie IN ({cat_placeholders}) "
        f"  AND id_user IN ({user_placeholders})",
        categories + user_ids
    )
    return cur.rowcount


async def _place_in_category(cur, target_categorie: str, user_ids: list[int]) -> int:
    """Insère (idempotent) les user_ids dans target_categorie."""
    await _ensure_meta_exists(cur, target_categorie)
    return await _bulk_insert_members(cur, target_categorie, user_ids)


async def _dedupe_categorie(cur, name_categorie: str) -> int:
    """Supprime les doublons intra-catégorie."""
    await cur.execute("""
        DELETE c1 FROM categories c1
        INNER JOIN categories c2
          ON  c1.name_categorie = c2.name_categorie
          AND c1.id_user        = c2.id_user
          AND c1.id             > c2.id
        WHERE c1.name_categorie = %s
    """, (name_categorie,))
    return cur.rowcount


# ════════════════════════════════════════════════════════════════════════════
# JOB PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════
async def sync_clients_actifs():
    """
    Synchronise les catégories d'abonnement.

    Définition unique et cohérente :
      - ACTIF   ⇔ expires_at > NOW()
      - EXPIRÉ  ⇔ aucun abonnement du user n'a expires_at > NOW()

    Le champ `status` en base est ignoré (peut être obsolète / non fiable).
    """
    now = datetime.now()
    ts  = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[sync_clients_actifs] ── Début {ts} ──")

    async with get_db() as cur:

        # ── 1. ACTIFS : au moins un abonnement encore valide ───────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE expires_at > NOW()
        """)
        actifs = [r["user_id"] for r in await cur.fetchall()]
        added_actifs = await _place_in_category(cur, CATEGORIE_ACTIFS, actifs)
        print(f"[sync_clients_actifs] ✓ ACTIFS        : {len(actifs)} user(s)  →  {added_actifs} nouveau(x)")

        # ── 2. J-7 ─────────────────────────────────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE expires_at > NOW()
              AND DATE(expires_at) = DATE(NOW() + INTERVAL 7 DAY)
        """)
        j7 = [r["user_id"] for r in await cur.fetchall()]
        added_j7 = await _place_in_category(cur, CATEGORIE_J7, j7)
        print(f"[sync_clients_actifs] ✓ J-7           : {len(j7)} user(s)  →  {added_j7} nouveau(x)")

        # ── 3. J-3 ─────────────────────────────────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE expires_at > NOW()
              AND DATE(expires_at) = DATE(NOW() + INTERVAL 3 DAY)
        """)
        j3 = [r["user_id"] for r in await cur.fetchall()]
        added_j3 = await _place_in_category(cur, CATEGORIE_J3, j3)
        print(f"[sync_clients_actifs] ✓ J-3           : {len(j3)} user(s)  →  {added_j3} nouveau(x)")

        # ── 4. J-1 ─────────────────────────────────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE expires_at > NOW()
              AND DATE(expires_at) = DATE(NOW() + INTERVAL 1 DAY)
        """)
        j1 = [r["user_id"] for r in await cur.fetchall()]
        added_j1 = await _place_in_category(cur, CATEGORIE_J1, j1)
        print(f"[sync_clients_actifs] ✓ J-1           : {len(j1)} user(s)  →  {added_j1} nouveau(x)")

        # ── 5. EXPIRÉS : aucun abonnement encore valide ────────────────────
        # Users qui existent dans subscriptions mais dont aucune ligne
        # n'a expires_at > NOW().
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE user_id NOT IN (
                SELECT DISTINCT user_id
                FROM subscriptions
                WHERE expires_at > NOW()
            )
        """)
        to_expire = [r["user_id"] for r in await cur.fetchall()]

        # Un expiré ne doit rester nulle part ailleurs
        removed_from_active = await _remove_from_categories(
            cur,
            [CATEGORIE_ACTIFS, CATEGORIE_J7, CATEGORIE_J3, CATEGORIE_J1],
            to_expire
        )
        added_exp = await _place_in_category(cur, CATEGORIE_EXPIRES, to_expire)

        print(f"[sync_clients_actifs] ✓ EXPIRÉS       : {len(to_expire)} user(s)  →  {added_exp} nouveau(x), {removed_from_active} retiré(s) des catégories actives")

        # ── 6. Nettoyage croisé : un user redevenu actif ne doit plus ──────
        # figurer dans clients_expires (cas d'un réabonnement).
        if actifs:
            removed_from_expired = await _remove_from_categories(
                cur, [CATEGORIE_EXPIRES], actifs
            )
            if removed_from_expired:
                print(f"[sync_clients_actifs] ✓ {removed_from_expired} user(s) retiré(s) de '{CATEGORIE_EXPIRES}' (réabonnés)")

        # ── 7. Vérification finale : doublons intra-catégorie ──────────────
        total_dupes = 0
        for cat in ALL_CATEGORIES:
            dupes = await _dedupe_categorie(cur, cat)
            total_dupes += dupes
            if dupes:
                print(f"[sync_clients_actifs] ⚠ {dupes} doublon(s) supprimé(s) dans '{cat}'")

        if not total_dupes:
            print(f"[sync_clients_actifs] ✓ Aucun doublon détecté")

        print(f"[sync_clients_actifs] ── Fin {datetime.now().strftime('%H:%M:%S')} ──\n")