# subscription_sync.py
# Synchronise la catégorie "clients_actifs" avec les abonnements chaque soir à 23h50.
# À importer dans ton fichier scheduler et enregistrer avec add_job.

from datetime import datetime
from db import get_db
from telegram_page.categorie import _bulk_insert_members, _ensure_meta_exists

CATEGORIE_ACTIFS = "clients_actifs"


async def sync_clients_actifs():
    """
    1. Insère dans clients_actifs tous les users avec abonnement actif non expiré
    2. Retire de clients_actifs tous les users dont l'abonnement est expiré
    3. Met à jour subscriptions.status = 'expired' pour les abonnements expirés
    """
    now = datetime.now()
    ts  = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[sync_clients_actifs] ── Début {ts} ──")

    async with get_db() as cur:

        # ── 1. Abonnements actifs non expirés ─────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE status = 'active'
              AND expires_at > NOW()
        """)
        actifs = [r["user_id"] for r in await cur.fetchall()]

        await _ensure_meta_exists(cur, CATEGORIE_ACTIFS)
        added = await _bulk_insert_members(cur, CATEGORIE_ACTIFS, actifs)

        print(f"[sync_clients_actifs] ✓ {len(actifs)} abonné(s) actif(s)  →  {added} nouveau(x) ajouté(s)")

        # ── 2. Abonnements expirés ─────────────────────────────────────────
        await cur.execute("""
            SELECT DISTINCT user_id
            FROM subscriptions
            WHERE expires_at <= NOW()
              AND status != 'expired'
        """)
        expires = [r["user_id"] for r in await cur.fetchall()]

        # Retire uniquement ceux qui n'ont PAS d'autre abonnement actif
        to_remove = []
        for uid in expires:
            await cur.execute("""
                SELECT COUNT(*) as n FROM subscriptions
                WHERE user_id = %s
                  AND status = 'active'
                  AND expires_at > NOW()
            """, (uid,))
            still_active = (await cur.fetchone())["n"]
            if not still_active:
                to_remove.append(uid)

        removed = 0
        if to_remove:
            placeholders = ",".join(["%s"] * len(to_remove))
            await cur.execute(
                f"DELETE FROM categories "
                f"WHERE name_categorie = %s AND id_user IN ({placeholders})",
                [CATEGORIE_ACTIFS] + to_remove
            )
            removed = cur.rowcount

        # ── 3. Marque les abonnements comme expirés ────────────────────────
        if expires:
            placeholders = ",".join(["%s"] * len(expires))
            await cur.execute(
                f"UPDATE subscriptions SET status = 'expired', updated_at = NOW() "
                f"WHERE user_id IN ({placeholders}) "
                f"  AND expires_at <= NOW() AND status != 'expired'",
                expires
            )
            expired_count = cur.rowcount
        else:
            expired_count = 0

        print(f"[sync_clients_actifs] ✓ {len(expires)} abonnement(s) expiré(s)  →  {removed} retiré(s) de la catégorie")
        print(f"[sync_clients_actifs] ✓ {expired_count} abonnement(s) marqué(s) 'expired' dans subscriptions")
        print(f"[sync_clients_actifs] ── Fin {datetime.now().strftime('%H:%M:%S')} ──\n")