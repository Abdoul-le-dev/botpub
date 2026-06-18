"""
subscription_sync.py — Synchronisation quotidienne des catégories d'abonnement.

Catégories gérées :

    CATEGORIE_ACTIFS    : status = 'active' (TOUS, sans exception)
    CATEGORIE_J7         : expire dans exactement 7 jours
    CATEGORIE_J3         : expire dans exactement 3 jours
    CATEGORIE_J1         : expire dans exactement 1 jour
    CATEGORIE_EXPIRES   : abonnement expiré (status mis à 'expired')

"""

from datetime import datetime
from db import get_db

CATEGORIE_ACTIFS  = "clients_actifs"
CATEGORIE_J7      = "clients_j7"
CATEGORIE_J3      = "clients_j3"
CATEGORIE_J1      = "clients_j1"
CATEGORIE_EXPIRES = "clients_expires"

# Toutes les catégories de la famille — utilisé pour le retrait croisé.
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
    now    = datetime.now().isoformat()
    added  = 0
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

async def _remove_from_active_categories(cur, user_ids: list[int]) -> int:
    """
    Retire les user_ids de CATEGORIE_ACTIFS, J7, J3, J1 (mais pas de
    CATEGORIE_EXPIRES). Utilisé uniquement quand un abonnement vient
    d'expirer : il ne doit plus apparaître nulle part ailleurs que
    dans clients_expires.
    Retourne le nombre de lignes supprimées.
    """
    if not user_ids:
        return 0

    non_expired_categories = [
        CATEGORIE_ACTIFS, CATEGORIE_J7, CATEGORIE_J3, CATEGORIE_J1
    ]
    cat_placeholders  = ",".join(["%s"] * len(non_expired_categories))
    user_placeholders = ",".join(["%s"] * len(user_ids))

    await cur.execute(
        f"DELETE FROM categories "
        f"WHERE name_categorie IN ({cat_placeholders}) "
        f"  AND id_user IN ({user_placeholders})",
        non_expired_categories + user_ids
    )
    return cur.rowcount


async def _place_in_category(cur, target_categorie: str, user_ids: list[int]) -> int:
    """
    Insère (idempotent) les user_ids dans target_categorie, sans toucher
    aux autres catégories. Un user peut donc se retrouver dans plusieurs
    catégories à la fois (ex: clients_actifs + clients_j7) — c'est attendu.
    Retourne le nombre de nouvelles lignes insérées.
    """
    await _ensure_meta_exists(cur, target_categorie)
    return await _bulk_insert_members(cur, target_categorie, user_ids)


async def _dedupe_categorie(cur, name_categorie: str) -> int:
    """
    Supprime les doublons (id_user en double) DANS une même catégorie,
    en gardant la ligne avec le plus petit id.
    """
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
    Synchronise les catégories d'abonnement, chaque jour :

      1. CATEGORIE_ACTIFS  : TOUS les status = 'active', sans exception
      2. CATEGORIE_J7       : expire dans exactement 7 jours
      3. CATEGORIE_J3       : expire dans exactement 3 jours
      4. CATEGORIE_J1       : expire dans exactement 1 jour
      5. CATEGORIE_EXPIRES : user dont TOUS les abonnements sont 'expired'
                              et qui n'ont aucun abonnement actif en cours.
                              Retiré de toutes les autres catégories.

    Un user actif qui expire dans 7/3/1 jour(s) se retrouve DANS LES DEUX
    catégories à la fois (ex: clients_actifs + clients_j7) — c'est le
    comportement attendu, pas une erreur.
    """
    now = datetime.now()
    ts  = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[sync_clients_actifs] ── Début {ts} ──")

    async with get_db() as cur:

        # ── 1. Tous les abonnements actifs ──────────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE status = 'active'
        """)
        actifs = [r["user_id"] for r in await cur.fetchall()]
        added_actifs = await _place_in_category(cur, CATEGORIE_ACTIFS, actifs)
        print(f"[sync_clients_actifs] ✓ ACTIFS        : {len(actifs)} user(s)  →  {added_actifs} nouveau(x)")

        # ── 2. Expire dans exactement 7 jours ───────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE status = 'active'
              AND DATE(expires_at) = DATE(NOW() + INTERVAL 7 DAY)
        """)
        j7 = [r["user_id"] for r in await cur.fetchall()]
        added_j7 = await _place_in_category(cur, CATEGORIE_J7, j7)
        print(f"[sync_clients_actifs] ✓ J-7           : {len(j7)} user(s)  →  {added_j7} nouveau(x)")

        # ── 3. Expire dans exactement 3 jours ───────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE status = 'active'
              AND DATE(expires_at) = DATE(NOW() + INTERVAL 3 DAY)
        """)
        j3 = [r["user_id"] for r in await cur.fetchall()]
        added_j3 = await _place_in_category(cur, CATEGORIE_J3, j3)
        print(f"[sync_clients_actifs] ✓ J-3           : {len(j3)} user(s)  →  {added_j3} nouveau(x)")

        # ── 4. Expire dans exactement 1 jour ────────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE status = 'active'
              AND DATE(expires_at) = DATE(NOW() + INTERVAL 1 DAY)
        """)
        j1 = [r["user_id"] for r in await cur.fetchall()]
        added_j1 = await _place_in_category(cur, CATEGORIE_J1, j1)
        print(f"[sync_clients_actifs] ✓ J-1           : {len(j1)} user(s)  →  {added_j1} nouveau(x)")

        # ── 5. Abonnements expirés ──────────────────────────────────────────
        # Users dont TOUS les abonnements sont 'expired'
        # et qui n'ont aucun abonnement actif valide en cours.
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE status = 'expired'
              AND user_id NOT IN (
                  SELECT DISTINCT user_id
                  FROM subscriptions
                  WHERE status = 'active'
                    AND expires_at > NOW()
              )
        """)
        to_expire = [r["user_id"] for r in await cur.fetchall()]

        # Un expiré ne doit rester nulle part ailleurs (actifs/J7/J3/J1)
        removed_from_active = await _remove_from_active_categories(cur, to_expire)
        added_exp = await _place_in_category(cur, CATEGORIE_EXPIRES, to_expire)

        print(f"[sync_clients_actifs] ✓ EXPIRÉS       : {len(to_expire)} user(s)  →  {added_exp} nouveau(x), {removed_from_active} retiré(s) des catégories actives")

        # ── 6. Vérification finale : doublons intra-catégorie ──────────────
        total_dupes = 0
        for cat in ALL_CATEGORIES:
            dupes = await _dedupe_categorie(cur, cat)
            total_dupes += dupes
            if dupes:
                print(f"[sync_clients_actifs] ⚠ {dupes} doublon(s) supprimé(s) dans '{cat}'")

        if not total_dupes:
            print(f"[sync_clients_actifs] ✓ Aucun doublon détecté")

        print(f"[sync_clients_actifs] ── Fin {datetime.now().strftime('%H:%M:%S')} ──\n")