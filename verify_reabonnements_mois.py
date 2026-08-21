"""
verify_reabonnements_mois.py
────────────────────────────
Script de contrôle et rattrapage des réabonnements du mois en cours.

Ce que fait le script :

  1. Liste TOUS les réabonnements du mois courant depuis `subscription_info`.
     Un réabonnement = un paiement pour un email qui avait déjà au moins
     un paiement antérieur.

  2. Compte combien de ces réabonnements ne sont PAS validés dans
     `subscriptions` (c'est-à-dire : pas de ligne active dans `subscriptions`
     pour le user_id correspondant, avec une expires_at cohérente).

  3. Valide AUTOMATIQUEMENT ces réabonnements non validés :
        - status = 'active' dans subscription_info
        - INSERT (ou UPDATE si actif existant) dans subscriptions
        - note = 'reabonnement auto-valide (rattrapage)'

Usage :
    python verify_reabonnements_mois.py            # mode DRY-RUN (par défaut)
    python verify_reabonnements_mois.py --apply    # exécute réellement les validations
"""

import asyncio
import sys
from datetime import datetime
from db import get_db


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _fmt(dt) -> str:
    if not dt:
        return "—"
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M")


async def _get_reabonnements_du_mois(cur) -> list[dict]:
    """
    Retourne tous les paiements du mois courant qui sont des RÉABONNEMENTS,
    c'est-à-dire : email ayant déjà au moins un paiement antérieur au mois.
    """
    await cur.execute("""
        SELECT
            si.id,
            si.email,
            si.plan,
            si.duration_days,
            si.started_at,
            si.expires_at,
            si.status,
            si.paid_at,
            si.amount_usd,
            si.note
        FROM subscription_info si
        WHERE YEAR(si.paid_at)  = YEAR(NOW())
          AND MONTH(si.paid_at) = MONTH(NOW())
          AND EXISTS (
              SELECT 1
              FROM subscription_info si2
              WHERE si2.email  = si.email
                AND si2.paid_at < si.paid_at
          )
        ORDER BY si.paid_at ASC
    """)
    return list(await cur.fetchall())


async def _find_telegram_id(cur, email: str) -> int | None:
    await cur.execute(
        "SELECT telegram_id FROM users WHERE email = %s LIMIT 1",
        (email,)
    )
    row = await cur.fetchone()
    if row and row.get("telegram_id"):
        return int(row["telegram_id"])
    return None


async def _find_active_subscription(cur, telegram_id: int) -> dict | None:
    """
    Cherche un abonnement ACTIF (au sens : expires_at > NOW()) dans
    `subscriptions` pour ce user.
    """
    await cur.execute(
        """
        SELECT id, expires_at, plan, duration_days
        FROM subscriptions
        WHERE user_id = %s
          AND expires_at > NOW()
        ORDER BY expires_at DESC
        LIMIT 1
        """,
        (telegram_id,)
    )
    return await cur.fetchone()


async def _is_validated(cur, telegram_id: int, expected_expires_at) -> bool:
    """
    Un réabonnement est considéré validé si :
      - il existe une ligne dans `subscriptions` pour ce user_id
      - avec expires_at >= expected_expires_at (à la journée près)
    """
    await cur.execute(
        """
        SELECT 1
        FROM subscriptions
        WHERE user_id = %s
          AND DATE(expires_at) >= DATE(%s)
        LIMIT 1
        """,
        (telegram_id, expected_expires_at)
    )
    return bool(await cur.fetchone())


async def _validate_reabonnement(cur, sub_info: dict, telegram_id: int) -> str:
    """
    Applique la validation :
      1. UPDATE subscription_info → status='active', note rattrapage
      2. Prolonge (ou insère) dans `subscriptions`
    Retourne un label indiquant l'action réalisée.
    """
    # ── 1. subscription_info
    await cur.execute(
        """
        UPDATE subscription_info
        SET status     = 'active',
            note       = 'reabonnement auto-valide (rattrapage)',
            updated_at = NOW()
        WHERE id = %s
        """,
        (sub_info["id"],)
    )

    # ── 2. subscriptions : prolongation ou création
    existing_active = await _find_active_subscription(cur, telegram_id)

    if existing_active:
        await cur.execute(
            """
            UPDATE subscriptions
            SET plan          = %s,
                duration_days = %s,
                started_at    = %s,
                expires_at    = %s,
                status        = 'active',
                note          = 'reabonnement auto-valide (rattrapage)',
                updated_at    = NOW()
            WHERE id = %s
            """,
            (
                sub_info["plan"],
                sub_info["duration_days"],
                sub_info["started_at"],
                sub_info["expires_at"],
                existing_active["id"],
            )
        )
        return f"prolongé (subscriptions.id={existing_active['id']})"

    await cur.execute(
        """
        INSERT IGNORE INTO subscriptions
            (user_id, plan, duration_days, started_at, expires_at,
             status, note, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, 'active',
                'reabonnement auto-valide (rattrapage)', NOW(), NOW())
        """,
        (
            telegram_id,
            sub_info["plan"],
            sub_info["duration_days"],
            sub_info["started_at"],
            sub_info["expires_at"],
        )
    )
    return "nouvelle ligne insérée"


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

async def main(apply_changes: bool = False):
    mode = "APPLY" if apply_changes else "DRY-RUN"
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n╔══════════════════════════════════════════════════════════════════╗")
    print(f"║  VÉRIFICATION RÉABONNEMENTS DU MOIS — mode: {mode:<10}          ║")
    print(f"║  Lancé le {ts}                                        ║")
    print(f"╚══════════════════════════════════════════════════════════════════╝\n")

    async with get_db() as cur:
        # ── 1. Récupération des réabonnements du mois ─────────────────────
        reabos = await _get_reabonnements_du_mois(cur)
        total  = len(reabos)
        print(f"📊 {total} réabonnement(s) trouvé(s) ce mois-ci\n")

        if total == 0:
            print("Rien à traiter. Fin.")
            return

        # ── 2. Analyse : validés vs non validés ───────────────────────────
        non_valides: list[tuple[dict, int | None]] = []
        deja_valides = 0
        orphelins    = 0  # pas de telegram_id trouvé

        for sub_info in reabos:
            email       = sub_info["email"]
            telegram_id = await _find_telegram_id(cur, email)

            if telegram_id is None:
                orphelins += 1
                non_valides.append((sub_info, None))
                continue

            if await _is_validated(cur, telegram_id, sub_info["expires_at"]):
                deja_valides += 1
            else:
                non_valides.append((sub_info, telegram_id))

        # ── 3. Rapport ─────────────────────────────────────────────────────
        print(f"┌─────────────────────────────────────────────────────┐")
        print(f"│  ✓  Déjà validés dans `subscriptions`  : {deja_valides:>4}       │")
        print(f"│  ⚠  Non validés (à traiter)            : {len(non_valides) - orphelins:>4}       │")
        print(f"│  ✗  Orphelins (pas de telegram_id)     : {orphelins:>4}       │")
        print(f"└─────────────────────────────────────────────────────┘\n")

        if not non_valides:
            print("✅ Tous les réabonnements du mois sont validés. Fin.")
            return

        # ── 4. Détail des non validés ─────────────────────────────────────
        print("Détail des réabonnements non validés :")
        print("─" * 90)
        print(f"{'ID':>6} │ {'Email':<32} │ {'Plan':<15} │ {'Expire le':<16} │ TG ID")
        print("─" * 90)
        for sub_info, tg in non_valides:
            tg_str = str(tg) if tg else "— (orphelin)"
            print(
                f"{sub_info['id']:>6} │ "
                f"{sub_info['email']:<32.32} │ "
                f"{sub_info['plan']:<15.15} │ "
                f"{_fmt(sub_info['expires_at']):<16} │ "
                f"{tg_str}"
            )
        print("─" * 90 + "\n")

        # ── 5. Validation (ou dry-run) ────────────────────────────────────
        if not apply_changes:
            print("🔍 Mode DRY-RUN — aucune modification effectuée.")
            print("   Relance avec --apply pour exécuter les validations.\n")
            return

        print("🚀 Application des validations...\n")
        validated = 0
        skipped   = 0

        for sub_info, tg in non_valides:
            if tg is None:
                print(f"  ⏭  id={sub_info['id']} ({sub_info['email']}) — orphelin, skip")
                skipped += 1
                continue

            try:
                action = await _validate_reabonnement(cur, sub_info, tg)
                print(f"  ✓ id={sub_info['id']} ({sub_info['email']}) — {action}")
                validated += 1
            except Exception as e:
                print(f"  ✗ id={sub_info['id']} ({sub_info['email']}) — ERREUR : {e}")
                skipped += 1

        print(f"\n╔══════════════════════════════════════════════════════════════════╗")
        print(f"║  BILAN : {validated} validé(s), {skipped} ignoré(s)                          ║")
        print(f"╚══════════════════════════════════════════════════════════════════╝\n")


async def _run(apply_flag: bool):
    """Initialise le pool DB, exécute main(), ferme proprement."""
    from db import init_pool, close_pool

    await init_pool()
    try:
        await main(apply_changes=apply_flag)
    finally:
        await close_pool()


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    asyncio.run(_run(apply_flag))